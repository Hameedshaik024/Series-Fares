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
WHATSAPP_CAPTION = "\U0001F4DE Contact Details : 9951661243"  # "📞 Contact Details : 9951661243"

# One-click send now covers three fixed routes, each posted into its own
# group with the same caption. WHATSAPP_GROUP_ID is kept as the HYD-DXB
# group so existing deployments don't lose their setting when upgrading.
WHATSAPP_ROUTES = [
    ("HYD", "DXB", os.environ.get("WHATSAPP_GROUP_ID_HYD_DXB") or os.environ.get("WHATSAPP_GROUP_ID", "")),
    ("HYD", "RUH", os.environ.get("WHATSAPP_GROUP_ID_HYD_RUH", "")),
    ("HYD", "MCT", os.environ.get("WHATSAPP_GROUP_ID_HYD_MCT", "")),
]

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


def _price_scraped(raw, dates, manual_markup):
    """Applies the shared pricing.pick_fare rule to a raw scrape result,
    returning the filtered+sorted rows poster.build expects (dates with no
    fare from either source are simply dropped)."""
    priced_days = []
    for d in dates:
        day_data = raw.get(d.isoformat(), {"airiq": [], "marketplace": []})
        priced = pricing.pick_fare(day_data, manual_markup=manual_markup)
        if priced:
            priced_days.append({"date": d, **priced})
    priced_days.sort(key=lambda r: r["date"])
    return priced_days


def _scrape_and_price(origin, dest, dates, manual_markup, progress_cb):
    """Shared by both job types: scrape both fare tabs for each date, apply
    the same pricing.pick_fare rule."""
    raw = airiq_client.scrape_range(origin, dest, dates, progress_cb=progress_cb)
    return _price_scraped(raw, dates, manual_markup)


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


def _run_whatsapp_job(job_id, theme):
    """Builds and sends a poster for each route in WHATSAPP_ROUTES, into its
    own group, same caption. One route's failure (no fares, send error,
    missing group id) doesn't stop the others - each is recorded
    independently so a partial run still gets the working routes out.

    Scrapes all active routes with ONE shared browser
    (airiq_client.scrape_multiple_routes) rather than launching a separate
    Chromium instance per route - launching 3 back-to-back was the likely
    cause of a confirmed Render free-tier OOM crash on a 3-route send."""
    dates = _next_30_days()
    results = {}

    active_routes = [(o, d, g) for o, d, g in WHATSAPP_ROUTES if g]
    for origin, dest, group_id in WHATSAPP_ROUTES:
        if not group_id:
            results[f"{origin}-{dest}"] = {"error": "group_not_set"}

    if not active_routes:
        with _jobs_lock:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["result"] = results
        return

    route_total = len(active_routes)

    def progress_cb(route_idx, route_total_, day, total, status):
        origin, dest, _ = active_routes[route_idx - 1]
        with _jobs_lock:
            _jobs[job_id]["progress"] = {
                "route": f"{origin}-{dest}", "route_num": route_idx, "route_total": route_total,
                "day": day, "total": total, "last_status": status,
            }

    session_died = False
    try:
        route_pairs = [(o, d) for o, d, _ in active_routes]
        # manual_markup=0: the automatic AirIQ+500/MarketPlace+0 rule in
        # pricing.pick_fare is what the WhatsApp button's markup now is -
        # no separate flat add-on on top (see plan for why).
        raw_by_route = airiq_client.scrape_multiple_routes(route_pairs, dates, progress_cb=progress_cb)
    except RuntimeError as e:
        if str(e) != "not_logged_in":
            with _jobs_lock:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = str(e)
                _jobs[job_id]["result"] = results
            return
        # Session died mid-run - keep whatever routes completed before
        # that instead of discarding the whole job.
        raw_by_route = getattr(e, "partial_results", {})
        session_died = True
    except Exception as e:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(e)
            _jobs[job_id]["result"] = results
        return

    # Build every poster first, send them all only once every route is
    # ready - so the messages land in their groups back-to-back rather
    # than trickling out as each one individually finishes building.
    posters = {}
    for origin, dest, group_id in active_routes:
        label = f"{origin}-{dest}"
        raw = raw_by_route.get((origin, dest))
        if raw is None:
            results[label] = {"error": "not_logged_in" if session_died else "not_scraped"}
            continue
        try:
            priced_days = _price_scraped(raw, dates, 0)
            posters[label] = (group_id, poster.build(origin, dest, dates, priced_days, theme, show_logo=True))
        except Exception as e:
            results[label] = {"error": str(e)}

    for label, (group_id, png_bytes) in posters.items():
        try:
            resp = requests.post(
                f"{WHATSAPP_SIDECAR_URL}/send",
                headers={"x-internal-secret": WHATSAPP_SHARED_SECRET},
                data={"groupId": group_id, "caption": WHATSAPP_CAPTION},
                files={"image": ("poster.png", png_bytes, "image/png")},
                timeout=30,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"whatsapp_send_failed: {resp.status_code} {resp.text}")

            results[label] = {"sent": True}
        except Exception as e:
            results[label] = {"error": str(e)}

    if session_died:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = "not_logged_in"
            _jobs[job_id]["result"] = results
        return

    with _jobs_lock:
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = results


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
    if not any(group_id for _, _, group_id in WHATSAPP_ROUTES):
        return jsonify({
            "error": "server_misconfigured",
            "detail": "No WHATSAPP_GROUP_ID_* env vars are set (need at least one of "
                       "WHATSAPP_GROUP_ID_HYD_DXB / _HYD_RUH / _HYD_MCT)",
        }), 500

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running", "progress": None, "result": None, "error": None,
            "created": time.time(), "kind": "whatsapp",
        }

    thread = threading.Thread(
        target=_run_whatsapp_job,
        args=(job_id, "sunset"),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id}), 202


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
