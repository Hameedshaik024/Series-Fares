# Deploying the Fare Poster app

Single Render Web Service: Flask (`backend/app.py`) serves both the API
(`/api/*`) and the frontend (`backend/static/index.html` + `app.js`) from
one process, one URL. No CORS, no separate frontend deploy.

(The standalone `frontend/` folder is kept only as a reference if you ever
want to split it out to Netlify later — it's not used in this setup.)

## Deploy to Render

1. Push this project to GitHub (already done if you're reading this from
   the deployed repo).
2. On Render: **New → Web Service**, connect the repo, set **Root
   Directory** to `backend`. It should auto-detect the `Dockerfile`
   (Runtime: Docker).
3. Set these environment variables (Settings → Environment):
   - `AIRIQ_USER` — your AirIQ login username
   - `AIRIQ_PASS` — your AirIQ login password
   - `ALHIND_USER`, `ALHIND_PASS` — your Alhind (travel.alhind.com) login,
     used for the named-flight/fare-class posters (see below)
   - `APP_PASSWORD` — a long random string; this is the password the app
     will ask for on the login screen
   - `WHATSAPP_SHARED_SECRET` — a long random string (internal only, see
     below)
   - `WHATSAPP_GROUP_ID_HYD_DXB`, `WHATSAPP_GROUP_ID_HYD_RUH`,
     `WHATSAPP_GROUP_ID_HYD_MCT` — leave blank for now, you'll fill these
     in after step 6 (one per route; any left blank are just skipped when
     you click "Send")
4. Deploy. Render gives you a public URL, e.g.
   `https://series-fares.onrender.com` — open it directly, that's the app.
5. **First login**: the container starts with no saved AirIQ session. Enter
   your `APP_PASSWORD` on the gate screen, then use the "AirIQ session
   expired" panel to send + enter an OTP once — this writes
   `state/storage_state.json` inside the running container.
   - Free-tier disk is ephemeral across redeploys/restarts, so you'll need
     to repeat this OTP step after every redeploy or whenever Render
     restarts the service (e.g. after being idle). This is a limit of
     AirIQ's OTP login, not something the app can get around.
6. **Link WhatsApp** (one-time, for the "Send to WhatsApp" button): in the
   app, click **Link WhatsApp** under "WhatsApp auto-post" and scan the QR
   with the WhatsApp account that should post into your groups (WhatsApp →
   Linked Devices → Link a Device). Once linked, click **Show my groups**,
   find each of your three target groups (one for HYD-DXB, one for
   HYD-RUH, one for HYD-MCT) in the list, and copy each ID (looks like
   `123456789-987654321@g.us`). Set them as `WHATSAPP_GROUP_ID_HYD_DXB`,
   `WHATSAPP_GROUP_ID_HYD_RUH`, `WHATSAPP_GROUP_ID_HYD_MCT` in Render's
   environment variables and redeploy once more to pick them up. You don't
   need all three set — any route left blank is just skipped when you
   click send, the others still go out.
   - **Important caveats**: this uses an unofficial, reverse-engineered
     WhatsApp Web protocol (Baileys) — not Meta's official API — because
     the official API cannot post into arbitrary group chats at all. This
     carries a small risk of the linked number being rate-limited/banned
     if used heavily; consider not using your primary personal number.
     Like the AirIQ session, the link is lost on every redeploy/restart
     and needs re-scanning.

## Daily automatic post at 8:00 AM IST

`.github/workflows/daily-whatsapp-post.yml` calls the app's own
`/api/whatsapp/send-monthly` endpoint once a day (02:30 UTC = 08:00 IST) -
it's just the alarm clock, the endpoint does the real work exactly like
clicking the button yourself.

Two things are required for this to actually work every morning, not just
sometimes:

1. **A GitHub Actions secret** named `APP_PASSWORD`, set to the same
   value as the Render env var of the same name: repo → **Settings** →
   **Secrets and variables** → **Actions** → **New repository secret**.
   (You can test the workflow immediately without waiting for 8 AM: repo →
   **Actions** tab → "Daily WhatsApp fare post" → **Run workflow**.)
2. **Keep the Render service from spinning down overnight**, since the
   AirIQ session and WhatsApp link live on that container's disk and are
   wiped by a restart (idle spin-down counts as a restart). The free,
   no-code-change way to do this: sign up at
   [uptimerobot.com](https://uptimerobot.com) (free tier) and add an HTTP(s)
   monitor hitting `https://series-fares.onrender.com/api/health` every 5
   minutes. As long as something's pinging it, Render's free tier never
   sees the 15 minutes of inactivity that triggers a spin-down, so the
   same container - and whatever's logged into AirIQ/WhatsApp on it -
   keeps running.

**What this doesn't fix**: the free tier's 512MB memory ceiling (a crash
there still wipes the session same as a restart would), and AirIQ's own
session eventually expiring on its own schedule, which we don't know the
length of. Either one means that morning's automatic run fails with
"session expired" and needs a human to do the OTP relogin (or WhatsApp
re-scan) before the next day's run can succeed - this can't be automated
away, since OTP entry needs a person to relay a live code. Worth
occasionally checking that the app is still logged in.

## Named-flight posters (Alhind) - specific flight + fare-class, not "cheapest"

A second, independent poster pipeline sourced from Alhind
(`backend/alhind_client.py`, `backend/named_flights.py`) for cases where
you want a poster for one *specific* flight number and fare class (e.g.
"IndiGo 6E 1273, Tactical fare") rather than AirIQ's "whatever's
cheapest that day" rule. Each named-flight group (currently just
`"muscat"`) is a list of routes in `named_flights.py`; running it builds
one poster per route, bundles them into a single PDF, and sends that PDF
to the group's WhatsApp group.

**One-time setup per container** (same ephemeral-disk caveat as AirIQ -
needed again after every redeploy/restart):
1. Log in via `POST /api/alhind/login/start` then `/api/alhind/login/verify`
   with the OTP (same two-step flow as AirIQ's login panel, just a
   separate endpoint - there's no UI button for this yet, so it needs a
   raw API call, e.g. from the browser's dev console or curl, using your
   `APP_PASSWORD` as the Bearer token).
2. After that, Alhind's session auto-relogs in with no OTP for every
   subsequent run - confirmed live this is genuinely different from
   AirIQ (which needs a fresh human-relayed OTP every time the session
   dies), though the session token itself expires faster, which is why
   every search transparently relogs in rather than assuming one login
   lasts the whole scrape.

**Trigger a run**: `POST /api/whatsapp/send-named-flights` with JSON body
`{"group": "muscat"}` (same async job pattern as the other WhatsApp
button - poll `/api/generate/status/<job_id>`). No UI button for this yet
either.

**Expect this to take a while**: unlike AirIQ (30 days in ~3 minutes),
each Alhind search takes ~20-50 seconds (its site has no "skip the
second check" shortcut and a faster-expiring session needing more
relogins), so a 6-route group scanning 30 days each can realistically
take 60-90+ minutes total. Size any external trigger's timeout/poll
window accordingly - the GitHub Actions daily-post pattern above is a
good reference, just with a much longer wait budget.

Confirmed live and baked into the scraper: Alhind's results are flooded
with connecting IndiGo itineraries unless the "Direct Flights" filter is
checked, which hides other carriers (SalamAir, Oman Air, etc.) entirely,
not just reorders them - `_ensure_direct_flight_checked()` in
`alhind_client.py` handles this on every search.

## Running locally first (recommended before deploying)

```bash
cd backend
pip install -r requirements.txt
python -m playwright install chromium
cd whatsapp && npm install && cd ..
WHATSAPP_SHARED_SECRET=testsecret node whatsapp/index.js &
AIRIQ_USER=... AIRIQ_PASS=... APP_PASSWORD=testpass123 WHATSAPP_SHARED_SECRET=testsecret python app.py
```

Then open `http://localhost:5000` in a browser and log in with
`testpass123`. (The WhatsApp sidecar is optional for local testing if
you're not working on that feature — the rest of the app works without it,
the WhatsApp panel will just show "service isn't reachable".)

## Notes / limitations

- Only one AirIQ session at a time — the backend keeps a single browser
  login, not one per user. Fine for personal/small-team use, not built for
  many concurrent users.
- Every "Generate" always covers the **next 30 days from today** (no month
  picker) and checks **both** the AIR IQ and Market Place tabs for each
  date, so it takes a couple of minutes (AirIQ has no bulk fare endpoint;
  the backend searches day-by-day, twice per day). The frontend shows a
  loading state with live progress during this — don't close the tab.
- Fare source rule (`backend/pricing.py`, shared by both the manual
  generator and the WhatsApp button): if AIR IQ has any fare for a date,
  use its cheapest and add a flat +₹500; otherwise fall back to Market
  Place's cheapest fare, unmarked up. Dates with no fare from either
  source are simply left off the poster.
- If AirIQ changes their page structure (field IDs, CSS classes), the
  scraper (`backend/airiq_client.py`) will need updating to match.
- The WhatsApp sidecar (`backend/whatsapp/`) runs as a background process
  inside the same container, reachable only via `localhost` — it's never
  exposed publicly. Flask proxies the QR/groups endpoints and calls
  `/send` directly over loopback. See `backend/whatsapp/index.js`.
- The one-click send button is hardcoded to three routes (HYD-DXB,
  HYD-RUH, HYD-MCT) and the next 30 days, per the original request — not a
  general-purpose route picker. It builds a separate poster per route and
  posts each into its own group (same caption on all three), using the
  same automatic AirIQ+500/Market Place+0 rule above, no separate flat
  add-on. If one route fails (no fares, AirIQ session dies, etc.) the
  others still go out — the status message after sending shows a
  per-route ✅/❌/⚪ result.
