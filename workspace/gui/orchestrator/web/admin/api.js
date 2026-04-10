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
  if (["RUNNING", "ACTIVE"].includes(s))                              return "ok";
  if (["ERROR", "FAILED", "OFFLINE", "REMOTE_OFFLINE"].includes(s))  return "bad";
  if (["NOT_LAUNCHED", "", "UNKNOWN"].includes(s))                    return "off";
  return "warn"; // IDLE, READY, PAUSED, LAUNCHED_NOT_READY, etc.
}

export function stateLabel(state) {
  const s = String(state || "").toUpperCase();
  if (s === "IDLE") return "READY";
  if (s === "ENDING") return "ENDING";
  return s || "—";
}

export function isEnding(state) {
  return String(state || "").toUpperCase() === "ENDING";
}

export function isRunning(state) {
  return ["RUNNING", "ACTIVE"].includes(String(state || "").toUpperCase());
}

export function isLaunched(state) {
  const s = String(state || "").toUpperCase();
  return !["", "NOT_LAUNCHED", "OFFLINE", "REMOTE_OFFLINE", "UNKNOWN"].includes(s);
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
