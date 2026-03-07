import { apiFetch, stateVariant, isRunning, isLaunched, fmtUptime, fmtTimestamp, esc, wsViewerUrl } from "./api.js";

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

// ---- Adaptive poll ----
function scheduleWsPoll() {
  const active = ["RUNNING","ACTIVE","LAUNCHED_NOT_READY"].includes(_lastState.toUpperCase());
  clearTimeout(_pollTimer);
  _pollTimer = setTimeout(async () => {
    await Promise.all([refreshStatus(), refreshLogs()]);
    scheduleWsPoll();
  }, active ? 1500 : 4000);
}

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

$("btnRefreshLogs").addEventListener("click", refreshLogs);
$("btnFollowLogs")?.addEventListener("click", () => {
  _logFollowing = true;
  logPre.scrollTop = logPre.scrollHeight;
  _updateFollowBtn();
});

init();
