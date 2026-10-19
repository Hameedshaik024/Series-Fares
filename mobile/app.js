"use strict";

// Posters + status.json come from this same GitHub Pages site (public).
// Starting a run and handing over an Alhind OTP go through the GitHub API
// with the token saved in Settings - see backend/daily_posters.py for the
// other side of the POSTERS_LIVE / ALHIND_OTP variables.
const REPO = "Hameedshaik024/Series-Fares";
const WORKFLOW = "daily-posters.yml";
const CAPTION = "\u{1F4DE} Contact Details : 9951661243";
const ACTIVE = new Set(["queued", "checking_login", "otp_needed", "verifying", "running", "building", "publishing"]);
const STALE_MS = 15 * 60 * 1000;
const CACHE_PREFIX = "fare-posters-";
const IS_IOS = /iphone|ipad|ipod/i.test(navigator.userAgent)
  || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

const STEPS = {
  queued: ["Starting a new run…", "GitHub is getting a computer ready - about 2 minutes."],
  checking_login: ["Logging in to Alhind…", "Using the saved login."],
  verifying: ["Checking the OTP with Alhind…", "Hang on a few seconds."],
  running: ["Collecting today's fares", ""],
  building: ["Drawing the posters…", "Nearly there."],
  publishing: ["Publishing the posters…", "They'll show up here in about a minute."],
};

const $ = (id) => document.getElementById(id);
const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } },
  remove(key) { try { localStorage.removeItem(key); } catch { /* private mode */ } },
};

const state = {
  status: null,        // status.json
  live: null,          // POSTERS_LIVE variable, parsed
  selected: null,      // ISO date of the day being shown
  queuedAt: 0,         // when this phone last started a run
  otpSentAt: 0,
  tokenProblem: "",
  timer: 0,
  statusKey: "",
  postersKey: "",
  checkedWorkflow: false,
};

// ---------- small helpers ----------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child != null && child !== false) node.append(child);
  }
  return node;
}

let toastTimer = 0;
function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 4000);
}

function localISO(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function dayLabel(iso) {
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (iso === localISO(today)) return "Today";
  if (iso === localISO(yesterday)) return "Yesterday";
  return new Date(`${iso}T00:00:00`).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
}

function timeOf(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function ms(iso) {
  const value = Date.parse(iso || "");
  return Number.isNaN(value) ? 0 : value;
}

// ---------- GitHub API ----------

function token() {
  return store.get("ghToken") || "";
}

function gh(path, options = {}) {
  const headers = {
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    Authorization: `Bearer ${token()}`,
  };
  if (options.body) headers["Content-Type"] = "application/json";
  return fetch(`https://api.github.com/repos/${REPO}${path}`, { ...options, headers, cache: "no-store" });
}

function apiProblem(res, what) {
  if (res.status === 401) return `${what}: GitHub didn't accept the token. Add a new one in Settings.`;
  if (res.status === 403 || res.status === 404) {
    return `${what}: the token is missing a permission (it needs Actions and Variables, read and write).`;
  }
  return `${what} (GitHub answered ${res.status}).`;
}

async function setVariable(name, value) {
  const body = JSON.stringify({ name, value });
  let res = await gh(`/actions/variables/${name}`, { method: "PATCH", body });
  if (res.status === 404) res = await gh("/actions/variables", { method: "POST", body });
  return res;
}

// GitHub switches off scheduled runs in a repo with no activity for 60
// days; opening the app switches them straight back on.
async function ensureWorkflowEnabled() {
  if (state.checkedWorkflow || !token()) return;
  try {
    const res = await gh(`/actions/workflows/${WORKFLOW}`);
    if (!res.ok) return;
    state.checkedWorkflow = true;
    const workflow = await res.json();
    if (String(workflow.state).startsWith("disabled")) {
      await gh(`/actions/workflows/${WORKFLOW}/enable`, { method: "PUT" });
    }
  } catch { /* offline - try again next time */ }
}

// ---------- loading ----------

async function loadStatus() {
  try {
    const res = await fetch(`status.json?t=${Date.now()}`, { cache: "no-store" });
    if (res.status === 404) { state.status = { days: [] }; return; }
    if (res.ok) state.status = await res.json();
  } catch { /* offline: keep what we have */ }
}

async function loadLive() {
  if (!token()) { state.live = null; return; }
  try {
    const res = await gh("/actions/variables/POSTERS_LIVE");
    if (res.status === 404) { state.live = null; state.tokenProblem = ""; return; }
    if (!res.ok) { state.tokenProblem = apiProblem(res, "Couldn't check today's run"); return; }
    state.live = JSON.parse((await res.json()).value);
    state.tokenProblem = "";
  } catch { /* offline: keep what we have */ }
}

// A run that died without saying so (cancelled, timed out) would
// otherwise look "in progress" forever.
async function settleStaleRun() {
  const live = state.live;
  if (!live || !ACTIVE.has(live.phase) || !live.run_id) return;
  if (Date.now() - ms(live.updated) < STALE_MS) return;
  if (live.phase === "otp_needed" && ms(live.deadline) > Date.now()) return;
  try {
    const res = await gh(`/actions/runs/${live.run_id}`);
    if (!res.ok) return;
    const run = await res.json();
    if (run.status === "completed") {
      state.live = run.conclusion === "success"
        ? { ...live, phase: "done" }
        : { ...live, phase: "failed", message: "The run stopped before it finished." };
    }
  } catch { /* offline */ }
}

function phase() {
  const live = state.live;
  if (state.queuedAt) {
    if (live && ms(live.updated) >= state.queuedAt) state.queuedAt = 0;
    else return { phase: "queued", waitedMs: Date.now() - state.queuedAt };
  }
  if (!live) return { phase: "idle" };
  const latest = state.status?.days?.[0];
  if (live.phase === "publishing" && latest && ms(latest.generated) >= ms(live.generated)) {
    return { ...live, phase: "done" };
  }
  return live;
}

async function refresh() {
  clearTimeout(state.timer);
  if (document.hidden) { schedule(); return; }
  await Promise.all([loadStatus(), loadLive()]);
  await settleStaleRun();
  render();
  schedule();
}

function schedule() {
  clearTimeout(state.timer);
  const current = phase().phase;
  const delay = current === "otp_needed" || current === "verifying" ? 3000 : ACTIVE.has(current) ? 8000 : 60000;
  state.timer = setTimeout(refresh, delay);
}

// ---------- rendering ----------

function render() {
  renderStatus();
  renderDays();
  renderPosters();
}

function renderStatus() {
  const p = phase();
  const latest = state.status?.days?.[0];
  // Re-rendering the OTP form while someone is typing would wipe it, so
  // only redraw when something visible actually changed.
  const key = JSON.stringify([p.phase, p.done, p.detail, p.message, p.deadline, latest?.generated,
    state.otpSentAt, state.tokenProblem, Math.floor((p.waitedMs || 0) / 300000)]);
  if (key === state.statusKey) return;
  state.statusKey = key;

  const card = $("statusCard");
  card.className = "card status";
  card.replaceChildren();

  if (p.phase === "otp_needed") {
    renderOtp(card, p);
  } else if (ACTIVE.has(p.phase)) {
    let [title, sub] = STEPS[p.phase] || ["Working…", ""];
    if (p.phase === "queued" && p.waitedMs > 5 * 60 * 1000) sub = "Still waiting for GitHub to start it - it can be slow at busy times.";
    const children = [el("div", { class: "lead" }, el("div", { class: "spinner", "aria-hidden": "true" }),
      el("div", { class: "lead-text" }, el("h2", { text: title }), sub ? el("p", { class: "muted small", text: sub }) : null))];
    if (p.phase === "running" && p.total) {
      const pct = Math.round((100 * (p.done || 0)) / p.total);
      const perSearch = p.done ? (ms(p.updated) - ms(p.started)) / p.done : 0;
      const minsLeft = perSearch ? Math.max(1, Math.round(((p.total - p.done) * perSearch) / 60000)) : null;
      children[0].lastChild.append(el("p", {
        class: "muted small",
        text: `${p.detail || ""} · ${p.done || 0} of ${p.total} searches${minsLeft ? ` · about ${minsLeft} min left` : ""}`,
      }));
      children.push(el("div", { class: "progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct) },
        el("i", { style: `width:${pct}%` })));
    }
    card.append(...children);
  } else if (p.phase === "failed" && (!latest || ms(p.updated) > ms(latest.generated))) {
    card.classList.add("problem");
    card.append(
      el("h2", { text: "The last run didn't finish" }),
      el("p", { text: p.message || "Something went wrong." }),
      el("div", { class: "actions" },
        el("button", { class: "btn primary", onclick: (e) => generateNow(e.currentTarget), text: "Try again" }),
        p.run_url ? el("a", { class: "btn", href: p.run_url, target: "_blank", rel: "noopener", text: "Details" }) : null),
    );
  } else {
    const isToday = latest && latest.date === localISO(new Date());
    const title = !latest ? "No posters yet" : isToday ? "Today's posters are ready" : `Latest posters: ${dayLabel(latest.date)}`;
    const sub = !latest
      ? "They're made every morning, or tap Generate now."
      : `Made at ${timeOf(latest.generated)} · new ones every morning by about 7:30 AM`;
    card.append(
      el("div", { class: "lead" }, latest ? el("div", { class: "dot", "aria-hidden": "true" }) : null,
        el("div", { class: "lead-text" }, el("h2", { text: title }), el("p", { class: "muted small", text: sub }))),
      el("div", { class: "actions" },
        el("button", { class: "btn", onclick: (e) => generateNow(e.currentTarget), text: "Generate now" })),
    );
  }
  if (state.tokenProblem) card.append(el("p", { class: "warning-line", text: state.tokenProblem }));
}

let countdownTimer = 0;
function renderOtp(card, p) {
  card.classList.add("attention");
  const input = el("input", {
    type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: "8",
    placeholder: "6-digit code", "aria-label": "OTP from Alhind",
  });
  const submit = el("button", { class: "btn primary", type: "submit", text: "Submit" });
  const countdown = el("p", { class: "muted small" });
  card.append(
    el("h2", { text: state.otpSentAt ? "OTP sent - waiting for Alhind…" : "Enter the Alhind OTP" }),
    el("p", { class: "small", text: "Alhind just texted a code to your phone. Type it here so today's posters can be made." }),
    el("form", { class: "otp-form", onsubmit: (e) => { e.preventDefault(); submitOtp(input.value, submit); } }, input, submit),
    countdown,
  );
  clearInterval(countdownTimer);
  const tick = () => {
    const left = Math.max(0, ms(p.deadline) - Date.now());
    const m = Math.floor(left / 60000);
    const s = String(Math.floor((left % 60000) / 1000)).padStart(2, "0");
    countdown.textContent = left ? `The run waits ${m}:${s} more for the code.` : "Time's up - tap Generate now to try again.";
    if (!left) clearInterval(countdownTimer);
  };
  tick();
  countdownTimer = setInterval(tick, 1000);
  if (!state.otpSentAt) setTimeout(() => input.focus(), 50);
}

function renderDays() {
  const days = state.status?.days || [];
  if (!days.some((d) => d.date === state.selected)) state.selected = days[0]?.date || null;
  const nav = $("days");
  nav.hidden = days.length < 2;
  nav.replaceChildren(...days.map((d) => el("button", {
    class: "chip", "aria-pressed": String(d.date === state.selected), text: dayLabel(d.date),
    onclick: () => { state.selected = d.date; renderDays(); renderPosters(); },
  })));
}

function selectedDay() {
  return (state.status?.days || []).find((d) => d.date === state.selected) || null;
}

function renderPosters() {
  const day = selectedDay();
  const key = JSON.stringify([day?.date, day?.generated]);
  if (key === state.postersKey) return;
  state.postersKey = key;

  const list = $("posters");
  list.replaceChildren();
  $("dayActions").hidden = !day;
  $("empty").hidden = !!day;
  if (!state.status) $("empty").textContent = "Loading…";
  else if (!day) $("empty").textContent = "No posters yet.";
  if (!day) return;

  for (const p of day.posters) {
    prefetch(p);
    list.append(el("article", { class: "card poster" },
      el("button", { class: "poster-img", "aria-label": `Open ${p.title} full screen`, onclick: () => openViewer(p) },
        el("img", { src: p.file, alt: `${p.title} poster, ${day.range}`, loading: "lazy", decoding: "async", width: "1200", height: "1613" })),
      el("div", { class: "poster-body" },
        el("h2", { text: p.title }),
        el("p", { class: "muted small", text: `${day.range} · made at ${timeOf(day.generated)}` }),
        p.notes?.length ? el("ul", { class: "notes" }, p.notes.map((n) => el("li", { text: `⚠ ${n}` }))) : null,
        el("div", { class: "row" },
          el("button", { class: "btn primary", onclick: () => share([p]), text: "Share" }),
          el("button", { class: "btn", onclick: () => save([p]), text: "Save" }))),
    ));
  }
  cleanPosterCache();
}

// ---------- share / save / view ----------

const files = new Map();
function prefetch(p) {
  if (!files.has(p.file)) {
    files.set(p.file, fetch(p.file)
      .then((res) => { if (!res.ok) throw new Error(`HTTP ${res.status}`); return res.blob(); })
      .then((blob) => new File([blob], p.file.split("/").pop(), { type: "image/png" }))
      .catch((e) => { files.delete(p.file); throw e; }));
  }
  return files.get(p.file);
}

async function share(posters) {
  let list;
  try {
    list = await Promise.all(posters.map(prefetch));
  } catch {
    toast("Couldn't load the poster - check your connection.");
    return;
  }
  if (navigator.canShare && navigator.canShare({ files: list })) {
    try {
      // WhatsApp on iPhone can keep the text and drop the images when both
      // come together, so images go alone there.
      await navigator.share(IS_IOS ? { files: list } : { files: list, text: CAPTION });
    } catch (e) {
      if (e.name !== "AbortError") toast("Sharing didn't work here - use Save instead.");
    }
  } else {
    await download(posters);
    toast("This browser can't share images - saved them instead.");
  }
}

// On iPhone a download only lands in the Files app; the share sheet's
// "Save Image" is what puts a poster in Photos.
async function save(posters) {
  if (IS_IOS && navigator.canShare) return share(posters);
  return download(posters);
}

async function download(posters) {
  try {
    for (const file of await Promise.all(posters.map(prefetch))) {
      const url = URL.createObjectURL(file);
      const link = el("a", { href: url, download: file.name });
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    }
  } catch {
    toast("Couldn't load the poster - check your connection.");
  }
}

let viewing = null;
function openViewer(p) {
  viewing = p;
  $("viewerImg").src = p.file;
  $("viewerImg").alt = `${p.title} poster`;
  $("viewer").hidden = false;
  $("viewer").scrollTop = 0;
  document.body.style.overflow = "hidden";
  history.pushState({ viewer: true }, "");
}

function closeViewer(fromHistory) {
  if ($("viewer").hidden) return;
  $("viewer").hidden = true;
  document.body.style.overflow = "";
  viewing = null;
  if (!fromHistory && history.state?.viewer) history.back();
}

async function cleanPosterCache() {
  if (!("caches" in window) || !state.status) return;
  const wanted = new Set(state.status.days.flatMap((d) => d.posters.map((p) => new URL(p.file, location.href).href)));
  try {
    for (const name of await caches.keys()) {
      if (!name.startsWith(CACHE_PREFIX)) continue;
      const cache = await caches.open(name);
      for (const req of await cache.keys()) {
        if (req.url.includes("/posters/") && !wanted.has(req.url)) await cache.delete(req);
      }
    }
  } catch { /* storage unavailable */ }
}

// ---------- actions ----------

async function generateNow(button) {
  if (!token()) {
    openSettings("Add your GitHub token here to start a run from the app.");
    return;
  }
  const current = phase().phase;
  if (ACTIVE.has(current)) {
    toast("A run is already going.");
    return;
  }
  button.disabled = true;
  try {
    await ensureWorkflowEnabled();
    const res = await gh(`/actions/workflows/${WORKFLOW}/dispatches`, {
      method: "POST", body: JSON.stringify({ ref: "main", inputs: { mode: "full" } }),
    });
    if (res.status === 204) {
      state.queuedAt = Date.now();
      state.otpSentAt = 0;
      toast("Started - new posters in about 45 minutes.");
    } else {
      toast(apiProblem(res, "Couldn't start a run"));
    }
  } catch {
    toast("No connection - try again.");
  } finally {
    button.disabled = false;
    render();
    schedule();
  }
}

async function submitOtp(value, button) {
  const code = value.replace(/\D/g, "");
  if (code.length < 4) {
    toast("Type the code from the SMS.");
    return;
  }
  button.disabled = true;
  try {
    const res = await setVariable("ALHIND_OTP", code);
    if (res.ok) {
      state.otpSentAt = Date.now();
      toast("Code sent - checking it with Alhind…");
    } else {
      toast(apiProblem(res, "Couldn't send the code"));
      button.disabled = false;
    }
  } catch {
    toast("No connection - try again.");
    button.disabled = false;
  }
  render();
  schedule();
}

// ---------- settings ----------

function openSettings(message) {
  $("tokenInput").value = token();
  const status = $("tokenStatus");
  status.className = "token-status";
  status.textContent = message || (token() ? "A token is saved on this phone." : "");
  $("settings").showModal();
}

async function saveToken() {
  const value = $("tokenInput").value.trim();
  const status = $("tokenStatus");
  if (!value) return;
  status.className = "token-status";
  status.textContent = "Checking…";
  store.set("ghToken", value);
  try {
    const [variables, workflow] = await Promise.all([
      gh("/actions/variables?per_page=1"),
      gh(`/actions/workflows/${WORKFLOW}`),
    ]);
    const bad = [variables, workflow].find((res) => !res.ok);
    if (bad) {
      status.className = "token-status err";
      status.textContent = apiProblem(bad, "Saved, but it won't work yet");
    } else {
      status.className = "token-status ok";
      status.textContent = "Connected - Generate now and OTPs will work from this phone.";
      state.checkedWorkflow = false;
      ensureWorkflowEnabled();
      refresh();
    }
  } catch {
    status.className = "token-status err";
    status.textContent = "Saved, but couldn't reach GitHub to check it.";
  }
}

// ---------- start-up ----------

$("settingsBtn").addEventListener("click", () => openSettings());
$("closeSettingsBtn").addEventListener("click", () => $("settings").close());
$("saveTokenBtn").addEventListener("click", saveToken);
$("removeTokenBtn").addEventListener("click", () => {
  store.remove("ghToken");
  $("tokenInput").value = "";
  $("tokenStatus").className = "token-status";
  $("tokenStatus").textContent = "Removed from this phone.";
  state.live = null;
  state.tokenProblem = "";
  render();
});
$("runsLink").href = `https://github.com/${REPO}/actions/workflows/${WORKFLOW}`;
$("refreshBtn").addEventListener("click", () => { toast("Checking for new posters…"); refresh(); });
$("shareAllBtn").addEventListener("click", () => { const day = selectedDay(); if (day) share(day.posters); });
$("saveAllBtn").addEventListener("click", () => { const day = selectedDay(); if (day) save(day.posters); });
$("viewerClose").addEventListener("click", () => closeViewer(false));
$("viewerShare").addEventListener("click", () => { if (viewing) share([viewing]); });
$("viewerSave").addEventListener("click", () => { if (viewing) save([viewing]); });
window.addEventListener("popstate", () => closeViewer(true));
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

let installPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installPrompt = e;
  $("installBtn").hidden = false;
});
$("installBtn").addEventListener("click", async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
  $("installBtn").hidden = true;
});
const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
if (IS_IOS && !standalone) $("iosHint").hidden = false;

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});

// One-time setup link (…/#token=github_pat_…): saves the token, then
// drops it from the address bar and history.
const tokenFromLink = new URLSearchParams(location.hash.slice(1)).get("token");
if (tokenFromLink) {
  store.set("ghToken", tokenFromLink.trim());
  history.replaceState(null, "", location.pathname + location.search);
}

render();
refresh();
ensureWorkflowEnabled();
