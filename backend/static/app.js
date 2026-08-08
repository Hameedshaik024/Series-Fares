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
    initApp(data.logged_in);
  } catch (e) {
    $("gateStatus").textContent = "Could not reach the server: " + e.message;
  }
});

// ---------- App init ----------

function monthOptions() {
  const now = new Date();
  const opts = [];
  for (let i = 0; i < 6; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() + i, 1);
    const label = d.toLocaleString("default", { month: "long", year: "numeric" });
    opts.push({ year: d.getFullYear(), month: d.getMonth() + 1, label });
  }
  return opts;
}

async function initApp(loggedIn) {
  $("month").innerHTML = monthOptions()
    .map((o) => `<option value="${o.year}-${o.month}">${o.label}</option>`)
    .join("");

  if (!loggedIn) {
    showRelogin();
  } else {
    loadOrigins();
  }
}

async function loadOrigins() {
  $("origin").innerHTML = `<option>Loading…</option>`;
  const res = await api("/api/origins");
  if (res.status === 401) { showRelogin(); return; }
  const data = await res.json();
  $("origin").innerHTML = data.map((o) => `<option value="${o.code}">${o.code} - ${o.label}</option>`).join("");
  loadDestinations($("origin").value);
}

$("origin").addEventListener("change", (e) => loadDestinations(e.target.value));

async function loadDestinations(origin) {
  if (!origin) return;
  $("dest").innerHTML = `<option>Loading…</option>`;
  const res = await api("/api/destinations?origin=" + encodeURIComponent(origin));
  if (res.status === 401) { showRelogin(); return; }
  const data = await res.json();
  if (!data.length) {
    $("dest").innerHTML = `<option value="">No routes from this origin</option>`;
    return;
  }
  $("dest").innerHTML = data.map((o) => `<option value="${o.code}">${o.code} - ${o.label}</option>`).join("");
}

// ---------- Re-login (OTP) ----------

function showRelogin() {
  $("reloginCard").style.display = "block";
  $("reloginStep1").style.display = "block";
  $("reloginStep2").style.display = "none";
  $("reloginStatus").textContent = "";
}

$("sendOtpBtn").addEventListener("click", async () => {
  $("reloginStatus").textContent = "Sending OTP…";
  const res = await api("/api/login/start", { method: "POST" });
  const data = await res.json();
  if (!res.ok) {
    $("reloginStatus").textContent = "Failed: " + (data.detail || data.error);
    return;
  }
  $("reloginStep1").style.display = "none";
  $("reloginStep2").style.display = "block";
  $("reloginStatus").textContent = "OTP sent — enter it below quickly, it expires fast.";
});

$("verifyOtpBtn").addEventListener("click", async () => {
  const otp = $("otpInput").value.trim();
  if (!otp) return;
  $("reloginStatus").textContent = "Verifying…";
  const res = await api("/api/login/verify", { method: "POST", body: JSON.stringify({ otp }) });
  const data = await res.json();
  if (res.ok && data.status === "ok") {
    $("reloginStatus").textContent = "Logged in!";
    $("reloginCard").style.display = "none";
    loadOrigins();
  } else {
    $("reloginStatus").textContent = "OTP invalid or expired — click Send OTP again and re-enter quickly.";
    $("reloginStep1").style.display = "block";
    $("reloginStep2").style.display = "none";
  }
});

// ---------- Generate ----------

$("generateBtn").addEventListener("click", async () => {
  const origin = $("origin").value;
  const dest = $("dest").value;
  const [year, month] = $("month").value.split("-").map(Number);
  const markup = Number($("markup").value || 0);
  const theme = $("theme").value;
  const showLogo = $("showLogo").checked;

  if (!origin || !dest) {
    setStatus("Pick an origin and destination first.", "err");
    return;
  }

  $("generateBtn").disabled = true;
  $("result").innerHTML = "";
  setStatus('<span class="spinner"></span> Starting…', "");

  try {
    const res = await api("/api/generate", {
      method: "POST",
      body: JSON.stringify({ origin, dest, year, month, markup, theme, showLogo }),
    });

    if (res.status === 401) {
      const data = await res.json().catch(() => ({}));
      if (data.error === "not_logged_in") {
        setStatus("AirIQ session expired.", "err");
        showRelogin();
      } else {
        setStatus("Wrong app password — reload and re-enter it.", "err");
      }
      $("generateBtn").disabled = false;
      return;
    }

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      setStatus("Failed: " + (data.detail || data.error || res.statusText), "err");
      $("generateBtn").disabled = false;
      return;
    }

    const { job_id } = await res.json();
    await pollJob(job_id, origin, dest, year, month);
  } catch (e) {
    setStatus("Error: " + e.message, "err");
    $("generateBtn").disabled = false;
  }
});

async function pollJob(jobId, origin, dest, year, month) {
  // AirIQ has no bulk fare endpoint, so this genuinely searches one day at
  // a time server-side (1-3+ min). Polling here means no HTTP/proxy
  // timeout on this end can kill it - the job keeps running server-side
  // regardless of how long the tab polls.
  while (true) {
    await new Promise((r) => setTimeout(r, 2500));

    const res = await api(`/api/generate/status/${jobId}`);
    if (!res.ok) {
      setStatus("Lost track of the job — try again.", "err");
      $("generateBtn").disabled = false;
      return;
    }
    const data = await res.json();

    if (data.status === "running") {
      const p = data.progress;
      const progressText = p ? `Day ${p.day}/${p.total} (${p.last_status})` : "Scraping AirIQ day-by-day…";
      setStatus(`<span class="spinner"></span> ${progressText} — this takes 1-3 minutes, please wait…`, "");
      continue;
    }

    if (data.status === "error") {
      if (data.error === "not_logged_in") {
        setStatus("AirIQ session expired.", "err");
        showRelogin();
      } else {
        setStatus("Failed: " + data.error, "err");
      }
      $("generateBtn").disabled = false;
      return;
    }

    // done
    const imgRes = await api(`/api/generate/result/${jobId}`);
    const blob = await imgRes.blob();
    const url = URL.createObjectURL(blob);
    $("result").innerHTML = `<img src="${url}"><br><a href="${url}" download="${origin}-${dest}-${year}-${month}-fares.png">Download PNG</a>`;
    setStatus("Done.", "ok");
    $("generateBtn").disabled = false;
    return;
  }
}

function setStatus(html, cls) {
  const el = $("status");
  el.innerHTML = html;
  el.className = cls || "";
}

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

// ---------- WhatsApp: one-click send ----------

$("sendWaBtn").addEventListener("click", async () => {
  $("sendWaBtn").disabled = true;
  const set = (html, cls) => {
    const el = $("waSendStatus");
    el.innerHTML = html;
    el.className = cls || "";
  };
  set('<span class="spinner"></span> Starting…', "");

  try {
    const res = await api("/api/whatsapp/send-monthly", { method: "POST" });
    const startData = await res.json().catch(() => ({}));

    if (res.status === 401) {
      if (startData.error === "not_logged_in") {
        set("AirIQ session expired.", "err");
        showRelogin();
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
      await new Promise((r) => setTimeout(r, 2500));
      const sres = await api(`/api/generate/status/${jobId}`);
      const data = await sres.json();

      if (data.status === "running") {
        const p = data.progress;
        const progressText = p ? `Day ${p.day}/${p.total} (${p.last_status})` : "Scraping AirIQ…";
        set(`<span class="spinner"></span> ${progressText} — this takes 1-3 minutes…`, "");
        continue;
      }
      if (data.status === "error") {
        if (data.error === "not_logged_in") {
          set("AirIQ session expired.", "err");
          showRelogin();
        } else {
          set("Failed: " + data.error, "err");
        }
        break;
      }
      set("✅ Sent to WhatsApp group.", "ok");
      break;
    }
  } catch (e) {
    set("Error: " + e.message, "err");
  } finally {
    $("sendWaBtn").disabled = false;
  }
});
