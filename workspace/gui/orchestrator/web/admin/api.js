// Shared API utilities — imported by dashboard.js and workspace.js

export const ORIGIN = window.location.origin;
export const API_BASE = "/orchestrator/api";

export function getToken() {
  return (localStorage.getItem("orch_token") || "").trim();
}

export function setToken(v) {
  localStorage.setItem("orch_token", String(v || "").trim());
}

export async function apiFetch(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  const tok = getToken();
  if (tok) headers["X-Orch-Token"] = tok;
  Object.assign(headers, opts.headers || {});

  const res  = await fetch(ORIGIN + API_BASE + path, { ...opts, headers });
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { raw: text }; }
  if (!res.ok) throw new Error(data?.error || data?.raw || res.statusText);
  return data;
}

export function stateVariant(state) {
  const s = String(state || "").toUpperCase();
  // IDLE/READY = "system is fine, waiting for you to press Start"
  // → green, same colour as RUNNING. The blink (driven by
  // ``isWaiting``) is what differentiates "your move" from "I'm
  // working" — colour stays green throughout.
  if (["RUNNING", "ACTIVE", "IDLE", "READY"].includes(s))            return "ok";
  if (["ERROR", "FAILED", "OFFLINE", "REMOTE_OFFLINE"].includes(s))  return "bad";
  if (["NOT_LAUNCHED", "", "UNKNOWN"].includes(s))                    return "off";
  return "warn"; // PAUSED, LAUNCHED_NOT_READY, PARKING
}

export function stateLabel(state) {
  const s = String(state || "").toUpperCase();
  if (s === "IDLE") return "READY";
  if (s === "PARKING") return "PARKING";
  return s || "—";
}

export function isParking(state) {
  return String(state || "").toUpperCase() === "PARKING";
}

export function isRunning(state) {
  return ["RUNNING", "ACTIVE"].includes(String(state || "").toUpperCase());
}

export function isLaunched(state) {
  const s = String(state || "").toUpperCase();
  return !["", "NOT_LAUNCHED", "OFFLINE", "REMOTE_OFFLINE", "UNKNOWN"].includes(s);
}

// True once the workspace has begun a run (RUNNING / PAUSED /
// PARKING). Drives the "Start" vs "Resume" label flip: pre-run
// the slot reads "Start" (the first action), post-run it reads
// "Resume" (enabled only when paused, disabled while running or
// parking — but the *label* stays "Resume" because conceptually
// the run has already started).
export function isStarted(state) {
  return ["RUNNING", "ACTIVE", "PAUSED", "PARKING"].includes(String(state || "").toUpperCase());
}

// True when the workspace needs operator attention — anything
// where the system is *not* progressing on its own:
//   IDLE / READY  → press Start (or Resume after a completed run)
//   PAUSED        → press Resume (or Kill)
//   ERROR / FAILED → acknowledge, fix, or Kill
// Drives the "blink for attention" visual on the body glow and
// state pill dot. RUNNING / PARKING are *active* progressing
// states — their visuals stay steady, the blink is reserved for
// "your move" moments.
export function isWaiting(state) {
  return ["IDLE", "READY", "PAUSED", "ERROR", "FAILED"].includes(String(state || "").toUpperCase());
}

export function fmtUptime(sec) {
  if (sec == null) return null;
  sec = Math.max(0, Math.floor(Number(sec) || 0));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const p = n => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${p(m)}:${p(s)}` : `${m}:${p(s)}`;
}

export function fmtTimestamp(v) {
  if (!v) return null;
  const t = typeof v === "number" ? v * 1000 : Date.parse(String(v));
  if (isNaN(t)) return String(v);
  return new Date(t).toLocaleString();
}

export function esc(s) {
  return String(s ?? "")
    .replace(/&/g,  "&amp;")
    .replace(/</g,  "&lt;")
    .replace(/>/g,  "&gt;")
    .replace(/"/g,  "&quot;");
}

/**
 * Connect to the live status WebSocket.
 * onStatus(statuses) is called with the full {name: statusObj} map.
 * Returns { close() } handle. Auto-reconnects on drop.
 */
export function connectStatusWS(onStatus) {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${window.location.host}/orchestrator/ws/status`;
  let ws = null;
  let closed = false;
  let retryMs = 1000;

  function connect() {
    if (closed) return;
    ws = new WebSocket(url);
    ws.onopen = () => { retryMs = 1000; };
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "status" && msg.statuses) onStatus(msg.statuses);
      } catch {}
    };
    ws.onclose = () => {
      if (closed) return;
      setTimeout(connect, retryMs);
      retryMs = Math.min(retryMs * 1.5, 8000);
    };
    ws.onerror = () => ws.close();
  }

  connect();
  return { close() { closed = true; ws?.close(); } };
}

export function wsViewerUrl(ws) {
  try {
    const host = ws.node_url ? new URL(ws.node_url).hostname : window.location.hostname;
    return `http://${host}:${ws.port}`;
  } catch {
    return `http://${window.location.hostname}:${ws.port}`;
  }
}

// confirmDialog is loaded globally from /vendor/confirm.js
// Re-export for ES module imports in dashboard.js / workspace.js
export const confirmDialog = window.confirmDialog;


// ── Device-fault gate ─────────────────────────────────────────────────
// Bulletproof guard for Start/Resume actions when one or more critical
// devices are still down. Two layers:
//   1. Fetch the workspace's latest /status (which carries
//      ``devices_summary`` per docs/device-guide.md §10) — never trust
//      the cached lastStatus snapshot for a safety decision, since a
//      WS push could be a few seconds stale and the operator's mental
//      model rewards the freshest read.
//   2. If the summary reports any blocking ids, show a
//      ``dangerous=true`` confirm dialog: Cancel auto-focused, Enter
//      does NOT confirm, structured list of the offending device ids.
//      Operator must click Confirm explicitly to proceed.
//
// Returns: ``true`` when safe to proceed (no blockers, OR operator
// confirmed). ``false`` when operator canceled. Failure to fetch
// summary fails OPEN — we don't want a transient HTTP hiccup to
// permanently block a workspace, and the runtime's own auto-pause
// path catches a real critical-down at the first state event anyway.

export async function fetchWorkspaceStatus(workspaceName) {
  try {
    const res = await fetch(
      `/orchestrator/api/workspace/${encodeURIComponent(workspaceName)}/status`,
      { cache: "no-store" }
    );
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/**
 * Show the device-fault confirmation modal.
 *
 * @param {Object} opts
 * @param {string} opts.workspaceName
 * @param {string[]} opts.blockingIds  device ids that gate the action
 * @param {string} opts.action         "Start" or "Resume"
 * @returns {Promise<boolean>}         true to proceed, false to abort
 */
export function deviceFaultConfirmDialog({ workspaceName, blockingIds, action }) {
  const a = action || "Start";
  const ids = Array.isArray(blockingIds) ? blockingIds : [];
  const count = ids.length;
  return confirmDialog({
    title: `${a} "${workspaceName}" with ${count} device${count === 1 ? "" : "s"} down?`,
    message:
      `${count} critical device${count === 1 ? " is" : "s are"} not OK. ` +
      `${a === "Resume" ? "Resuming" : "Starting"} now will hit the device${count === 1 ? "" : "s"} ` +
      `on the first call and the workflow will fail or auto-pause again.`,
    lines: ids,
    confirm: `${a} anyway`,
    cancel: "Cancel",
    variant: "danger",
    icon: "warning",
    dangerous: true,   // Cancel auto-focused; Enter doesn't confirm
  });
}

/**
 * Run the gate. Fetches fresh status, decides if confirmation is
 * needed, returns true when the caller should proceed.
 *
 * @param {string} workspaceName
 * @param {string} action  "Start" | "Resume"  (used in dialog text)
 * @returns {Promise<boolean>}
 */
export async function deviceFaultGate(workspaceName, action) {
  const status = await fetchWorkspaceStatus(workspaceName);
  // Fail-open on fetch failure — see module comment above.
  if (!status) return true;
  const summary = status.devices_summary;
  if (!summary || !summary.blocking || !Array.isArray(summary.blocking_ids)
      || summary.blocking_ids.length === 0) {
    return true;
  }
  return await deviceFaultConfirmDialog({
    workspaceName,
    blockingIds: summary.blocking_ids,
    action,
  });
}
