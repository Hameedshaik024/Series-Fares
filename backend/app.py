import os
import time
import uuid
import datetime
import functools
import threading
import requests
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

import alhind_client
import poster
import named_flights

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
ALHIND_USER = os.environ.get("ALHIND_USER", "")
ALHIND_PASS = os.environ.get("ALHIND_PASS", "")

WHATSAPP_SIDECAR_URL = os.environ.get("WHATSAPP_SIDECAR_URL", "http://127.0.0.1:3000")
WHATSAPP_SHARED_SECRET = os.environ.get("WHATSAPP_SHARED_SECRET", "")
WHATSAPP_CAPTION = "\U0001F4DE Contact Details : 9951661243"  # "📞 Contact Details : 9951661243"

# Named-flight (Alhind-sourced) groups - each is a bundle of specific
# flight+fare-class routes (see named_flights.py), one poster image per
# route, sent to that group's WhatsApp group. Add a new "<KEY>":
# os.environ.get("WHATSAPP_GROUP_ID_<KEY>", "") entry here (matching a
# key in named_flights.NAMED_FLIGHT_GROUPS) for each new group.
ALHIND_GROUP_IDS = {
    "dubai": os.environ.get("WHATSAPP_GROUP_ID_DUBAI", ""),
}

# In-memory job store. A named-flight run scrapes many dates across
# several routes, which reliably takes longer than any HTTP/proxy timeout
# on slow free-tier hardware. So the HTTP request only *starts* the job
# and returns immediately; the frontend polls for progress/result. This
# makes it immune to gunicorn/Render timeouts regardless of how long the
# underlying scrape takes.
_jobs = {}
_jobs_lock = threading.Lock()
_JOB_TTL_SECONDS = 3600


def _prune_old_jobs():
    cutoff = time.time() - _JOB_TTL_SECONDS
    with _jobs_lock:
        for jid in [j for j, v in _jobs.items() if v["created"] < cutoff]:
            del _jobs[jid]


def _next_days(n):
    today = datetime.date.today()
    return [today + datetime.timedelta(days=i) for i in range(n)]


def _alhind_flight_to_poster_entry(date, match):
    """Converts one alhind_client.find_named_fare() result into the
    {"date", "source", "flight", "base_fare", "final_fare"} shape
    poster.build() expects. No markup is added here: these are specific
    named flights/fare-classes chosen by hand, not a resale-price rule."""
    flight = {
        "airline": match.get("airline"),
        "logo_url": match.get("logo_url"),
        "flight_no": match.get("flight_no"),
        "time": (f"{match['dep_time']} - {match['arr_time']}"
                 if match.get("dep_time") and match.get("arr_time") else None),
        "duration": match.get("duration"),
        "stops": match.get("stops"),
        "baggage": match.get("baggage"),
    }
    fare = match.get("fare_inr")
    return {"date": date, "source": "alhind", "flight": flight, "base_fare": fare, "final_fare": fare}


def _route_label(route):
    """Human-readable label for a named_flights.py route entry, for job
    progress/results. flight_no may be a single string or a list of
    alternative flight numbers (matches any of them); fare_type may be
    None, meaning "lowest fare of any class"."""
    flight_no = route["flight_no"]
    flight_no_text = " / ".join(flight_no) if isinstance(flight_no, (list, tuple)) else flight_no
    fare_type_text = route.get("fare_type") or "lowest fare"
    return f"{route['origin_code']}-{route['dest_code']} {flight_no_text} ({fare_type_text})"


NAMED_FLIGHT_DAYS_PER_POSTER = 15


def _run_alhind_named_job(job_id, group_key, theme="sunset"):
    """named_flights.NAMED_FLIGHT_GROUPS[group_key] is a list of
    "posters" - each poster bundles several routes (e.g. 3 routes all
    flying into the same hub city) into ONE stacked image via
    poster.build_multi(), all sharing the same NAMED_FLIGHT_DAYS_PER_POSTER
    -day range. Every route across every poster in the group is scraped
    in one shared browser session first; only once everything's scraped
    are the poster images built and sent, one image per poster, back-to-
    back, to the group's WhatsApp group. A route with no matches at all
    is just left out of its poster's image; a poster with NO route
    matches at all is skipped entirely."""
    posters_def = named_flights.NAMED_FLIGHT_GROUPS.get(group_key)
    if not posters_def:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = f"unknown_group: {group_key}"
        return

    all_routes = [route for p in posters_def for route in p["routes"]]
    if not all_routes:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = "group_not_configured"
        return

    group_id = ALHIND_GROUP_IDS.get(group_key)
    if not group_id:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = "group_not_set"
        return

    dates = _next_days(NAMED_FLIGHT_DAYS_PER_POSTER)
    route_total = len(all_routes)
    results = {}
    labels = [_route_label(r) for r in all_routes]

    def progress_cb(route_idx, _route_total, day, total, status):
        with _jobs_lock:
            _jobs[job_id]["progress"] = {
                "route": labels[route_idx - 1], "route_num": route_idx, "route_total": route_total,
                "day": day, "total": total, "last_status": status,
            }

    try:
        # Scrapes every route across every poster sharing ONE browser
        # session, rather than a fresh session (and fresh login) per
        # route - confirmed live this matters a lot: separate sessions
        # per route meant that many more relogin attempts, and Alhind
        # started requiring a fresh device-verification OTP on many of
        # them (a stream of unsolicited OTP texts, and routes silently
        # failing since a mid-scrape OTP prompt can't be completed
        # unattended).
        raw_by_route = alhind_client.scrape_named_flight_ranges(
            username=ALHIND_USER, password=ALHIND_PASS,
            routes=all_routes, dates=dates, progress_cb=progress_cb,
        )
    except Exception as e:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(e)
        return

    # Build every poster image first, send them all only once every
    # route is scraped - so the messages land in the group back-to-back
    # rather than trickling out as each one individually finishes.
    built_posters = []  # (title, png_bytes)
    idx = 0
    for poster_def in posters_def:
        title = poster_def["title"]
        segments = []
        route_results = {}
        any_fares = False
        for route in poster_def["routes"]:
            label = labels[idx]
            raw = raw_by_route.get(idx, {})
            priced_days = [_alhind_flight_to_poster_entry(d, raw[d.isoformat()])
                            for d in dates if raw.get(d.isoformat())]
            if priced_days:
                any_fares = True
                route_results[label] = {"fare_days": len(priced_days)}
            else:
                route_results[label] = {"error": "not_found_any_day"}
            segments.append({
                "origin": route["origin_code"], "dest": route["dest_code"],
                "origin_label": route["origin_label"], "dest_label": route["dest_label"],
                "priced_days": priced_days,
            })
            idx += 1

        if not any_fares:
            results[title] = {"error": "not_found_any_route", "routes": route_results}
            continue

        try:
            png_bytes = poster.build_multi(segments, dates, theme, show_logo=True, poster_title=title)
            built_posters.append((title, png_bytes, route_results))
        except Exception as e:
            results[title] = {"error": str(e), "routes": route_results}

    for title, png_bytes, route_results in built_posters:
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
            results[title] = {"sent": True, "routes": route_results}
        except Exception as e:
            results[title] = {"error": str(e), "routes": route_results}

    with _jobs_lock:
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = results


ALHIND_KEEP_ALIVE_INTERVAL_SECONDS = 600  # 10 min


def _any_job_running():
    with _jobs_lock:
        return any(j["status"] == "running" for j in _jobs.values())


def _alhind_keep_alive_loop():
    """Background loop, started once at process startup: gently pings
    the Alhind session every ALHIND_KEEP_ALIVE_INTERVAL_SECONDS so it
    doesn't go idle and die between real scrapes - see alhind_client.
    keep_alive_ping for exactly what it does and doesn't do (never sends
    a fresh OTP). Skips a cycle entirely while a real job is running,
    both because there's no need (the job's own searches already keep
    the session active) and to avoid two Chromium instances competing
    for memory on the free tier."""
    while True:
        time.sleep(ALHIND_KEEP_ALIVE_INTERVAL_SECONDS)
        if _any_job_running():
            continue
        try:
            result = alhind_client.keep_alive_ping(ALHIND_USER, ALHIND_PASS)
            print(f"[keep_alive] alhind: {result}", flush=True)
        except Exception as e:
            print(f"[keep_alive] alhind error: {e}", flush=True)


# ALHIND_KEEP_ALIVE=0 lets one-off scripts (daily_posters.py) import this
# module's helpers without a second browser relogging in behind their back.
if ALHIND_USER and ALHIND_PASS and os.environ.get("ALHIND_KEEP_ALIVE", "1") != "0":
    threading.Thread(target=_alhind_keep_alive_loop, daemon=True).start()


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
    return jsonify({
        "status": "ok",
        "alhind_logged_in": alhind_client.has_session(),
    })


@app.route("/api/alhind/login/start", methods=["POST"])
@require_auth
def alhind_login_start():
    """Only needed ONCE ever per container to register the device with
    Alhind (see alhind_client.py) - after that, every scrape relogs in
    automatically with no OTP."""
    if not ALHIND_USER or not ALHIND_PASS:
        return jsonify({"error": "server_misconfigured", "detail": "ALHIND_USER/ALHIND_PASS not set"}), 500
    try:
        result = alhind_client.login_start(ALHIND_USER, ALHIND_PASS)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": "login_failed", "detail": str(e)}), 500


@app.route("/api/alhind/login/verify", methods=["POST"])
@require_auth
def alhind_login_verify():
    body = request.get_json(force=True) or {}
    otp = body.get("otp")
    if not otp:
        return jsonify({"error": "missing_otp"}), 400
    try:
        result = alhind_client.login_verify(otp)
        status_code = 200 if result["status"] == "ok" else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"error": "verify_failed", "detail": str(e)}), 500


@app.route("/api/generate/status/<job_id>")
@require_auth
def generate_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return jsonify({"error": "unknown_job"}), 404
        return jsonify({
            "status": job["status"], "progress": job["progress"],
            "error": job["error"], "result": job["result"],
        })


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


@app.route("/api/whatsapp/send-named-flights", methods=["POST"])
@require_auth
def whatsapp_send_named_flights():
    """group: which named_flights.NAMED_FLIGHT_GROUPS entry to run, e.g.
    "dubai" - builds one poster per specific flight+fare-class in that
    group and sends each as its own image to that group's WhatsApp
    group."""
    _prune_old_jobs()
    body = request.get_json(force=True) or {}
    group_key = body.get("group")
    if not group_key or group_key not in named_flights.NAMED_FLIGHT_GROUPS:
        return jsonify({
            "error": "bad_group",
            "detail": f"group must be one of {list(named_flights.NAMED_FLIGHT_GROUPS)}",
        }), 400
    if not ALHIND_USER or not ALHIND_PASS:
        return jsonify({"error": "server_misconfigured", "detail": "ALHIND_USER/ALHIND_PASS not set"}), 500
    if not alhind_client.has_session():
        return jsonify({"error": "not_logged_in"}), 401
    if not ALHIND_GROUP_IDS.get(group_key):
        return jsonify({
            "error": "server_misconfigured",
            "detail": f"No WhatsApp group id set for '{group_key}'",
        }), 500

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running", "progress": None, "result": None, "error": None,
            "created": time.time(), "kind": "whatsapp_named",
        }

    thread = threading.Thread(
        target=_run_alhind_named_job,
        args=(job_id, group_key, "sunset"),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id}), 202


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
