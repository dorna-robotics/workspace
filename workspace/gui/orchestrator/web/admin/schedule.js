// Schedule view — clean Gantt driven by explicit action_start / action_end
// events. No time axis, no wall-clock math; the layout is read-only and
// "where are we right now" is answered by the currently-running block's
// flashing border.

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
  ws.onmessage = (e) => { try { _ingest(JSON.parse(e.data)); } catch {} };
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

  // Resource rows: tool resource on top, the rest alphabetical.
  const resSet = new Set([tres]);
  for (const a of actions) for (const r of (a.resources || [])) resSet.add(r);
  const rows = [...resSet];
  rows.sort((a, b) => (a === tres ? -1 : b === tres ? 1 : a.localeCompare(b)));

  // Layout — generous spacing, no time axis.
  const ROW_H  = 56;
  const ROW_PAD = 10;
  const LEFT_W = 140;
  const TOP_PAD = 16;
  const BOT_PAD = 16;
  const horizon = Math.max(_plan.makespan || 0, 30);
  const avail = Math.max(420, _ganttEl.clientWidth - 40 - LEFT_W);
  const pxPerSec = avail / horizon;
  const W = LEFT_W + horizon * pxPerSec + 24;
  const H = TOP_PAD + rows.length * ROW_H + BOT_PAD;

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width",  String(W));
  svg.setAttribute("height", String(H));
  svg.setAttribute("class",  "sched-svg");

  // Row labels (left gutter)
  rows.forEach((r, i) => {
    const t = document.createElementNS(svgNS, "text");
    t.setAttribute("x", String(LEFT_W - 14));
    t.setAttribute("y", String(TOP_PAD + i * ROW_H + ROW_H / 2 + 5));
    t.setAttribute("text-anchor", "end");
    t.setAttribute("class", "sched-rowlabel");
    t.textContent = r;
    svg.appendChild(t);

    // Row divider line (thin, subtle)
    if (i > 0) {
      const line = document.createElementNS(svgNS, "line");
      line.setAttribute("x1", String(LEFT_W - 4));
      line.setAttribute("x2", String(W - 8));
      line.setAttribute("y1", String(TOP_PAD + i * ROW_H));
      line.setAttribute("y2", String(TOP_PAD + i * ROW_H));
      line.setAttribute("class", "sched-rowline");
      svg.appendChild(line);
    }
  });

  // Action blocks
  for (const a of actions) {
    const rowIdx = _primaryRow(rows, a.resources, tres);
    if (rowIdx < 0) continue;
    const x = LEFT_W + a.start_t * pxPerSec;
    const w = Math.max(6, a.duration * pxPerSec);
    const y = TOP_PAD + rowIdx * ROW_H + ROW_PAD;
    const h = ROW_H - ROW_PAD * 2;
    _appendBlock(svg, x, y, w, h, a, _leafState.get(a.leaf_name) || "pending");
  }

  // Tool swaps — narrow neutral ticks on the robot row
  const swapRow = rows.indexOf(tres);
  if (swapRow >= 0) {
    for (const s of swaps) {
      const x = LEFT_W + s.start_t * pxPerSec;
      const w = Math.max(3, s.duration * pxPerSec);
      const y = TOP_PAD + swapRow * ROW_H + ROW_PAD + 6;
      const h = ROW_H - ROW_PAD * 2 - 12;
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

function _appendBlock(svg, x, y, w, h, a, state) {
  const svgNS = "http://www.w3.org/2000/svg";

  // Unique clipPath ID so labels can never escape their block.
  const clipId = `clip_${a.leaf_name.replace(/[^a-z0-9]/gi, "_")}`;

  let defs = svg.querySelector("defs");
  if (!defs) {
    defs = document.createElementNS(svgNS, "defs");
    svg.insertBefore(defs, svg.firstChild);
  }
  const clip = document.createElementNS(svgNS, "clipPath");
  clip.id = clipId;
  const clipRect = document.createElementNS(svgNS, "rect");
  clipRect.setAttribute("x", String(x + 8));
  clipRect.setAttribute("y", String(y));
  clipRect.setAttribute("width",  String(Math.max(0, w - 16)));
  clipRect.setAttribute("height", String(h));
  clip.appendChild(clipRect);
  defs.appendChild(clip);

  const g = document.createElementNS(svgNS, "g");
  g.setAttribute("class", `sched-block sched-${state}`);

  const rect = document.createElementNS(svgNS, "rect");
  rect.setAttribute("x", String(x));
  rect.setAttribute("y", String(y));
  rect.setAttribute("width",  String(w));
  rect.setAttribute("height", String(h));
  rect.setAttribute("rx", "6");
  rect.setAttribute("fill", toolColor(a.tool));
  rect.setAttribute("class", "sched-block-fill");
  g.appendChild(rect);

  // Border overlay — for running state, this gets the flashing animation.
  const border = document.createElementNS(svgNS, "rect");
  border.setAttribute("x", String(x));
  border.setAttribute("y", String(y));
  border.setAttribute("width",  String(w));
  border.setAttribute("height", String(h));
  border.setAttribute("rx", "6");
  border.setAttribute("class", "sched-block-border");
  g.appendChild(border);

  const lab = document.createElementNS(svgNS, "text");
  lab.setAttribute("x", String(x + w / 2));
  lab.setAttribute("y", String(y + h / 2 + 5));
  lab.setAttribute("text-anchor", "middle");
  lab.setAttribute("class", "sched-blocklabel");
  lab.setAttribute("clip-path", `url(#${clipId})`);
  lab.textContent = `${a.name}(${a.item})`;
  g.appendChild(lab);

  const title = document.createElementNS(svgNS, "title");
  title.textContent = `${a.name}(${a.item}) — ${state}\ntool: ${a.tool ?? "—"}`;
  g.appendChild(title);

  svg.appendChild(g);
}

function _primaryRow(rows, resources, toolRes) {
  if (!resources || !resources.length) return rows.indexOf(toolRes);
  for (const r of resources) {
    if (r !== toolRes && rows.includes(r)) return rows.indexOf(r);
  }
  return rows.indexOf(resources[0]);
}
