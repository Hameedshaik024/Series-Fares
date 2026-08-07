# Deploying the Fare Poster app

Two pieces: `backend/` (Flask + Playwright, does the actual AirIQ scraping)
and `frontend/` (static HTML/JS). They deploy separately.

## 1. Backend → Render (or Railway)

Render's free "Web Service" works well since the `Dockerfile` already has
Chromium baked in.

1. Push this project to a GitHub repo (or connect Render directly to your
   local folder via their CLI).
2. On Render: **New → Web Service**, point it at the repo, set **Root
   Directory** to `backend`, and it will auto-detect the `Dockerfile`.
3. Set these environment variables in Render's dashboard (Settings →
   Environment):
   - `AIRIQ_USER` — your AirIQ login username
   - `AIRIQ_PASS` — your AirIQ login password
   - `APP_PASSWORD` — a long random string; this is the password the web app
     will ask for
4. Deploy. Note the public URL Render gives you, e.g.
   `https://sea-sky-fares.onrender.com`.
5. **First login**: the container starts with no saved AirIQ session. Open
   the frontend (step 2 below) and use the "AirIQ session expired" panel to
   send + enter an OTP once — this writes `state/storage_state.json` inside
   the running container.
   - Free-tier disk is ephemeral across redeploys/restarts, so you'll need
     to repeat this OTP step after every redeploy or whenever Render
     restarts the service (e.g. after being idle). This is a limit of
     AirIQ's OTP login, not something the app can get around.

## 2. Frontend → Netlify

1. Edit `frontend/config.js` and set `API_BASE_URL` to your Render URL from
   step 1.4 above, e.g.:
   ```js
   window.CONFIG = { API_BASE_URL: "https://sea-sky-fares.onrender.com" };
   ```
2. On Netlify: **Add new site → Deploy manually** (drag the `frontend`
   folder in), or connect the repo with **Base directory** set to
   `frontend` and no build command (it's static files, `netlify.toml`
   already sets `publish = "."`).
3. Netlify gives you a public URL, e.g. `https://sea-sky-fares.netlify.app`.
   Open it, enter the `APP_PASSWORD` you set in Render, and use the app.

## Running locally first (recommended before deploying)

```bash
cd backend
pip install -r requirements.txt
python -m playwright install chromium
AIRIQ_USER=... AIRIQ_PASS=... APP_PASSWORD=testpass123 python app.py
```

Then open `frontend/index.html` directly in a browser (with
`frontend/config.js` pointing at `http://localhost:5000`, which is the
default) and log in with `testpass123`.

## Notes / limitations

- Only one AirIQ session at a time — the backend keeps a single browser
  login, not one per user. Fine for a small team, not built for many
  concurrent users.
- A `/api/generate` call takes **1-3 minutes** (AirIQ has no bulk fare
  endpoint; the backend searches one day at a time). The frontend shows a
  loading state during this — don't close the tab.
- Only the **AIR IQ** fare tab is read; the **MARKET PLACE** tab is never
  clicked, per your requirement to ignore market fares.
- If AirIQ changes their page structure (field IDs, CSS classes), the
  scraper (`backend/airiq_client.py`) will need updating to match.
