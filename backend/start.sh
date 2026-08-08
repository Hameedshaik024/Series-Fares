#!/bin/sh
set -e

# WhatsApp sidecar runs in the background on localhost only - never
# exposed publicly. Flask proxies the couple of endpoints a human needs
# (QR code, group list) and calls /send directly over loopback.
node whatsapp/index.js &

exec gunicorn -b 0.0.0.0:5000 --timeout 300 --workers 1 app:app
