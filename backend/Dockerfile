FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Node.js for the WhatsApp (Baileys) sidecar - the base image is Ubuntu
# (jammy), so NodeSource's setup script works directly.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN cd whatsapp && npm install --omit=dev

# storage_state.json (the AirIQ login session) and the WhatsApp link both
# live under here; on most platforms' free tiers this disk is ephemeral
# across redeploys, so a fresh AirIQ OTP login and a fresh WhatsApp QR
# scan will both be needed after every redeploy/restart.
RUN mkdir -p /app/state /app/whatsapp/state

RUN chmod +x start.sh

ENV PORT=5000
EXPOSE 5000

CMD ["./start.sh"]
