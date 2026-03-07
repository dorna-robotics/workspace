import { apiFetch, stateVariant, isRunning, isLaunched, fmtUptime, fmtTimestamp, esc, wsViewerUrl } from "./api.js";

const POLL_MS = 1500;
const params  = new URLSearchParams(window.location.search);
const wsName  = (params.get("name") || "").trim();

if (!wsName) window.location.replace("index.html");

let wsInfo      = null;
let lastLogs    = "";
let iframeReady = false;

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

document.title = `${wsName} — Dorna Lab`;
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

async function refreshLogs() {
  try {
    const j    = await apiFetch(`/workspace/${encodeURIComponent(wsName)}/logs?tail=400`);
    const text = typeof j === "string" ? j : (j?.text || "");
    if (text === lastLogs) return;
    lastLogs = text;
    const atBottom = logPre.scrollHeight - logPre.scrollTop - logPre.clientHeight <= 10;
    logPre.innerHTML = colorizeLogs(text);
    if (atBottom) logPre.scrollTop = logPre.scrollHeight;
  } catch { /* ignore */ }
}

// ---- UI updates ----
function updateStatusUI(st) {
  const state   = st?.state || "unknown";
  const variant = stateVariant(state);
  const running = isRunning(state);
  const launched = isLaunched(state);

  statePill.className = `pill ${variant}`;
  statePill.innerHTML = `<span class="dot ${variant}${running ? " pulse" : ""}"></span>${esc(state)}`;

  uptimeVal.textContent  = fmtUptime(st?.uptime_s)      || "—";
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

  renderControls(launched, running);
  updateIframe(launched);
}

function renderControls(launched, running) {
  controls.innerHTML = "";

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
      frame.src = "about:blank";
      placeholder.style.display = "";
    }
    return;
  }
  if (!iframeReady) {
    iframeReady = true;
    frame.addEventListener("load", () => {
      const theme = document.documentElement.getAttribute("data-theme") || "dark";
      frame.contentWindow.postMessage({ type: "theme", value: theme }, "*");
    }, { once: true });
    const theme = document.documentElement.getAttribute("data-theme") || "dark";
    frame.src = wsViewerUrl(wsInfo) + "/?theme=" + theme;
    placeholder.style.display = "none";
  }
}

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
    pathVal.textContent   = wsInfo.path_to_file;
    pathVal.title         = wsInfo.path_to_file;

    await Promise.all([refreshStatus(), refreshLogs()]);
    setInterval(() => Promise.all([refreshStatus(), refreshLogs()]), POLL_MS);
  } catch (err) {
    toast(String(err), "bad");
  }
}

$("btnRefreshLogs").addEventListener("click", refreshLogs);

init();
