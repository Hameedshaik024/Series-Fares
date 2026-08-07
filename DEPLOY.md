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

## Running locally first (recommended before deploying)

```bash
cd backend
pip install -r requirements.txt
python -m playwright install chromium
AIRIQ_USER=... AIRIQ_PASS=... APP_PASSWORD=testpass123 python app.py
```

Then open `http://localhost:5000` in a browser and log in with
`testpass123`.

## Notes / limitations

- Only one AirIQ session at a time — the backend keeps a single browser
  login, not one per user. Fine for personal/small-team use, not built for
  many concurrent users.
- A "Generate" call takes **1-3 minutes** (AirIQ has no bulk fare endpoint;
  the backend searches one day at a time). The frontend shows a loading
  state during this — don't close the tab.
- Only the **AIR IQ** fare tab is read; the **MARKET PLACE** tab is never
  clicked, per your requirement to ignore market fares.
- If AirIQ changes their page structure (field IDs, CSS classes), the
  scraper (`backend/airiq_client.py`) will need updating to match.
