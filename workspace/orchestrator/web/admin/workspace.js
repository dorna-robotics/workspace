import { apiFetch, stateVariant, stateLabel, isRunning, isLaunched, fmtUptime, fmtTimestamp, esc, wsViewerUrl, connectStatusWS } from "./api.js";
import { renderKwargsForm, readKwargsForm, validateKwargsForm } from "./kwargs.js";

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
let _wasRunning = false;  // track if we were just running (for auto-kill transition)

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
  el.addEventListener("click", () => el.remove());
  toastArea.appendChild(el);
  setTimeout(() => el.remove(), type === "bad" ? 7000 : 5000);
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

// ---- Kwargs (parameters) ----
let _wsKwargsValues = {};  // last-saved kwargs for this workspace

// ---- Parameters Modal ----
const paramsModal = $("paramsModalOverlay");
const paramsTitle = $("paramsModalTitle");
const paramsForm  = $("paramsForm");
const paramsFoot  = $("paramsModalFoot");

$("btnParamsClose").addEventListener("click", () => paramsModal.classList.remove("show"));
paramsModal.addEventListener("click", (e) => { if (e.target === paramsModal) paramsModal.classList.remove("show"); });

async function openParamsModal(frozen) {
  let schema = {}, values = {}, fetchError = false;
  try {
    const j = await apiFetch(`/workspace/${encodeURIComponent(wsName)}/launch_config`);
    schema = j.kwargs_schema || {};
    values = j.kwargs_values || {};
    _wsKwargsValues = values;
  } catch {
    fetchError = true;
  }

  paramsTitle.textContent = `Parameters — ${wsName}`;

  if (fetchError) {
    paramsForm.innerHTML = `<div class="kwargs-empty">Could not load parameters</div>`;
    paramsFoot.innerHTML = `<button class="btn" id="btnParamsDone">Cancel</button>`;
    $("btnParamsDone").addEventListener("click", () => paramsModal.classList.remove("show"));
  } else {
    renderKwargsForm(paramsForm, schema, values, frozen, wsName);

    if (frozen) {
      paramsFoot.innerHTML = `<button class="btn" id="btnParamsDone">Cancel</button>`;
      $("btnParamsDone").addEventListener("click", () => paramsModal.classList.remove("show"));
    } else if (Object.keys(schema).length) {
      paramsFoot.innerHTML = `
        <button class="btn" id="btnParamsCancel">Cancel</button>
        <div class="spacer"></div>
        <button class="btn" id="btnParamsReset">Reset All</button>
        <button class="btn btn-primary" id="btnParamsSet">Set</button>`;
      $("btnParamsCancel").addEventListener("click", () => paramsModal.classList.remove("show"));
      $("btnParamsReset").addEventListener("click", () => {
        renderKwargsForm(paramsForm, schema, {}, false, wsName);
        toast("Reset to defaults", "ok");
      });
      $("btnParamsSet").addEventListener("click", async () => {
        const errs = validateKwargsForm(paramsForm, schema);
        if (errs.length) { toast(`Invalid: ${errs[0].message} (${errs[0].key})`, "bad"); return; }
        const vals = readKwargsForm(paramsForm);
        try {
          await apiFetch(`/workspace/${encodeURIComponent(wsName)}/kwargs`, {
            method: "POST", body: JSON.stringify({ kwargs_values: vals })
          });
          _wsKwargsValues = vals;
          toast("Parameters set", "ok");
          paramsModal.classList.remove("show");
        } catch (err) { toast(String(err), "bad"); }
      });
    } else {
      paramsFoot.innerHTML = `<button class="btn" id="btnParamsDone">Cancel</button>`;
      $("btnParamsDone").addEventListener("click", () => paramsModal.classList.remove("show"));
    }
  }

  paramsModal.classList.add("show");
}

// ---- API ----
async function sendCmd(cmd, kwargs) {
  const payload = { cmd };
  if (kwargs) payload.kwargs = kwargs;
  return apiFetch(`/workspace/${encodeURIComponent(wsName)}/cmd`, {
    method: "POST",
    body: JSON.stringify(payload),
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
  statePill.innerHTML = `<span class="dot ${variant}${running ? " pulse" : ""}"></span>${esc(stateLabel(state))}`;

  // Live uptime: store base so the 1s ticker can interpolate
  if (st?.uptime_s != null) {
    _uptimeBase = Number(st.uptime_s);
    // Only tick live when launched; freeze the display when not launched
    _uptimeAt = launched ? performance.now() : null;
    uptimeVal.textContent = fmtUptime(_uptimeBase) || "—";
  } else {
    _uptimeBase = null;
    _uptimeAt = null;
    uptimeVal.textContent = "—";
  }
  // Track running→idle for auto-kill transition UI
  const prevUpper = _lastState.toUpperCase();
  const curUpper  = state.toUpperCase();
  if (prevUpper === "RUNNING" && curUpper === "IDLE") _wasRunning = true;
  if (curUpper !== "IDLE") _wasRunning = false;

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

  renderStep(st?.step, running);
  updateProgress(st?.step?.progress, launched);
  renderControls(state, launched, running);
  updateIframe(state, launched);
  if (typeof updatePendantUI === "function") updatePendantUI();

  // Reload run params on state change (e.g. NOT_LAUNCHED → IDLE after launch)
  if (prevUpper !== curUpper) loadRunParams();
}

let _prevStepCount = 0;
let _prevStepRunning = false;
let _stepsExpanded = true;

// ---- Direct step WebSocket to runtime ----
let _stepWs = null;
let _stepWsClosed = false;
let _stepWsRetryMs = 1000;
let _stepWsUrl = "";

function connectStepWS(runtimeUrl) {
  const wsUrl = runtimeUrl.replace(/^http/, "ws") + "/ws/steps";
  if (_stepWs && _stepWsUrl === wsUrl) return;  // already connected
  disconnectStepWS();
  _stepWsUrl = wsUrl;
  _stepWsClosed = false;
  _stepWsRetryMs = 1000;
  _tryStepWS();
}

function _tryStepWS() {
  if (_stepWsClosed || !_stepWsUrl) return;
  const ws = new WebSocket(_stepWsUrl);
  _stepWs = ws;
  ws.onopen = () => { _stepWsRetryMs = 1000; };
  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (Array.isArray(msg.steps) && msg.steps.length) {
        const running = isRunning(_lastState);
        renderStep({ steps: msg.steps }, running);
      }
    } catch {}
  };
  ws.onclose = () => {
    if (_stepWsClosed) return;
    setTimeout(_tryStepWS, _stepWsRetryMs);
    _stepWsRetryMs = Math.min(_stepWsRetryMs * 1.5, 8000);
  };
  ws.onerror = () => ws.close();
}

function disconnectStepWS() {
  _stepWsClosed = true;
  if (_stepWs) { try { _stepWs.close(); } catch {} _stepWs = null; }
  _stepWsUrl = "";
}

function renderStep(step, running) {
  const section = $("stepSection");
  const el = $("stepTimeline");
  const badge = $("stepCountBadge");
  if (!section || !el) return;

  const steps = step?.steps;
  if (!steps || !steps.length) {
    _lastStepLabel = "";
    _lastStepLevel = "info";
    if (_prevStepCount > 0) {
      el.innerHTML = `<div class="step-empty">No steps yet</div>`;
      if (badge) badge.textContent = "";
      _prevStepCount = 0;
    }
    return;
  }

  if (badge) badge.textContent = `${steps.length}`;

  // Only rebuild if step count or running state changed
  if (steps.length === _prevStepCount && running === _prevStepRunning) return;
  _prevStepCount = steps.length;
  _prevStepRunning = running;

  // Auto-expand when first step arrives
  if (steps.length === 1 && !_stepsExpanded) {
    _stepsExpanded = true;
    el.style.display = "";
    const chev = $("stepChevron");
    if (chev) chev.classList.add("open");
  }

  el.innerHTML = steps.map((s, i) => {
    // Support both new {label, level} objects and legacy plain strings
    const label = typeof s === "string" ? s : (s.label || "");
    const level = (typeof s === "object" && s.level) ? s.level : "info";
    const isLast = i === steps.length - 1;
    const cls = (isLast && running) ? "active" : "done";
    return `<div class="step-card ${cls}" data-level="${esc(level)}"><span class="step-dot-wrap"><span class="step-dot"></span></span><span class="step-text">${esc(label)}</span></div>`;
  }).join("");

  // Auto-scroll to latest
  el.scrollTop = el.scrollHeight;

  // Track last step for pendant display
  const lastStep = steps[steps.length - 1];
  const lastLevel = (typeof lastStep === "object" && lastStep.level) ? lastStep.level : "info";
  const lastLabel = typeof lastStep === "string" ? lastStep : (lastStep.label || "");
  _lastStepLabel = lastLabel;
  _lastStepLevel = lastLevel;
  if ((lastLevel === "error" || lastLevel === "warning") && lastLabel !== _lastBannerMsg) {
    _lastBannerMsg = lastLabel;
    _showBanner(lastLabel, lastLevel);
  } else if (lastLevel === "info") {
    _hideBanner();
  }
}

let _lastBannerMsg = "";
let _lastStepLabel = "";
let _lastStepLevel = "info";

function _showBanner(msg, level) {
  // Main banner
  const banner = $("alarmBanner");
  const text = $("alarmText");
  if (banner && text) {
    text.textContent = msg;
    banner.style.display = "";
    banner.setAttribute("data-level", level);
    // Push page and pendant overlay down so banner doesn't cover them
    const h = banner.offsetHeight;
    document.body.style.paddingTop = h + "px";
    const overlay = $("pendantOverlay");
    if (overlay) overlay.style.top = h + "px";
    const exitBtn = $("pendantExit");
    if (exitBtn) exitBtn.style.top = (h + 16) + "px";
  }
  // Pendant banner
  const pAlarm = $("pendantAlarm");
  const pText = $("pendantAlarmText");
  if (pAlarm && pText) {
    pText.textContent = msg;
    pAlarm.style.display = "";
    pAlarm.setAttribute("data-level", level);
  }
  // Audio + notification for errors only
  if (level === "error") {
    _alarmBeep();
    _alarmNotify(msg);
  }
}

function _hideBanner() {
  const banner = $("alarmBanner");
  if (banner) banner.style.display = "none";
  document.body.style.paddingTop = "";
  const overlay = $("pendantOverlay");
  if (overlay) overlay.style.top = "";
  const exitBtn = $("pendantExit");
  if (exitBtn) exitBtn.style.top = "";
  const pAlarm = $("pendantAlarm");
  if (pAlarm) pAlarm.style.display = "none";
}

function _alarmBeep() {
  try {
    const ctx = _audioCtx;
    const now = ctx.currentTime;
    // Two-tone alarm: high-low-high
    [[880, 0.15], [0, 0.05], [660, 0.15], [0, 0.05], [880, 0.15]].reduce((t, [freq, dur]) => {
      if (freq > 0) {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "square";
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.15, t);
        gain.gain.exponentialRampToValueAtTime(0.001, t + dur);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(t);
        osc.stop(t + dur);
      }
      return t + dur;
    }, now);
  } catch {}
}

function _alarmNotify(msg) {
  if (!("Notification" in window)) return;
  if (Notification.permission === "granted") {
    new Notification("Robot Alarm", { body: msg, icon: "favicon.png" });
  } else if (Notification.permission !== "denied") {
    Notification.requestPermission().then(p => {
      if (p === "granted") new Notification("Robot Alarm", { body: msg, icon: "favicon.png" });
    });
  }
}

let _lastProgress = -1;
function updateProgress(progress, launched) {
  const section = $("progressSection");
  const fill = $("progressFill");
  const label = $("progressLabel");
  if (!section || !fill || !label) return;

  if (progress == null || progress < 0 || !launched) {
    if (_lastProgress >= 0) {
      section.style.display = "none";
      _lastProgress = -1;
    }
    return;
  }

  const p = Math.min(100, Math.max(0, progress));
  section.style.display = "";
  fill.style.width = p + "%";
  fill.classList.toggle("done", p >= 100);
  label.textContent = p + "%";
  _lastProgress = p;
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
      if (cmd === "kill" && !confirm("Stop the workspace? This will kill the process.")) return;
      b.disabled = true;
      try {
        const kwargs = (cmd === "start" && Object.keys(_wsKwargsValues).length) ? _wsKwargsValues : undefined;
        await sendCmd(cmd, kwargs);
        toast(`${cmd} sent`, "ok");
        await refreshStatus();
        if (cmd === "launch") loadRunParams();
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
    const lbl = document.createElement("span");
    lbl.className = "ctrl-starting";
    lbl.textContent = "Starting…";
    controls.appendChild(lbl);
    addBtn("Kill", "kill", { danger: true });
  } else if (s === "IDLE" && _wasRunning) {
    const lbl = document.createElement("span");
    lbl.className = "ctrl-starting";
    lbl.textContent = "Finishing…";
    controls.appendChild(lbl);
  } else {
    addBtn("Start",    "start",    { primary: true, disabled: running });
    addBtn("Pause",    "pause",    { disabled: !running });
    addBtn("Restart", "relaunch");
    addBtn("Kill",     "kill",     { danger: true });
  }

  // Gear button for parameters — always last, matches bordered btn style
  const gear = document.createElement("button");
  gear.className = "btn btn-sm btn-icon";
  gear.title = "Parameters";
  gear.innerHTML = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`;
  gear.addEventListener("click", () => openParamsModal(launched));
  controls.appendChild(gear);
}

function updateIframe(state, launched) {
  if (!wsInfo) return;
  const ready = launched && (state || "").toUpperCase() !== "LAUNCHED_NOT_READY";
  if (!ready) {
    if (iframeReady) {
      iframeReady = false;
      iframeUrl   = "";
      frame.src   = "about:blank";
      placeholder.style.display = "";
      disconnectStepWS();
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
    connectStepWS(targetUrl);
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
    _wsKwargsValues = {};
    const fullUrl = wsViewerUrl(wsInfo);
    urlVal.textContent = fullUrl;
    urlVal.title       = fullUrl;
    pathVal.textContent = wsInfo.path_to_file;
    pathVal.title       = wsInfo.path_to_file;

    await Promise.all([refreshStatus(), refreshLogs(), loadRunParams()]);
    scheduleWsPoll();
  } catch (err) {
    toast(String(err), "bad");
  }
}

// Collapse / expand steps
$("btnToggleSteps")?.addEventListener("click", () => {
  _stepsExpanded = !_stepsExpanded;
  const el = $("stepTimeline");
  if (el) el.style.display = _stepsExpanded ? "" : "none";
  const chevron = $("stepChevron");
  if (chevron) chevron.classList.toggle("open", _stepsExpanded);
});

// ---- Run Parameters section ----
let _paramsExpanded = false;
const paramsPre = $("paramsPre");

$("btnToggleParams")?.addEventListener("click", () => {
  _paramsExpanded = !_paramsExpanded;
  paramsPre.style.display = _paramsExpanded ? "" : "none";
  const chevron = $("paramsChevron");
  if (chevron) chevron.classList.toggle("open", _paramsExpanded);
});

$("btnCopyParams")?.addEventListener("click", (e) => {
  e.stopPropagation();
  const text = paramsPre.textContent || "";
  if (!text.trim() || text === "(no parameters)") { toast("No parameters to copy", "warn"); return; }
  _copyToClipboard(text, e.currentTarget);
});

function formatParamsYaml(values) {
  if (!values || !Object.keys(values).length) return "(no parameters)";
  return Object.entries(values).map(([k, v]) => {
    if (v === null || v === undefined) return `${k}: null`;
    if (typeof v === "object") return `${k}: ${JSON.stringify(v)}`;
    if (typeof v === "string" && v === "") return `${k}: ""`;
    return `${k}: ${v}`;
  }).join("\n");
}

async function loadRunParams() {
  // Only show params when workspace is launched
  if (!isLaunched(_lastState)) {
    paramsPre.textContent = "(not launched)";
    return;
  }
  try {
    const j = await apiFetch(`/workspace/${encodeURIComponent(wsName)}/launch_config`);
    const schema = j.kwargs_schema || {};
    const values = j.kwargs_values || {};
    const filtered = {};
    for (const k of Object.keys(schema)) {
      filtered[k] = values[k] !== undefined ? values[k] : (schema[k].default ?? null);
    }
    paramsPre.textContent = Object.keys(schema).length ? formatParamsYaml(filtered) : "(no parameters defined)";
  } catch {
    paramsPre.textContent = "(could not load)";
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

// Copy helper — matches scene builder pattern (checkmark on success, fallback for non-HTTPS)
function _copyToClipboard(text, btn) {
  const origHtml = btn.innerHTML;
  const showSuccess = () => {
    btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--green)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;
    setTimeout(() => { btn.innerHTML = origHtml; }, 1500);
  };
  const fallback = () => {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.cssText = "position:fixed;left:-9999px;top:-9999px";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      if (ok) showSuccess(); else toast("Copy failed", "bad");
    } catch(e) { toast("Copy failed", "bad"); }
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(showSuccess).catch(fallback);
  } else {
    fallback();
  }
}

// Copy logs
$("btnCopyLogs").addEventListener("click", (e) => {
  e.stopPropagation();
  const text = logPre.textContent || "";
  if (!text.trim()) { toast("Nothing to copy", "warn"); return; }
  _copyToClipboard(text, e.currentTarget);
});

// Copy steps
$("btnCopySteps").addEventListener("click", (e) => {
  e.stopPropagation();
  const timeline = $("stepTimeline");
  const cards = timeline.querySelectorAll(".step-card .step-text");
  if (!cards.length) { toast("No steps to copy", "warn"); return; }
  const lines = Array.from(cards).map(el => el.textContent.trim());
  _copyToClipboard(lines.join("\n"), e.currentTarget);
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

  // Tint the overlay background based on state
  pendantOverlay.setAttribute("data-variant", variant);

  const stateEl = $("pendantState");
  if (stateEl) {
    stateEl.setAttribute("data-variant", variant);
    const textEl = stateEl.querySelector(".pendant-state-text");
    if (textEl) textEl.textContent = stateLabel(state);
  }

  // Step message
  const stepEl = $("pendantStep");
  if (stepEl) {
    if (_lastStepLabel && launched) {
      stepEl.textContent = _lastStepLabel;
      stepEl.className = `pendant-step${_lastStepLevel === "error" ? " err" : _lastStepLevel === "warning" ? " warn" : ""}`;
      stepEl.style.display = "";
    } else {
      stepEl.style.display = "none";
    }
  }

  // Pendant progress bar
  const pendantProg = $("pendantProgress");
  const pendantFill = $("pendantProgressFill");
  const pendantLabel = $("pendantProgressLabel");
  if (pendantProg && pendantFill && pendantLabel) {
    if (_lastProgress >= 0 && launched) {
      pendantProg.style.display = "";
      pendantFill.style.width = _lastProgress + "%";
      pendantFill.classList.toggle("done", _lastProgress >= 100);
      pendantLabel.textContent = _lastProgress + "%";
    } else {
      pendantProg.style.display = "none";
    }
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
    if (cmd === "kill" && !confirm("Stop the workspace?")) return;
    btn.disabled = true;
    btn.classList.add("pendant-pressed");
    pendantClickSound();
    pendantVibrate(40);
    setTimeout(() => btn.classList.remove("pendant-pressed"), 400);
    try {
      const kwargs = (cmd === "start" && Object.keys(_wsKwargsValues).length) ? _wsKwargsValues : undefined;
      await sendCmd(cmd, kwargs);
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

// Pendant params button
$("pendantParams").addEventListener("click", () => {
  const launched = isLaunched(_lastState);
  openParamsModal(launched);
});

$("btnPendant").addEventListener("click", () => togglePendant(true));
$("pendantExit").addEventListener("click", () => togglePendant(false));



// Alarm dismiss
$("alarmDismiss").addEventListener("click", () => _hideBanner());

// Expose for console testing
window._showBanner = _showBanner;
window._hideBanner = _hideBanner;

init();
