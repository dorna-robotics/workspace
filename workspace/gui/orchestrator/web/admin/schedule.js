// Schedule view — clean Gantt driven by explicit action_start / action_end
// events. Predicted durations are layout-only; "where are we right now" is
// answered exclusively by the framework's start/end signals, never by
// wall-clock arithmetic against a predicted duration.
//
// What's on screen, by design:
//   * one rectangle per scheduled action, positioned by predicted start_t
//   * each rectangle's STATE comes from events:
//       pending — not yet started
//       running — action_start fired, action_end has not
//       done    — action_end fired (success or skipped)
//   * the glowing running block IS the "now" indicator; no separate cursor.
//   * tool swaps shown as thin dividers on the robot row, not blocks.
//   * resource rows on the left, subtle time ticks at the top.

let _ws = null;
let _wsUrl = "";
let _wsClosed = false;
let _wsRetryMs = 1000;

let _plan = null;
let _replanId = 0;
const _leafState = new Map();   // leaf_name -> "pending" | "running" | "done" | "skipped"

let _summaryEl = null;
let _modalEl = null;
let _ganttEl = null;

// ── tool colors (dark-theme accessible) ────────────────────────────────
const TOOL_COLORS = {
  gripper:      "#4f9cf9",
  needle:       "#3ec46d",
  gripper_2ml:  "#b384ff",
  feeder_tool:  "#ffa84a",
  null:         "#27a3a3",
};
function toolColor(t) {
  return TOOL_COLORS[t ?? "null"] || "#7f8694";
}

// ── public entrypoint ──────────────────────────────────────────────────
export function connectScheduleWS(runtimeUrl) {
  const url = runtimeUrl.replace(/^http/, "ws") + "/ws/schedule";
  if (_ws && _wsUrl === url) return;
  disconnectScheduleWS();
  _wsUrl = url;
  _wsClosed = false;
  _wsRetryMs = 1000;
  _initDOM();
  _tryWS();
}

export function disconnectScheduleWS() {
  _wsClosed = true;
  if (_ws) { try { _ws.close(); } catch {} _ws = null; }
  _wsUrl = "";
}

function _tryWS() {
  if (_wsClosed || !_wsUrl) return;
  const ws = new WebSocket(_wsUrl);
  _ws = ws;
  ws.onopen = () => { _wsRetryMs = 1000; };
  ws.onmessage = (e) => {
    try { _ingest(JSON.parse(e.data)); } catch {}
  };
  ws.onclose = () => {
    if (_wsClosed) return;
    setTimeout(_tryWS, _wsRetryMs);
    _wsRetryMs = Math.min(_wsRetryMs * 1.5, 8000);
  };
  ws.onerror = () => ws.close();
}

// ── event handling ─────────────────────────────────────────────────────
function _ingest(msg) {
  if (msg.type === "schedule") {
    _plan = msg;
    _replanId = msg.replan_id || 0;
    _leafState.clear();
    _render();
  } else if (msg.type === "action_start" || msg.type === "swap_start") {
    _leafState.set(msg.name, "running");
    _render();
  } else if (msg.type === "action_end" || msg.type === "swap_end") {
    _leafState.set(msg.name, msg.skipped ? "skipped" : "done");
    _render();
  }
}

// ── DOM wiring ─────────────────────────────────────────────────────────
function _initDOM() {
  _summaryEl = document.getElementById("scheduleSummary");
  _modalEl   = document.getElementById("scheduleModalOverlay");
  _ganttEl   = document.getElementById("ganttContainer");

  const openBtn  = document.getElementById("btnOpenSchedule");
  const closeBtn = document.getElementById("btnScheduleClose");
  if (openBtn && !openBtn._wired) {
    openBtn.addEventListener("click", openScheduleModal);
    openBtn._wired = true;
  }
  if (closeBtn && !closeBtn._wired) {
    closeBtn.addEventListener("click", closeScheduleModal);
    closeBtn._wired = true;
  }
  if (_modalEl && !_modalEl._wired) {
    _modalEl.addEventListener("click", (e) => {
      if (e.target === _modalEl) closeScheduleModal();
    });
    _modalEl._wired = true;
  }
}

export function openScheduleModal() {
  _initDOM();
  if (!_modalEl) return;
  _modalEl.classList.add("show");
  _renderGantt();
}

export function closeScheduleModal() {
  if (!_modalEl) return;
  _modalEl.classList.remove("show");
}

// ── render ─────────────────────────────────────────────────────────────
function _render() {
  _renderSummary();
  if (_modalEl?.classList.contains("show")) _renderGantt();
}

function _renderSummary() {
  if (!_summaryEl) return;
  if (!_plan) { _summaryEl.textContent = "—"; return; }
  const total  = (_plan.actions || []).length;
  const done   = [..._leafState.values()].filter(v => v === "done" || v === "skipped").length;
  const active = [..._leafState.entries()].find(([, v]) => v === "running")?.[0];
  let bits = `<span class="sched-replan">#${_replanId}</span>`;
  if (active) {
    bits += ` <span class="sched-active">${_short(active)}</span>`;
  } else if (done >= total && total > 0) {
    bits += ` <span class="sched-done">complete</span>`;
  } else {
    bits += ` ${done}/${total}`;
  }
  _summaryEl.innerHTML = bits;
}

function _short(leafName) {
  // "inspected(t0)" → "inspected(0)"
  return _esc(leafName.replace("(t", "(").replace(")", ")"));
}

function _esc(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;" }[c]));
}

// ── SVG Gantt ──────────────────────────────────────────────────────────
function _renderGantt() {
  if (!_ganttEl) return;
  if (!_plan) {
    _ganttEl.innerHTML = `<div class="sched-empty">No plan yet.</div>`;
    return;
  }

  const actions = _plan.actions || [];
  const swaps   = _plan.swaps   || [];
  const tres    = _plan.tool_resource || "robot";

  // Resource rows: tool resource on top, then every other declared
  // resource alphabetically.
  const resSet = new Set([tres]);
  for (const a of actions) for (const r of (a.resources || [])) resSet.add(r);
  const rows = [...resSet];
  rows.sort((a, b) => (a === tres ? -1 : b === tres ? 1 : a.localeCompare(b)));

  // Layout.
  const ROW_H   = 28;
  const HEAD_H  = 22;
  const LEFT_W  = 92;
  const PAD     = 14;
  const horizon = Math.max(_plan.makespan || 0, 30);
  const avail   = Math.max(420, _ganttEl.clientWidth - PAD * 2 - LEFT_W);
  const pxPerSec = avail / horizon;
  const W = LEFT_W + horizon * pxPerSec + 24;
  const H = HEAD_H + rows.length * ROW_H + 12;

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width",  String(W));
  svg.setAttribute("height", String(H));
  svg.setAttribute("class",  "sched-svg");

  // Subtle row stripes.
  rows.forEach((r, i) => {
    const rect = document.createElementNS(svgNS, "rect");
    rect.setAttribute("x", "0");
    rect.setAttribute("y", String(HEAD_H + i * ROW_H));
    rect.setAttribute("width",  String(W));
    rect.setAttribute("height", String(ROW_H));
    rect.setAttribute("class",  `sched-row ${i % 2 ? "alt" : ""}`);
    svg.appendChild(rect);
  });

  // Resource labels.
  rows.forEach((r, i) => {
    const t = document.createElementNS(svgNS, "text");
    t.setAttribute("x", String(LEFT_W - 10));
    t.setAttribute("y", String(HEAD_H + i * ROW_H + ROW_H / 2 + 4));
    t.setAttribute("text-anchor", "end");
    t.setAttribute("class", "sched-rowlabel");
    t.textContent = r;
    svg.appendChild(t);
  });

  // Time axis ticks.
  const step = _chooseTickStep(horizon, pxPerSec);
  for (let s = 0; s <= horizon; s += step) {
    const x = LEFT_W + s * pxPerSec;
    const l = document.createElementNS(svgNS, "line");
    l.setAttribute("x1", String(x)); l.setAttribute("x2", String(x));
    l.setAttribute("y1", String(HEAD_H - 4));
    l.setAttribute("y2", String(H - 4));
    l.setAttribute("class", "sched-tick");
    svg.appendChild(l);
    if (s % (step * 2) === 0) {
      const t = document.createElementNS(svgNS, "text");
      t.setAttribute("x", String(x));
      t.setAttribute("y", String(HEAD_H - 8));
      t.setAttribute("class", "sched-ticklabel");
      t.setAttribute("text-anchor", "middle");
      t.textContent = _fmtDur(s);
      svg.appendChild(t);
    }
  }

  // Action blocks.
  for (const a of actions) {
    const rowIdx = _primaryRow(rows, a.resources, tres);
    if (rowIdx < 0) continue;
    const x = LEFT_W + a.start_t * pxPerSec;
    const w = Math.max(3, a.duration * pxPerSec);
    const y = HEAD_H + rowIdx * ROW_H + 5;
    const h = ROW_H - 10;
    const state = _leafState.get(a.leaf_name) || "pending";

    const g = document.createElementNS(svgNS, "g");
    g.setAttribute("class", `sched-block sched-${state}`);

    const rect = document.createElementNS(svgNS, "rect");
    rect.setAttribute("x", String(x));
    rect.setAttribute("y", String(y));
    rect.setAttribute("width",  String(w));
    rect.setAttribute("height", String(h));
    rect.setAttribute("rx", "4");
    rect.setAttribute("fill", toolColor(a.tool));
    g.appendChild(rect);

    if (w > 36) {
      const lab = document.createElementNS(svgNS, "text");
      lab.setAttribute("x", String(x + w / 2));
      lab.setAttribute("y", String(y + h / 2 + 3));
      lab.setAttribute("text-anchor", "middle");
      lab.setAttribute("class", "sched-blocklabel");
      lab.textContent = `${a.name}(${a.item})`;
      g.appendChild(lab);
    }

    const title = document.createElementNS(svgNS, "title");
    title.textContent =
      `${a.name}(${a.item}) — ${state}\n` +
      `tool: ${a.tool ?? "—"}\n` +
      `planned: t=${_fmtDur(a.start_t)} for ${_fmtDur(a.duration)}`;
    g.appendChild(title);
    svg.appendChild(g);
  }

  // Tool swaps — narrow ticks on the tool_resource row, not full blocks.
  const swapRow = rows.indexOf(tres);
  if (swapRow >= 0) {
    for (const s of swaps) {
      const x = LEFT_W + s.start_t * pxPerSec;
      const w = Math.max(2, s.duration * pxPerSec);
      const y = HEAD_H + swapRow * ROW_H + 5;
      const h = ROW_H - 10;
      const state = _leafState.get(s.leaf_name) || "pending";
      const r = document.createElementNS(svgNS, "rect");
      r.setAttribute("x", String(x));
      r.setAttribute("y", String(y));
      r.setAttribute("width",  String(w));
      r.setAttribute("height", String(h));
      r.setAttribute("rx", "2");
      r.setAttribute("class", `sched-swap sched-${state}`);
      const title = document.createElementNS(svgNS, "title");
      title.textContent = `swap ${s.from ?? "∅"} → ${s.to ?? "∅"} — ${state}`;
      r.appendChild(title);
      svg.appendChild(r);
    }
  }

  _ganttEl.innerHTML = "";
  _ganttEl.appendChild(svg);
}

function _primaryRow(rows, resources, toolRes) {
  if (!resources || !resources.length) return rows.indexOf(toolRes);
  for (const r of resources) {
    if (r !== toolRes && rows.includes(r)) return rows.indexOf(r);
  }
  return rows.indexOf(resources[0]);
}

function _chooseTickStep(horizon, pxPerSec) {
  const target = 70 / pxPerSec;
  for (const cand of [5, 10, 15, 30, 60, 120, 300, 600]) {
    if (cand >= target) return cand;
  }
  return 600;
}

function _fmtDur(secs) {
  if (secs < 60) return `${secs.toFixed(0)}s`;
  if (secs < 3600) return `${(secs / 60).toFixed(1)}m`;
  return `${(secs / 3600).toFixed(2)}h`;
}
