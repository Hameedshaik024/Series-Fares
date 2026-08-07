FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# storage_state.json (the AirIQ login session) lives here; on most platforms'
# free tiers this disk is ephemeral across redeploys, so a fresh OTP login
# will be needed after every redeploy/restart.
RUN mkdir -p /app/state

ENV PORT=5000
EXPOSE 5000

CMD ["gunicorn", "-b", "0.0.0.0:5000", "--timeout", "300", "--workers", "1", "app:app"]
