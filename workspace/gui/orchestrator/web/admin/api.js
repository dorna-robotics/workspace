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

/* ── Toast — the one implementation for the admin pages ────────────────
   Only for something the operator cannot already see: an error, or a
   result that shows nowhere else. Never an echo of a click whose effect
   the page already shows (a state pill, a list, a closed modal).
   ok: 2.5 s · warn: 5 s · bad: stays until clicked. Three at most.
   Look: base.css .toast (a plain card with a mark, no tinted box). */
const TOAST_MS = { ok: 2500, warn: 5000 };
export function toast(msg, type = "ok") {
  const area = document.getElementById("toastArea");
  if (!area) return;
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.setAttribute("role", type === "bad" ? "alert" : "status");
  el.textContent = msg;
  el.title = type === "bad" ? "Click to dismiss" : "";
  el.addEventListener("click", () => el.remove());
  area.appendChild(el);
  while (area.children.length > 3) area.firstElementChild.remove();
  const ms = TOAST_MS[type];
  if (ms) setTimeout(() => el.remove(), ms);
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

// ── Operator feedback: sound + vibration ──────────────────────────────
// One voice for every surface that sends a run command (dashboard card,
// workspace page, pendant). A tap answers the INSTANT it lands — before
// any network round trip — and the outcome answers again: a tone for
// done, a double buzz for refused. Park / Kill (hold-to-activate) tick
// upward under the finger for the whole hold, so the operator hears
// and feels that something is happening, not only sees it.
//
// Audio: browsers start an AudioContext muted until a user gesture
// resumes it. The context is made, or resumed, by the first pointer or
// key press ANYWHERE on the page — so a kiosk opened straight into the
// pendant gets sound from its first touch. Vibration is what the
// browser offers (Android Chrome; iOS and desktops ignore it).
let _actx = null;
function _audio() {
  if (!_actx) {
    try { _actx = new (window.AudioContext || window.webkitAudioContext)(); }
    catch { _actx = null; }
  }
  return _actx;
}
for (const t of ["pointerdown", "keydown"]) {
  document.addEventListener(t, () => {
    const c = _audio();
    if (c && c.state === "suspended") c.resume().catch(() => {});
  }, { capture: true, passive: true });
}
function _tone(freq, dur, { type = "sine", vol = 0.1, at = 0 } = {}) {
  const c = _audio();
  if (!c || c.state !== "running") return;
  try {
    const t0 = c.currentTime + at;
    const osc = c.createOscillator();
    const gain = c.createGain();
    osc.type = type;
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(vol, t0);
    gain.gain.exponentialRampToValueAtTime(0.001, t0 + dur);
    osc.connect(gain).connect(c.destination);
    osc.start(t0);
    osc.stop(t0 + dur);
  } catch {}
}
function _buzz(pattern) { try { navigator.vibrate?.(pattern); } catch {} }

export const feedback = {
  /** The tap landed. */
  press() { _tone(660, 0.04); _buzz(30); },
  /** The command was accepted. */
  ok() { _tone(1000, 0.1, { vol: 0.08 }); _buzz(15); },
  /** The command was refused or failed. */
  err() {
    _tone(280, 0.12, { type: "square" });
    _tone(220, 0.15, { type: "square", vol: 0.08, at: 0.1 });
    _buzz([50, 30, 50]);
  },
  /** A robot alarm or a critical device down: high-low-high, a long buzz. */
  alarm() {
    _tone(880, 0.15, { type: "square", vol: 0.15 });
    _tone(660, 0.15, { type: "square", vol: 0.15, at: 0.2 });
    _tone(880, 0.15, { type: "square", vol: 0.15, at: 0.4 });
    _buzz([200, 100, 200]);
  },
  /** A hold of ``ms`` began: ticks rising in pitch, a pulse each, until
   *  ``fire()`` (the hold completed) or ``cancel()`` (released early). */
  hold(ms) {
    const TICKS = 8;
    let n = 0;
    const tick = () => {
      _tone(440 * Math.pow(2, n / TICKS), 0.035, { vol: 0.07 });
      _buzz(12);
      n += 1;
    };
    tick();
    const timer = setInterval(() => { if (n < TICKS) tick(); }, ms / TICKS);
    const stop = () => clearInterval(timer);
    return {
      fire() { stop(); _tone(880, 0.07); _tone(1320, 0.12, { at: 0.07 }); _buzz(80); },
      cancel() { stop(); _tone(300, 0.06, { vol: 0.05 }); },
    };
  },
};

// ── Run commands — one wiring for every button that sends one ─────────
// ``run()`` does the command (gate, send, refresh). It returns false
// when the operator backed out (a canceled confirm): the button comes
// back as it was, nothing sounds. It throws when the command failed:
// the error buzz, a toast, the button back. Otherwise the done tone;
// the button stays disabled until the next status render sets it.
// Park and Kill go through holdToActivate (the hold is their press);
// every other command is a tap.
export function wireCommand(btn, verb, run) {
  const go = async (pressed) => {
    if (pressed) feedback.press();
    btn.disabled = true;
    btn.classList.add("cmd-pressed");
    setTimeout(() => btn.classList.remove("cmd-pressed"), 400);
    try {
      if (await run() === false) { btn.disabled = false; return; }
      feedback.ok();
    } catch (err) {
      feedback.err();
      toast(String(err?.message || err), "bad");
      btn.disabled = false;
    }
  };
  if (verb === "park" || verb === "kill") holdToActivate(btn, () => go(false), { verb });
  else btn.addEventListener("click", (e) => { e.preventDefault(); if (!btn.disabled) go(true); });
}

// ── Hold-to-activate ──────────────────────────────────────────────────
// Park and Kill are never a click. The operator presses and HOLDS the
// button for HOLD_MS; the button fills while they do (style.css .hold)
// and the command fires when the fill completes. Releasing, leaving or
// blurring before that cancels, fill and all. One grammar for every
// surface that shows those two buttons: dashboard cards, the workspace
// page, the pendant. Keyboard: hold Space or Enter the same way.
export const HOLD_MS = 2000;
export function holdToActivate(btn, onActivate, { ms = HOLD_MS, verb } = {}) {
  if (!btn || btn.dataset.holdWired) return;
  btn.dataset.holdWired = "1";
  btn.classList.add("hold");
  btn.style.setProperty("--hold-ms", `${ms}ms`);
  // The sweep's colour is the COMMAND's, whatever the button looks
  // like: red for kill, the warn orange for park (style.css swaps it
  // to white on the filled variants, where those colours vanish).
  if (verb === "kill" || verb === "park") btn.classList.add(`hold-${verb}`);
  const what = verb || btn.textContent.trim().toLowerCase();
  btn.title = `Hold ${Math.round(ms / 1000)} s to ${what}`;
  btn.setAttribute("aria-label", `${btn.textContent.trim()} — hold ${Math.round(ms / 1000)} seconds`);
  let timer = null;
  let held = null;           // feedback.hold — the sound of the hold
  const arm = (e) => {
    if (btn.disabled || timer) return;
    if (e.type === "pointerdown" && e.button !== 0) return;
    if (e.type === "keydown" && (e.repeat || !(e.key === " " || e.key === "Enter"))) return;
    e.preventDefault();
    btn.classList.add("holding");
    held = feedback.hold(ms);
    timer = setTimeout(async () => {
      timer = null;
      held.fire();
      held = null;
      btn.classList.remove("holding");
      btn.classList.add("hold-fired");
      setTimeout(() => btn.classList.remove("hold-fired"), 400);
      try { await onActivate(); } catch (err) { console.error(`hold-to-activate ${what}:`, err); }
    }, ms);
  };
  const disarm = () => {
    if (timer) { clearTimeout(timer); timer = null; }
    if (held) { held.cancel(); held = null; }
    btn.classList.remove("holding");
  };
  btn.addEventListener("pointerdown", arm);
  for (const t of ["pointerup", "pointercancel", "pointerleave"]) btn.addEventListener(t, disarm);
  btn.addEventListener("keydown", arm);
  btn.addEventListener("keyup", disarm);
  btn.addEventListener("blur", disarm);
  // A plain click never fires the command — the hold is the only path.
  btn.addEventListener("click", (e) => { e.preventDefault(); e.stopImmediatePropagation(); }, true);
}


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
