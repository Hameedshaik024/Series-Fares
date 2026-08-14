#!/bin/sh
set -e

# WhatsApp sidecar runs in the background on localhost only - never
# exposed publicly. Flask proxies the couple of endpoints a human needs
# (QR code, group list) and calls /send directly over loopback.
# --max-old-space-size caps Node's heap so it can't grow unbounded and
# contend with Playwright's Chromium for the free tier's 512MB total -
# relevant after a confirmed OOM-style crash ("Instance failed" in
# Render's event log) during a WhatsApp send.
node --max-old-space-size=128 whatsapp/index.js &

exec gunicorn -b 0.0.0.0:5000 --timeout 300 --workers 1 app:app
