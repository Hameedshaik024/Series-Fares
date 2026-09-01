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
    initApp(data.logged_in, data.alhind_logged_in);
  } catch (e) {
    $("gateStatus").textContent = "Could not reach the server: " + e.message;
  }
});

// ---------- App init ----------

async function initApp(loggedIn, alhindLoggedIn) {
  if (!loggedIn) {
    showRelogin();
  } else {
    loadOrigins();
  }
  if (!alhindLoggedIn) {
    showAlhindRelogin();
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

// ---------- Generate ----------

$("generateBtn").addEventListener("click", async () => {
  const origin = $("origin").value;
  const dest = $("dest").value;
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
      body: JSON.stringify({ origin, dest, markup, theme, showLogo }),
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
    await pollJob(job_id, origin, dest);
  } catch (e) {
    setStatus("Error: " + e.message, "err");
    $("generateBtn").disabled = false;
  }
});

async function pollJob(jobId, origin, dest) {
  // AirIQ has no bulk fare endpoint, and now checks both the AIR IQ and
  // Market Place tabs per day, so this genuinely searches day-by-day,
  // twice each, server-side (2-5+ min for a 30-day range). Polling here
  // means no HTTP/proxy timeout on this end can kill it - the job keeps
  // running server-side regardless of how long the tab polls.
  while (true) {
    await new Promise((r) => setTimeout(r, 2500));

    const res = await api(`/api/generate/status/${jobId}`);
    if (!res.ok) {
      setStatus("Lost track of the job — try again.", "err");
      $("generateBtn").disabled = false;
      return;
    }
    let data;
    try {
      data = await res.json();
    } catch (e) {
      // Server returned something that isn't JSON (e.g. an HTML error page
      // from a container restart) - the job is gone either way, no point
      // continuing to poll.
      setStatus("Lost connection to the server mid-job (it may have restarted) — try again.", "err");
      $("generateBtn").disabled = false;
      return;
    }

    if (data.status === "running") {
      const p = data.progress;
      const progressText = p ? `Day ${p.day}/${p.total} (${p.last_status})` : "Scraping AirIQ + Market Place day-by-day…";
      setStatus(`<span class="spinner"></span> ${progressText} — this takes a few minutes, please wait…`, "");
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
    $("result").innerHTML = `<img src="${url}"><br><a href="${url}" download="${origin}-${dest}-fares.png">Download PNG</a>`;
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
      let data;
      try {
        data = await sres.json();
      } catch (e) {
        // Server returned something that isn't JSON (e.g. an HTML error
        // page from a container restart mid-job) - whatever routes had
        // already sent are done; the rest didn't happen and the job is
        // gone, so there's nothing left to poll for.
        set("Lost connection to the server mid-job (it may have restarted) — routes already sent went through; check WhatsApp for the rest, then try again.", "err");
        break;
      }

      if (data.status === "running") {
        const p = data.progress;
        const progressText = p
          ? `Route ${p.route_num}/${p.route_total} (${p.route}) — day ${p.day}/${p.total} (${p.last_status})`
          : "Starting…";
        set(`<span class="spinner"></span> ${progressText} — this takes a while, please wait…`, "");
        continue;
      }
      if (data.status === "error") {
        if (data.error === "not_logged_in") {
          set("AirIQ session expired partway through — routes already sent are done, the rest were skipped.", "err");
          showRelogin();
        } else {
          set("Failed: " + data.error, "err");
        }
        break;
      }

      // done - data.result is {"HYD-DXB": {sent:true} | {error:...}, ...} per route
      const lines = Object.entries(data.result || {}).map(([route, r]) => {
        if (r.sent) return `✅ ${route}: sent`;
        if (r.error === "group_not_set") return `⚪ ${route}: skipped (no group ID set)`;
        if (r.error === "too_few_fares") return `⚪ ${route}: skipped (only ${r.fare_days} date${r.fare_days === 1 ? "" : "s"} with fares, need 5+)`;
        return `❌ ${route}: ${r.error}`;
      });
      const anySent = Object.values(data.result || {}).some((r) => r.sent);
      set(lines.join("<br>"), anySent ? "ok" : "err");
      break;
    }
  } catch (e) {
    set("Error: " + e.message, "err");
  } finally {
    $("sendWaBtn").disabled = false;
  }
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
      // Named-flight scans take much longer than the AirIQ routes (no
      // "skip the second check" shortcut, and Alhind's session expires
      // faster, needing more relogins along the way) - realistically
      // 60-90+ minutes for a 6-route group, so this polls patiently.
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
        set(`<span class="spinner"></span> ${progressText} — this can take 60-90+ minutes, please wait…`, "");
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

      // done - data.result is {"HYD-MCT 6E 1273 Tactical": {sent:true, fare_days:N} | {error:...}, ...}
      const lines = Object.entries(data.result || {}).map(([route, r]) => {
        if (r.sent) return `✅ ${route}: sent (${r.fare_days} dates)`;
        if (r.error === "not_found_any_day") return `⚪ ${route}: skipped (not found on any day)`;
        return `❌ ${route}: ${r.error}`;
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
