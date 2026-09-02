#!/bin/sh
set -e

# WhatsApp sidecar runs in the background on localhost only - never
# exposed publicly. Flask proxies the couple of endpoints a human needs
# (QR code, group list) and calls /send directly over loopback.
# --max-old-space-size caps Node's heap so it can't grow unbounded and
# contend with Playwright's Chromium for the free tier's 512MB total -
# relevant after a confirmed OOM-style crash ("Instance failed" in
# Render's event log) during a WhatsApp send.
#
# Wrapped in a restart loop: confirmed live that an uncaught Baileys
# exception (a WhatsApp session decryption error, unrelated to anything
# in our own code) can crash this process entirely - and nothing was
# restarting it, so the sidecar just stayed dead for the rest of the
# container's life, silently failing any later WhatsApp send (including
# a 60-90 minute named-flight job's PDF send at the very end). The short
# pause avoids a tight crash-loop pegging CPU if it keeps failing.
(
  while true; do
    node --max-old-space-size=128 whatsapp/index.js
    echo "WhatsApp sidecar exited (code $?) - restarting in 5s" >&2
    sleep 5
  done
) &

exec gunicorn -b 0.0.0.0:5000 --timeout 300 --workers 1 app:app
