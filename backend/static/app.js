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
  setStatus('<span class="spinner"></span> Scraping AirIQ day-by-day for this route — this takes 1-3 minutes, please wait…', "");

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
      return;
    }

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      setStatus("Failed: " + (data.detail || data.error || res.statusText), "err");
      return;
    }

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    $("result").innerHTML = `<img src="${url}"><br><a href="${url}" download="${origin}-${dest}-${year}-${month}-fares.png">Download PNG</a>`;
    setStatus("Done.", "ok");
  } catch (e) {
    setStatus("Error: " + e.message, "err");
  } finally {
    $("generateBtn").disabled = false;
  }
});

function setStatus(html, cls) {
  const el = $("status");
  el.innerHTML = html;
  el.className = cls || "";
}
