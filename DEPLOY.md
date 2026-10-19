# Deploying the Fare Poster app

Single Render Web Service: Flask (`backend/app.py`) serves both the API
(`/api/*`) and the frontend (`backend/static/index.html` + `app.js`) from
one process, one URL. No CORS, no separate frontend deploy.

(The standalone `frontend/` folder is kept only as a reference if you ever
want to split it out to Netlify later — it's not used in this setup.)

## Phone app + daily posters (free, runs on GitHub)

The simpler, free setup, with no server to keep alive:
`.github/workflows/daily-posters.yml` runs `backend/daily_posters.py`
every morning at 06:37 IST on GitHub Actions. It scrapes, renders the
posters, and publishes them to GitHub Pages together with the phone app in
`mobile/` - an installable web app (add it to the home screen) that shows
the last 7 days of posters with Share/Save buttons, a **Generate now**
button, and a box for the Alhind OTP when one is needed.

One-time setup:

1. Create a fine-grained token (GitHub → Settings → Developer settings →
   Fine-grained tokens) for this repository only, with **Actions**,
   **Secrets** and **Variables** set to *Read and write*.
2. Repo → Settings → Secrets and variables → Actions → add the secrets
   `ALHIND_USER`, `ALHIND_PASS` and `GH_PAT` (that token). The run itself
   keeps the Alhind login in an `ALHIND_STATE` secret.
3. Repo → Settings → Pages → Source: **GitHub Actions**.
4. On the phone, open `https://hameedshaik024.github.io/Series-Fares/`,
   add it to the home screen, and paste the same token into the app's
   Settings (only needed for Generate now and OTPs).

OTPs: Alhind only asks for one when it doesn't recognise the "device" -
its `Loginseckey` cookie, which lasts 15 days and is renewed by every
login. The daily run logs in each morning and saves the renewed cookie
straight away, so normally no OTP is ever needed. If Alhind asks anyway,
the run has it texted, waits up to 15 minutes, and takes the code from
the `ALHIND_OTP` repo variable - typed into the app, or created by an SMS
automation on the phone (a plain POST of the whole SMS text to
`/repos/Hameedshaik024/Series-Fares/actions/variables` with
`{"name": "ALHIND_OTP", "value": "<sms>"}`). Progress goes to the
`POSTERS_LIVE` variable, which the app polls.

## Deploy to Render

1. Push this project to GitHub (already done if you're reading this from
   the deployed repo).
2. On Render: **New → Web Service**, connect the repo, set **Root
   Directory** to `backend`. It should auto-detect the `Dockerfile`
   (Runtime: Docker).
3. Set these environment variables (Settings → Environment):
   - `ALHIND_USER`, `ALHIND_PASS` — your Alhind (travel.alhind.com) login,
     used for the named-flight/fare-class posters
   - `APP_PASSWORD` — a long random string; this is the password the app
     will ask for on the login screen
   - `WHATSAPP_SHARED_SECRET` — a long random string (internal only, see
     below)
   - `WHATSAPP_GROUP_ID_DUBAI` — leave blank for now, you'll fill it in
     after step 5
4. Deploy. Render gives you a public URL, e.g.
   `https://series-fares.onrender.com` — open it directly, that's the app.
5. **Link WhatsApp** (one-time, for the "Send" button): in the app, click
   **Link WhatsApp** under "WhatsApp auto-post" and scan the QR with the
   WhatsApp account that should post into your groups (WhatsApp → Linked
   Devices → Link a Device). Once linked, click **Show my groups**, find
   the Dubai group in the list, and copy its ID (looks like
   `123456789-987654321@g.us`). Set it as `WHATSAPP_GROUP_ID_DUBAI` in
   Render's environment variables and redeploy once more to pick it up.
   - **Important caveats**: this uses an unofficial, reverse-engineered
     WhatsApp Web protocol (Baileys) — not Meta's official API — because
     the official API cannot post into arbitrary group chats at all. This
     carries a small risk of the linked number being rate-limited/banned
     if used heavily; consider not using your primary personal number.
     The link is lost on every redeploy/restart and needs re-scanning
     (free-tier disk is ephemeral).
6. **First Alhind login**: the container starts with no saved Alhind
   session. Enter your `APP_PASSWORD` on the gate screen, then use the
   "Alhind session expired" panel to send + enter an OTP once — this
   writes `state/alhind_storage_state.json` inside the running container.
   After that, every subsequent run relogs in automatically with no OTP
   (until the container restarts/redeploys, since free-tier disk is
   ephemeral) — see **Keeping the Alhind session alive** below for what
   happens when Alhind asks for OTP again anyway, which it can do
   unpredictably even without a restart.

## Named-flight posters (Alhind) - specific flight + fare-class, not "cheapest"

`backend/alhind_client.py` + `backend/named_flights.py` scrape a
*specific* flight number and fare class per route (e.g. "IndiGo 6E 1273,
Tactical fare"), not "whatever's cheapest that day", with a fallback so
no date is ever left blank: if the named flight isn't offered that date,
it falls back to the cheapest 25kg/30kg-baggage fare (any airline), and
if even that finds nothing, the single cheapest fare on the page,
period. Each named-flight group (currently just `"dubai"`) is a list of
"posters" in `named_flights.py`, each poster bundling several routes
into one stacked image (`poster.build_multi` - e.g. 3 routes all flying
into the same hub city, sharing one date range); running it scrapes
every route across every poster in the group and sends each poster as
its own image to the group's WhatsApp group (built all first, then sent
back-to-back).

**One-time setup per container** (needed again after every
redeploy/restart, since free-tier disk is ephemeral):
1. Log in via the "Alhind session expired" panel in the app (Send OTP,
   then enter it).
2. After that, Alhind's session auto-relogs in with no OTP for every
   subsequent run — though the session token itself expires faster than
   a typical B2B portal's, which is why every search transparently
   relogs in rather than assuming one login lasts the whole scrape.

### Keeping the Alhind session alive

`app.py` runs a background thread (`_alhind_keep_alive_loop`, started
automatically whenever `ALHIND_USER`/`ALHIND_PASS` are set) that pings
the Alhind session every 10 minutes via `alhind_client.keep_alive_ping` -
just reloading the search page, and relogging in with username+password
if the session died, so it's less likely to be dead right when a real
job needs it. It skips its ping entirely while a real job is running
(no need, and avoids two Chromium instances competing for memory on the
free tier).

**This is a mitigation, not a guarantee.** Alhind's own security policy
can require a fresh OTP at unpredictable times (confirmed live - it
happened mid-session with no restart involved), and completing an OTP
prompt needs a human; nothing here can automate that step. The keep-
alive deliberately never calls `login_start()` / requests a fresh OTP
itself - confirmed live that repeating **that** specific call is what
got Alhind to rate-limit OTP sends ("You have tried otp multiple times
wait sometimes"). If a ping instead finds Alhind asking for OTP, it just
notes that and backs off from re-submitting the login form for an hour
(`alhind_client._OTP_BACKOFF_SECONDS`) rather than hammering it -  at
that point a human needs to complete the OTP via the app's "Alhind
session expired" panel same as any other time.

**Trigger a run**: the "Named-flight posters" card in the app (select
group, click Generate & send), or `POST /api/whatsapp/send-named-flights`
with JSON body `{"group": "dubai"}` directly (same async job pattern —
poll `/api/generate/status/<job_id>`).

**Expect this to take a while**: each Alhind search takes ~20-50 seconds
(its site has no "skip the second check" shortcut and a faster-expiring
session needing more relogins), so a 6-route group (e.g. "dubai": 2
posters x 3 routes) scanning 15 days each can realistically take
15-30+ minutes total.

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
ALHIND_USER=... ALHIND_PASS=... APP_PASSWORD=testpass123 WHATSAPP_SHARED_SECRET=testsecret python app.py
```

Then open `http://localhost:5000` in a browser and log in with
`testpass123`. (The WhatsApp sidecar is optional for local testing if
you're not working on that feature — the rest of the app works without it,
the WhatsApp panel will just show "service isn't reachable".)

## Notes / limitations

- Only one Alhind session at a time — the backend keeps a single browser
  login, not one per user. Fine for personal/small-team use, not built for
  many concurrent users.
- If Alhind changes their page structure (field IDs, CSS classes), the
  scraper (`backend/alhind_client.py`) will need updating to match.
- The WhatsApp sidecar (`backend/whatsapp/`) runs as a background process
  inside the same container, reachable only via `localhost` — it's never
  exposed publicly. Flask proxies the QR/groups endpoints and calls
  `/send` directly over loopback. See `backend/whatsapp/index.js`.
- Named-flight groups (`backend/named_flights.py`) build one poster per
  entry (each poster stacking several routes) and post each into its
  own group (same caption on all posters in a group). If one route or
  poster fails (no fares found across all 15 days, session dies, etc.)
  the others still go out — the status message after sending shows a
  per-poster and per-route ✅/❌/⚪ result.
