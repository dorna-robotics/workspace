// Live BT schedule view — minimal Apple-flavoured Gantt. Resource rows,
// uniformly-styled action cards (one per planned action), thin connector
// lines for tool swaps. Currently-running card pulses with an accent
// glow. Everything is driven by explicit framework events
// (action_start / action_end) so reality and visuals stay in sync even
// when predicted durations drift.

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
  ws.onmessage = (e) => { try { _ingest(JSON.parse(e.data)); } catch {} };
  ws.onclose = () => {
    if (_wsClosed) return;
    setTimeout(_tryWS, _wsRetryMs);
    _wsRetryMs = Math.min(_wsRetryMs * 1.5, 8000);
  };
  ws.onerror = () => ws.close();
}

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

function _initDOM() {
  _summaryEl = document.getElementById("scheduleSummary");
  _modalEl   = document.getElementById("scheduleModalOverlay");
  _ganttEl   = document.getElementById("ganttContainer");
  const openBtn  = document.getElementById("btnOpenSchedule");
  const closeBtn = document.getElementById("btnScheduleClose");
  if (openBtn && !openBtn._wired)  { openBtn.addEventListener("click", openScheduleModal); openBtn._wired = true; }
  if (closeBtn && !closeBtn._wired) { closeBtn.addEventListener("click", closeScheduleModal); closeBtn._wired = true; }
  if (_modalEl && !_modalEl._wired) {
    _modalEl.addEventListener("click", (e) => { if (e.target === _modalEl) closeScheduleModal(); });
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
  const tres    = _plan.tool_resource || "robot";

  // Resource rows: tool resource on top, the rest alphabetical.
  const resSet = new Set([tres]);
  for (const a of actions) for (const r of (a.resources || [])) resSet.add(r);
  const rows = [...resSet];
  rows.sort((a, b) => (a === tres ? -1 : b === tres ? 1 : a.localeCompare(b)));

  // Auto-fit pxPerSec — each block must be wide enough for its label.
  const FONT_PX = 14;
  const CHAR_W = FONT_PX * 0.62;
  const PAD_X = 36;
  function labelOf(a) { return `${a.class_name || a.name}(${a.item})`; }
  function neededWidth(a) { return labelOf(a).length * CHAR_W + PAD_X; }

  const ROW_H   = 68;
  const ROW_PAD = 14;
  const LEFT_W  = 160;
  const TOP_PAD = 22;
  const BOT_PAD = 22;
  const SIDE_PAD = 28;
  const horizon = Math.max(_plan.makespan || 0, 30);

  let minPxPerSec = 6;
  for (const a of actions) {
    if (a.duration > 0) {
      const ratio = neededWidth(a) / a.duration;
      if (ratio > minPxPerSec) minPxPerSec = ratio;
    }
  }
  const containerAvail = Math.max(420, _ganttEl.clientWidth - 2 * SIDE_PAD - LEFT_W);
  const fillPxPerSec = containerAvail / horizon;
  const pxPerSec = Math.max(fillPxPerSec, minPxPerSec);

  const W = LEFT_W + horizon * pxPerSec + 32;
  const H = TOP_PAD + rows.length * ROW_H + BOT_PAD;

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width",  String(W));
  svg.setAttribute("height", String(H));
  svg.setAttribute("class",  "sched-svg");

  // Layered <g> elements so paint order is deterministic.
  const gRows = document.createElementNS(svgNS, "g");
  const gConn = document.createElementNS(svgNS, "g");
  const gBlocks = document.createElementNS(svgNS, "g");
  svg.appendChild(gRows);
  svg.appendChild(gConn);
  svg.appendChild(gBlocks);

  // Row labels + dividers — clean Apple-y rhythm.
  rows.forEach((r, i) => {
    const t = document.createElementNS(svgNS, "text");
    t.setAttribute("x", String(LEFT_W - 18));
    t.setAttribute("y", String(TOP_PAD + i * ROW_H + ROW_H / 2 + 4));
    t.setAttribute("text-anchor", "end");
    t.setAttribute("class", "sched-rowlabel");
    t.textContent = r;
    gRows.appendChild(t);
    if (i > 0) {
      const line = document.createElementNS(svgNS, "line");
      line.setAttribute("x1", String(LEFT_W - 8));
      line.setAttribute("x2", String(W - 16));
      line.setAttribute("y1", String(TOP_PAD + i * ROW_H));
      line.setAttribute("y2", String(TOP_PAD + i * ROW_H));
      line.setAttribute("class", "sched-rowline");
      gRows.appendChild(line);
    }
  });

  // Per-row layout records — used both for blocks and connector lines.
  const placements = new Map();   // leaf_name -> {x, w, y, h, rowIdx}
  for (const a of actions) {
    const rowIdx = _primaryRow(rows, a.resources, tres);
    if (rowIdx < 0) continue;
    const x = LEFT_W + a.start_t * pxPerSec;
    const w = Math.max(neededWidth(a), a.duration * pxPerSec);
    const y = TOP_PAD + rowIdx * ROW_H + ROW_PAD;
    const h = ROW_H - ROW_PAD * 2;
    placements.set(a.leaf_name, { a, x, w, y, h, rowIdx });
  }

  // Connector lines: subtle 1px lines on the tool-resource row that
  // bridge consecutive blocks across the gap reserved for a tool swap.
  // Replaces the previous "grey block" rendering for swaps.
  const robotActions = actions
    .filter(a => (a.resources || []).includes(tres))
    .sort((x, y) => x.start_t - y.start_t);
  for (let i = 0; i + 1 < robotActions.length; i++) {
    const pa = placements.get(robotActions[i].leaf_name);
    const pb = placements.get(robotActions[i + 1].leaf_name);
    if (!pa || !pb) continue;
    if (pa.x + pa.w >= pb.x - 2) continue;          // no gap → no line
    const y = pa.y + pa.h / 2;
    const stateA = _leafState.get(robotActions[i].leaf_name) || "pending";
    const stateB = _leafState.get(robotActions[i + 1].leaf_name) || "pending";
    const cls = (stateA === "done" || stateA === "skipped")
      ? (stateB === "running" || stateB === "done" || stateB === "skipped"
          ? "sched-connector done" : "sched-connector half")
      : "sched-connector";
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", String(pa.x + pa.w));
    line.setAttribute("x2", String(pb.x));
    line.setAttribute("y1", String(y));
    line.setAttribute("y2", String(y));
    line.setAttribute("class", cls);
    gConn.appendChild(line);
  }

  // Action blocks.
  for (const a of actions) {
    const p = placements.get(a.leaf_name);
    if (!p) continue;
    const state = _leafState.get(a.leaf_name) || "pending";
    _appendBlock(gBlocks, p.x, p.y, p.w, p.h, a, state);
  }

  _ganttEl.innerHTML = "";
  _ganttEl.appendChild(svg);
}

function _appendBlock(parent, x, y, w, h, a, state) {
  const svgNS = "http://www.w3.org/2000/svg";
  const g = document.createElementNS(svgNS, "g");
  g.setAttribute("class", `sched-block sched-${state}`);

  const rect = document.createElementNS(svgNS, "rect");
  rect.setAttribute("x", String(x));
  rect.setAttribute("y", String(y));
  rect.setAttribute("width",  String(w));
  rect.setAttribute("height", String(h));
  rect.setAttribute("rx", "12");
  rect.setAttribute("class", "sched-block-fill");
  g.appendChild(rect);

  const border = document.createElementNS(svgNS, "rect");
  border.setAttribute("x", String(x));
  border.setAttribute("y", String(y));
  border.setAttribute("width",  String(w));
  border.setAttribute("height", String(h));
  border.setAttribute("rx", "12");
  border.setAttribute("class", "sched-block-border");
  g.appendChild(border);

  const label = `${a.class_name || a.name}(${a.item})`;
  const lab = document.createElementNS(svgNS, "text");
  lab.setAttribute("x", String(x + w / 2));
  lab.setAttribute("y", String(y + h / 2 + 5));
  lab.setAttribute("text-anchor", "middle");
  lab.setAttribute("class", "sched-blocklabel");
  lab.textContent = label;
  g.appendChild(lab);

  const title = document.createElementNS(svgNS, "title");
  title.textContent = `${label} — ${state}`;
  g.appendChild(title);

  parent.appendChild(g);
}

function _primaryRow(rows, resources, toolRes) {
  if (!resources || !resources.length) return rows.indexOf(toolRes);
  for (const r of resources) {
    if (r !== toolRes && rows.includes(r)) return rows.indexOf(r);
  }
  return rows.indexOf(resources[0]);
}
