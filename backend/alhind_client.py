"""
Alhind (travel.alhind.com) scraping client.

Used for named-flight/fare-class fare lookups (e.g. "IndiGo 6E 1273,
Tactical fare on HYD-MCT") - a different model from AirIQ's "pick the
cheapest available" (see pricing.py). Angular Material SPA, confirmed
live via direct exploration.

Two important differences from airiq_client.py, both confirmed live:

1. OTP is only required ONCE, to register the browser/device (the
   underlying session cookies, not just the storage_state.json file).
   After that, plain username+password login succeeds directly - no
   human OTP relay needed for routine scraping. BUT the session token
   itself expires faster than AirIQ's (observed within ~15-20 minutes of
   the tab being idle, and confirmed to happen mid-scrape), so
   _search_once() re-navigates to the search form and transparently
   relogs in (no OTP) at the start of every single search rather than
   treating expiry as fatal or assuming a session stays valid across
   multiple searches in one run.

2. Angular Material's overlay backdrops (dropdowns, alert dialogs, the
   date-picker) leave a lingering transparent backdrop in the DOM that
   silently eats the next click aimed at whatever's underneath it - a
   plain page.click(), even with force=True, still lands on that overlay
   (force only skips Playwright's actionability check, it doesn't change
   what's actually at that screen position). Every click in this module
   goes through _click_clean(), which checks what's actually under the
   target point first and clears a stray overlay before clicking for
   real. Confirmed this is why naive clicks kept silently doing nothing
   during initial exploration.
"""
import os
import re
import time
import threading
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "state")
os.makedirs(STATE_DIR, exist_ok=True)
AUTH_STATE_PATH = os.path.join(STATE_DIR, "alhind_storage_state.json")

HOME_URL = "https://travel.alhind.com/#/Home/Air"

_lock = threading.Lock()
_pending_login = {"browser": None, "playwright": None, "page": None, "context": None,
                   "username": None, "password": None}

LOGIN_FIELD = "input[placeholder='Enter Mobile Number/ Email ID']"
OTP_FIELD = "input[placeholder='Mobile OTP']"


class OtpRequired(RuntimeError):
    """Alhind wants a fresh device-verification OTP. Never transient:
    retrying only resubmits the login form (and can text another OTP
    each time), so scrapes stop on this rather than retrying every date,
    and a human has to complete login_start()/login_verify()."""

    def __init__(self):
        super().__init__("otp_required")


def has_session():
    return os.path.exists(AUTH_STATE_PATH)


_REAL_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)

# Confirmed live: Alhind's app renders a completely empty page (no JS
# errors, no failed requests - it just never bootstraps) for a plain
# Playwright session, while a real browser works fine. That signature -
# silent, no visible error - is the standard behavior of client-side
# bot-detection scripts that check automation markers before deciding
# whether to let the app run at all, rather than the site itself being
# broken. navigator.webdriver=true is the most common single marker
# (WebDriver-based tools set it deliberately per spec); the other patches
# below cover several more of the well-known headless-Chromium tells.
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
window.chrome = window.chrome || { runtime: {} };
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters)
);
"""


def _new_context(p, headless=True, fresh=False):
    """fresh=True skips loading any existing storage_state.json, even if
    has_session() is True - used by login_start() so an explicit "log me
    in" request always sees Alhind's real current state, rather than
    trusting a saved session that might be stale. Confirmed live this
    matters: a stale-but-existing state file (written by an earlier
    partially-successful run) was just valid enough that Alhind didn't
    redirect to the login page on a plain load, so login_start() wrongly
    concluded "already_logged_in" and silently skipped the OTP flow -
    while that same stale session then failed every actual search."""
    browser = p.chromium.launch(headless=headless, args=[
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-blink-features=AutomationControlled",
        "--js-flags=--max-old-space-size=128",
    ])
    ctx = browser.new_context(
        storage_state=AUTH_STATE_PATH if (has_session() and not fresh) else None,
        viewport={"width": 1400, "height": 900},
        user_agent=_REAL_USER_AGENT,
        locale="en-US",
    )
    ctx.add_init_script(_STEALTH_INIT_SCRIPT)
    return browser, ctx


# ---------- overlay-safe click helpers (see module docstring) ----------

def _overlay_count(page):
    return len(page.query_selector_all(".cdk-overlay-backdrop-showing"))


def _click_field(page, locator, max_tries=2):
    """For elements that OPEN a dropdown/menu: click, and if a stray
    leftover overlay just ate that click (closing itself instead of
    opening the intended menu), click again."""
    for _ in range(max_tries):
        locator.click(force=True)
        page.wait_for_timeout(500)
        if _overlay_count(page) > 0:
            return True
    return _overlay_count(page) > 0


def _open_city_dropdown(page, index, max_tries=3):
    """Clicks the FROM (index=0) or TO (index=1) city button and confirms
    the autocomplete search box actually appeared. Confirmed live that
    the generic overlay-count check in _click_field isn't reliable here -
    an unrelated stray overlay elsewhere on the page can satisfy it,
    giving a false "it opened" signal that then makes the caller try to
    fill a search box that was never actually shown."""
    for _ in range(max_tries):
        page.locator(".cityName").nth(index).click(force=True)
        page.wait_for_timeout(500)
        try:
            page.wait_for_selector("input[placeholder='Start typing...']", state="visible", timeout=2000)
            return
        except Exception:
            continue
    raise RuntimeError(f"Could not open the city dropdown at index {index}")


def _click_clean(page, locator, max_tries=5):
    """For any click: clear a stray overlay sitting on top of the target
    point first, then click for real."""
    for _ in range(max_tries):
        box = locator.bounding_box()
        if not box:
            locator.click(force=True)
            page.wait_for_timeout(400)
            continue
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        cls = page.evaluate(
            "([x,y]) => { const e = document.elementFromPoint(x,y); return e ? e.className : null; }",
            [cx, cy],
        )
        if cls and "cdk-overlay-backdrop" in cls:
            page.mouse.click(cx, cy)
            page.wait_for_timeout(400)
            continue
        locator.click(force=True)
        page.wait_for_timeout(400)
        return True
    return False


def _click_login_button(page):
    """Confirmed live: _click_clean() (bounding_box() + an elementFromPoint
    evaluate() before clicking) silently prevented this specific button's
    click from reaching Alhind's login API at all - zero network requests
    fired, no error, nothing. A plain page.click(force=True) with no
    extra JS evaluation in between fill() and click() does fire the real
    request. Not fully understood why this one button differs from every
    other _click_clean() use on this site that's worked fine, but this is
    the proven-working path, so it's used here rather than the general
    helper.

    No-ops if the button isn't there - callers use this as a "nudge" retry
    partway through waiting for the login to take effect, and if the
    button's already gone, that means an earlier click DID register and
    the page has moved on; retrying would just hit a timeout waiting for
    a button that's no longer coming back."""
    btn = page.locator("button:has-text('LOGIN')")
    if btn.count() == 0:
        return
    btn.first.click(force=True, timeout=3000)


def _dismiss_alert(page):
    """Dismisses a generic "Alert" modal (Session Expired, Kindly Re-Login
    Now, Disclaimer, etc.) if one is showing. Returns True if one was
    dismissed."""
    ok_btn = page.get_by_role("button", name="Ok", exact=True)
    if ok_btn.count() > 0:
        _click_clean(page, ok_btn.first)
        page.wait_for_timeout(800)
        return True
    return False


# ---------- Login ----------

def login_start(username, password):
    """Two-step login (start sends OTP, verify submits it), matching
    airiq_client's shape. Only actually needed the FIRST time this
    account is used on a fresh container - after that, has_session() is
    True and every search function's _search_once() call handles routine
    relogins itself, with no OTP needed."""
    with _lock:
        if _pending_login["browser"]:
            _cleanup_pending_login()

        p = sync_playwright().start()
        browser, ctx = _new_context(p, headless=True, fresh=True)
        page = ctx.new_page()

        # Confirmed live: the page loads with an empty body and a generic
        # title ("Dhanhind" - the underlying booking-engine vendor, not
        # Alhind's own branded title) - the Angular app itself never
        # actually renders. Capturing console/page errors and failed
        # requests to find out why, rather than guessing further.
        console_msgs = []
        page.on("console", lambda m: console_msgs.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: console_msgs.append(f"pageerror: {e}"))
        page.on("requestfailed", lambda r: console_msgs.append(f"requestfailed: {r.url} - {r.failure}"))

        page.goto(HOME_URL, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(1000)
        _dismiss_alert(page)

        # Confirmed live: the URL is not a reliable signal of auth state
        # at all - Alhind's app renders "#/Home/Air" regardless of
        # whether the session is actually authenticated (it apparently
        # only redirects to the login route on a failed API call, not on
        # a plain page load), so every previous check based on "TLogin in
        # page.url" was fundamentally checking the wrong thing. Checking
        # for the actual login form field instead - that's genuinely
        # only present when a login is actually needed.
        #
        # Confirmed live: the empty-body symptom is a real render-timing
        # race, not (or not only) bot detection - Render's logs showed a
        # JS chunk (a lazy-loaded route module, by the filename) getting
        # net::ERR_ABORTED immediately after a reload we triggered. That
        # means our own retry was cancelling an in-flight chunk fetch that
        # likely just needed more time, guaranteeing that attempt would
        # fail. So: wait patiently (polling) for the body to actually
        # render before ever reloading, rather than reloading eagerly
        # after a single short, fixed wait.
        login_field = page.locator("input[placeholder='Enter Mobile Number/ Email ID']")
        for attempt in range(3):
            for _ in range(10):  # up to ~10s of patient polling per attempt
                login_field = page.locator("input[placeholder='Enter Mobile Number/ Email ID']")
                if login_field.count() > 0 or page.locator(".cityName").count() > 0:
                    break
                if page.inner_text("body").strip():
                    break  # something rendered, even if not what we expect yet
                page.wait_for_timeout(1000)
            if login_field.count() > 0 or page.locator(".cityName").count() > 0:
                break
            if not page.inner_text("body").strip():
                print(f"[alhind_client] login_start: empty body on attempt {attempt + 1} - "
                      f"console/errors: {console_msgs[:20]}", flush=True)
            console_msgs.clear()
            page.reload(wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(1500)
            _dismiss_alert(page)
            login_field = page.locator("input[placeholder='Enter Mobile Number/ Email ID']")

        if login_field.count() == 0:
            # No login field even after a reload. If the actual search
            # form is showing, this genuinely is already logged in and
            # usable; otherwise something's actually broken.
            if page.locator(".cityName").count() > 0:
                ctx.storage_state(path=AUTH_STATE_PATH)
                browser.close()
                p.stop()
                return {"status": "already_logged_in"}
            try:
                body_snippet = page.inner_text("body")[:400].replace("\n", " | ")
            except Exception as e:
                body_snippet = f"(couldn't read body: {e})"
            print(f"[alhind_client] login_start: no login field and no search form - "
                  f"url={page.url!r} title={page.title()!r} body_start={body_snippet!r}", flush=True)
            browser.close()
            p.stop()
            raise RuntimeError(f"Could not find the Alhind login form (url={page.url})")

        login_field.fill(username)
        page.fill("input[placeholder='Password']", password)
        _click_login_button(page)
        # Confirmed live: the LOGIN click itself is genuinely flaky - the
        # exact same click sometimes fires the real login request and
        # sometimes visibly does nothing at all (no request, no state
        # change), same class of unreliability as everything else on this
        # site. A pure wait can't fix a click that just didn't register,
        # so this re-clicks periodically while polling, rather than
        # trusting the first click alone.
        # Confirmed live: checking body text for "Mobile OTP" was a
        # fundamentally broken check the whole time, unrelated to any of
        # the click/timing issues - "Mobile OTP" is that input's
        # `placeholder` attribute, and placeholder text is never part of
        # innerText in any browser. The OTP screen was often already
        # showing (confirmed via a body dump - the retype-value and
        # "VERIFY OTP" button were right there) while this check kept
        # reporting failure. Checking for the actual input element
        # instead of scanning for text that could never appear.
        # Bumped from 16*0.5s=8s to 30*1s=30s - confirmed live the login
        # click can genuinely take upwards of 15-20s to actually surface
        # the OTP screen (slow backend, not a stuck click), so 8s was
        # bailing out and raising "Unexpected page after login" on
        # attempts that would have succeeded with a little more patience.
        otp_screen_shown = False
        for i in range(30):
            page.wait_for_timeout(1000)
            if page.locator("input[placeholder='Mobile OTP']").count() > 0:
                otp_screen_shown = True
                break
            if i in (5, 12, 20):  # ~6s, ~13s, ~21s in - give it a nudge
                _click_login_button(page)

        if not otp_screen_shown:
            title = page.title()
            try:
                body_snippet = page.inner_text("body")[:400].replace("\n", " | ")
            except Exception as e:
                body_snippet = f"(couldn't read body: {e})"
            print(f"[alhind_client] login_start: OTP screen never appeared - "
                  f"url={page.url!r} title={title!r} body_start={body_snippet!r}", flush=True)
            browser.close()
            p.stop()
            raise RuntimeError(f"Unexpected page after login: {title}")

        _pending_login.update({
            "browser": browser, "playwright": p, "page": page, "context": ctx,
            "username": username, "password": password,
        })
        return {"status": "otp_sent"}


def login_verify(otp):
    """Submits the OTP. Confirmed live: after OTP verification the site
    bounces back to a plain login form ("Kindly Re-Login Now") rather
    than going straight into the app - this does that follow-up plain
    login automatically so the caller just sees one clean result."""
    with _lock:
        page = _pending_login.get("page")
        ctx = _pending_login.get("context")
        browser = _pending_login.get("browser")
        if not page:
            raise RuntimeError("No login in progress. Call login_start first.")

        displayed = page.evaluate("""() => {
            const divs = Array.from(document.querySelectorAll('div'));
            for (const d of divs) {
                const t = d.textContent.trim();
                if (/^\\d{6}$/.test(t)) return t;
            }
            return null;
        }""")
        page.fill("input[placeholder='Mobile OTP']", otp)
        if displayed:
            page.fill("input[placeholder='Enter Above Value']", displayed)
        # Plain click, not _click_clean() - confirmed live that helper
        # (bounding_box() + an elementFromPoint evaluate() before
        # clicking) silently prevents the LOGIN button's click from
        # firing at all; same risk here, same fix.
        page.click("button:has-text('VERIFY OTP')", force=True)
        # Generous flat wait, not a poll: there's no single target URL to
        # watch for here (success could land on the app itself OR bounce
        # to the "Kindly Re-Login Now" form, both are valid outcomes
        # checked below) - but confirmed live this step needs real time
        # to settle, same as the other login steps.
        page.wait_for_timeout(5000)
        # Confirmed live (Oct 2026): the VERIFY click can silently not
        # register, leaving the OTP screen up with the code still filled
        # in. That screen has no login field either, so the check below
        # used to call it a success - and every search then failed on
        # the OTP screen. Re-click while it's still showing.
        for _ in range(2):
            if page.locator(OTP_FIELD).count() == 0:
                break
            page.click("button:has-text('VERIFY OTP')", force=True)
            page.wait_for_timeout(5000)

        # Confirmed live: page.url isn't a reliable "still on the login
        # form" signal - Alhind's app doesn't consistently redirect to a
        # distinct login URL. Checking for the actual login field's
        # presence instead, same fix as login_start().
        ok = page.locator(OTP_FIELD).count() == 0
        login_field = page.locator("input[placeholder='Enter Mobile Number/ Email ID']")
        if ok and login_field.count() > 0:
            username = _pending_login.get("username")
            password = _pending_login.get("password")
            # Confirmed live: even with nudge-retries, this follow-up
            # login can still fail to take effect within one attempt -
            # same flaky-click issue, just needing more persistence than
            # a same-page re-click gives it. A full reload resets
            # whatever state the stuck click left behind, giving each
            # attempt a genuinely fresh shot rather than repeatedly
            # clicking on a page that might be stuck.
            for full_attempt in range(3):
                login_field = page.locator("input[placeholder='Enter Mobile Number/ Email ID']")
                if login_field.count() == 0:
                    break
                login_field.fill(username)
                page.fill("input[placeholder='Password']", password)
                _click_login_button(page)
                for i in range(16):
                    page.wait_for_timeout(500)
                    login_field = page.locator("input[placeholder='Enter Mobile Number/ Email ID']")
                    if login_field.count() == 0:
                        break
                    if i in (5, 10):
                        _click_login_button(page)
                if login_field.count() == 0:
                    break
                if full_attempt < 2:
                    page.reload(wait_until="networkidle", timeout=45000)
                    page.wait_for_timeout(1500)
                    _dismiss_alert(page)
            ok = login_field.count() == 0

        # Neither check above proves we're in - only the search form
        # itself showing up does.
        if ok:
            ok = _search_form_ready(page)

        if not ok:
            try:
                body_snippet = page.inner_text("body")[:400].replace("\n", " | ")
            except Exception as e:
                body_snippet = f"(couldn't read body: {e})"
            print(f"[alhind_client] login_verify: giving up - "
                  f"url={page.url!r} title={page.title()!r} body_start={body_snippet!r}", flush=True)

        if ok:
            ctx.storage_state(path=AUTH_STATE_PATH)

        _cleanup_pending_login()
        return {"status": "ok" if ok else "invalid_or_expired_otp"}


def _search_form_ready(page, timeout_s=20):
    """True once Alhind's flight search form is actually showing - the one
    reliable "logged in" signal (the OTP screen has no login field
    either, and the URL doesn't change)."""
    if page.locator(".cityName").count() == 0:
        # goto() to the identical "#/Home/Air" URL is a no-op (see
        # _search_once) - reload() is what actually re-renders it.
        if "#/Home/Air" in page.url:
            page.reload(wait_until="load", timeout=45000)
        else:
            page.goto(HOME_URL, wait_until="load", timeout=45000)
    for _ in range(timeout_s):
        _dismiss_alert(page)
        if page.locator(".cityName").count() > 0:
            return True
        page.wait_for_timeout(1000)
    return False


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
    _pending_login.update({"browser": None, "playwright": None, "page": None, "context": None,
                            "username": None, "password": None})


# ---------- Route + date selection ----------

def _date_selected(page, day_button, date):
    """Confirmed live (Oct 2026): the calendar now closes itself as soon
    as a day is clicked (moving straight on to the travellers popup), so
    the clicked cell's aria-pressed can no longer be read afterwards -
    waiting on it just timed out every search. While the calendar is
    still open, aria-pressed is the check; once it's closed, the DEPART
    field's own "19 Oct 26"-style text is."""
    if page.locator(".mat-calendar-body-cell").count() > 0 and day_button.count() > 0:
        return day_button.get_attribute("aria-pressed", timeout=2000) == "true"
    depart = rf"\b{date.day}\s+{date.strftime('%b')}\s+{date.strftime('%y')}\b"
    return re.search(depart, page.inner_text("body")) is not None


def _select_route_and_date(page,origin_search, origin_option_text, dest_search, dest_option_text, date):
    _open_city_dropdown(page, 0)
    page.fill("input[placeholder='Start typing...']", origin_search)
    page.wait_for_timeout(700)
    page.click(f"text={origin_option_text}", force=True)
    page.wait_for_timeout(500)

    _open_city_dropdown(page, 1)
    page.fill("input[placeholder='Start typing...']", dest_search)
    page.wait_for_timeout(700)
    page.click(f"text={dest_option_text}", force=True)
    page.wait_for_timeout(500)

    # The date picker auto-opens after TO is selected, defaulted to
    # today's month. It's a stock Angular Material calendar (unlike the
    # rest of this app's custom widgets) - confirmed live via its DOM:
    # reliable .mat-calendar-period-button / -next-button / -previous-button
    # selectors, no overlay-backdrop quirk to work around here.
    #
    # Wait for it to actually render before reading its month label -
    # confirmed live this is a real race: the fixed 500ms wait above
    # isn't always enough, and the month-check loop below was treating
    # "calendar not visible yet" (0 matches) exactly the same as
    # "already on the target month" (its break condition), silently
    # skipping navigation it should have done and leaving the calendar
    # on whatever month it happened to default to.
    for _ in range(20):
        if page.locator(".mat-calendar-body-cell").count() > 0:
            break
        page.wait_for_timeout(500)
    else:
        raise RuntimeError("Alhind's date-picker calendar never rendered")

    for _ in range(24):  # safety cap: 2 years
        label_el = page.locator(".mat-calendar-period-button span[aria-hidden='true']").first
        if label_el.count() == 0:
            break
        label = label_el.inner_text().strip().upper()  # e.g. "SEPT 2026"
        target_label = date.strftime("%b %Y").upper()[:3]
        if target_label in label and str(date.year) in label:
            break
        next_btn = page.locator(".mat-calendar-next-button")
        if next_btn.get_attribute("disabled") is not None:
            raise RuntimeError(f"Alhind's calendar has no month past {label} - can't reach {date}")
        _click_clean(page, next_btn)
        page.wait_for_timeout(500)
    else:
        raise RuntimeError(f"Could not navigate Alhind's calendar to {date}")

    # NOT page.get_by_text(day_str, exact=True) over the whole page - an
    # unscoped exact-text "18" search risks matching something else on
    # the page entirely, silently leaving the calendar on whatever date
    # it last had selected (no error, just a wrong-date search that
    # looks completely normal downstream) - confirmed live this is very
    # likely why the same fare value was seen repeating across several
    # genuinely different target dates. Each day cell is a real <button
    # class="mat-calendar-body-cell" aria-label="18 September 2026">
    # (confirmed live via the actual DOM) - the aria-label spells out the
    # full date, so matching on that is unambiguous by construction,
    # unlike the bare visible day number.
    #
    # get_by_role(name=...), NOT a raw CSS [aria-label='...'] attribute
    # selector - confirmed live the latter times out despite the label
    # visibly matching, almost certainly some internal whitespace (e.g.
    # a non-breaking space) that isn't byte-identical to a plain Python-
    # built string. get_by_role() matches the accessible name with
    # normalized whitespace, which is exactly what's needed here.
    expected_label = f"{date.day} {date.strftime('%B')} {date.year}"
    day_button = page.get_by_role("button", name=expected_label, exact=True).first
    if day_button.count() == 0:
        raise RuntimeError(f"Calendar has no cell for {date} (looked for aria-label={expected_label!r})")
    if day_button.get_attribute("aria-disabled") == "true":
        raise RuntimeError(f"Calendar cell for {date} is disabled (aria-label={expected_label!r})")
    _click_clean(page, day_button)
    page.wait_for_timeout(500)

    # Verify the click actually registered rather than trusting it -
    # retries once, then fails loudly instead of silently searching a
    # stale date.
    if not _date_selected(page, day_button, date):
        if day_button.count() > 0:
            _click_clean(page, day_button)
            page.wait_for_timeout(500)
        if not _date_selected(page, day_button, date):
            raise RuntimeError(f"Calendar didn't confirm selecting {date} (aria-label={expected_label!r})")

    _ensure_direct_flight_checked(page)


def _ensure_direct_flight_checked(page, max_tries=5):
    """Confirmed live and required: without this, results are flooded
    with IndiGo multi-leg connections and the named non-IndiGo carriers
    (SalamAir, Oman Air) the user actually asked for don't appear in the
    list at all - not a fare-class mismatch, they're just not shown.
    Ticking "Direct Flights" (confirmed exact label - it's plural,
    unlike what the checkbox's own icon/tooltip suggests) surfaces them.

    Uses the actual checkbox `is_checked()` state, not a generic overlay
    heuristic - confirmed live that a naive text-label click sometimes
    silently doesn't register (same click flakiness as everywhere else
    on this site), so this verifies and retries rather than assuming."""
    checkbox = page.locator("mat-checkbox:has-text('Direct Flights') input[type=checkbox]")
    for _ in range(max_tries):
        if checkbox.is_checked():
            return
        _click_clean(page, checkbox)
        page.wait_for_timeout(500)
    raise RuntimeError("Could not check 'Direct Flights'")


def _extract_flights(page):
    """Extracts every flight card on a results page, each with its full
    list of fare-class options (e.g. Tactical/Saver/Corp for IndiGo,
    Value/Flexi for others) - confirmed live against real HYD-MCT
    results. Unlike AirIQ, fare "class" (bucket) is a first-class concept
    here, shown directly per flight rather than needing a separate
    Market Place tab."""
    return page.evaluate("""
() => {
  const cards = Array.from(document.querySelectorAll('.row.pt-2.pb-1'));
  return cards.map(card => {
    const img = card.querySelector('img.airlogopadding');
    const lines = card.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
    // airline name and flight number are the two lines right after the logo
    let airline = null, flightNo = null;
    const idx = lines.findIndex(l => /^[A-Z0-9]{1,3}\\s?\\d{2,5}$/.test(l));
    if (idx > 0) {
      flightNo = lines[idx];
      airline = lines[idx - 1];
    }
    const timeMatches = card.innerText.match(/\\b\\d{1,2}:\\d{2}\\b/g) || [];
    const baggageMatch = card.innerText.match(/(No Baggage|No Freebag|\\d+\\s*kg)/i);
    const durationMatch = card.innerText.match(/\\d+\\s*Hrs?\\s*\\d*\\s*Mins?/i);
    const stopsMatch = card.innerText.match(/Non[- ]?Stop|\\d\\s*Stop/i);

    const radios = Array.from(card.querySelectorAll('mat-radio-button'));
    const fares = radios.map(r => {
      const spans = r.querySelectorAll('label span');
      let type = null, price = null;
      spans.forEach(s => {
        const t = s.textContent.trim();
        if (t.startsWith('\\u20b9')) price = t;
        else if (t.length) type = t;
      });
      return {type, price};
    }).filter(f => f.type && f.price);

    return {
      airline, flight_no: flightNo,
      logo_url: img ? img.src : null,
      dep_time: timeMatches[0] || null,
      arr_time: timeMatches[1] || null,
      duration: durationMatch ? durationMatch[0] : null,
      baggage: baggageMatch ? baggageMatch[0] : null,
      stops: stopsMatch ? stopsMatch[0] : null,
      fares,
    };
  });
}
""")


def _parse_price(price_str):
    if not price_str:
        return None
    digits = re.sub(r"[^\d.]", "", price_str)
    return float(digits) if digits else None


def _normalize(text):
    return re.sub(r"\s+", "", (text or "")).lower()


def _parse_baggage_kg(baggage_str):
    """"30kg" -> 30, "25 Kg" -> 25, "No Baggage"/"No Freebag"/None -> None."""
    if not baggage_str:
        return None
    m = re.search(r"(\d+)\s*kg", baggage_str, re.I)
    return int(m.group(1)) if m else None


def _is_zero_baggage(baggage_str):
    """True for "0kg", "No Baggage", "No Freebag" - a hand-baggage-only
    fare. Baggage is extracted once per flight CARD (not per fare
    bucket), so this excludes a card whose displayed allowance is zero -
    used to make sure a "lowest fare of any class" pick never lands on a
    cheap-but-no-checked-baggage fare bucket."""
    if not baggage_str:
        return False
    if re.search(r"no\s*(baggage|freebag)", baggage_str, re.I):
        return True
    return _parse_baggage_kg(baggage_str) == 0


def _cheapest_of(candidates):
    """candidates: list of (price, flight, fare) - returns the poster-
    ready dict for the cheapest one, or None if the list is empty."""
    if not candidates:
        return None
    price, flight, fare = min(candidates, key=lambda c: c[0])
    return {
        "airline": flight["airline"],
        "flight_no": flight["flight_no"],
        "logo_url": flight["logo_url"],
        "dep_time": flight["dep_time"],
        "arr_time": flight["arr_time"],
        "duration": flight["duration"],
        "baggage": flight["baggage"],
        "stops": flight["stops"],
        "fare_type": fare["type"],
        "fare_inr": price,
    }


def find_named_fare(flights, flight_no, fare_type=None):
    """flights: output of _extract_flights(). Finds the flight(s) matching
    flight_no and returns the single cheapest fare option among them.

    flight_no: a flight number (e.g. "6E 1273", whitespace/case-
        insensitive), or a list of alternatives (e.g. ["6E 1485",
        "1X 921"]) when the same route is sometimes filed under a
        different code - any of them matches.
    fare_type: a specific fare-class name (e.g. "Tactical") to require -
        a flight can list the same fare_type twice at different prices
        (seen live: two "Tactical" options), so this picks the cheaper
        one rather than assuming there's only one. Pass None (or omit)
        to instead take the single cheapest fare of ANY class on the
        matching flight(s) - "whatever's lowest today". Either way, a
        card with a 0kg/hand-baggage-only allowance is never picked -
        confirmed live that "lowest fare of any class" without this
        exclusion could land on a technically-cheaper no-checked-baggage
        fare bucket, which isn't the deal this is meant to advertise."""
    target_nos = {_normalize(n) for n in flight_no} if isinstance(flight_no, (list, tuple)) \
        else {_normalize(flight_no)}
    target_type = _normalize(fare_type) if fare_type else None

    candidates = []
    for flight in flights:
        if _normalize(flight.get("flight_no")) not in target_nos:
            continue
        if _is_zero_baggage(flight.get("baggage")):
            continue
        fares = flight["fares"] if target_type is None \
            else [f for f in flight["fares"] if _normalize(f["type"]) == target_type]
        for f in fares:
            price = _parse_price(f["price"])
            if price is not None:
                candidates.append((price, flight, f))

    return _cheapest_of(candidates)


def find_fallback_fare(flights, baggage_kg_options=(25, 30)):
    """flights: output of _extract_flights(). Used when the specifically
    named flight isn't on offer that date - rather than leaving the
    poster cell blank, finds the single cheapest fare among ALL flights
    (any airline/flight number) whose baggage allowance is one of
    baggage_kg_options, so the date still gets a real, bookable fare."""
    candidates = []
    for flight in flights:
        if _parse_baggage_kg(flight.get("baggage")) not in baggage_kg_options:
            continue
        for f in flight["fares"]:
            price = _parse_price(f["price"])
            if price is not None:
                candidates.append((price, flight, f))

    return _cheapest_of(candidates)


def find_any_fare(flights):
    """Last-resort fallback: the single cheapest fare among ALL flights
    on the page, no flight-number filter at all (still excludes 0kg/
    hand-baggage-only cards, same as find_named_fare). Only reached when
    both the named flight AND the 25kg/30kg fallback found nothing -
    guarantees a date is only left blank when the search genuinely
    returned zero flights with any real checked baggage."""
    candidates = []
    for flight in flights:
        if _is_zero_baggage(flight.get("baggage")):
            continue
        for f in flight["fares"]:
            price = _parse_price(f["price"])
            if price is not None:
                candidates.append((price, flight, f))

    return _cheapest_of(candidates)


def _search_once(page, ctx, username, password, origin_search, origin_option_text,
                  dest_search, dest_option_text, date, flight_no, fare_type):
    """One search attempt. Returns the matched fare dict, or None if that
    flight/fare-class genuinely isn't there this attempt (caller decides
    whether to retry).

    Always starts by navigating back to the search form - confirmed live
    this is required, not optional: after a search, the page is on the
    *results* page (no .cityName field there at all), so reusing the page
    for a second date without navigating back first fails every time with
    a ".cityName" timeout that looks identical to a session-expiry
    failure. Landing on the search form also doubles as the session-expiry
    check (a dead session shows the login form's own input field again -
    the URL itself doesn't reliably change, confirmed live), so this
    function transparently relogs in (no OTP needed) rather than treating
    that as fatal - confirmed live that Alhind's session token expires
    faster than AirIQ's, well within a 30-date scrape."""
    # Confirmed live: the results page never changes the URL away from
    # "#/Home/Air" (same hash as the search form itself) - goto() to an
    # identical URL is a browser no-op, it does NOT re-trigger Angular's
    # routing or reset the view. reload() is the only reliable way back
    # to the search form once we're past it; goto() is only meaningful
    # for the very first navigation in a fresh page.
    if "#/Home/Air" in page.url:
        page.reload(wait_until="load", timeout=30000)
    else:
        page.goto(HOME_URL, wait_until="load", timeout=30000)
    page.wait_for_timeout(1000)
    _dismiss_alert(page)
    # Confirmed live: page.url isn't a reliable "session is dead" signal -
    # Alhind's app doesn't consistently redirect to a distinct login URL
    # even when logged out. Checking for the actual login field's
    # presence instead, same fix as login_start()/login_verify().
    login_field = page.locator("input[placeholder='Enter Mobile Number/ Email ID']")
    if login_field.count() > 0:
        login_field.fill(username)
        page.fill("input[placeholder='Password']", password)
        _click_login_button(page)
        # Poll rather than a fixed sleep - confirmed live (twice) that
        # even a few seconds isn't always enough for the redirect to
        # complete, causing a false "not_registered" when the login would
        # have succeeded a moment later. This only costs time on an
        # actual relogin (rare relative to the per-date search budget),
        # so it's fine to be generous here specifically. Also confirmed
        # live: the click itself can silently not register at all, so
        # this re-clicks partway through rather than trusting one click.
        for i in range(30):
            page.wait_for_timeout(1000)
            login_field = page.locator("input[placeholder='Enter Mobile Number/ Email ID']")
            if login_field.count() == 0:
                break
            if i in (5, 12, 20):
                _click_login_button(page)
        if login_field.count() > 0:
            raise RuntimeError("not_registered")
        # Confirmed live: the login field disappearing does NOT always
        # mean login succeeded - Alhind can also land on the OTP-entry
        # screen instead (a different field, so login_field.count()==0
        # either way). Without this check, the code below would try to
        # search on what's actually the OTP page and fail with an opaque
        # error on every date until the caller gives up - this raises a
        # specific, clear error immediately instead. An unattended scrape
        # can't complete an OTP prompt, so this route needs a human to
        # run login_start()/login_verify() again before retrying.
        if page.locator("input[placeholder='Mobile OTP']").count() > 0:
            raise OtpRequired()
        ctx.storage_state(path=AUTH_STATE_PATH)

    _select_route_and_date(page, origin_search, origin_option_text,
                            dest_search, dest_option_text, date)
    _click_clean(page, page.locator("button:has-text('Search Flights')"))
    # The flat 6s wait here was pure dead time before the poll loop even
    # started checking - the loop already handles the real variance in
    # how long results take to render, so a short buffer plus starting
    # the poll sooner catches fast results faster without needing to
    # trust a guessed fixed delay. Total max budget (buffer + poll) cut
    # from 36s to ~24s - still generous versus observed real load times
    # (7-20s), just without padding every single search with dead time.
    page.wait_for_timeout(1500)
    _dismiss_alert(page)  # the one-time "Disclaimer" popup

    # Bumped from 15 to 20 iterations (~22.5s -> 30s) - a date coming back
    # with zero results was sometimes just a slow render, not genuinely
    # zero flights, confirmed by re-checking one live; more patience here
    # catches those before ever falling through to "not found".
    found = False
    for _ in range(20):
        body_text = page.inner_text("body")
        if "Select" in body_text and re.search(r"₹[\d,]+", body_text):
            found = True
            break
        page.wait_for_timeout(1500)

    if not found:
        return None
    flights = _extract_flights(page)
    # If the specifically named flight isn't offered this date, fall back
    # to the cheapest 25kg/30kg-baggage fare (any airline); if even that
    # finds nothing, fall back once more to the single cheapest fare of
    # ANY flight on the page - so a date only ever comes back empty when
    # the search itself genuinely returned zero flights.
    return (find_named_fare(flights, flight_no, fare_type)
            or find_fallback_fare(flights)
            or find_any_fare(flights))


def search_named_flight_fare(username, password, origin_search, origin_option_text,
                              dest_search, dest_option_text, date, flight_no, fare_type):
    """One-off, single-date lookup with its own browser. For a whole
    date range, use scrape_named_flight_range instead - it shares one
    browser across all dates and retries transient misses, both of which
    matter a lot at 30 dates."""
    with sync_playwright() as p:
        browser, ctx = _new_context(p, headless=True)
        page = ctx.new_page()
        result = _search_once(page, ctx, username, password, origin_search, origin_option_text,
                               dest_search, dest_option_text, date, flight_no, fare_type)
        browser.close()
        return result


def _scrape_dates_for_route(page, ctx, username, password, origin_search, origin_option_text,
                             dest_search, dest_option_text, flight_no, fare_type, dates,
                             progress_cb=None):
    """One route, every date, on an already-open page/browser. Shared by
    scrape_named_flight_range (one route, its own browser) and
    scrape_named_flight_ranges (multiple routes, one shared browser) so
    the retry logic only lives in one place.

    Confirmed live that a search can transiently come back empty for a
    date that genuinely has the flight/fare (re-running the identical
    search recovered it) - same class of flakiness seen on AirIQ. Retries
    twice (3 attempts total) before accepting "not found" as real -
    bumped from a single retry since a genuinely blank poster date is
    worse than the extra time this costs. _search_once() handles session
    expiry itself (relogs in transparently) - no separate recovery step
    needed here.

    progress_cb(day_idx, day_total, status) - status one of "ok" /
    "not_found" / "error"."""
    results = {}
    total = len(dates)

    for idx, date in enumerate(dates, start=1):
        key = date.isoformat()
        result = None
        status = "not_found"

        for attempt in range(3):
            try:
                result = _search_once(page, ctx, username, password,
                                       origin_search, origin_option_text,
                                       dest_search, dest_option_text, date, flight_no, fare_type)
                status = "ok" if result else "not_found"
                if result:
                    break
            except OtpRequired:
                raise
            except Exception:
                status = "error"
            if attempt < 2:
                page.wait_for_timeout(1000)

        results[key] = result
        if progress_cb:
            progress_cb(idx, total, status)

    return results


def scrape_named_flight_range(username, password, origin_search, origin_option_text,
                               dest_search, dest_option_text, dates, flight_no, fare_type,
                               progress_cb=None):
    """Same named-flight/fare-class lookup as search_named_flight_fare,
    across a whole list of dates, sharing ONE browser (same memory
    rationale as airiq_client.scrape_multiple_routes) rather than
    launching Chromium per date. For MULTIPLE routes, use
    scrape_named_flight_ranges instead - it shares one session across all
    of them too, which matters a lot for how often a relogin (and its OTP
    risk) gets triggered.

    Returns {date.isoformat(): matched_flight_dict_or_None}."""
    with sync_playwright() as p:
        browser, ctx = _new_context(p, headless=True)
        page = ctx.new_page()
        results = _scrape_dates_for_route(page, ctx, username, password,
                                           origin_search, origin_option_text,
                                           dest_search, dest_option_text,
                                           flight_no, fare_type, dates, progress_cb)
        browser.close()

    return results


def scrape_named_flight_ranges(username, password, routes, dates, progress_cb=None):
    """Same named-flight/fare-class lookup as scrape_named_flight_range,
    but across MULTIPLE routes sharing ONE browser session for the whole
    batch - not one fresh browser (and one fresh login) per route.

    Confirmed live this matters a lot, not just for memory: running each
    route as its own session meant up to 6x the login attempts for a
    6-route group, and Alhind started requiring a fresh device-
    verification OTP on many of those relogins (very likely a security
    response to the sheer volume of automated logins across a long
    debugging session) - since _search_once() can't complete an OTP
    prompt mid-scrape, most routes ended up failing silently, and the
    account's phone got a stream of unsolicited OTP texts. Sharing one
    session across all routes cuts the number of logins needed roughly
    6x. It doesn't eliminate relogins entirely - the ~15-20 minute
    session lifetime still means some are unavoidable across a 60-90
    minute run - but far fewer than before.

    routes: list of dicts with keys origin_search, origin_option,
    dest_search, dest_option, flight_no, fare_type (matches
    named_flights.py's shape).

    progress_cb(route_idx, route_total, day_idx, day_total, status)

    Returns {route_index: {date.isoformat(): matched_flight_dict_or_None}}
    keyed by each route's position in the input list."""
    route_total = len(routes)
    all_results = {}

    with sync_playwright() as p:
        browser, ctx = _new_context(p, headless=True)
        page = ctx.new_page()

        for route_idx, route in enumerate(routes, start=1):
            def _cb(day_idx, day_total, status, _ridx=route_idx):
                if progress_cb:
                    progress_cb(_ridx, route_total, day_idx, day_total, status)

            all_results[route_idx - 1] = _scrape_dates_for_route(
                page, ctx, username, password,
                route["origin_search"], route["origin_option"],
                route["dest_search"], route["dest_option"],
                route["flight_no"], route["fare_type"],
                dates, _cb,
            )

        browser.close()

    return all_results


# Set once a keep-alive ping (or anything else) discovers Alhind wants a
# fresh OTP - used to back off from re-submitting the login form every
# cycle, since repeated login ATTEMPTS (not just OTP sends) still look
# like the kind of automated pattern that got the OTP-send path rate-
# limited once already ("You have tried otp multiple times wait
# sometimes"). A human completing login via the app's own relogin flow
# clears this naturally next successful ping.
_otp_required_since = {"ts": 0}
_OTP_BACKOFF_SECONDS = 3600


def check_session(username, password):
    """One-shot "can an unattended run search right now?" check, for the
    daily poster job (daily_posters.py): loads the search page with the
    saved session, relogs in with username+password if it died, and
    only reports "ok" once the search form itself shows (unlike
    keep_alive_ping's "no login field" check, which the OTP screen also
    passes). Never requests an OTP. Saves the refreshed session on
    success.

    Returns "ok", "otp_required", "not_registered", "no_session_file",
    or "unknown" (nothing recognisable rendered - worth one retry)."""
    if not has_session():
        return "no_session_file"

    with sync_playwright() as p:
        browser, ctx = _new_context(p, headless=True)
        page = ctx.new_page()
        try:
            page.goto(HOME_URL, wait_until="load", timeout=45000)
            submitted_at = None
            for i in range(60):
                page.wait_for_timeout(1000)
                _dismiss_alert(page)
                if page.locator(".cityName").count() > 0:
                    ctx.storage_state(path=AUTH_STATE_PATH)
                    return "ok"
                if page.locator(OTP_FIELD).count() > 0:
                    return "otp_required"
                login_field = page.locator(LOGIN_FIELD)
                if login_field.count() > 0:
                    if submitted_at is None:
                        login_field.fill(username)
                        page.fill("input[placeholder='Password']", password)
                        _click_login_button(page)
                        submitted_at = i
                    elif i - submitted_at in (6, 14, 22):
                        _click_login_button(page)  # the LOGIN click is flaky, see login_start()
                elif i == 25 and submitted_at is None:
                    # Still blank - the render race login_start() documents.
                    page.reload(wait_until="load", timeout=45000)
            if page.locator(LOGIN_FIELD).count() > 0:
                return "not_registered"
            return "unknown"
        finally:
            browser.close()


def keep_alive_ping(username, password):
    """Gently keeps the Alhind session fresh between real scrapes, meant
    to be called every ~10 minutes from a background thread on the
    deployed server - NOT from the same hot path as a real scrape (skip
    calling this while a real job is already running; two Chromium
    instances at once risks the same OOM crash documented for the
    WhatsApp sidecar).

    Deliberately minimal: just reloads the search page (no actual
    search), and if that shows the session is dead, submits username +
    password AT MOST once. It NEVER calls login_start() / requests a
    fresh OTP - that's the one thing confirmed to trigger Alhind's rate-
    limit, so if Alhind wants OTP, this only notes that (and backs off
    for _OTP_BACKOFF_SECONDS) rather than pushing further; only a human
    via the app's own relogin flow can complete an OTP prompt anyway.

    Returns one of: "no_session_file", "backing_off", "already_logged_in",
    "relogged_in", "otp_required", "not_registered", or an error string
    (never raises - a keep-alive ping failing should never crash its
    background loop)."""
    if not has_session():
        return "no_session_file"

    if _otp_required_since["ts"] and time.time() - _otp_required_since["ts"] < _OTP_BACKOFF_SECONDS:
        return "backing_off"

    try:
        with sync_playwright() as p:
            browser, ctx = _new_context(p, headless=True, fresh=False)
            page = ctx.new_page()
            try:
                page.goto(HOME_URL, wait_until="load", timeout=30000)
                page.wait_for_timeout(1500)
                _dismiss_alert(page)
                login_field = page.locator("input[placeholder='Enter Mobile Number/ Email ID']")
                if login_field.count() == 0:
                    return "already_logged_in"

                login_field.fill(username)
                page.fill("input[placeholder='Password']", password)
                _click_login_button(page)
                for i in range(20):
                    page.wait_for_timeout(1000)
                    login_field = page.locator("input[placeholder='Enter Mobile Number/ Email ID']")
                    if login_field.count() == 0:
                        break
                    if i in (5, 12):
                        _click_login_button(page)

                if page.locator("input[placeholder='Mobile OTP']").count() > 0:
                    _otp_required_since["ts"] = time.time()
                    return "otp_required"
                if login_field.count() > 0:
                    return "not_registered"

                ctx.storage_state(path=AUTH_STATE_PATH)
                _otp_required_since["ts"] = 0
                return "relogged_in"
            finally:
                browser.close()
    except Exception as e:
        return f"error: {e}"

    return all_results
