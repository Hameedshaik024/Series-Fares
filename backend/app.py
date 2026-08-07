import os
import functools
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

import airiq_client
import poster

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
AIRIQ_USER = os.environ.get("AIRIQ_USER", "")
AIRIQ_PASS = os.environ.get("AIRIQ_PASS", "")

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

    try:
        fares = airiq_client.scrape_month(origin, dest, int(year), int(month))
    except RuntimeError as e:
        if str(e) == "not_logged_in":
            return jsonify({"error": "not_logged_in"}), 401
        return jsonify({"error": "scrape_failed", "detail": str(e)}), 500
    except Exception as e:
        return jsonify({"error": "scrape_failed", "detail": str(e)}), 500

    try:
        png_bytes = poster.build(origin, dest, int(year), int(month), fares, markup, theme, show_logo=show_logo)
    except Exception as e:
        return jsonify({"error": "render_failed", "detail": str(e)}), 500

    return Response(png_bytes, mimetype="image/png")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
