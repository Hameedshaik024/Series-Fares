import os
import time
import uuid
import functools
import threading
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

import airiq_client
import poster

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
AIRIQ_USER = os.environ.get("AIRIQ_USER", "")
AIRIQ_PASS = os.environ.get("AIRIQ_PASS", "")

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


def _run_generate_job(job_id, origin, dest, year, month, markup, theme, show_logo):
    def progress_cb(day, total, status):
        with _jobs_lock:
            _jobs[job_id]["progress"] = {"day": day, "total": total, "last_status": status}

    try:
        fares = airiq_client.scrape_month(origin, dest, year, month, progress_cb=progress_cb)
        png_bytes = poster.build(origin, dest, year, month, fares, markup, theme, show_logo=show_logo)
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
    year = body.get("year")
    month = body.get("month")
    markup = float(body.get("markup") or 0)
    theme = body.get("theme", "sunset")
    show_logo = bool(body.get("showLogo", True))

    if not all([origin, dest, year, month]):
        return jsonify({"error": "missing_params", "detail": "origin, dest, year, month are required"}), 400
    if theme not in poster.THEMES:
        return jsonify({"error": "bad_theme", "detail": f"theme must be one of {list(poster.THEMES)}"}), 400
    if not airiq_client.has_session():
        return jsonify({"error": "not_logged_in"}), 401

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "progress": None, "result": None, "error": None, "created": time.time()}

    thread = threading.Thread(
        target=_run_generate_job,
        args=(job_id, origin, dest, int(year), int(month), markup, theme, show_logo),
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
        return jsonify({"status": job["status"], "progress": job["progress"], "error": job["error"]})


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
