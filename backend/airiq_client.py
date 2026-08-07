"""
AirIQ (airiq.in) scraping client.

Wraps the login (OTP-gated), route discovery, and per-day fare search flows
against the AirIQ B2B agent portal, using Playwright.

Only reads the default "AIR IQ" tab of search results (never clicks the
"MARKET PLACE" tab) per the product requirement to ignore market fares.
"""
import os
import re
import time
import calendar
import threading
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "state")
os.makedirs(STATE_DIR, exist_ok=True)
AUTH_STATE_PATH = os.path.join(STATE_DIR, "storage_state.json")

SEARCH_URL = "https://airiq.in/Admin/Search.aspx"
HOME_URL = "https://airiq.in/"

_origins_cache = None
_destinations_cache = {}  # origin code -> list of {code, label}
_lock = threading.Lock()

# in-memory state for the two-step (start/verify) login flow
_pending_login = {"browser": None, "playwright": None, "page": None, "context": None}


def has_session():
    return os.path.exists(AUTH_STATE_PATH)


def _new_context(p, headless=True):
    browser = p.chromium.launch(headless=headless)
    ctx = browser.new_context(storage_state=AUTH_STATE_PATH if has_session() else None)
    return browser, ctx


def session_is_valid():
    """Quick check: does our saved session still land on the Search page (not redirected to login)?"""
    if not has_session():
        return False
    with sync_playwright() as p:
        browser, ctx = _new_context(p)
        page = ctx.new_page()
        page.goto(SEARCH_URL, wait_until="networkidle")
        ok = "Search" in page.title() and "dest_cmd" in page.content()
        browser.close()
        return ok


# ---------- Login (two-step: start sends OTP, verify submits it) ----------

def login_start(username, password):
    """Fills credentials, submits, and leaves the browser open on the OTP page
    waiting for login_verify(). Returns once the OTP has been sent."""
    with _lock:
        if _pending_login["browser"]:
            _cleanup_pending_login()

        p = sync_playwright().start()
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(HOME_URL, wait_until="networkidle")
        page.fill("#user_txt", username)
        page.fill("#pwd_txt", password)
        with page.expect_navigation(wait_until="networkidle", timeout=20000):
            page.click("#LinkButton1")

        if "OTP" not in page.title():
            # unexpected - maybe bad credentials, no OTP step
            browser.close()
            p.stop()
            raise RuntimeError(f"Unexpected page after login: {page.title()}")

        _pending_login.update({"browser": browser, "playwright": p, "page": page})
        return {"status": "otp_sent"}


def login_verify(otp):
    with _lock:
        page = _pending_login.get("page")
        if not page:
            raise RuntimeError("No login in progress. Call login_start first.")

        page.fill("#ReceivedOTP", otp)
        with page.expect_navigation(wait_until="networkidle", timeout=20000):
            page.click("#SubmitButton")

        ok = "OTP" not in page.title()
        if ok:
            page.context.storage_state(path=AUTH_STATE_PATH)

        _cleanup_pending_login()
        return {"status": "ok" if ok else "invalid_or_expired_otp"}


def _cleanup_pending_login():
    browser = _pending_login.get("browser")
    p = _pending_login.get("playwright")
    if browser:
        try:
            browser.close()
        except Exception:
            pass
    if p:
        try:
            p.stop()
        except Exception:
            pass
    _pending_login.update({"browser": None, "playwright": None, "page": None, "context": None})


# ---------- Route discovery ----------

def list_origins(force_refresh=False):
    global _origins_cache
    if _origins_cache is not None and not force_refresh:
        return _origins_cache

    with sync_playwright() as p:
        browser, ctx = _new_context(p)
        page = ctx.new_page()
        # "load" not "networkidle": the page keeps background analytics
        # requests going, which can make networkidle hang 10+ seconds for
        # no benefit here (we only need the <select> options, already
        # present at load).
        page.goto(SEARCH_URL, wait_until="load")
        page.wait_for_selector("#dest_cmd option", state="attached", timeout=15000)
        opts = page.query_selector_all("#dest_cmd option")
        origins = []
        for o in opts:
            val = o.get_attribute("value")
            label = (o.inner_text() or "").strip()
            if val:
                origins.append({"code": val, "label": label})
        browser.close()

    _origins_cache = sorted(origins, key=lambda x: x["code"])
    return _origins_cache


def list_destinations(origin, force_refresh=False):
    if origin in _destinations_cache and not force_refresh:
        return _destinations_cache[origin]

    with sync_playwright() as p:
        browser, ctx = _new_context(p)
        page = ctx.new_page()
        page.goto(SEARCH_URL, wait_until="load")
        page.wait_for_selector("#dest_cmd option", state="attached", timeout=15000)
        with page.expect_navigation(wait_until="load", timeout=30000):
            page.select_option("#dest_cmd", origin)
        page.wait_for_selector("#to_cmd option", state="attached", timeout=15000)
        opts = page.query_selector_all("#to_cmd option")
        dests = []
        for o in opts:
            val = o.get_attribute("value")
            label = (o.inner_text() or "").strip()
            if val:
                dests.append({"code": val, "label": label})
        browser.close()

    dests = sorted(dests, key=lambda x: x["code"])
    _destinations_cache[origin] = dests
    return dests


# ---------- Fare scraping ----------

def _navigate_datepicker_to_month(page, target_year, target_month):
    """Click the jQuery UI datepicker's Next/Prev arrows until it shows target_year/target_month."""
    page.click("#check_out")
    page.wait_for_selector("#ui-datepicker-div", state="visible", timeout=5000)

    for _ in range(36):  # safety cap: 3 years
        month_txt = page.eval_on_selector("#ui-datepicker-div .ui-datepicker-month", "el => el.textContent").strip()
        year_txt = page.eval_on_selector("#ui-datepicker-div .ui-datepicker-year", "el => el.textContent").strip()
        cur_month = list(calendar.month_name).index(month_txt)
        cur_year = int(year_txt)
        if cur_year == target_year and cur_month == target_month:
            return
        if (cur_year, cur_month) < (target_year, target_month):
            page.click("#ui-datepicker-div .ui-datepicker-next")
        else:
            page.click("#ui-datepicker-div .ui-datepicker-prev")
        page.wait_for_timeout(150)

    raise RuntimeError("Could not navigate datepicker to target month")


def _pick_day(page, year, month, day):
    _navigate_datepicker_to_month(page, year, month)
    page.click(f"#ui-datepicker-div a.ui-state-default >> text='{day}'", timeout=3000)


def _extract_flights(page):
    flights = []
    for box in page.query_selector_all(".flit-box"):
        airline_el = box.query_selector(".flit-item-img p")
        airline = airline_el.inner_text().strip() if airline_el else "?"
        flightno_el = box.query_selector(".flit-item5")
        flight_no = flightno_el.inner_text().strip() if flightno_el else None
        time_el = box.query_selector(".flit-item6")
        time_txt = time_el.inner_text().strip().replace("\n", " - ") if time_el else None

        bag_el = box.query_selector(".flit-item3 span") or box.query_selector(".flit-item3")
        baggage = None
        if bag_el:
            raw = bag_el.inner_text().strip()
            # collapse the odd internal whitespace/newlines AirIQ's markup has
            # around the baggage icon+text (e.g. "5 KG ,   20 KG")
            baggage = re.sub(r"\s+", " ", raw).strip() or None

        # Flight duration isn't in a normal visible field - it's only present
        # inside the "Copy Flight Details" button's onclick text
        # (e.g. "...*Flight Duration* : 2hr 5min\n..."). Pull it via regex.
        duration = None
        copy_el = box.query_selector(".copybtn a")
        if copy_el:
            onclick = copy_el.get_attribute("onclick") or ""
            m = re.search(r"Flight Duration\*?\s*:\s*([^\\\n]+)", onclick)
            if m:
                duration = m.group(1).strip()

        rate_el = box.query_selector(".rate")
        fare = rate_el.get_attribute("data-inr") if rate_el else None
        flights.append({
            "airline": airline,
            "flight_no": flight_no,
            "time": time_txt,
            "duration": duration,
            "baggage": baggage,
            "fare_inr": float(fare) if fare else None,
        })
    return flights


def scrape_month(origin, dest, year, month, progress_cb=None):
    """Returns {day: {"status": "ok"|"sold_out"|"past_or_unavailable", "flights": [...], "cheapest": {...}}}"""
    if not has_session():
        raise RuntimeError("not_logged_in")

    days_in_month = calendar.monthrange(year, month)[1]
    results = {}

    with sync_playwright() as p:
        browser, ctx = _new_context(p)
        page = ctx.new_page()
        page.goto(SEARCH_URL, wait_until="load")
        page.wait_for_selector("#dest_cmd option", state="attached", timeout=15000)

        if "Search" not in page.title():
            browser.close()
            raise RuntimeError("not_logged_in")

        with page.expect_navigation(wait_until="load", timeout=30000):
            page.select_option("#dest_cmd", origin)
        page.wait_for_selector("#to_cmd option", state="attached", timeout=15000)
        with page.expect_navigation(wait_until="load", timeout=30000):
            page.select_option("#to_cmd", dest)

        for day in range(1, days_in_month + 1):
            try:
                _pick_day(page, year, month, day)
            except Exception:
                results[day] = {"status": "past_or_unavailable"}
                if progress_cb:
                    progress_cb(day, days_in_month, "unavailable")
                continue

            try:
                with page.expect_navigation(wait_until="load", timeout=30000):
                    page.click("#SearchBtn")
            except Exception as e:
                results[day] = {"status": "error", "error": str(e)}
                if progress_cb:
                    progress_cb(day, days_in_month, "error")
                continue

            flights = _extract_flights(page)
            if not flights:
                results[day] = {"status": "sold_out"}
                if progress_cb:
                    progress_cb(day, days_in_month, "sold_out")
            else:
                cheapest = min(flights, key=lambda f: f["fare_inr"] or float("inf"))
                results[day] = {"status": "ok", "flights": flights, "cheapest": cheapest}
                if progress_cb:
                    progress_cb(day, days_in_month, "ok")

        browser.close()

    return results
