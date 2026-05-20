// Schedule view — clean Gantt with uniform light blocks and a flashing
// border for the currently-running action. Block widths auto-fit the
// action's class name; "where are we right now" is driven by explicit
// action_start / action_end events from the framework.

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
  const swaps   = _plan.swaps   || [];
  const tres    = _plan.tool_resource || "robot";

  // Resource rows: tool resource on top, the rest alphabetical.
  const resSet = new Set([tres]);
  for (const a of actions) for (const r of (a.resources || [])) resSet.add(r);
  const rows = [...resSet];
  rows.sort((a, b) => (a === tres ? -1 : b === tres ? 1 : a.localeCompare(b)));

  // ── Auto-fit pxPerSec so every block is wide enough for its label ──
  // For each action, compute the min width needed for its label, derive
  // the pxPerSec that would give that. Take the global max so every
  // block fits.
  const FONT_PX = 14;
  const CHAR_W = FONT_PX * 0.62;   // approx for the monospace label
  const PAD_X = 28;                // 14 each side inside the block
  function labelOf(a) { return `${a.class_name || a.name}(${a.item})`; }
  function neededWidth(a) { return labelOf(a).length * CHAR_W + PAD_X; }

  const ROW_H   = 60;
  const ROW_PAD = 12;
  const LEFT_W  = 150;
  const TOP_PAD = 18;
  const BOT_PAD = 18;
  const SIDE_PAD = 24;
  const horizon = Math.max(_plan.makespan || 0, 30);

  // Minimum pxPerSec needed so each block fits its label.
  let minPxPerSec = 6;
  for (const a of actions) {
    if (a.duration > 0) {
      const ratio = neededWidth(a) / a.duration;
      if (ratio > minPxPerSec) minPxPerSec = ratio;
    }
  }
  // Use the larger of "fill the container" and "fit labels".
  const containerAvail = Math.max(420, _ganttEl.clientWidth - 2 * SIDE_PAD - LEFT_W);
  const fillPxPerSec = containerAvail / horizon;
  const pxPerSec = Math.max(fillPxPerSec, minPxPerSec);

  const W = LEFT_W + horizon * pxPerSec + 24;
  const H = TOP_PAD + rows.length * ROW_H + BOT_PAD;

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width",  String(W));
  svg.setAttribute("height", String(H));
  svg.setAttribute("class",  "sched-svg");

  // Row labels + dividers
  rows.forEach((r, i) => {
    const t = document.createElementNS(svgNS, "text");
    t.setAttribute("x", String(LEFT_W - 14));
    t.setAttribute("y", String(TOP_PAD + i * ROW_H + ROW_H / 2 + 5));
    t.setAttribute("text-anchor", "end");
    t.setAttribute("class", "sched-rowlabel");
    t.textContent = r;
    svg.appendChild(t);
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
    const w = Math.max(neededWidth(a), a.duration * pxPerSec);
    const y = TOP_PAD + rowIdx * ROW_H + ROW_PAD;
    const h = ROW_H - ROW_PAD * 2;
    _appendBlock(svg, x, y, w, h, a, _leafState.get(a.leaf_name) || "pending");
  }

  // Tool swaps — slim ticks on robot row
  const swapRow = rows.indexOf(tres);
  if (swapRow >= 0) {
    for (const s of swaps) {
      const x = LEFT_W + s.start_t * pxPerSec;
      const w = Math.max(4, s.duration * pxPerSec);
      const y = TOP_PAD + swapRow * ROW_H + ROW_PAD + 8;
      const h = ROW_H - ROW_PAD * 2 - 16;
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
  const g = document.createElementNS(svgNS, "g");
  g.setAttribute("class", `sched-block sched-${state}`);

  // Fill — same light color for every action, opacity changes per state.
  const rect = document.createElementNS(svgNS, "rect");
  rect.setAttribute("x", String(x));
  rect.setAttribute("y", String(y));
  rect.setAttribute("width",  String(w));
  rect.setAttribute("height", String(h));
  rect.setAttribute("rx", "8");
  rect.setAttribute("class", "sched-block-fill");
  g.appendChild(rect);

  // Border ring — flashes when running.
  const border = document.createElementNS(svgNS, "rect");
  border.setAttribute("x", String(x));
  border.setAttribute("y", String(y));
  border.setAttribute("width",  String(w));
  border.setAttribute("height", String(h));
  border.setAttribute("rx", "8");
  border.setAttribute("class", "sched-block-border");
  g.appendChild(border);

  // Label — exactly the class name (PascalCase), with item index in parens.
  const label = `${a.class_name || a.name}(${a.item})`;
  const lab = document.createElementNS(svgNS, "text");
  lab.setAttribute("x", String(x + w / 2));
  lab.setAttribute("y", String(y + h / 2 + 5));
  lab.setAttribute("text-anchor", "middle");
  lab.setAttribute("class", "sched-blocklabel");
  lab.textContent = label;
  g.appendChild(lab);

  const title = document.createElementNS(svgNS, "title");
  title.textContent = `${label} — ${state}\ntool: ${a.tool ?? "—"}`;
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
