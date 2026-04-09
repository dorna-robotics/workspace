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

// ── Confirm Dialog ─────────────────────────────────────────────────────
// iOS-style alert that replaces window.confirm().
// Returns a Promise<boolean>.
//
// Usage:
//   if (!await confirmDialog({ title, message, confirm, variant })) return;
//
// variant: "danger" (red) | "default" (accent blue)

const _CONFIRM_ICONS = {
  kill: `<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="12" cy="12" r="10"/><path d="M15 9l-6 6M9 9l6 6"/></svg>`,
  remove: `<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
    <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/>
    <line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>`,
  end: `<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
    <rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6" rx="0.5"/></svg>`,
  warning: `<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
    <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
    <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
};

let _confirmOverlay = null;

function _ensureOverlay() {
  if (_confirmOverlay) return _confirmOverlay;
  const el = document.createElement("div");
  el.className = "confirm-overlay";
  el.innerHTML = `
    <div class="confirm-dialog">
      <div class="confirm-icon"></div>
      <div class="confirm-title"></div>
      <div class="confirm-message"></div>
      <label class="confirm-remember">
        <input type="checkbox" class="confirm-remember-cb">
        <span>Don't ask me again</span>
      </label>
      <div class="confirm-actions">
        <button class="btn confirm-cancel">Cancel</button>
        <button class="btn confirm-ok">Confirm</button>
      </div>
    </div>`;
  document.body.appendChild(el);
  _confirmOverlay = el;
  // close on backdrop click
  el.addEventListener("click", (e) => {
    if (e.target === el) el.querySelector(".confirm-cancel").click();
  });
  return el;
}

export function confirmDialog({
  title = "Are you sure?",
  message = "",
  confirm: confirmLabel = "Confirm",
  cancel: cancelLabel = "Cancel",
  variant = "danger",
  icon = "warning",
  remember = null,  // localStorage key — if set, shows "Don't ask me again" checkbox
} = {}) {
  // If previously remembered, skip the dialog
  if (remember && localStorage.getItem(`confirm_skip_${remember}`) === "1") {
    return Promise.resolve(true);
  }

  return new Promise((resolve) => {
    const el = _ensureOverlay();
    const dialog = el.querySelector(".confirm-dialog");
    const iconEl = el.querySelector(".confirm-icon");
    const titleEl = el.querySelector(".confirm-title");
    const msgEl = el.querySelector(".confirm-message");
    const rememberLabel = el.querySelector(".confirm-remember");
    const rememberCb = el.querySelector(".confirm-remember-cb");
    const cancelBtn = el.querySelector(".confirm-cancel");
    const okBtn = el.querySelector(".confirm-ok");

    iconEl.innerHTML = _CONFIRM_ICONS[icon] || _CONFIRM_ICONS.warning;
    iconEl.dataset.variant = variant;
    titleEl.textContent = title;
    msgEl.textContent = message;
    cancelBtn.textContent = cancelLabel;
    okBtn.textContent = confirmLabel;

    // Show/hide remember checkbox
    rememberLabel.style.display = remember ? "" : "none";
    rememberCb.checked = false;

    okBtn.className = `btn confirm-ok ${variant === "danger" ? "confirm-ok-danger" : "btn-primary"}`;

    dialog.classList.remove("confirm-out");
    el.classList.add("show");
    void dialog.offsetWidth;
    okBtn.focus();

    function cleanup(result) {
      // Save preference if checked and confirmed
      if (result && remember && rememberCb.checked) {
        localStorage.setItem(`confirm_skip_${remember}`, "1");
      }
      dialog.classList.add("confirm-out");
      el.classList.add("hiding");
      setTimeout(() => {
        el.classList.remove("show", "hiding");
        dialog.classList.remove("confirm-out");
      }, 180);
      cancelBtn.removeEventListener("click", onCancel);
      okBtn.removeEventListener("click", onOk);
      document.removeEventListener("keydown", onKey);
      resolve(result);
    }

    function onCancel() { cleanup(false); }
    function onOk() { cleanup(true); }
    function onKey(e) {
      if (e.key === "Escape") cleanup(false);
      if (e.key === "Enter") cleanup(true);
    }

    cancelBtn.addEventListener("click", onCancel);
    okBtn.addEventListener("click", onOk);
    document.addEventListener("keydown", onKey);
  });
}
