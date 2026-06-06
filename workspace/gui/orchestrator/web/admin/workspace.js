// Note: this page currently opens 5 WebSocket connections (steps,
// status, devices, operator_actions, logs). Architectural cleanup to
// fold the four workspace-side channels into a single multiplexed WS
// is planned but deferred — see docs/internal/ws-multiplexing-plan.md
// for the design and trigger conditions.
import { apiFetch, stateVariant, stateLabel, isRunning, isLaunched, isStarted, isWaiting, fmtUptime, fmtTimestamp, esc, wsViewerUrl, connectStatusWS, confirmDialog, deviceFaultGate } from "./api.js";
import { renderKwargsForm, readKwargsForm, validateKwargsForm, loadKwargsFromFile } from "./kwargs.js";
import { connectScheduleWS, disconnectScheduleWS, openScheduleModal } from "./schedule.js";

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

// Tab title status dot — a colored emoji prepended to ``document.title``
// so an operator with multiple workspaces open can spot which one needs
// attention from the tab strip alone. The favicon stays as the plain
// Dorna logo (set in HTML); only the title's leading glyph changes.
//
// Colour mapping mirrors the state pill + body wash:
//   green  = RUNNING / ACTIVE      (working, watch)
//   blue   = IDLE / READY          (primed, your move)
//   amber  = PAUSED / PARKING      (held)
//   red    = ERROR / OFFLINE       (problem)
//   gray   = NOT_LAUNCHED / unknown
const _TITLE_DOTS = {
  ok:      "\u{1F7E2}",  // 🟢 RUNNING / ACTIVE
  waiting: "\u{1F535}",  // 🔵 IDLE / READY (operator's move)
  warn:    "\u{1F7E1}",  // 🟡 PAUSED / PARKING / LAUNCHED_NOT_READY
  bad:     "\u{1F534}",  // 🔴 ERROR / OFFLINE
  off:     "⚫",          // ⚫ NOT_LAUNCHED / unknown
};
let _titleLastKey = null;

function _setFaviconForState(state, variant, isInRun) {
  // "ok + waiting" splits off as its own blue key so the tab strip
  // matches the in-app body wash + state pill colour.
  const key = (variant === "ok" && isWaiting(state)) ? "waiting" : variant;
  if (key === _titleLastKey) return;
  _titleLastKey = key;
  const dot = _TITLE_DOTS[key] || _TITLE_DOTS.off;
  document.title = `${dot} ${wsName} — Dorna Workspace`;
}

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

// Initial title carries the gray "off" dot so there's no flash before
// the first status arrives. _setFaviconForState swaps it as state changes.
document.title = `${_TITLE_DOTS.off} ${wsName} — Dorna Workspace`;
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
$("btnParamsLoad").addEventListener("click", () => loadKwargsFromFile(paramsForm, toast));
paramsModal.addEventListener("click", (e) => { if (e.target === paramsModal) paramsModal.classList.remove("show"); });

// Device detail modal — close on X button or backdrop click.
const _deviceModal = $("deviceModalOverlay");
$("btnDeviceModalClose").addEventListener("click", () => closeDeviceModal());
_deviceModal.addEventListener("click", (e) => { if (e.target === _deviceModal) closeDeviceModal(); });

// One global ESC handler closes whichever modal is currently open.
// Cheaper and more consistent than per-modal keydown handlers (which
// previously only existed for the device modal — params + opActions
// modals had no ESC support at all). Closes the topmost visible modal
// only; if multiple stack up, ESC dismisses one at a time.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const _MODAL_IDS = [
    "deviceModalOverlay",
    "paramsModalOverlay",
    "opActionsModalOverlay",
    "scheduleModalOverlay",
  ];
  for (const id of _MODAL_IDS) {
    const m = document.getElementById(id);
    if (m && m.classList.contains("show")) {
      if (id === "deviceModalOverlay") {
        closeDeviceModal();
      } else {
        m.classList.remove("show");
      }
      return; // close only the topmost
    }
  }
});

// Cached launch_config to dedupe fetches between openParamsModal +
// loadRunParams which run back-to-back on first launch. 2 min TTL is
// fine: the schema rarely changes mid-session and the operator's
// last-saved values are still echoed by the server on every fetch.
let _launchConfigCache = null;
const LAUNCH_CONFIG_TTL_MS = 120_000;

async function _getLaunchConfig() {
  const now = Date.now();
  if (_launchConfigCache && (now - _launchConfigCache.at < LAUNCH_CONFIG_TTL_MS)) {
    return _launchConfigCache.data;
  }
  const j = await apiFetch(`/workspace/${encodeURIComponent(wsName)}/launch_config`);
  _launchConfigCache = { data: j, at: now };
  return j;
}

function _invalidateLaunchConfig() {
  _launchConfigCache = null;
}

async function openParamsModal(frozen) {
  let schema = {}, values = {}, fetchError = false;
  try {
    const j = await _getLaunchConfig();
    schema = j.kwargs_schema || {};
    values = j.kwargs_values || {};
    _wsKwargsValues = values;
  } catch {
    fetchError = true;
  }

  paramsTitle.textContent = `Parameters — ${wsName}`;
  $("btnParamsLoad").style.display = frozen ? "none" : "";

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

// Cached last HTTP /status response. Used to fill in fields (uptime,
// port, log path, etc.) when only WS-pushed updates arrive — the WS
// snapshot only carries state + last_error + counters, so we layer it
// on top of the most recent HTTP poll for a complete UI render.
let _lastHttpStatus = {};

async function refreshStatus() {
  try {
    const st = await apiFetch(`/workspace/${encodeURIComponent(wsName)}/status`);
    _lastHttpStatus = st || {};
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
  // HTTP fallback path — used only if the WS isn't connected (reconnect
  // window or unsupported environment). When the WS is live, log lines
  // arrive via append events and this function is a no-op.
  if (_logsWs && _logsWs.readyState === WebSocket.OPEN) return;
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

// ---- Logs WebSocket — live-tail replacing the HTTP polling above ----
//
// Three guards keep this safe under heavy log volume on a customer
// machine (Pi-class browser, weak laptop, etc.):
//
// 1. ``lastLogs`` is capped at LOGS_BUFFER_BYTES — older bytes are
//    dropped on append. Otherwise an 8-hour run with verbose logging
//    would grow it into the megabytes and tank rendering.
//
// 2. DOM updates are coalesced via requestAnimationFrame. A burst of
//    appends within one frame reflows the log panel ONCE, not per
//    message. Without this the 250 ms file-poll cadence × MB of
//    innerHTML can freeze the tab.
//
// 3. innerHTML rewrites only run on actual content change AND skip
//    when the operator has text selected — clobbering a selection
//    mid-copy is annoying and breaks Ctrl-C.
let _logsWs = null;
let _logsWsClosed = false;
let _logsWsRetryMs = 1000;
let _logsWsRetryCount = 0;
let _logsWsToastShown = false;
let _logsRenderQueued = false;

const LOGS_BUFFER_BYTES = 256 * 1024;   // ~256 KB rolling buffer
const LOGS_WS_TOAST_AFTER_RETRIES = 3;  // notify operator after this many failures

function connectLogsWS() {
  // Path is on the orchestrator (the workspace itself doesn't own the
  // log file — orchestrator captures the subprocess's stdout into one).
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${window.location.host}/orchestrator/ws/logs/${encodeURIComponent(wsName)}`;
  if (_logsWs) {
    try { _logsWs.close(); } catch {}
  }
  _logsWsClosed = false;
  _tryLogsWS(url);
}

function _appendLogs(chunk) {
  if (!chunk) return;
  lastLogs += chunk;
  if (lastLogs.length > LOGS_BUFFER_BYTES) {
    // Drop oldest content, keeping the freshest LOGS_BUFFER_BYTES. Trim
    // to the next newline so we don't render a half line at the top.
    const cut = lastLogs.length - LOGS_BUFFER_BYTES;
    const nl = lastLogs.indexOf("\n", cut);
    lastLogs = lastLogs.slice(nl >= 0 ? nl + 1 : cut);
  }
}

function _renderLogsCoalesced() {
  if (_logsRenderQueued) return;
  _logsRenderQueued = true;
  requestAnimationFrame(() => {
    _logsRenderQueued = false;
    // Skip if the operator is mid-selection inside the log panel —
    // overwriting innerHTML would drop their selection mid-copy.
    const sel = window.getSelection();
    if (sel && sel.rangeCount && logPre.contains(sel.anchorNode) && !sel.isCollapsed) return;
    logPre.innerHTML = colorizeLogs(lastLogs);
    if (_logFollowing) logPre.scrollTop = logPre.scrollHeight;
  });
}

function _tryLogsWS(url) {
  if (_logsWsClosed) return;
  const ws = new WebSocket(url);
  _logsWs = ws;
  ws.onopen = () => {
    _logsWsRetryMs = 1000;
    // Reset the retry counter on successful (re)connect so a later
    // hiccup gets the same N-retry grace window before the toast fires
    // again.
    if (_logsWsRetryCount > 0 && _logsWsToastShown) {
      toast("Logs streaming restored", "ok");
    }
    _logsWsRetryCount = 0;
    _logsWsToastShown = false;
  };
  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === "snapshot") {
        lastLogs = msg.text || "";
        // Apply same buffer cap to a snapshot (in case a long-running
        // workspace produced more than 256 KB before we connected).
        if (lastLogs.length > LOGS_BUFFER_BYTES) {
          const cut = lastLogs.length - LOGS_BUFFER_BYTES;
          const nl = lastLogs.indexOf("\n", cut);
          lastLogs = lastLogs.slice(nl >= 0 ? nl + 1 : cut);
        }
      } else if (msg.type === "append") {
        _appendLogs(msg.text);
      } else {
        return;
      }
      _renderLogsCoalesced();
    } catch {}
  };
  ws.onclose = () => {
    if (_logsWsClosed) return;
    _logsWsRetryCount += 1;
    // Tell the operator once when we've crossed the retry threshold —
    // they'd otherwise think logs were live when really we've fallen
    // back to HTTP polling. One-shot via the toastShown latch.
    if (_logsWsRetryCount === LOGS_WS_TOAST_AFTER_RETRIES && !_logsWsToastShown) {
      _logsWsToastShown = true;
      toast("Live log streaming unstable — falling back to polling", "warn");
    }
    setTimeout(() => _tryLogsWS(url), _logsWsRetryMs);
    _logsWsRetryMs = Math.min(_logsWsRetryMs * 1.5, 8000);
  };
  ws.onerror = () => ws.close();
}

function disconnectLogsWS() {
  _logsWsClosed = true;
  if (_logsWs) { try { _logsWs.close(); } catch {} _logsWs = null; }
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

  // Pulse signals "operator action required" — fires when we're
  // launched-and-idle (waiting for Start). While actually RUNNING
  // the dot is solid: no pulse, no breath, the system is working
  // and doesn't need your attention. See ``isWaiting`` in api.js.
  const waiting = isWaiting(state);
  statePill.className = `pill ${variant}`;
  statePill.innerHTML = `<span class="dot ${variant}${waiting ? " pulse" : ""}"></span>${esc(stateLabel(state))}`;

  // Live uptime: store base so the 1s ticker can interpolate. Tick
  // only when the run is in flight — RUNNING (active motion), PAUSED
  // (operator paused mid-run, wall clock still counts), or PARKING
  // (graceful wrap-up). On IDLE the run has finished and the server's
  // uptime_s is the frozen final value, so leave _uptimeAt null and
  // the ticker simply leaves the display alone.
  const isInRun = ["RUNNING", "ACTIVE", "PAUSED", "PARKING"]
    .includes((state || "").toUpperCase());

  // Tab favicon mirrors the state pill so an operator with multiple
  // workspaces open in tabs can spot which one needs attention from
  // the tab strip alone (green = running, amber = paused/parking,
  // red = error/offline, gray = not launched).
  _setFaviconForState(state, variant, isInRun);
  if (st?.uptime_s != null) {
    _uptimeBase = Number(st.uptime_s);
    _uptimeAt   = isInRun ? performance.now() : null;
    uptimeVal.textContent = fmtUptime(_uptimeBase) || "—";
  } else {
    _uptimeBase = null;
    _uptimeAt = null;
    uptimeVal.textContent = "—";
  }
  // Track previous state so we can detect transitions (e.g. reload
  // run params when going NOT_LAUNCHED → IDLE after a fresh Launch).
  const prevUpper = (_lastState || "").toUpperCase();
  const curUpper  = state.toUpperCase();
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

  // Steps panel is purely a live view — when the workspace dies, the
  // panel resets cleanly to "No steps yet". History lives in the
  // timestamped log file under <project_dir>/status/<name>.log; the
  // dashboard card's "Last run" indicator and the LOGS panel here are
  // the durable surfaces. This avoids a stale half-rendered timeline
  // after a kill that no longer reflects what's running.
  renderStep(st?.step, running);
  updateProgress(st?.step?.progress, launched);
  renderControls(state, launched, running);
  updateIframe(state, launched);
  if (typeof updatePendantUI === "function") updatePendantUI();
  if (typeof updateOperatorActionsGate === "function") updateOperatorActionsGate(state);

  // Reload run params on state change (e.g. NOT_LAUNCHED → IDLE after launch)
  if (prevUpper !== curUpper) {
    // Invalidate the cached launch_config — a fresh Launch may have
    // pulled in an edited launch.yaml, so the schema could differ
    // from the prior session.
    _invalidateLaunchConfig();
    loadRunParams();
  }
}

let _prevStepCount = 0;
let _prevStepRunning = false;
// Steps start collapsed — auto-expands when the first step arrives.
// Matches the empty-by-default behaviour for Devices below.
let _stepsExpanded = false;

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
      const running = isRunning(_lastState);
      if (Array.isArray(msg.steps) && msg.steps.length) {
        renderStep({ steps: msg.steps }, running);
      }
      // Apply the progress snapshot whenever the runtime emits one.
      // Without this the bar only updated via the slower HTTP poll —
      // the workflow's final 100% can fire and be replaced by
      // mark_idle in less than one polling interval, leaving the bar
      // stuck at the previous value (e.g. 80%).
      if (typeof msg.progress === "number" && msg.progress >= 0) {
        updateProgress(msg.progress, isLaunched(_lastState));
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

// ── Status WebSocket — push runtime state changes in real time ──────
let _statusWs = null;
let _statusWsClosed = false;
let _statusWsRetryMs = 1000;
let _statusWsUrl = "";

// Per-workspace runtime status WS — direct connection to the workspace
// process's /ws/status endpoint. Distinct from api.js's
// ``connectStatusWS`` which subscribes to the orchestrator-level
// /orchestrator/ws/status broadcasting all workspaces' statuses on a
// 2-second poll cycle. This direct connection gets sub-100ms updates.
function connectRuntimeStatusWS(runtimeUrl) {
  const wsUrl = runtimeUrl.replace(/^http/, "ws") + "/ws/status";
  if (_statusWs && _statusWsUrl === wsUrl) return;
  disconnectRuntimeStatusWS();
  _statusWsUrl = wsUrl;
  _statusWsClosed = false;
  _statusWsRetryMs = 1000;
  _tryStatusWS();
}

function _tryStatusWS() {
  if (_statusWsClosed || !_statusWsUrl) return;
  const ws = new WebSocket(_statusWsUrl);
  _statusWs = ws;
  ws.onopen = () => { _statusWsRetryMs = 1000; };
  ws.onmessage = (e) => {
    try {
      const snap = JSON.parse(e.data);
      // Merge: cached HTTP fields first (port, log, _orch, etc.),
      // then snap's fresh values on top. The runtime now ships
      // ``uptime_s`` / ``run_started_at`` / ``run_finished_at`` /
      // ``step`` / ``devices_summary`` in every WS push, so the
      // moment state changes those land instantly — no flicker from
      // a stale HTTP poll's uptime resurrecting itself between the
      // WS push and the next 1.5 s poll.
      updateStatusUI({
        ..._lastHttpStatus,
        ...snap,
      });
    } catch {}
  };
  ws.onclose = () => {
    if (_statusWsClosed) return;
    setTimeout(_tryStatusWS, _statusWsRetryMs);
    _statusWsRetryMs = Math.min(_statusWsRetryMs * 1.5, 8000);
  };
  ws.onerror = () => ws.close();
}

function disconnectRuntimeStatusWS() {
  _statusWsClosed = true;
  if (_statusWs) { try { _statusWs.close(); } catch {} _statusWs = null; }
  _statusWsUrl = "";
}

// ── Devices panel (project-scoped) ───────────────────────────────────
let _devicesWs = null;
let _devicesWsUrl = "";
let _devicesWsClosed = true;
let _devicesWsRetryMs = 1000;
let _devicesUrl = "";   // base http URL, used for recover POST
const _devices = new Map();   // id → snapshot
// Devices we just clicked Recover on. Holds id → {note, until} so the
// row keeps showing "Recovering…" until the device reports a non-recovering
// state or the safety deadline passes (handles dropped MQTT replies).
const _devicesPending = new Map();
const RECOVER_FALLBACK_MS = 35_000;

// Confirm prompt before any device-side Recover (inline row OR modal
// footer). Recover re-initializes a real device on the bus — a
// fat-finger on a tablet shouldn't trigger it silently. Same dialog
// shape Park / Kill already use.
async function _confirmRecover(deviceId) {
  return await confirmDialog({
    title: "Recover device?",
    body: `Re-initialize ${deviceId}. The device will reconnect and the runtime may resume.`,
    confirmLabel: "Recover",
    confirmVariant: "primary",
  });
}

function connectDevicesWS(runtimeUrl) {
  // HTTP fetches and recover commands go through the admin proxy at
  // /orchestrator/api/workspace/<name>/devices to avoid cross-origin
  // requests against the workspace's own port. The WebSocket connects
  // straight to the workspace process — Tornado's WebSocketHandler
  // accepts cross-origin via check_origin=True, so no proxy needed.
  _devicesUrl = runtimeUrl;
  const wsUrl = runtimeUrl.replace(/^http/, "ws") + "/ws/devices";
  if (_devicesWs && _devicesWsUrl === wsUrl) return;
  disconnectDevicesWS();
  _devicesWsUrl = wsUrl;
  _devicesWsClosed = false;
  _devicesWsRetryMs = 1000;
  _devices.clear();
  _tryDevicesWS();
  // Initial seed via the admin proxy (no CORS).
  fetch(`/orchestrator/api/workspace/${encodeURIComponent(wsName)}/devices`)
    .then(r => r.ok ? r.json() : null)
    .then(payload => {
      if (!payload || !Array.isArray(payload.devices)) return;
      for (const d of payload.devices) _devices.set(d.id, d);
      renderDevicesPanel();
    })
    .catch(() => {});
}

function _tryDevicesWS() {
  if (_devicesWsClosed || !_devicesWsUrl) return;
  const ws = new WebSocket(_devicesWsUrl);
  _devicesWs = ws;
  ws.onopen = () => { _devicesWsRetryMs = 1000; };
  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      // Full snapshot from the server — fired on connect AND any time
      // ``workspace.add_component`` / ``remove_component`` runs (the
      // device list could have changed). Replace the in-memory map
      // wholesale and re-render.
      if (msg && msg.type === "snapshot" && Array.isArray(msg.devices)) {
        _devices.clear();
        for (const d of msg.devices) {
          if (d && d.id) _devices.set(d.id, d);
        }
        renderDevicesPanel();
        return;
      }
      if (msg && msg.type === "device_state" && msg.id) {
        const prev = _devices.get(msg.id);
        _devices.set(msg.id, msg);
        // Any state event for a pending device clears its "in-flight" mark
        // so the button stops showing "Recovering…" once the cycle ends.
        if (_devicesPending.has(msg.id) && msg.state !== "recovering") {
          _devicesPending.delete(msg.id);
        }
        // Operator paging on the rising edge of a critical-down. Same UX
        // as the step-driven alarm banner (audio + desktop notification),
        // applied uniformly to every device on the bus — robot, camera,
        // future printer/pipette/etc. We page only on the transition
        // (prev state was not down) to avoid spamming when the panel
        // first loads with already-down devices.
        if (
          msg.state === "down"
          && msg.critical !== false
          && (!prev || prev.state !== "down")
        ) {
          _alarmBeep();
          _alarmNotify(`${msg.id}: ${(msg.msg || "down").trim()}`);
        }
        renderDevicesPanel();
      }
    } catch {}
  };
  ws.onclose = () => {
    if (_devicesWsClosed) return;
    setTimeout(_tryDevicesWS, _devicesWsRetryMs);
    _devicesWsRetryMs = Math.min(_devicesWsRetryMs * 1.5, 8000);
  };
  ws.onerror = () => ws.close();
}

function disconnectDevicesWS() {
  _devicesWsClosed = true;
  if (_devicesWs) { try { _devicesWs.close(); } catch {} _devicesWs = null; }
  _devicesWsUrl = "";
}

// Cache of last-rendered HTML for the devices list. WS device_state
// events fire often; if the visible output hasn't changed (e.g. a
// state event arrived but the fields we render are identical), skip
// the innerHTML write entirely. Saves a layout + paint per silent
// event when nothing visible actually changed.
let _devicesListLastHtml = null;

function renderDevicesPanel() {
  const el = $("devicesList");
  const badge = $("devicesCountBadge");
  if (!el) return;
  const list = Array.from(_devices.values()).sort((a, b) => a.id.localeCompare(b.id));
  if (badge) badge.textContent = list.length ? `${list.length}` : "";
  if (!list.length) {
    const emptyHtml = `<div class="step-empty">No devices declared</div>`;
    if (_devicesListLastHtml !== emptyHtml) {
      el.innerHTML = emptyHtml;
      _devicesListLastHtml = emptyHtml;
    }
    return;
  }
  // Auto-expand the first time devices appear — empty-default UX
  // (collapsed) shouldn't hide real content from the operator.
  if (!_devicesExpanded) {
    _devicesExpanded = true;
    el.style.display = "";
    const chev = $("devicesChevron");
    if (chev) chev.classList.add("open");
  }
  const now = Date.now();
  const newHtml = list.map(d => {
    const state = d.state || "down";
    const online = d.online !== false;  // default true for back-compat
    const pending = _devicesPending.get(d.id);
    const isPending = !!(pending && pending.until > now);
    if (pending && pending.until <= now) _devicesPending.delete(d.id);

    // Visual state: pending click overrides server state until the bus
    // confirms or the safety deadline elapses. Offline devices render
    // distinctly and hide the recover button.
    const visualState = isPending ? "recovering" : state;
    const dotClass = visualState === "ok"
      ? "ok pulse"
      : visualState === "recovering"
        ? "warn pulse"
        : "bad";
    const rowClass = visualState === "ok"
      ? ""
      : (visualState === "recovering" ? "is-recovering" : "is-down");

    const msg = (pending?.note || d.msg || "").trim();
    // Two independent sim signals; pill appears if either is true:
    //   * d.sim — publisher self-flagged (e.g. workspace robot in sim)
    //   * d.claim === "sim" — this project uses the device in sim mode,
    //     even if a separate daemon publishes truth on the bus.
    // The pill conveys "this project is faking this device." The dot
    // colour still reflects the publisher's truth, so a sim-claimed
    // camera that's physically down shows red dot + SIM pill — operator
    // sees both layers, neither hides the other.
    const isPublisherSim = d.sim === true;
    const isProjectSim = d.claim === "sim";
    const isSim = isPublisherSim || isProjectSim;
    const simTitle = isPublisherSim
      ? "Device publisher is in simulation mode"
      : "This project uses this device in simulation mode";
    const simPill = isSim
      ? `<span class="device-pill device-pill--sim" title="${escAttr(simTitle)}">SIM</span>`
      : "";
    let control = "";
    if (visualState === "recovering") {
      control = `<button class="btn btn-sm btn-primary" disabled>Recovering…</button>`;
    } else if (state !== "ok" && !isPublisherSim) {
      // Recover applies to any real bus entry that's down — including
      // ones this project happens to claim sim. The operator may still
      // want to fix the underlying device for OTHER projects, and
      // hiding the button would lock them out of that. Only suppress
      // when the publisher itself flags sim (no real device exists).
      if (online) {
        control = `<button class="btn btn-sm btn-primary" data-device-act="recover">Recover</button>`;
      } else {
        control = `<span class="device-pill">offline</span>`;
      }
    }

    return `
      <div class="device-row ${rowClass}" data-device-id="${escAttr(d.id)}" title="Click for details">
        <span class="dot ${dotClass}"></span>
        <span class="device-id">${escHtml(d.id)}</span>
        ${msg ? `<span class="device-msg" title="${escAttr(msg)}">${escHtml(msg)}</span>` : ""}
        ${control}
        ${simPill}
      </div>`;
  }).join("");
  // Skip the DOM write when nothing visible changed. WS events stream
  // often during a run — this catches silent state events (e.g. only
  // ``ts`` or ``last_update`` changed) and avoids the layout + paint.
  if (newHtml !== _devicesListLastHtml) {
    el.innerHTML = newHtml;
    _devicesListLastHtml = newHtml;
  }

  // Keep an open modal in sync as state events stream in.
  if (_openDeviceId && _devices.has(_openDeviceId)) {
    _renderDeviceModalBody(_devices.get(_openDeviceId));
  }
}

// Event delegation: one click listener on the parent handles every
// row + recover button, forever. The previous pattern re-attached a
// listener per row on every WS event — fine for 2 devices, leaky for
// many. Delegated handlers also survive innerHTML rewrites; nothing
// to re-wire.
function _wireDevicesPanelDelegation() {
  const el = $("devicesList");
  if (!el || el.dataset.delegated === "1") return;
  el.dataset.delegated = "1";
  el.addEventListener("click", async (ev) => {
    // Recover button takes priority + cancels row-click open-modal.
    const recoverBtn = ev.target.closest('[data-device-act="recover"]');
    if (recoverBtn) {
      ev.stopPropagation();
      const row = recoverBtn.closest(".device-row");
      const id = row?.getAttribute("data-device-id");
      if (id && await _confirmRecover(id)) recoverDevice(id);
      return;
    }
    const row = ev.target.closest(".device-row");
    if (row) {
      const id = row.getAttribute("data-device-id");
      if (id) openDeviceModal(id);
    }
  });
}

// ── Device detail modal ─────────────────────────────────────────────
let _openDeviceId = null;

function openDeviceModal(id) {
  const d = _devices.get(id);
  if (!d) return;
  _openDeviceId = id;
  const overlay = $("deviceModalOverlay");
  $("deviceModalTitle").textContent = d.id;
  _renderDeviceModalBody(d);
  overlay.classList.add("show");
}

function closeDeviceModal() {
  _openDeviceId = null;
  $("deviceModalOverlay").classList.remove("show");
}

function _renderDeviceModalBody(d) {
  const body = $("deviceModalBody");
  const foot = $("deviceModalFoot");
  if (!body || !foot) return;

  const state = d.state || "down";
  const online = d.online !== false;
  const isPublisherSim = d.sim === true;
  const isProjectSim = d.claim === "sim";
  const isSim = isPublisherSim || isProjectSim;

  // Pending-Recover overlay — same source of truth as the panel
  // (``_devicesPending``) so the modal mirrors the row's
  // "Recovering…" state instead of reverting to "DOWN + Recover".
  const now = Date.now();
  const pending = _devicesPending.get(d.id);
  if (pending && pending.until <= now) _devicesPending.delete(d.id);
  const isPending = _devicesPending.has(d.id);
  const visualState = isPending ? "recovering" : state;

  const dotClass = visualState === "ok"
    ? "ok pulse"
    : visualState === "recovering" ? "warn pulse" : "bad";
  const ageStr = d.ts ? _agoStr(d.ts * 1000) : "—";
  const meta = (d.meta && Object.keys(d.meta).length)
    ? JSON.stringify(d.meta, null, 2)
    : "(none)";
  // Two sim sources reported separately so the operator can debug:
  // is this sim because the publisher said so, or because the project
  // claims it? Both surfaces are useful in different scenarios.
  const claimVal = d.claim || "real";
  const simTitle = isPublisherSim
    ? "Publisher self-flagged sim"
    : (isProjectSim ? "Project claims this device in sim mode" : "");
  const msg = (pending?.note || d.msg || "").trim();

  body.innerHTML = `
    <div class="dd-id">${escHtml(d.id)}</div>
    <div class="dd-state-row">
      <span class="dot ${dotClass}"></span>
      <span class="dd-state">${escHtml(visualState)}</span>
      ${isSim ? `<span class="device-pill device-pill--sim" title="${escAttr(simTitle)}">SIM</span>` : ""}
      ${online ? "" : `<span class="device-pill">offline</span>`}
    </div>
    ${msg ? `<div class="dd-msg">${escHtml(msg)}</div>` : ""}
    <div class="dd-table">
      <div class="dd-key">kind</div>      <div class="dd-val">${escHtml(d.kind || "—")}</div>
      <div class="dd-key">critical</div>  <div class="dd-val">${d.critical === false ? "false" : "true"}</div>
      <div class="dd-key">publisher sim</div><div class="dd-val">${isPublisherSim ? "true" : "false"}</div>
      <div class="dd-key">project claim</div><div class="dd-val">${escHtml(claimVal)}</div>
      <div class="dd-key">online</div>    <div class="dd-val">${online ? "true" : "false"}</div>
      <div class="dd-key">last update</div><div class="dd-val">${escHtml(ageStr)}</div>
    </div>
    <div class="dd-key" style="font-size:11px;">meta</div>
    <pre class="dd-meta">${escHtml(meta)}</pre>
  `;

  // Action buttons in footer: mirror the inline row's logic exactly
  // — pending → disabled "Recovering…", down + online → Recover,
  // down + offline → offline pill, ok/sim → nothing.
  foot.innerHTML = "";
  if (isPending) {
    const btn = document.createElement("button");
    btn.className = "btn btn-primary";
    btn.disabled = true;
    btn.textContent = "Recovering…";
    foot.appendChild(btn);
  } else if (state !== "ok" && !isPublisherSim) {
    if (online) {
      const btn = document.createElement("button");
      btn.className = "btn btn-primary";
      btn.textContent = "Recover";
      btn.addEventListener("click", async () => {
        if (!await _confirmRecover(d.id)) return;
        recoverDevice(d.id);
        // Stay open — the modal re-renders via the WS-driven
        // ``renderDevicesPanel`` loop and immediately reflects the
        // pending "Recovering…" state so the operator sees the
        // click landed without losing context.
      });
      foot.appendChild(btn);
    } else {
      const pill = document.createElement("span");
      pill.className = "device-pill";
      pill.textContent = "service offline — start the service";
      foot.appendChild(pill);
    }
  }
}

function _agoStr(tsMs) {
  const dt = Math.max(0, Date.now() - tsMs);
  if (dt < 1000) return "just now";
  if (dt < 60000) return `${Math.floor(dt / 1000)}s ago`;
  if (dt < 3600000) return `${Math.floor(dt / 60000)}m ago`;
  return `${Math.floor(dt / 3600000)}h ago`;
}

async function recoverDevice(deviceId) {
  // Mark in-flight; UI flips to "Recovering…" immediately and stays there
  // until the device publishes a non-recovering state (or the deadline).
  _devicesPending.set(deviceId, {
    note: "recover requested",
    until: Date.now() + RECOVER_FALLBACK_MS,
  });
  renderDevicesPanel();
  // Schedule a refresh so the row clears itself after the deadline if
  // no state event arrives (defensive; normally WS clears it earlier).
  setTimeout(() => {
    if (_devicesPending.has(deviceId)) {
      const p = _devicesPending.get(deviceId);
      if (p.until <= Date.now()) _devicesPending.delete(deviceId);
      renderDevicesPanel();
    }
  }, RECOVER_FALLBACK_MS + 500);

  try {
    const r = await fetch(
      `/orchestrator/api/workspace/${encodeURIComponent(wsName)}/devices/${encodeURIComponent(deviceId)}/recover`,
      { method: "POST" },
    );
    const reply = await r.json().catch(() => ({}));
    // Fast-fail (offline service): drop the pending mark so the offline
    // pill renders and the operator sees the reason.
    if (reply && reply.offline === true) {
      _devicesPending.delete(deviceId);
      renderDevicesPanel();
      return;
    }
    if (reply && reply.ok === false) {
      // Toast immediately so the operator sees the failure without
      // having to wait for the 4 s pending mark to expire.
      toast(`Recover failed: ${reply.msg || "unknown error"}`, "bad");
      _devicesPending.set(deviceId, {
        note: reply.msg || "recover failed",
        until: Date.now() + 4000,
      });
      renderDevicesPanel();
    }
    // ok=true (queued): keep showing "Recovering…" — the WS state events
    // own the rest of the lifecycle and will clear the pending mark.
  } catch (e) {
    // Network-level failure (orchestrator unreachable, CORS, etc.).
    // Toast so the operator doesn't think the request landed.
    toast("Recover request failed — check connection", "bad");
    _devicesPending.set(deviceId, {
      note: "request failed",
      until: Date.now() + 4000,
    });
    renderDevicesPanel();
  }
}

// Light HTML/attribute escapers used by renderDevicesPanel.
function escHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ───────────────────────────── Operator Actions ─────────────────────────
// Single WebSocket to /ws/operator_actions on the workspace runtime
// handles both directions:
//   server → client: ``{type:"actions", actions:[...]}`` on connect
//                    (and any future refresh)
//   client → server: ``{type:"invoke", component, method}`` per click,
//                    reply: ``{type:"invoke_result", ok, msg?, result?}``
// Connection lives for the workspace session — every button click is
// a single ws.send() with no HTTP handshake, so the round-trip is
// sub-millisecond on the LAN.
let _opActions = [];              // [{component, label, method}, ...]
let _opActionsExpanded = false;   // sidebar section collapsed by default
let _opActionsWs = null;
let _opActionsWsClosed = false;
let _opActionsWsUrl = "";
let _opActionsWsRetryMs = 1000;

function connectOpActionsWS(runtimeUrl) {
  const wsUrl = runtimeUrl.replace(/^http/, "ws") + "/ws/operator_actions";
  if (_opActionsWs && _opActionsWsUrl === wsUrl) return;
  disconnectOpActionsWS();
  _opActionsWsUrl = wsUrl;
  _opActionsWsClosed = false;
  _opActionsWsRetryMs = 1000;
  _tryOpActionsWS();
}

function _tryOpActionsWS() {
  if (_opActionsWsClosed || !_opActionsWsUrl) return;
  const ws = new WebSocket(_opActionsWsUrl);
  _opActionsWs = ws;
  ws.onopen = () => { _opActionsWsRetryMs = 1000; };
  ws.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type === "actions") {
      _opActions = Array.isArray(msg.actions) ? msg.actions : [];
      renderOperatorActionsPanel();
      updateOperatorActionsGate(_lastState);
    } else if (msg.type === "invoke_result") {
      const tag = `${msg.component}.${msg.method}`;
      if (msg.ok) toast(`${tag} ✓`, "ok");
      else        toast(`${tag}: ${msg.msg || "failed"}`, "bad");
    }
  };
  ws.onclose = () => {
    if (_opActionsWsClosed) return;
    setTimeout(_tryOpActionsWS, _opActionsWsRetryMs);
    _opActionsWsRetryMs = Math.min(_opActionsWsRetryMs * 1.5, 8000);
  };
  ws.onerror = () => ws.close();
}

function disconnectOpActionsWS() {
  _opActionsWsClosed = true;
  if (_opActionsWs) { try { _opActionsWs.close(); } catch {} _opActionsWs = null; }
  _opActionsWsUrl = "";
}

function _opActionsHtml(disabled) {
  // Group entries by component so the buttons render as a series of
  // small per-component rows — matches the operator's mental model
  // ("I want to do something with the gripper").
  const groups = new Map();
  for (const a of _opActions) {
    if (!groups.has(a.component)) groups.set(a.component, []);
    groups.get(a.component).push(a);
  }
  if (!groups.size) return `<div class="step-empty">No operator actions declared</div>`;
  const rows = [];
  for (const [component, actions] of groups) {
    const buttons = actions.map(a => `
      <button class="btn btn-sm op-action-btn"
              data-component="${escHtml(component)}"
              data-method="${escHtml(a.method)}"
              ${disabled ? "disabled" : ""}>${escHtml(a.label)}</button>
    `).join("");
    rows.push(`
      <div class="op-action-group">
        <div class="op-action-component">${escHtml(component)}</div>
        <div class="op-action-buttons">${buttons}</div>
      </div>
    `);
  }
  return rows.join("");
}

function renderOperatorActionsPanel() {
  // Sidebar section is always visible — mirrors Devices, so the
  // operator can see the surface exists even when no actions are
  // declared yet. Empty state is rendered inline.
  const list = $("opActionsList");
  const disabled = isRunning(_lastState);
  const html = _opActionsHtml(disabled);
  if (list) list.innerHTML = html;

  // Pendant: only show the secondary-row button when there's
  // actually something to do. The pendant row is too constrained
  // to spend a tile on an empty surface.
  const pendantBtn = $("pendantOpActions");
  if (pendantBtn) pendantBtn.style.display = _opActions.length ? "" : "none";
  const modalBody = $("opActionsModalBody");
  if (modalBody) modalBody.innerHTML = html;
}

function updateOperatorActionsGate(state) {
  // Re-render so disabled state flips with workflow state. Cheap —
  // the list itself is short.
  if (_opActions.length) renderOperatorActionsPanel();
}

function runOperatorAction(component, method) {
  // Fire-and-forget over the open WS. Reply comes back as an
  // ``invoke_result`` message handled in ``ws.onmessage`` and shown
  // via toast — no per-click promise / await needed.
  if (!_opActionsWs || _opActionsWs.readyState !== 1) {
    toast(`${component}.${method}: not connected`, "bad");
    return;
  }
  try {
    _opActionsWs.send(JSON.stringify({ type: "invoke", component, method }));
  } catch (e) {
    toast(`${component}.${method}: ${e}`, "bad");
  }
}

// Sidebar section toggle (chevron expand/collapse — matches the
// devices / steps sections).
function _wireOpActionsSection() {
  const header = $("btnToggleOpActions");
  const list = $("opActionsList");
  const chev = $("opActionsChevron");
  if (!header || !list) return;
  header.addEventListener("click", () => {
    _opActionsExpanded = !_opActionsExpanded;
    list.style.display = _opActionsExpanded ? "" : "none";
    if (chev) chev.classList.toggle("open", _opActionsExpanded);
  });
}

// Click delegation — fires both for the sidebar list and the pendant
// modal body. Single handler keeps everything in lockstep with the
// re-render.
function _wireOpActionsClicks() {
  const handler = (e) => {
    const btn = e.target.closest(".op-action-btn");
    if (!btn || btn.disabled) return;
    const c = btn.dataset.component;
    const m = btn.dataset.method;
    if (c && m) runOperatorAction(c, m);
  };
  $("opActionsList")?.addEventListener("click", handler);
  $("opActionsModalBody")?.addEventListener("click", handler);
}

// Pendant Controls button — opens the modal. Modal closes via the X
// or by clicking the backdrop.
function _wirePendantOpActionsModal() {
  $("pendantOpActions")?.addEventListener("click", () => {
    $("opActionsModalOverlay")?.classList.add("show");
  });
  $("btnOpActionsModalClose")?.addEventListener("click", () => {
    $("opActionsModalOverlay")?.classList.remove("show");
  });
  $("opActionsModalOverlay")?.addEventListener("click", (e) => {
    if (e.target.id === "opActionsModalOverlay") {
      e.currentTarget.classList.remove("show");
    }
  });
}

_wireOpActionsSection();
_wireOpActionsClicks();
_wirePendantOpActionsModal();
function escAttr(s) { return escHtml(s); }

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

let _lastControlsState = null;
function renderControls(state, launched, running) {
  // Sidebar controls only depend on ``state`` (launched / running /
  // parking / start-label all derive from it). When state hasn't
  // changed, neither has the button set — short-circuit before
  // tearing down the DOM and reattaching listeners.
  const s = (state || "").toUpperCase();
  if (s === _lastControlsState) {
    // schedule visibility is class-driven (.js-schedule-open) and
    // depends only on ``launched``, which is derived from s → also
    // stable across this short-circuit.
    return;
  }
  _lastControlsState = s;
  controls.innerHTML = "";

  const addBtn = (label, cmd, opts = {}) => {
    const b = document.createElement("button");
    b.className = `btn btn-sm${opts.primary ? " btn-primary" : ""}${opts.danger ? " btn-danger" : ""}${opts.warn ? " btn-warn" : ""}`;
    b.textContent = label;
    if (opts.disabled) b.disabled = true;
    b.addEventListener("click", async () => {
      if (cmd === "park" && !await confirmDialog({
        title: "Park Workflow?",
        message: "The current action will finish, then the project's Park steps run and the workflow ends. Click Start to begin a new run.",
        confirm: "Park Workflow",
        icon: "park",
        variant: "danger",
      })) return;
      if (cmd === "kill" && !await confirmDialog({
        title: "Kill Process?",
        message: "This will immediately terminate the workspace. Any running workflow will be aborted.",
        confirm: "Kill Process",
        icon: "kill",
      })) return;
      // Device-fault gate for Start / Resume. Identical contract to
      // the dashboard card: fetch fresh status, prompt with the list
      // of blocking device ids if any, abort if operator cancels. See
      // deviceFaultGate in api.js.
      if (cmd === "start") {
        const action = (s === "PAUSED") ? "Resume" : "Start";
        const ok = await deviceFaultGate(wsName, action);
        if (!ok) return;
      }
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
  } else {
    // PARKING is a *flag* — the robot keeps working through its
    // current step and the park-cleanup subtree, so Pause / Resume
    // must still be reachable. Only the Park button itself is
    // disabled (already parking) and Start is disabled (no new run
    // while parking is in flight). Kill remains always-on below.
    const parking   = s === "PARKING";
    const active    = running || parking;
    // "Start" while the workspace hasn't begun a run yet; "Resume"
    // once it has — even when disabled (i.e. RUNNING / PARKING).
    // The slot's *meaning* is "begin or continue the run", and
    // post-start that meaning is Resume regardless of enabled state.
    const startLabel = isStarted(s) ? "Resume" : "Start";
    addBtn(startLabel, "start", { primary: true, disabled: active });
    addBtn("Pause",    "pause", { disabled: !active });
    // Park only makes sense when the workflow is actually in flight.
    // Before Start the runtime is IDLE — clicking Park there would
    // trigger the cleanup subtree against a non-running workflow and
    // crash. Require ``active`` (running || parking) and disable when
    // already parking.
    addBtn("Park",     "park",  { warn: true, disabled: !active || parking });
  }

  // Gear button for parameters — only before launch
  if (!launched) {
    const gear = document.createElement("button");
    gear.className = "btn btn-sm";
    gear.textContent = "Parameters";
    gear.addEventListener("click", () => openParamsModal(launched));
    controls.appendChild(gear);
  }

  // Kill — separated to the right with spacer
  if (launched) {
    const spacer = document.createElement("div");
    spacer.className = "spacer";
    controls.appendChild(spacer);
    addBtn("Kill", "kill", { danger: true });
  }

  // Schedule lives in the top-bar icon row (alongside Fullscreen /
  // Theme / Pendant — it's a *view*, not a workflow command). The
  // pendant overlay nav carries a twin in its action cluster; both
  // share the ``.js-schedule-open`` class so visibility stays in
  // sync without a per-button branch here. Show only once the
  // workspace is launched; before then there's no plan to open.
  document.querySelectorAll(".js-schedule-open").forEach(b => {
    b.style.display = launched ? "" : "none";
  });
}

function updateIframe(state, launched) {
  if (!wsInfo) return;
  const ready = launched && (state || "").toUpperCase() !== "LAUNCHED_NOT_READY";
  if (!ready) {
    if (iframeReady) {
      iframeReady = false;
      iframeUrl   = "";
      // Don't blank the iframe — leave the last rendered 3D scene
      // visible after the process exits so operators can keep
      // looking at where the run ended. The iframe's already-loaded
      // content stays cached in the browser; a fresh Launch will
      // overwrite ``frame.src`` below when the new process is ready.
      // Step / device / status WSes disconnect — their endpoints are
      // gone with the process; they'll reconnect on next Launch.
      // Logs WS stays connected — its endpoint is on the orchestrator,
      // not the workspace process, and the file persists after kill.
      disconnectStepWS();
      disconnectDevicesWS();
      disconnectRuntimeStatusWS();
      disconnectScheduleWS();
    }
    return;
  }
  const theme     = document.documentElement.getAttribute("data-theme") || "dark";
  const targetUrl = wsViewerUrl(wsInfo);
  if (!iframeReady || iframeUrl !== targetUrl) {
    iframeReady = true;
    iframeUrl   = targetUrl;
    // Timer-based load-failure detection. The iframe's ``error`` event
    // fires unreliably for cross-origin / 404 cases — a missing 3D
    // server typically just hangs without notifying. Toast after
    // VIEWER_LOAD_TIMEOUT_MS if ``load`` hasn't fired, so the operator
    // knows the 3D server isn't reachable (vs. assuming it's "still
    // loading"). One-shot per src change.
    const VIEWER_LOAD_TIMEOUT_MS = 10_000;
    const loadTimer = setTimeout(() => {
      toast("3D viewer didn't respond — is the workspace process up?", "warn");
    }, VIEWER_LOAD_TIMEOUT_MS);
    frame.addEventListener("load", () => {
      clearTimeout(loadTimer);
      frame.contentWindow?.postMessage({ type: "theme", value: theme }, "*");
      // Replay the pendant render state on iframe (re)load. Without
      // this, if pendant was open *before* this iframe finished
      // loading — or if the workspace switches and reloads the
      // iframe while pendant is open — the new iframe instance
      // would default to ``_pendantPaused = false``. ``apply…``
      // routes the message correctly based on current pendant +
      // preview state.
      if (_pendantMode) applyPendantRenderState();
    }, { once: true });
    frame.src = targetUrl + "/?theme=" + theme;
    placeholder.style.display = "none";
    connectStepWS(targetUrl);
    connectDevicesWS(targetUrl);
    connectRuntimeStatusWS(targetUrl);
    connectScheduleWS(targetUrl);
    connectOpActionsWS(targetUrl);
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
// Guard against unchanged values — the formatted uptime only flips
// once per second of wall clock, so most ticks would write the
// same string back to the DOM and trigger needless reflow. Cache
// last value and bail if it matches.
let _lastUptimeStr = null;
setInterval(() => {
  if (_uptimeBase != null && _uptimeAt != null) {
    const elapsed = (performance.now() - _uptimeAt) / 1000;
    const live = _uptimeBase + elapsed;
    const str = fmtUptime(live) || "—";
    if (str === _lastUptimeStr) return;
    _lastUptimeStr = str;
    uptimeVal.textContent = str;
    // Keep the pendant timer in sync. Only writes if pendant is the
    // active view — the element is hidden otherwise.
    if (_pendantMode) {
      const ptv = document.getElementById("pendantTimerValue");
      if (ptv && ptv.parentElement.style.display !== "none") {
        ptv.textContent = str === "—" ? "0:00" : str;
      }
    }
  }
}, 1000);

// ---- Init ----
// One-time a11y bootstrap: mirror title → aria-label on every icon
// button. Buttons all have helpful ``title`` already; ``aria-label``
// is what screen readers announce. Cheaper to do here than to edit
// every button's HTML, and any future button gets it for free.
function _hydrateAriaLabels(root = document) {
  root.querySelectorAll('.btn-icon[title]').forEach((b) => {
    if (!b.hasAttribute('aria-label')) {
      b.setAttribute('aria-label', b.title);
    }
  });
}

// Focus management for modals — single observer covers every
// ``.modal-overlay`` element. When .show is added: remember the
// previously focused element and move focus into the modal so the
// keyboard user lands somewhere relevant. When .show is removed:
// restore focus to wherever it was. Future modals get this for free.
const _modalFocusStack = new Map();   // overlayEl → previous focus
function _focusFirstIn(overlay) {
  const candidates = overlay.querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
  );
  for (const el of candidates) {
    if (el.offsetParent !== null && !el.disabled) {
      el.focus();
      return;
    }
  }
  // Fallback: make the modal body itself focusable + focus it so ESC
  // and arrow keys land somewhere.
  const body = overlay.querySelector('.modal-body') || overlay;
  body.setAttribute('tabindex', '-1');
  body.focus();
}
function _setupModalFocusObserver() {
  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.type !== 'attributes' || m.attributeName !== 'class') continue;
      const el = m.target;
      if (!el.classList.contains('modal-overlay')) continue;
      const wasShown = m.oldValue && m.oldValue.includes('show');
      const isShown  = el.classList.contains('show');
      if (!wasShown && isShown) {
        // Opening — remember current focus, move into modal.
        _modalFocusStack.set(el, document.activeElement);
        // Tiny defer so the show transition can paint before we focus
        // (some elements measure 0 size until visible).
        requestAnimationFrame(() => _focusFirstIn(el));
      } else if (wasShown && !isShown) {
        // Closing — restore.
        const prev = _modalFocusStack.get(el);
        _modalFocusStack.delete(el);
        if (prev && typeof prev.focus === 'function') {
          try { prev.focus(); } catch {}
        }
      }
    }
  });
  document.querySelectorAll('.modal-overlay').forEach((el) => {
    observer.observe(el, { attributes: true, attributeOldValue: true, attributeFilter: ['class'] });
  });
}

async function init() {
  _hydrateAriaLabels();
  _setupModalFocusObserver();
  _wireDevicesPanelDelegation();
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
    // Open the live-log WS once the page knows the workspace exists.
    // The endpoint is on the orchestrator (file owner), so it stays
    // valid across workspace process Kill+Launch cycles — no need to
    // tear it down in updateIframe.
    connectLogsWS();
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

// Collapse / expand devices
// Devices start collapsed — auto-expands when the first device arrives.
let _devicesExpanded = false;
$("btnToggleDevices")?.addEventListener("click", () => {
  _devicesExpanded = !_devicesExpanded;
  const el = $("devicesList");
  if (el) el.style.display = _devicesExpanded ? "" : "none";
  const chevron = $("devicesChevron");
  if (chevron) chevron.classList.toggle("open", _devicesExpanded);
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
    const j = await _getLaunchConfig();
    const schema = j.kwargs_schema || {};
    const values = j.kwargs_values || {};
    // Restore the page-side cache from the server's saved values so a
    // refresh doesn't lose what the operator set. Without this the
    // Start click after a refresh would send no kwargs and rely on
    // server-side fallback — works, but the in-page state should
    // also reflect reality.
    if (Object.keys(values).length) {
      _wsKwargsValues = { ...values };
    }
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
  // Destructive + irreversible — always confirm. Mirrors the Park /
  // Kill prompt pattern so the operator can't fat-finger away a
  // useful debug log on a tablet.
  if (!await confirmDialog({
    title: "Clear logs?",
    body: "This deletes the workspace log file. Currently buffered output stays in memory but on-disk history is gone.",
    confirmLabel: "Clear",
    confirmVariant: "danger",
  })) return;
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
    btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--green)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;
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

// Whether the pendant shows a live floating 3D preview (top-right
// PiP) or hides the viewer entirely. Persisted so the operator's
// preference survives reloads. Default ON — the situational
// awareness is the main reason to keep this feature at all; if you
// don't want the resource cost, flip it off and we revert to the
// full pause.
let _pendantPreview = localStorage.getItem("pendant_preview_3d") !== "off";

function applyPendantRenderState() {
  // Three combined flags decide whether the iframe is rendering:
  //   - _pendantMode: is the operator in pendant view at all?
  //   - _pendantPreview: do they want the PiP preview right now?
  //   - the iframe's own _renderEnabled (eye toggle, untouched here)
  // We pause when pendant is open AND preview is off. Pendant
  // closed = always resume. Pendant open + preview on = resume
  // (so the PiP shows live frames).
  document.documentElement.classList.toggle("pendant-open", _pendantMode);
  document.documentElement.classList.toggle("pendant-preview-on", _pendantMode && _pendantPreview);
  const shouldPause = _pendantMode && !_pendantPreview;
  // While in pendant PiP mode also strip the iframe's internal UI
  // (toolbar, ViewCube) — they'd dominate the 280×210 floating
  // preview. Full UI returns the moment pendant closes.
  const uiMode = (_pendantMode && _pendantPreview) ? "minimal" : "full";
  if (frame && frame.contentWindow) {
    frame.contentWindow.postMessage({ type: "render", value: shouldPause ? "pause" : "resume" }, "*");
    frame.contentWindow.postMessage({ type: "ui",     value: uiMode }, "*");
  }
}

function togglePendantPreview() {
  _pendantPreview = !_pendantPreview;
  localStorage.setItem("pendant_preview_3d", _pendantPreview ? "on" : "off");
  applyPendantRenderState();
  updatePendantPreviewBtn();
}

function updatePendantPreviewBtn() {
  const btn = $("pendantBtnPreview");
  if (!btn) return;
  btn.title = _pendantPreview ? "Hide 3D preview" : "Show 3D preview";
  btn.classList.toggle("active", _pendantPreview);
}

function togglePendant(on) {
  _pendantMode = on !== undefined ? on : !_pendantMode;
  pendantOverlay.style.display = _pendantMode ? "" : "none";
  applyPendantRenderState();
  updatePendantPreviewBtn();

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

  // Status-coloured underline on the pendant nav, mirroring the
  // main ``.ws-header[data-state]`` rule (base.css). Same attribute,
  // same colours, so both navbars carry an identical state cue.
  const navEl = document.querySelector(".pendant-nav");
  if (navEl) navEl.setAttribute("data-state", variant);

  // Ambient state wash on the body (gradient lives there now, not on
  // the navbar — base.css keeps the nav chrome clean). The
  // ``is-waiting`` class drives the breathing animation: ON when
  // we're idle/ready (operator's move), OFF when actually running
  // (system working — visuals stay steady).
  const waiting = isWaiting(state);
  const bodyEl = document.querySelector(".pendant-body");
  if (bodyEl) {
    bodyEl.setAttribute("data-variant", variant);
    bodyEl.classList.toggle("is-waiting", waiting);
  }

  // State pill uses the shared ``.pill`` variant classes (ok / warn
  // / bad / off) so it looks identical to the main top-bar's pill.
  // The inner dot pulses while *waiting* for the operator to press
  // Start — not while running. Running = steady, waiting = blinking.
  const stateEl = $("pendantState");
  if (stateEl) {
    stateEl.classList.remove("ok", "warn", "bad", "off");
    stateEl.classList.add(variant);
    const dotEl = stateEl.querySelector(".dot");
    if (dotEl) {
      dotEl.classList.remove("ok", "warn", "bad", "off", "pulse");
      dotEl.classList.add(variant);
      if (waiting) dotEl.classList.add("pulse");
    }
    const textEl = stateEl.querySelector(".pendant-state-text");
    if (textEl) textEl.textContent = stateLabel(state);
  }

  // Run elapsed time — same behaviour as the sidebar's "Up" field
  // (workspace.js line ~349): always visible, shows "—" when there's
  // no uptime data, ticks live while running, freezes on the final
  // value when the run ends.
  const timerEl = $("pendantTimer");
  if (timerEl) {
    timerEl.style.display = "";
    if (_uptimeBase != null) {
      const live = _uptimeAt != null
        ? _uptimeBase + (performance.now() - _uptimeAt) / 1000
        : _uptimeBase;
      $("pendantTimerValue").textContent = fmtUptime(live) || "—";
    } else {
      $("pendantTimerValue").textContent = "—";
    }
  }

  // Step timeline in pendant — mirror the sidebar's step list, but
  // surgically. Old version rebuilt the whole innerHTML on every
  // status tick (forced full reflow). New version diffs in place:
  // reuse existing card nodes, only update text / class / level when
  // they actually changed. Only resets innerHTML when the *count*
  // changes (rare — only on new steps appearing or list being
  // cleared).
  const pendantStepsEl = $("pendantSteps");
  if (pendantStepsEl) {
    const timeline = $("stepTimeline");
    if (timeline && launched) {
      const cards = timeline.querySelectorAll(".step-card");
      const existing = pendantStepsEl.children;
      if (cards.length !== existing.length) {
        // Count changed — rebuild from scratch (cheap because
        // ``cards`` is a finite list, not a per-tick recomputation).
        let html = "";
        cards.forEach(card => {
          const text  = card.querySelector(".step-text")?.textContent || "";
          const level = card.dataset.level || "info";
          const cls   = card.classList.contains("active") ? "active" : "done";
          html += `<div class="pendant-step-card ${cls}" data-level="${level}"><span class="pendant-step-dot"></span><span>${text}</span></div>`;
        });
        pendantStepsEl.innerHTML = html;
        pendantStepsEl.scrollTop = pendantStepsEl.scrollHeight;
      } else if (cards.length) {
        // Same count — patch in place: text, active/done class,
        // data-level. Skip writes when the value matches.
        let needsScroll = false;
        cards.forEach((card, i) => {
          const dst   = existing[i];
          const text  = card.querySelector(".step-text")?.textContent || "";
          const level = card.dataset.level || "info";
          const cls   = card.classList.contains("active") ? "active" : "done";
          const span  = dst.lastElementChild;
          if (span && span.textContent !== text) span.textContent = text;
          if (dst.dataset.level !== level) dst.dataset.level = level;
          const isActive = cls === "active";
          if (dst.classList.contains("active") !== isActive) {
            dst.classList.toggle("active", isActive);
            dst.classList.toggle("done",  !isActive);
            needsScroll = isActive;  // newly-active card → bring into view
          }
        });
        if (needsScroll) pendantStepsEl.scrollTop = pendantStepsEl.scrollHeight;
      }
    } else if (pendantStepsEl.children.length) {
      pendantStepsEl.innerHTML = "";
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

  // PARKING is a *flag* — the robot keeps working through its current
  // step and the park-cleanup subtree, so Pause / Resume stay reachable.
  // Only the Park button itself is disabled (already parking) and Start
  // is disabled (no new run while parking is in flight).
  const parking = state.toUpperCase() === "PARKING";
  const active  = running || parking;
  $("pendantLaunch").disabled  = launched;
  $("pendantStart").disabled   = !launched || active;
  $("pendantPause").disabled   = !active;
  // Park needs an in-flight workflow — see the sidebar comment above.
  $("pendantPark").disabled    = !active || parking;
  $("pendantKill").disabled    = !launched;

  // Relabel the Start tile to "Resume" once the workspace has begun
  // a run — same cmd, but the slot's meaning shifts from "begin" to
  // "continue". Same rule everywhere (sidebar, dashboard card,
  // pendant) so an operator sees one consistent vocabulary.
  const startLabelEl = $("pendantStart")?.querySelector("span");
  if (startLabelEl) {
    startLabelEl.textContent = isStarted(state) ? "Resume" : "Start";
  }
}

// Wire pendant buttons
document.querySelectorAll(".pendant-btn[data-cmd]").forEach(btn => {
  btn.addEventListener("click", async () => {
    const cmd = btn.dataset.cmd;
    if (cmd === "park" && !await confirmDialog({
      title: "Park Workflow?",
      message: "The current action will finish, then the project's Park steps run and the workflow ends. Click Start to begin a new run.",
      confirm: "Park Workflow",
      icon: "park",
      variant: "danger",
    })) return;
    if (cmd === "kill" && !await confirmDialog({
      title: "Kill Process?",
      message: "This will immediately terminate the workspace.",
      confirm: "Kill Process",
      icon: "kill",
    })) return;
    // Device-fault gate also covers the pendant Start/Resume — same
    // contract as the sidebar button. Pendant pressed sound/haptics
    // come AFTER the gate so a canceled prompt doesn't beep falsely.
    if (cmd === "start") {
      const action = ((_lastState || "").toUpperCase() === "PAUSED") ? "Resume" : "Start";
      const ok = await deviceFaultGate(wsName, action);
      if (!ok) return;
    }
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

// Pendant Kill button (secondary, separate from pendant-btn grid)
$("pendantKill").addEventListener("click", async () => {
  if (!await confirmDialog({
    title: "Emergency Stop",
    message: "Kill the process immediately? This cannot be undone.",
    confirm: "Kill Now",
    icon: "kill",
  })) return;
  try {
    await sendCmd("kill");
    pendantErrorSound();
    toast("kill sent", "ok");
    await refreshStatus();
    updatePendantUI();
  } catch (err) {
    toast(String(err), "bad");
  }
});

// Pendant params button
$("pendantParams").addEventListener("click", () => {
  const launched = isLaunched(_lastState);
  openParamsModal(launched);
});

// Schedule buttons (top-bar + pendant nav) — share the
// ``.js-schedule-open`` class so a single binding covers both.
document.querySelectorAll(".js-schedule-open").forEach(b => {
  b.addEventListener("click", () => openScheduleModal());
});

$("btnPendant").addEventListener("click", () => togglePendant(true));
$("pendantExit").addEventListener("click", () => togglePendant(false));
$("pendantBtnPreview")?.addEventListener("click", togglePendantPreview);



// Alarm dismiss
$("alarmDismiss").addEventListener("click", () => _hideBanner());

// Expose for console testing
window._showBanner = _showBanner;
window._hideBanner = _hideBanner;

init();
