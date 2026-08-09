import os
import time
import uuid
import datetime
import functools
import threading
import requests
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

import airiq_client
import poster
import pricing

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
AIRIQ_USER = os.environ.get("AIRIQ_USER", "")
AIRIQ_PASS = os.environ.get("AIRIQ_PASS", "")

WHATSAPP_SIDECAR_URL = os.environ.get("WHATSAPP_SIDECAR_URL", "http://127.0.0.1:3000")
WHATSAPP_SHARED_SECRET = os.environ.get("WHATSAPP_SHARED_SECRET", "")
WHATSAPP_GROUP_ID = os.environ.get("WHATSAPP_GROUP_ID", "")
WHATSAPP_CAPTION = "\U0001F4DE Contact Details : 9951661243"  # "📞 Contact Details : 9951661243"

# In-memory job store for /api/generate. A "Generate" call scrapes a whole
# month day-by-day (AirIQ has no bulk endpoint), which reliably takes
# longer than any HTTP/proxy timeout on slow free-tier hardware. So the
# HTTP request only *starts* the job and returns immediately; the frontend
# polls for progress/result. This makes it immune to gunicorn/Render
# timeouts regardless of how long the underlying scrape takes.
_jobs = {}
_jobs_lock = threading.Lock()
_JOB_TTL_SECONDS = 3600


def _prune_old_jobs():
    cutoff = time.time() - _JOB_TTL_SECONDS
    with _jobs_lock:
        for jid in [j for j, v in _jobs.items() if v["created"] < cutoff]:
            del _jobs[jid]


def _next_30_days():
    today = datetime.date.today()
    return [today + datetime.timedelta(days=i) for i in range(30)]


def _scrape_and_price(origin, dest, dates, manual_markup, progress_cb):
    """Shared by both job types: scrape both fare tabs for each date, apply
    the same pricing.pick_fare rule, return the filtered+sorted rows
    poster.build expects (dates with no fare from either source are simply
    dropped)."""
    raw = airiq_client.scrape_range(origin, dest, dates, progress_cb=progress_cb)
    priced_days = []
    for d in dates:
        day_data = raw.get(d.isoformat(), {"airiq": [], "marketplace": []})
        priced = pricing.pick_fare(day_data, manual_markup=manual_markup)
        if priced:
            priced_days.append({"date": d, **priced})
    priced_days.sort(key=lambda r: r["date"])
    return priced_days


def _run_generate_job(job_id, origin, dest, markup, theme, show_logo):
    def progress_cb(day, total, status):
        with _jobs_lock:
            _jobs[job_id]["progress"] = {"day": day, "total": total, "last_status": status}

    try:
        dates = _next_30_days()
        priced_days = _scrape_and_price(origin, dest, dates, markup, progress_cb)
        png_bytes = poster.build(origin, dest, dates, priced_days, theme, show_logo=show_logo)
        with _jobs_lock:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["result"] = png_bytes
    except RuntimeError as e:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = "not_logged_in" if str(e) == "not_logged_in" else str(e)
    except Exception as e:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(e)


def _run_whatsapp_job(job_id, origin, dest, theme):
    def progress_cb(day, total, status):
        with _jobs_lock:
            _jobs[job_id]["progress"] = {"day": day, "total": total, "last_status": status}

    try:
        dates = _next_30_days()
        # manual_markup=0: the automatic AirIQ+500/MarketPlace+0 rule in
        # pricing.pick_fare is what the WhatsApp button's markup now is -
        # no separate flat add-on on top (see plan for why).
        priced_days = _scrape_and_price(origin, dest, dates, 0, progress_cb)
        png_bytes = poster.build(origin, dest, dates, priced_days, theme, show_logo=True)

        resp = requests.post(
            f"{WHATSAPP_SIDECAR_URL}/send",
            headers={"x-internal-secret": WHATSAPP_SHARED_SECRET},
            data={"groupId": WHATSAPP_GROUP_ID, "caption": WHATSAPP_CAPTION},
            files={"image": ("poster.png", png_bytes, "image/png")},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"whatsapp_send_failed: {resp.status_code} {resp.text}")

        with _jobs_lock:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["result"] = {"sent": True}
    except RuntimeError as e:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = "not_logged_in" if str(e) == "not_logged_in" else str(e)
    except Exception as e:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(e)


app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)  # harmless no-op now that frontend is same-origin; kept in case of a split deploy later


@app.route("/")
def index():
    return app.send_static_file("index.html")


def require_auth(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not APP_PASSWORD:
            return jsonify({"error": "server_misconfigured", "detail": "APP_PASSWORD not set"}), 500
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if token != APP_PASSWORD:
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "logged_in": airiq_client.has_session()})


@app.route("/api/login/start", methods=["POST"])
@require_auth
def login_start():
    if not AIRIQ_USER or not AIRIQ_PASS:
        return jsonify({"error": "server_misconfigured", "detail": "AIRIQ_USER/AIRIQ_PASS not set"}), 500
    try:
        result = airiq_client.login_start(AIRIQ_USER, AIRIQ_PASS)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": "login_failed", "detail": str(e)}), 500


@app.route("/api/login/verify", methods=["POST"])
@require_auth
def login_verify():
    body = request.get_json(force=True) or {}
    otp = body.get("otp")
    if not otp:
        return jsonify({"error": "missing_otp"}), 400
    try:
        result = airiq_client.login_verify(otp)
        status_code = 200 if result["status"] == "ok" else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"error": "verify_failed", "detail": str(e)}), 500


@app.route("/api/origins")
@require_auth
def origins():
    try:
        return jsonify(airiq_client.list_origins())
    except Exception as e:
        return jsonify({"error": "fetch_failed", "detail": str(e)}), 500


@app.route("/api/destinations")
@require_auth
def destinations():
    origin = request.args.get("origin", "")
    if not origin:
        return jsonify({"error": "missing_origin"}), 400
    try:
        return jsonify(airiq_client.list_destinations(origin))
    except Exception as e:
        return jsonify({"error": "fetch_failed", "detail": str(e)}), 500


@app.route("/api/generate", methods=["POST"])
@require_auth
def generate():
    _prune_old_jobs()
    body = request.get_json(force=True) or {}
    origin = body.get("origin")
    dest = body.get("dest")
    markup = float(body.get("markup") or 0)
    theme = body.get("theme", "sunset")
    show_logo = bool(body.get("showLogo", True))

    if not all([origin, dest]):
        return jsonify({"error": "missing_params", "detail": "origin and dest are required"}), 400
    if theme not in poster.THEMES:
        return jsonify({"error": "bad_theme", "detail": f"theme must be one of {list(poster.THEMES)}"}), 400
    if not airiq_client.has_session():
        return jsonify({"error": "not_logged_in"}), 401

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "progress": None, "result": None, "error": None, "created": time.time(), "kind": "poster"}

    thread = threading.Thread(
        target=_run_generate_job,
        args=(job_id, origin, dest, markup, theme, show_logo),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id}), 202


@app.route("/api/generate/status/<job_id>")
@require_auth
def generate_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"error": "unknown_job"}), 404
        payload = {"status": job["status"], "progress": job["progress"], "error": job["error"]}
        if job.get("kind") == "whatsapp":
            payload["result"] = job["result"]
        return jsonify(payload)


@app.route("/api/generate/result/<job_id>")
@require_auth
def generate_result(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"error": "unknown_job"}), 404
        if job["status"] != "done":
            return jsonify({"error": "not_ready", "status": job["status"]}), 409
        png_bytes = job["result"]
    return Response(png_bytes, mimetype="image/png")


@app.route("/api/whatsapp/qr")
@require_auth
def whatsapp_qr():
    try:
        resp = requests.get(
            f"{WHATSAPP_SIDECAR_URL}/qr",
            headers={"x-internal-secret": WHATSAPP_SHARED_SECRET},
            timeout=10,
        )
    except Exception as e:
        return jsonify({"error": "sidecar_unreachable", "detail": str(e)}), 502
    if resp.headers.get("Content-Type", "").startswith("image/"):
        return Response(resp.content, mimetype="image/png")
    return Response(resp.content, status=resp.status_code, mimetype="application/json")


@app.route("/api/whatsapp/groups")
@require_auth
def whatsapp_groups():
    try:
        resp = requests.get(
            f"{WHATSAPP_SIDECAR_URL}/groups",
            headers={"x-internal-secret": WHATSAPP_SHARED_SECRET},
            timeout=15,
        )
    except Exception as e:
        return jsonify({"error": "sidecar_unreachable", "detail": str(e)}), 502
    return Response(resp.content, status=resp.status_code, mimetype="application/json")


@app.route("/api/whatsapp/send-monthly", methods=["POST"])
@require_auth
def whatsapp_send_monthly():
    _prune_old_jobs()
    if not airiq_client.has_session():
        return jsonify({"error": "not_logged_in"}), 401
    if not WHATSAPP_GROUP_ID:
        return jsonify({"error": "server_misconfigured", "detail": "WHATSAPP_GROUP_ID not set"}), 500

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running", "progress": None, "result": None, "error": None,
            "created": time.time(), "kind": "whatsapp",
        }

    thread = threading.Thread(
        target=_run_whatsapp_job,
        args=(job_id, "HYD", "DXB", "sunset"),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id}), 202


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
