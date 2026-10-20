"""Daily poster run for GitHub Actions (.github/workflows/daily-posters.yml).

Scrapes the Dubai named-flight group, renders its posters and assembles
the phone app's static site (mobile/ + posters/ + status.json) for GitHub
Pages. No server stays up between runs, so:

- The Alhind session lives in the ALHIND_STATE repo secret: the workflow
  hands it in, and this run writes it back after every good login. Every
  login renews Alhind's 15-day remembered-device cookie, so a daily run
  normally never sees an OTP.
- Live progress goes to the POSTERS_LIVE repo variable, which the phone
  app polls. If Alhind does want an OTP, the code reaches this run through
  the ALHIND_OTP variable - typed into the app, or forwarded straight from
  the SMS by an automation on the phone. Both need GH_PAT, since the
  workflow's own GITHUB_TOKEN can't touch variables or secrets.
- Every Pages deploy replaces the whole site, so earlier days' posters
  are fetched back from the live site and published again.

Usage: python backend/daily_posters.py --site DIR [--app-only] [--days N]
"""
import argparse
import base64
import datetime
import json
import os
import re
import shutil
import statistics
import sys
import time
from pathlib import Path

import requests

# Import app.py's helpers without its keep-alive thread relogging in to
# Alhind in the background while this run scrapes.
os.environ["ALHIND_KEEP_ALIVE"] = "0"
import alhind_client  # noqa: E402
import app  # noqa: E402
import named_flights  # noqa: E402
import poster  # noqa: E402

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
GROUP = "dubai"
KEEP_DAYS = 7
OTP_WAIT_SECONDS = 15 * 60
LIVE_VAR = "POSTERS_LIVE"
OTP_VAR = "ALHIND_OTP"
STATE_SECRET = "ALHIND_STATE"
MOBILE_DIR = Path(__file__).resolve().parent.parent / "mobile"


class Failure(Exception):
    """Ends the run, with a message written for the phone app."""


def now_ist():
    return datetime.datetime.now(IST)


def short_date(d, weekday=False):
    return f"{d:%a} {d.day} {d:%b}" if weekday else f"{d.day} {d:%b}"


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class GitHub:
    """The few repo endpoints this run needs, authenticated with GH_PAT."""

    def __init__(self, token, repo):
        self.base = f"https://api.github.com/repos/{repo}"
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def _call(self, method, path, **kwargs):
        return self.session.request(method, self.base + path, timeout=30, **kwargs)

    def get_var(self, name):
        resp = self._call("GET", f"/actions/variables/{name}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()["value"]

    def set_var(self, name, value):
        resp = self._call("PATCH", f"/actions/variables/{name}", json={"name": name, "value": value})
        if resp.status_code == 404:
            resp = self._call("POST", "/actions/variables", json={"name": name, "value": value})
        resp.raise_for_status()

    def delete_var(self, name):
        resp = self._call("DELETE", f"/actions/variables/{name}")
        if resp.status_code not in (204, 404):
            resp.raise_for_status()

    def set_secret(self, name, value):
        from nacl import encoding, public  # PyNaCl, installed by the workflow

        key = self._call("GET", "/actions/secrets/public-key")
        key.raise_for_status()
        key = key.json()
        box = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
        encrypted = base64.b64encode(box.encrypt(value.encode())).decode()
        resp = self._call("PUT", f"/actions/secrets/{name}",
                          json={"encrypted_value": encrypted, "key_id": key["key_id"]})
        resp.raise_for_status()


class Live:
    """This run's status for the phone app, kept in the POSTERS_LIVE
    variable. Best-effort: a GitHub API hiccup never fails the run."""

    def __init__(self, gh, run_id, run_url):
        self.gh = gh
        self.base = {"run_id": run_id, "run_url": run_url,
                     "started": now_ist().isoformat(timespec="seconds")}
        self._last_progress = 0

    def set(self, phase, **fields):
        if phase != "running":
            print(f"[live] {phase} {fields or ''}", flush=True)
        if not self.gh:
            return
        state = {**self.base, "phase": phase,
                 "updated": now_ist().isoformat(timespec="seconds"), **fields}
        try:
            self.gh.set_var(LIVE_VAR, json.dumps(state, ensure_ascii=False))
        except requests.RequestException as e:
            print(f"[live] couldn't update {LIVE_VAR}: {e}", flush=True)

    def progress(self, done, total, detail):
        if done < total and time.time() - self._last_progress < 20:
            return
        self._last_progress = time.time()
        self.set("running", done=done, total=total, detail=detail)


# ---------- Alhind session ----------

def restore_session():
    state = os.environ.get("ALHIND_STATE", "").strip()
    if state:
        Path(alhind_client.AUTH_STATE_PATH).write_text(state, encoding="utf-8")


def save_session(gh):
    if not gh or not alhind_client.has_session():
        return
    try:
        gh.set_secret(STATE_SECRET, Path(alhind_client.AUTH_STATE_PATH).read_text(encoding="utf-8"))
        print("[login] saved the Alhind session for the next run", flush=True)
    except Exception as e:
        print(f"[login] couldn't save the Alhind session: {e}", flush=True)


def ensure_logged_in(gh, live, user, password):
    """Uses the saved session if Alhind still accepts it, otherwise gets
    an OTP through the phone app. Returns True if an OTP was used."""
    live.set("checking_login")
    status = alhind_client.check_session(user, password)
    if status == "unknown":
        status = alhind_client.check_session(user, password)
    print(f"[login] session check: {status}", flush=True)
    if status == "ok":
        # Each login pushes Alhind's remembered-device cookie (Loginseckey)
        # another 15 days out - saving it straight away, not just after a
        # successful scrape, is what keeps daily runs free of OTPs.
        save_session(gh)
        return False
    if status == "unknown":
        # Nothing recognisable twice running: let the scrape's own relogin have a go.
        return False
    if status == "not_registered":
        raise Failure("Alhind rejected the username or password - check the "
                      "ALHIND_USER and ALHIND_PASS secrets on GitHub.")
    otp_login(gh, live, user, password)
    return True


def extract_otp(text):
    """The code from whatever arrived: just the digits typed into the app,
    or a whole Alhind SMS ("123456 is your OTP ...") from a phone that
    forwards it automatically."""
    match = re.search(r"(?<!\d)\d{6}(?!\d)", text) or re.search(r"(?<!\d)\d{4,8}(?!\d)", text)
    return match.group(0) if match else None


def otp_login(gh, live, user, password):
    """Has Alhind text an OTP and waits for the code to show up in the
    ALHIND_OTP variable - typed into the phone app, or forwarded straight
    from the SMS by an automation on the phone (which creates the variable
    with a plain POST, so it's deleted here before and after use)."""
    if not gh:
        raise Failure("Alhind needs a new OTP, and there's no GH_PAT secret to receive it through.")
    gh.delete_var(OTP_VAR)  # never reuse a code left over from an earlier run
    if alhind_client.login_start(user, password).get("status") == "already_logged_in":
        return
    deadline = now_ist() + datetime.timedelta(seconds=OTP_WAIT_SECONDS)
    live.set("otp_needed", deadline=deadline.isoformat(timespec="seconds"))
    try:
        otp = None
        while not otp and now_ist() < deadline:
            time.sleep(3)
            try:
                value = gh.get_var(OTP_VAR)
            except requests.RequestException as e:
                print(f"[login] couldn't check for the OTP: {e}", flush=True)
                continue
            if value:
                gh.delete_var(OTP_VAR)
                otp = extract_otp(value)
                if not otp:
                    print("[login] ALHIND_OTP held no code - still waiting", flush=True)
        if not otp:
            raise Failure("No OTP was entered within 15 minutes. Tap Generate now to try again.")
        live.set("verifying")
        result = alhind_client.login_verify(otp)
    finally:
        # login_verify cleans up after itself, except when it raises.
        if alhind_client._pending_login["browser"]:
            alhind_client._cleanup_pending_login()
    if result.get("status") != "ok":
        raise Failure("Alhind didn't accept that OTP - it may have been mistyped or "
                      "expired. Tap Generate now to try again.")
    save_session(gh)


# ---------- Scrape + posters ----------

def scrape(gh, live, user, password, routes, dates, used_otp):
    labels = [app._route_label(r) for r in routes]
    names = [f"{r['origin_code']} → {r['dest_code']}" for r in routes]
    total = len(routes) * len(dates)
    done = 0

    def progress(route_idx, route_total, day, day_total, status):
        nonlocal done
        done += 1
        print(f"[{route_idx}/{route_total}] {labels[route_idx - 1]} day {day}/{day_total}: {status}", flush=True)
        live.progress(done, total, f"{names[route_idx - 1]} · day {day} of {day_total}")

    while True:
        done = 0
        live.set("running", done=0, total=total, detail=f"{names[0]} · day 1 of {len(dates)}")
        try:
            return alhind_client.scrape_named_flight_ranges(user, password, routes, dates, progress)
        except alhind_client.OtpRequired:
            if used_otp:
                raise Failure("Alhind asked for another OTP partway through. Tap Generate now to try again.")
            print("[login] Alhind wants an OTP mid-scrape - getting one, then starting over", flush=True)
            otp_login(gh, live, user, password)
            used_otp = True


def blank_days(name, priced, dates):
    have = {p["date"] for p in priced}
    missing = [d for d in dates if d not in have]
    return f"{name} {', '.join(short_date(d) for d in missing)}" if missing else None


def high_fares(name, priced):
    """Fares far above the route's usual price - worth a look before
    posting (e.g. a fallback to a much pricier airline)."""
    notes = []
    fares = [p["final_fare"] for p in priced if p.get("final_fare")]
    if len(fares) >= 4:
        usual = statistics.median(fares)
        for p in priced:
            fare = p.get("final_fare")
            if fare and fare >= 2 * usual:
                flight = p["flight"]
                who = " ".join(x for x in (flight.get("airline"), flight.get("flight_no")) if x)
                notes.append(f"{name} on {short_date(p['date'], weekday=True)}: ₹{fare:,.0f}"
                             f"{f' ({who})' if who else ''} - over twice the usual ₹{usual:,.0f}")
    return notes


def build_posters(posters_def, raw_by_route, dates, out_dir, stamp):
    """Renders each poster in the group; returns their status.json entries."""
    entries = []
    idx = 0
    for poster_def in posters_def:
        segments, routes, blanks, notes = [], [], [], []
        for route in poster_def["routes"]:
            raw = raw_by_route.get(idx, {})
            idx += 1
            priced = [app._alhind_flight_to_poster_entry(d, raw[d.isoformat()])
                      for d in dates if raw.get(d.isoformat())]
            segments.append({
                "origin": route["origin_code"], "dest": route["dest_code"],
                "origin_label": route["origin_label"], "dest_label": route["dest_label"],
                "priced_days": priced,
            })
            name = f"{route['origin_code']} → {route['dest_code']}"
            routes.append({"route": name, "priced": len(priced), "total": len(dates)})
            blanks.append(blank_days(name, priced, dates))
            notes += high_fares(name, priced)
        if any(blanks):
            notes.insert(0, "Left blank: " + " · ".join(b for b in blanks if b))
        title = poster_def["title"]
        if not any(s["priced_days"] for s in segments):
            print(f"{title}: no fares on any route, skipped", flush=True)
            continue
        png = poster.build_multi(segments, dates, "sunset", show_logo=True, poster_title=title)
        filename = f"{slugify(title)}-{stamp}.png"
        (out_dir / filename).write_bytes(png)
        print(f"saved {filename}", flush=True)
        entries.append({"title": title, "file": f"posters/{out_dir.name}/{filename}",
                        "routes": routes, "notes": notes})
    return entries


# ---------- Pages site ----------

def fetch_status(pages_url):
    if not pages_url:
        return {}
    try:
        resp = requests.get(pages_url + "status.json", params={"t": int(time.time())}, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        print(f"[site] no earlier status.json (HTTP {resp.status_code}) - starting fresh", flush=True)
    except (requests.RequestException, ValueError) as e:
        print(f"[site] couldn't fetch the earlier status.json: {e}", flush=True)
    return {}


def download(url, dest):
    if dest.exists():
        return True
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[site] couldn't fetch {url}: {e} - dropping that day", flush=True)
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return True


def assemble_site(site, pages_url, today=None):
    """The whole Pages site: the phone app, status.json, and the last
    KEEP_DAYS days of posters."""
    shutil.copytree(MOBILE_DIR, site, dirs_exist_ok=True)
    previous = fetch_status(pages_url)
    earlier = [d for d in previous.get("days", []) if not today or d["date"] != today["date"]]
    earlier.sort(key=lambda d: d["date"], reverse=True)
    kept = []
    for day in earlier[:KEEP_DAYS - (1 if today else 0)]:
        if all(download(pages_url + p["file"], site / p["file"]) for p in day["posters"]):
            kept.append(day)
    days = sorted(([today] if today else []) + kept, key=lambda d: d["date"], reverse=True)
    status = {"generated": today["generated"] if today else previous.get("generated"), "days": days}
    (site / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Make today's fare posters and the phone app's site.")
    parser.add_argument("--site", required=True, type=Path, help="output folder for the Pages site")
    parser.add_argument("--app-only", action="store_true",
                        help="only republish the phone app, keeping the current posters")
    parser.add_argument("--days", type=int, default=app.NAMED_FLIGHT_DAYS_PER_POSTER)
    parser.add_argument("--skip-if-made-today", action="store_true",
                        help="for the scheduled runs: if today's posters are already up, only republish them")
    args = parser.parse_args()

    pages_url = os.environ.get("PAGES_URL", "")
    if pages_url and not pages_url.endswith("/"):
        pages_url += "/"
    args.site.mkdir(parents=True, exist_ok=True)

    if args.skip_if_made_today:
        latest = (fetch_status(pages_url).get("days") or [{}])[0]
        if latest.get("date") == now_ist().date().isoformat():
            print(f"Today's posters are already up (made {latest.get('generated')}) - republishing them as they are",
                  flush=True)
            args.app_only = True

    if args.app_only:
        assemble_site(args.site, pages_url)
        return

    user, password = os.environ["ALHIND_USER"], os.environ["ALHIND_PASS"]
    token, repo = os.environ.get("GH_PAT"), os.environ.get("GITHUB_REPOSITORY")
    gh = GitHub(token, repo) if token and repo else None
    run_id = os.environ.get("GITHUB_RUN_ID")
    run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{run_id}"
               if run_id else None)
    live = Live(gh, run_id, run_url)
    restore_session()

    try:
        used_otp = ensure_logged_in(gh, live, user, password)
        posters_def = named_flights.NAMED_FLIGHT_GROUPS[GROUP]
        routes = [r for p in posters_def for r in p["routes"]]
        today = now_ist().date()
        dates = [today + datetime.timedelta(days=i) for i in range(args.days)]
        raw_by_route = scrape(gh, live, user, password, routes, dates, used_otp)
        save_session(gh)

        live.set("building")
        generated = now_ist()
        out_dir = args.site / "posters" / today.isoformat()
        out_dir.mkdir(parents=True, exist_ok=True)
        posters = build_posters(posters_def, raw_by_route, dates, out_dir, generated.strftime("%H%M"))
        if not posters:
            raise Failure("Alhind returned no fares for any route.")
        entry = {"date": today.isoformat(), "generated": generated.isoformat(timespec="seconds"),
                 "range": f"{short_date(dates[0])} – {short_date(dates[-1])}", "posters": posters}
        assemble_site(args.site, pages_url, entry)
        live.set("publishing", generated=entry["generated"])
    except Failure as e:
        live.set("failed", message=str(e))
        sys.exit(f"FAILED: {e}")
    except Exception as e:
        live.set("failed", message=f"Something went wrong: {str(e).splitlines()[0][:200]}")
        raise


if __name__ == "__main__":
    main()
