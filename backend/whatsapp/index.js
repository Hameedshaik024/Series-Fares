const express = require("express");
const multer = require("multer");
const QRCode = require("qrcode");
const pino = require("pino");
const path = require("path");
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
} = require("@whiskeysockets/baileys");

const PORT = process.env.WHATSAPP_PORT || 3000;
const SECRET = process.env.WHATSAPP_SHARED_SECRET || "";
const STATE_DIR = path.join(__dirname, "state");

const app = express();
app.use(express.json());

const upload = multer({ storage: multer.memoryStorage() });

let sock = null;
let latestQrPng = null; // Buffer | null
let isLinked = false;

function requireSecret(req, res, next) {
  if (!SECRET) {
    return res.status(500).json({ error: "server_misconfigured", detail: "WHATSAPP_SHARED_SECRET not set" });
  }
  if (req.headers["x-internal-secret"] !== SECRET) {
    return res.status(401).json({ error: "unauthorized" });
  }
  next();
}

async function startSock() {
  const { state, saveCreds } = await useMultiFileAuthState(STATE_DIR);

  sock = makeWASocket({
    auth: state,
    logger: pino({ level: "warn" }),
    printQRInTerminal: false,
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async (update) => {
    const { connection, qr, lastDisconnect } = update;

    if (qr) {
      latestQrPng = await QRCode.toBuffer(qr, { width: 400 });
      isLinked = false;
    }

    if (connection === "open") {
      isLinked = true;
      latestQrPng = null;
      console.log("WhatsApp linked.");
    }

    if (connection === "close") {
      isLinked = false;
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const loggedOut = statusCode === DisconnectReason.loggedOut;
      console.log("WhatsApp connection closed.", { statusCode, loggedOut });
      if (!loggedOut) {
        // Without a delay here, an unlinked session (the normal state right
        // after every deploy, since this state is as ephemeral as AirIQ's)
        // reconnects in a tight loop - each attempt opens a socket, gets a
        // fresh QR, times out waiting to be scanned, closes, repeat. That
        // pegs the container's CPU and can starve the Python worker from
        // ever finishing its own startup within Render's port-scan window.
        setTimeout(() => {
          startSock().catch((e) => console.error("reconnect failed", e));
        }, 5000);
      }
    }
  });
}

startSock().catch((e) => console.error("startSock failed", e));

app.get("/health", (req, res) => {
  res.json({ status: "ok", linked: isLinked });
});

app.get("/qr", requireSecret, async (req, res) => {
  if (isLinked) {
    return res.json({ linked: true });
  }
  if (!latestQrPng) {
    return res.status(202).json({ linked: false, qr_ready: false });
  }
  res.set("Content-Type", "image/png");
  res.send(latestQrPng);
});

app.get("/groups", requireSecret, async (req, res) => {
  if (!isLinked || !sock) {
    return res.status(409).json({ error: "not_linked" });
  }
  try {
    const groups = await sock.groupFetchAllParticipating();
    const list = Object.values(groups).map((g) => ({ id: g.id, name: g.subject }));
    res.json(list);
  } catch (e) {
    res.status(500).json({ error: "fetch_failed", detail: String(e) });
  }
});

app.post("/send", requireSecret, upload.single("image"), async (req, res) => {
  if (!isLinked || !sock) {
    return res.status(409).json({ error: "not_linked" });
  }
  const { groupId, caption } = req.body;
  if (!groupId || !req.file) {
    return res.status(400).json({ error: "missing_params", detail: "groupId and image are required" });
  }
  try {
    await sock.sendMessage(groupId, { image: req.file.buffer, caption: caption || "" });
    res.json({ status: "sent" });
  } catch (e) {
    res.status(500).json({ error: "send_failed", detail: String(e) });
  }
});

app.post("/send-document", requireSecret, upload.single("document"), async (req, res) => {
  if (!isLinked || !sock) {
    return res.status(409).json({ error: "not_linked" });
  }
  const { groupId, caption, fileName } = req.body;
  if (!groupId || !req.file) {
    return res.status(400).json({ error: "missing_params", detail: "groupId and document are required" });
  }
  try {
    await sock.sendMessage(groupId, {
      document: req.file.buffer,
      mimetype: req.file.mimetype || "application/pdf",
      fileName: fileName || "fares.pdf",
      caption: caption || "",
    });
    res.json({ status: "sent" });
  } catch (e) {
    res.status(500).json({ error: "send_failed", detail: String(e) });
  }
});

app.listen(PORT, "127.0.0.1", () => {
  console.log(`WhatsApp sidecar listening on 127.0.0.1:${PORT}`);
});
