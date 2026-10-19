const API = ""; // same-origin: Flask serves this file and the /api/* routes

const $ = (id) => document.getElementById(id);

function authHeaders() {
  const token = sessionStorage.getItem("appToken") || "";
  return { "Authorization": "Bearer " + token, "Content-Type": "application/json" };
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, { ...opts, headers: { ...authHeaders(), ...(opts.headers || {}) } });
  return res;
}

// ---------- Password gate ----------

$("gateBtn").addEventListener("click", async () => {
  const pw = $("pw").value.trim();
  if (!pw) return;
  sessionStorage.setItem("appToken", pw);
  $("gateStatus").textContent = "Checking…";
  try {
    const res = await api("/api/health");
    if (res.status === 401) {
      $("gateStatus").textContent = "Wrong password.";
      sessionStorage.removeItem("appToken");
      return;
    }
    const data = await res.json();
    $("gate").style.display = "none";
    $("app").style.display = "block";
    initApp(data.alhind_logged_in);
  } catch (e) {
    $("gateStatus").textContent = "Could not reach the server: " + e.message;
  }
});

// ---------- App init ----------

async function initApp(alhindLoggedIn) {
  if (!alhindLoggedIn) {
    showAlhindRelogin();
  }
}

// ---------- Alhind re-login (OTP - one-time; auto-relogs in after) ----------

function showAlhindRelogin() {
  $("alhindReloginCard").style.display = "block";
  $("alhindReloginStep1").style.display = "block";
  $("alhindReloginStep2").style.display = "none";
  $("alhindReloginStatus").textContent = "";
}

$("alhindSendOtpBtn").addEventListener("click", async () => {
  $("alhindReloginStatus").textContent = "Sending OTP…";
  const res = await api("/api/alhind/login/start", { method: "POST" });
  const data = await res.json();
  if (!res.ok) {
    $("alhindReloginStatus").textContent = "Failed: " + (data.detail || data.error);
    return;
  }
  if (data.status === "already_logged_in") {
    $("alhindReloginStatus").textContent = "Already logged in!";
    $("alhindReloginCard").style.display = "none";
    return;
  }
  $("alhindReloginStep1").style.display = "none";
  $("alhindReloginStep2").style.display = "block";
  $("alhindReloginStatus").textContent = "OTP sent — enter it below quickly, it expires fast.";
});

$("alhindVerifyOtpBtn").addEventListener("click", async () => {
  const otp = $("alhindOtpInput").value.trim();
  if (!otp) return;
  $("alhindReloginStatus").textContent = "Verifying…";
  const res = await api("/api/alhind/login/verify", { method: "POST", body: JSON.stringify({ otp }) });
  const data = await res.json();
  if (res.ok && data.status === "ok") {
    $("alhindReloginStatus").textContent = "Logged in! This only needed the OTP once - future runs relog in automatically.";
    $("alhindReloginCard").style.display = "none";
  } else {
    $("alhindReloginStatus").textContent = "OTP invalid or expired — click Send OTP again and re-enter quickly.";
    $("alhindReloginStep1").style.display = "block";
    $("alhindReloginStep2").style.display = "none";
  }
});

// ---------- WhatsApp: link (QR) ----------

let waPolling = false;

$("linkWaBtn").addEventListener("click", async () => {
  if (waPolling) return;
  waPolling = true;
  $("linkWaBtn").disabled = true;
  $("waLinkStatus").textContent = "Loading QR…";

  for (let i = 0; i < 60; i++) { // ~3 min of polling
    let res;
    try {
      res = await api("/api/whatsapp/qr");
    } catch (e) {
      $("waLinkStatus").textContent = "Error: " + e.message;
      break;
    }

    if (res.status === 502) {
      $("waLinkStatus").textContent = "WhatsApp service isn't reachable yet — try again in a few seconds.";
      break;
    }

    const contentType = res.headers.get("Content-Type") || "";
    if (contentType.startsWith("image/")) {
      const blob = await res.blob();
      $("waQrImg").src = URL.createObjectURL(blob);
      $("waQrWrap").style.display = "block";
      $("waLinkStatus").textContent = "Scan the QR above.";
    } else {
      const data = await res.json();
      if (data.linked) {
        $("waQrWrap").style.display = "none";
        $("waLinkStatus").textContent = "✅ WhatsApp linked.";
        break;
      }
      // qr_ready: false - still starting up, keep polling
    }
    await new Promise((r) => setTimeout(r, 3000));
  }

  waPolling = false;
  $("linkWaBtn").disabled = false;
});

// ---------- WhatsApp: list groups ----------

$("showGroupsBtn").addEventListener("click", async () => {
  $("waGroups").textContent = "Loading…";
  const res = await api("/api/whatsapp/groups");
  const data = await res.json();
  if (!res.ok) {
    $("waGroups").textContent = "Failed: " + (data.detail || data.error);
    return;
  }
  if (!data.length) {
    $("waGroups").textContent = "No groups found — make sure WhatsApp is linked and the account has joined a group.";
    return;
  }
  $("waGroups").innerHTML = data
    .map((g) => `<div style="padding:6px 0;border-bottom:1px solid var(--border);"><b>${g.name}</b><br><span style="color:var(--ink-secondary);user-select:all;">${g.id}</span></div>`)
    .join("");
});

// ---------- Named-flight posters (Alhind): one-click send ----------

$("sendNamedBtn").addEventListener("click", async () => {
  $("sendNamedBtn").disabled = true;
  const set = (html, cls) => {
    const el = $("namedSendStatus");
    el.innerHTML = html;
    el.className = cls || "";
  };
  const group = $("namedGroup").value;
  set('<span class="spinner"></span> Starting…', "");

  try {
    const res = await api("/api/whatsapp/send-named-flights", {
      method: "POST",
      body: JSON.stringify({ group }),
    });
    const startData = await res.json().catch(() => ({}));

    if (res.status === 401) {
      if (startData.error === "not_logged_in") {
        set("Alhind session expired.", "err");
        showAlhindRelogin();
      } else {
        set("Wrong app password — reload and re-enter it.", "err");
      }
      return;
    }
    if (!res.ok) {
      set("Failed: " + (startData.detail || startData.error || res.statusText), "err");
      return;
    }

    const jobId = startData.job_id;
    while (true) {
      // Named-flight scans take longer than a simple lookup (no
      // "skip the second check" shortcut, and Alhind's session expires
      // fast, needing relogins along the way) - this polls patiently.
      await new Promise((r) => setTimeout(r, 3000));
      const sres = await api(`/api/generate/status/${jobId}`);
      let data;
      try {
        data = await sres.json();
      } catch (e) {
        set("Lost connection to the server mid-job (it may have restarted) — routes already sent went through; check WhatsApp for the rest, then try again.", "err");
        break;
      }

      if (data.status === "running") {
        const p = data.progress;
        const progressText = p
          ? `Route ${p.route_num}/${p.route_total} (${p.route}) — day ${p.day}/${p.total} (${p.last_status})`
          : "Starting…";
        set(`<span class="spinner"></span> ${progressText} — this can take a while, please wait…`, "");
        continue;
      }
      if (data.status === "error") {
        if (data.error === "not_logged_in") {
          set("Alhind session expired partway through and couldn't auto-relogin.", "err");
          showAlhindRelogin();
        } else {
          set("Failed: " + data.error, "err");
        }
        break;
      }

      // done - data.result is {"Fares to Dubai": {sent:true, routes:{"HYD-DXB 6E 1234 Value": {fare_days:N} | {error:...}, ...}} | {error:...}, ...}
      const lines = Object.entries(data.result || {}).flatMap(([title, r]) => {
        const posterLine = r.sent
          ? `✅ ${title}: sent`
          : r.error === "not_found_any_route"
          ? `⚪ ${title}: skipped (no route found on any day)`
          : `❌ ${title}: ${r.error}`;
        const routeLines = Object.entries(r.routes || {}).map(([route, rr]) =>
          rr.error
            ? `&nbsp;&nbsp;⚪ ${route}: ${rr.error === "not_found_any_day" ? "not found on any day" : rr.error}`
            : `&nbsp;&nbsp;• ${route}: ${rr.fare_days} dates`
        );
        return [posterLine, ...routeLines];
      });
      const anySent = Object.values(data.result || {}).some((r) => r.sent);
      set(lines.join("<br>"), anySent ? "ok" : "err");
      break;
    }
  } catch (e) {
    set("Error: " + e.message, "err");
  } finally {
    $("sendNamedBtn").disabled = false;
  }
});
