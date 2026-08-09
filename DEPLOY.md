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
   - `APP_PASSWORD` — a long random string; this is the password the app
     will ask for on the login screen
   - `WHATSAPP_SHARED_SECRET` — a long random string (internal only, see
     below)
   - `WHATSAPP_GROUP_ID` — leave blank for now, you'll fill this in after
     step 6
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
   with the WhatsApp account that should post into your group (WhatsApp →
   Linked Devices → Link a Device). Once linked, click **Show my groups**,
   find your target group in the list, and copy its ID (looks like
   `123456789-987654321@g.us`). Set that as `WHATSAPP_GROUP_ID` in Render's
   environment variables and redeploy once more to pick it up.
   - **Important caveats**: this uses an unofficial, reverse-engineered
     WhatsApp Web protocol (Baileys) — not Meta's official API — because
     the official API cannot post into arbitrary group chats at all. This
     carries a small risk of the linked number being rate-limited/banned
     if used heavily; consider not using your primary personal number.
     Like the AirIQ session, the link is lost on every redeploy/restart
     and needs re-scanning.

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
- The "Send HYD → DXB to WhatsApp" button is hardcoded to that one route
  and the next 30 days, per the original request — not a general-purpose
  route picker. Its markup is the same automatic AirIQ+500/Market Place+0
  rule above, no separate flat add-on.
