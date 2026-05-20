// Live BT schedule view — clean flow Gantt.
//
// Each row lays its blocks out side-by-side with a fixed gap, sized to
// fit the full action name. Cross-row alignment respects start-time
// order (a shaker block lands underneath the robot action that
// triggered it). Consecutive blocks on the same row are joined by a
// thin connector line. State is driven by explicit framework events.

let _ws = null;
let _wsUrl = "";
let _wsClosed = false;
let _wsRetryMs = 1000;

let _plan = null;
const _leafState = new Map();   // leaf_name -> "pending" | "running" | "done" | "skipped"

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
  _modalEl = document.getElementById("scheduleModalOverlay");
  _ganttEl = document.getElementById("ganttContainer");
  const closeBtn = document.getElementById("btnScheduleClose");
  if (closeBtn && !closeBtn._wired) {
    closeBtn.addEventListener("click", closeScheduleModal);
    closeBtn._wired = true;
  }
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
  if (_modalEl?.classList.contains("show")) _renderGantt();
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

  // ── Layout knobs ───────────────────────────────────────────────────
  const FONT_PX   = 12;
  const CHAR_W    = FONT_PX * 0.62;
  const LABEL_PAD = 22;       // 11 px each side inside the block
  const BLOCK_GAP = 18;       // fixed visual gap between consecutive blocks
  const ROW_H     = 48;
  const ROW_PAD   = 8;
  const LEFT_W    = 120;
  const TOP_PAD   = 18;
  const BOT_PAD   = 18;

  function labelOf(a)     { return `${a.class_name || a.name}(${a.item})`; }
  function neededWidth(a) { return labelOf(a).length * CHAR_W + LABEL_PAD; }

  // ── Phase 1: place each row's blocks flow-left-to-right ───────────
  const placements = new Map(); // leaf_name -> {a, x, w, y, h, rowIdx}
  for (let rowIdx = 0; rowIdx < rows.length; rowIdx++) {
    const arr = actions
      .filter(a => _primaryRow(rows, a.resources, tres) === rowIdx)
      .sort((x, y) => x.start_t - y.start_t);
    let cursor = LEFT_W + 8;
    for (const a of arr) {
      const w = neededWidth(a);
      const y = TOP_PAD + rowIdx * ROW_H + ROW_PAD;
      const h = ROW_H - ROW_PAD * 2;
      placements.set(a.leaf_name, { a, x: cursor, w, y, h, rowIdx });
      cursor += w + BLOCK_GAP;
    }
  }

  // ── Phase 2: cross-row alignment ───────────────────────────────────
  // For each non-robot row, shift its blocks so they sit AFTER the
  // robot block whose end-of-execution they follow. Aligning to the
  // anchor's right edge (rather than its left) makes "ShakerOne
  // starts after LoadedShaker(0) finishes" read correctly — the
  // shaker block sits visually to the right of its trigger.
  const robotRowIdx = rows.indexOf(tres);
  if (robotRowIdx >= 0) {
    const robotByStart = [...placements.values()]
      .filter(p => p.rowIdx === robotRowIdx)
      .sort((a, b) => a.a.start_t - b.a.start_t);
    for (const p of placements.values()) {
      if (p.rowIdx === robotRowIdx) continue;
      // Anchor = the latest robot block whose end <= this block's start.
      let anchor = null;
      for (const rp of robotByStart) {
        const rpEnd = rp.a.start_t + rp.a.duration;
        if (rpEnd > p.a.start_t) break;
        anchor = rp;
      }
      if (anchor) p.x = anchor.x + anchor.w + 8;
    }
  }

  // Compute SVG dimensions from the actual placements.
  let maxRight = LEFT_W + 200;
  for (const p of placements.values()) {
    if (p.x + p.w > maxRight) maxRight = p.x + p.w;
  }
  const W = maxRight + 16;
  const H = TOP_PAD + rows.length * ROW_H + BOT_PAD;

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width",  String(W));
  svg.setAttribute("height", String(H));
  svg.setAttribute("class",  "sched-svg");

  const gRows = document.createElementNS(svgNS, "g");
  const gConn = document.createElementNS(svgNS, "g");
  const gBlocks = document.createElementNS(svgNS, "g");
  svg.appendChild(gRows);
  svg.appendChild(gConn);
  svg.appendChild(gBlocks);

  // Row labels + dividers
  rows.forEach((r, i) => {
    const t = document.createElementNS(svgNS, "text");
    t.setAttribute("x", String(LEFT_W - 14));
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

  // Connector lines — one short blue dashed line between every pair of
  // consecutive blocks on the same row.
  for (let rowIdx = 0; rowIdx < rows.length; rowIdx++) {
    const arr = [...placements.values()]
      .filter(p => p.rowIdx === rowIdx)
      .sort((a, b) => a.x - b.x);
    for (let i = 0; i + 1 < arr.length; i++) {
      const A = arr[i], B = arr[i + 1];
      const x1 = A.x + A.w;
      const x2 = B.x;
      if (x2 - x1 < 4) continue;
      const yMid = A.y + A.h / 2;
      const stA = _leafState.get(A.a.leaf_name) || "pending";
      const stB = _leafState.get(B.a.leaf_name) || "pending";
      const cls = (stA === "done" || stA === "skipped")
        ? "sched-connector done"
        : "sched-connector pending";
      const line = document.createElementNS(svgNS, "line");
      line.setAttribute("x1", String(x1));
      line.setAttribute("x2", String(x2));
      line.setAttribute("y1", String(yMid));
      line.setAttribute("y2", String(yMid));
      line.setAttribute("class", cls);
      gConn.appendChild(line);
    }
  }

  // Action blocks
  for (const p of placements.values()) {
    const state = _leafState.get(p.a.leaf_name) || "pending";
    _appendBlock(gBlocks, p, state);
  }

  _ganttEl.innerHTML = "";
  _ganttEl.appendChild(svg);
}

function _appendBlock(parent, p, state) {
  const svgNS = "http://www.w3.org/2000/svg";
  const { a, x, y, w, h } = p;
  const g = document.createElementNS(svgNS, "g");
  g.setAttribute("class", `sched-block sched-${state}`);

  const rect = document.createElementNS(svgNS, "rect");
  rect.setAttribute("x", String(x));
  rect.setAttribute("y", String(y));
  rect.setAttribute("width",  String(w));
  rect.setAttribute("height", String(h));
  rect.setAttribute("rx", "8");
  rect.setAttribute("class", "sched-block-fill");
  g.appendChild(rect);

  const border = document.createElementNS(svgNS, "rect");
  border.setAttribute("x", String(x));
  border.setAttribute("y", String(y));
  border.setAttribute("width",  String(w));
  border.setAttribute("height", String(h));
  border.setAttribute("rx", "8");
  border.setAttribute("class", "sched-block-border");
  g.appendChild(border);

  const label = `${a.class_name || a.name}(${a.item})`;
  const lab = document.createElementNS(svgNS, "text");
  lab.setAttribute("x", String(x + w / 2));
  lab.setAttribute("y", String(y + h / 2 + 4));
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
