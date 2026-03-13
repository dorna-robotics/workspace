import { apiFetch, stateVariant, isRunning, isLaunched, fmtUptime, fmtTimestamp, esc, wsViewerUrl, connectStatusWS } from "./api.js";

const params  = new URLSearchParams(window.location.search);
const wsName  = (params.get("name") || "").trim();

if (!wsName) window.location.replace("index.html");

let wsInfo      = null;
let lastLogs    = "";
let iframeReady = false;
let iframeUrl   = "";

// Adaptive poll: fast when active, slow when idle
let _pollTimer  = null;
let _lastState  = "";

// Live uptime: interpolate locally between polls
let _uptimeBase = null;   // uptime_s from last server response
let _uptimeAt   = null;   // performance.now() when received

// Log follow mode
let _logFollowing = true;

// ---- DOM refs ----
const $  = id => document.getElementById(id);
const wsNameEl    = $("wsName");
const wsLabelEl   = $("wsLabel");
const statePill   = $("statePill");
const controls    = $("controls");
const uptimeVal   = $("uptimeVal");
const startedVal  = $("startedVal");
const urlVal      = $("urlVal");
const pathVal     = $("pathVal");
const lastErrRow  = $("lastErrRow");
const lastErrVal  = $("lastErrVal");
const logPre      = $("logPre");
const frame       = $("ws3dFrame");
const placeholder = $("viewerPlaceholder");
const toastArea   = $("toastArea");

document.title = `${wsName} — Dorna Workspace`;
wsNameEl.textContent = wsName;

// ---- Toast ----
function toast(msg, type = "ok") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  toastArea.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ---- Log colorizer ----
function colorizeLogs(text) {
  return text.split("\n").map(line => {
    const e = esc(line);
    if (/error|exception|traceback|critical/i.test(line)) return `<span class="log-err">${e}</span>`;
    if (/warning|warn/i.test(line))                       return `<span class="log-warn">${e}</span>`;
    if (/^---/.test(line.trim()))                         return `<span class="log-dim">${e}</span>`;
    if (/^\d{4}-\d{2}-\d{2}/.test(line.trim()))          return `<span class="log-ts">${e}</span>`;
    return e;
  }).join("\n");
}

// ---- API ----
async function sendCmd(cmd) {
  return apiFetch(`/workspace/${encodeURIComponent(wsName)}/cmd`, {
    method: "POST",
    body: JSON.stringify({ cmd }),
  });
}

async function refreshStatus() {
  try {
    const st = await apiFetch(`/workspace/${encodeURIComponent(wsName)}/status`);
    updateStatusUI(st);
    return st;
  } catch (e) {
    updateStatusUI({ state: "OFFLINE", last_error: String(e) });
  }
}

function _updateFollowBtn() {
  const btn = $("btnFollowLogs");
  if (btn) btn.style.display = _logFollowing ? "none" : "";
}

async function refreshLogs() {
  try {
    const j    = await apiFetch(`/workspace/${encodeURIComponent(wsName)}/logs?tail=400`);
    const text = typeof j === "string" ? j : (j?.text || "");
    if (text === lastLogs) return;
    // Skip update if user is selecting text inside the log
    const sel = window.getSelection();
    if (sel && sel.rangeCount && logPre.contains(sel.anchorNode) && !sel.isCollapsed) return;
    lastLogs = text;
    logPre.innerHTML = colorizeLogs(text);
    if (_logFollowing) logPre.scrollTop = logPre.scrollHeight;
  } catch { /* ignore */ }
}

logPre.addEventListener("scroll", () => {
  const atBottom = logPre.scrollHeight - logPre.scrollTop - logPre.clientHeight <= 24;
  if (_logFollowing && !atBottom) { _logFollowing = false; _updateFollowBtn(); }
});

// ---- UI updates ----
function updateStatusUI(st) {
  const state   = st?.state || "unknown";
  const variant = stateVariant(state);
  const running = isRunning(state);
  const launched = isLaunched(state);

  statePill.className = `pill ${variant}`;
  statePill.innerHTML = `<span class="dot ${variant}${running ? " pulse" : ""}"></span>${esc(state)}`;

  // Live uptime: store base so the 1s ticker can interpolate
  if (st?.uptime_s != null) {
    _uptimeBase = Number(st.uptime_s);
    _uptimeAt   = performance.now();
    uptimeVal.textContent = fmtUptime(_uptimeBase) || "—";
  } else {
    _uptimeBase = null;
    uptimeVal.textContent = "—";
  }
  _lastState = state;
  startedVal.textContent = fmtTimestamp(st?.started_at) || "—";

  if (st?.last_error) {
    lastErrRow.style.display = "";
    lastErrVal.textContent   = st.last_error;
    lastErrVal.title         = st.last_error;
  } else {
    lastErrRow.style.display = "none";
  }

  // Accent the header border with state colour
  document.querySelector(".ws-header")?.setAttribute("data-state", variant);

  renderControls(state, launched, running);
  updateIframe(launched);
  if (typeof updatePendantUI === "function") updatePendantUI();
}

function renderControls(state, launched, running) {
  controls.innerHTML = "";
  const s = (state || "").toUpperCase();

  const addBtn = (label, cmd, opts = {}) => {
    const b = document.createElement("button");
    b.className = `btn btn-sm${opts.primary ? " btn-primary" : ""}${opts.danger ? " btn-danger" : ""}`;
    b.textContent = label;
    if (opts.disabled) b.disabled = true;
    b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await sendCmd(cmd);
        toast(`${cmd} sent`, "ok");
        await refreshStatus();
      } catch (err) {
        toast(String(err), "bad");
        b.disabled = false;
      }
    });
    controls.appendChild(b);
  };

  if (!launched) {
    addBtn("Launch", "launch", { primary: true });
  } else if (s === "LAUNCHED_NOT_READY") {
    // Server process is starting — show a spinner label and only a Kill escape hatch
    const lbl = document.createElement("span");
    lbl.className = "ctrl-starting";
    lbl.textContent = "Starting…";
    controls.appendChild(lbl);
    addBtn("Kill", "kill", { danger: true });
  } else {
    addBtn("Start",    "start",    { primary: true, disabled: running });
    addBtn("Pause",    "pause",    { disabled: !running });
    addBtn("Relaunch", "relaunch");
    addBtn("Kill",     "kill",     { danger: true });
  }
}

function updateIframe(launched) {
  if (!wsInfo) return;
  if (!launched) {
    if (iframeReady) {
      iframeReady = false;
      iframeUrl   = "";
      frame.src   = "about:blank";
      placeholder.style.display = "";
    }
    return;
  }
  const theme     = document.documentElement.getAttribute("data-theme") || "dark";
  const targetUrl = wsViewerUrl(wsInfo);
  if (!iframeReady || iframeUrl !== targetUrl) {
    iframeReady = true;
    iframeUrl   = targetUrl;
    frame.addEventListener("load", () => {
      frame.contentWindow?.postMessage({ type: "theme", value: theme }, "*");
    }, { once: true });
    frame.src = targetUrl + "/?theme=" + theme;
    placeholder.style.display = "none";
  }
}

// Theme changes: postMessage the iframe instead of reloading
new MutationObserver(() => {
  if (!iframeReady) return;
  const theme = document.documentElement.getAttribute("data-theme") || "dark";
  frame.contentWindow?.postMessage({ type: "theme", value: theme }, "*");
}).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

// ---- Adaptive poll (fallback when WS not connected) ----
let _wsConnected = false;

function scheduleWsPoll() {
  if (_wsConnected) {
    // WS handles status — only poll logs
    clearTimeout(_pollTimer);
    const active = ["RUNNING","ACTIVE","LAUNCHED_NOT_READY"].includes(_lastState.toUpperCase());
    _pollTimer = setTimeout(async () => {
      await refreshLogs();
      scheduleWsPoll();
    }, active ? 1500 : 4000);
    return;
  }
  const active = ["RUNNING","ACTIVE","LAUNCHED_NOT_READY"].includes(_lastState.toUpperCase());
  clearTimeout(_pollTimer);
  _pollTimer = setTimeout(async () => {
    await Promise.all([refreshStatus(), refreshLogs()]);
    scheduleWsPoll();
  }, active ? 1500 : 4000);
}

// ---- WebSocket live status ----
try {
  connectStatusWS((statuses) => {
    _wsConnected = true;
    const st = statuses[wsName];
    if (st) updateStatusUI(st);
  });
} catch (_) { /* WS unavailable — polling handles it */ }

// ---- Live uptime ticker (1 s interval, no server call) ----
setInterval(() => {
  if (_uptimeBase != null && _uptimeAt != null) {
    const elapsed = (performance.now() - _uptimeAt) / 1000;
    uptimeVal.textContent = fmtUptime(_uptimeBase + elapsed) || "—";
  }
}, 1000);

// ---- Init ----
async function init() {
  try {
    const j   = await apiFetch("/workspaces");
    const arr = Array.isArray(j?.workspaces) ? j.workspaces : [];
    wsInfo    = arr.find(w => w.name === wsName);

    if (!wsInfo) {
      toast(`Workspace "${wsName}" not found`, "bad");
      return;
    }

    wsLabelEl.textContent = wsInfo.label || "";
    const fullUrl = wsViewerUrl(wsInfo);
    urlVal.textContent = fullUrl;
    urlVal.title       = fullUrl;
    pathVal.textContent = wsInfo.path_to_file;
    pathVal.title       = wsInfo.path_to_file;

    await Promise.all([refreshStatus(), refreshLogs()]);
    scheduleWsPoll();
  } catch (err) {
    toast(String(err), "bad");
  }
}

$("btnRefreshLogs").addEventListener("click", (e) => { e.stopPropagation(); refreshLogs(); });
$("btnFollowLogs")?.addEventListener("click", (e) => {
  e.stopPropagation();
  _logFollowing = true;
  logPre.scrollTop = logPre.scrollHeight;
  _updateFollowBtn();
});

// Collapse / expand — clicking anywhere on the header row toggles
let _logsExpanded = false;
$("btnToggleLogs").addEventListener("click", () => {
  _logsExpanded = !_logsExpanded;
  logPre.style.display = _logsExpanded ? "" : "none";
  const chevron = document.getElementById("logChevron");
  if (chevron) chevron.classList.toggle("open", _logsExpanded);
  if (_logsExpanded) refreshLogs();
});

// Clear logs — stop propagation so it doesn't also toggle collapse
$("btnClearLogs").addEventListener("click", async (e) => {
  e.stopPropagation();
  try {
    await apiFetch(`/workspace/${encodeURIComponent(wsName)}/logs`, { method: "DELETE" });
    lastLogs = "";
    logPre.innerHTML = "";
  } catch (e) {
    toast("Failed to clear logs", "bad");
  }
});

// ---- Pendant mode ----
let _pendantMode = false;
const pendantOverlay = $("pendantOverlay");

// Audio feedback — Web Audio API (no files needed)
const _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
function pendantBeep(freq = 880, duration = 0.08, type = "sine", vol = 0.12) {
  try {
    const osc = _audioCtx.createOscillator();
    const gain = _audioCtx.createGain();
    osc.type = type;
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(vol, _audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, _audioCtx.currentTime + duration);
    osc.connect(gain);
    gain.connect(_audioCtx.destination);
    osc.start();
    osc.stop(_audioCtx.currentTime + duration);
  } catch(_) {}
}
function pendantClickSound()   { pendantBeep(660, 0.04, "sine", 0.10); }
function pendantSuccessSound() { pendantBeep(1000, 0.1, "sine", 0.08); }
function pendantErrorSound()   { pendantBeep(280, 0.12, "square", 0.10); setTimeout(() => pendantBeep(220, 0.15, "square", 0.08), 100); }

// Haptic vibration for touch devices
function pendantVibrate(ms = 30) {
  try { navigator.vibrate?.(ms); } catch(_) {}
}

function togglePendant(on) {
  _pendantMode = on !== undefined ? on : !_pendantMode;
  pendantOverlay.style.display = _pendantMode ? "" : "none";
  if (_pendantMode) {
    // Resume audio context (required after user gesture)
    if (_audioCtx.state === "suspended") _audioCtx.resume();
    updatePendantUI();
  }
}

function updatePendantUI() {
  if (!_pendantMode) return;
  const state = (_lastState || "").toUpperCase();
  const variant = stateVariant(state);
  const running = isRunning(state);
  const launched = isLaunched(state);

  const stateEl = $("pendantState");
  if (stateEl) {
    stateEl.setAttribute("data-variant", variant);
    const textEl = stateEl.querySelector(".pendant-state-text");
    if (textEl) textEl.textContent = state || "—";
  }

  // Enable/disable buttons based on state
  $("pendantStart").disabled   = !launched || running;
  $("pendantPause").disabled   = !running;
  $("pendantRelaunch").disabled = false;
  $("pendantKill").disabled    = !launched;
}

// Wire pendant buttons
document.querySelectorAll(".pendant-btn[data-cmd]").forEach(btn => {
  btn.addEventListener("click", async () => {
    const cmd = btn.dataset.cmd;
    btn.disabled = true;
    btn.classList.add("pendant-pressed");
    pendantClickSound();
    pendantVibrate(40);
    setTimeout(() => btn.classList.remove("pendant-pressed"), 400);
    try {
      await sendCmd(cmd);
      pendantSuccessSound();
      pendantVibrate(20);
      toast(`${cmd} sent`, "ok");
      await refreshStatus();
      updatePendantUI();
    } catch (err) {
      pendantErrorSound();
      pendantVibrate([50, 30, 50]); // double buzz for error
      toast(String(err), "bad");
    }
    updatePendantUI();
  });
});

$("btnPendant").addEventListener("click", () => togglePendant(true));
$("pendantExit").addEventListener("click", () => togglePendant(false));

init();
