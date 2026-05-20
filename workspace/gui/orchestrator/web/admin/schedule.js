// Schedule view — live BT plan + execution Gantt for one workspace.
//
// Connects to /ws/schedule on the project's runtime server. Maintains
// a small in-memory model of the current plan and the per-leaf
// start/end events that arrive as the BT executes. Renders a compact
// summary into the sidebar; the click-open modal renders a full SVG
// Gantt with predicted vs actual bars and a live "now" cursor.

let _ws = null;
let _wsUrl = "";
let _wsClosed = false;
let _wsRetryMs = 1000;

// Current model
let _plan = null;                  // last `schedule` event payload
let _scheduleStartWall = 0;        // wall_ts at which the schedule began
let _replanId = 0;
const _liveByLeaf = new Map();     // leaf_name -> { startedAt, endedAt, status }

// DOM refs
let _summaryEl = null;
let _modalEl = null;
let _ganttEl = null;
let _modalStatsEl = null;

let _rafHandle = null;

// ── tool colors (dark-theme accessible) ────────────────────────────────
const TOOL_COLORS = {
  gripper:      "#4f9cf9",
  needle:       "#3ec46d",
  gripper_2ml:  "#b384ff",
  feeder_tool:  "#ffa84a",
  null:         "#27a3a3",   // tool-agnostic (shaker, etc.)
};
const SWAP_STRIPE = "#5a5d68";
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
  _startCursorLoop();
}

export function disconnectScheduleWS() {
  _wsClosed = true;
  if (_ws) { try { _ws.close(); } catch {} _ws = null; }
  _wsUrl = "";
  _stopCursorLoop();
}

function _tryWS() {
  if (_wsClosed || !_wsUrl) return;
  const ws = new WebSocket(_wsUrl);
  _ws = ws;
  ws.onopen = () => { _wsRetryMs = 1000; };
  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      _ingest(msg);
    } catch {}
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
    _scheduleStartWall = msg.wall_ts || (Date.now() / 1000);
    _replanId = msg.replan_id || 0;
    _liveByLeaf.clear();
    _render();
  } else if (msg.type === "action_start" || msg.type === "swap_start") {
    const ent = _liveByLeaf.get(msg.name) || {};
    ent.startedAt = msg.wall_ts;
    ent.status = "running";
    _liveByLeaf.set(msg.name, ent);
    _render();
  } else if (msg.type === "action_end" || msg.type === "swap_end") {
    const ent = _liveByLeaf.get(msg.name) || {};
    ent.endedAt = msg.wall_ts;
    ent.status = msg.skipped ? "skipped" : "done";
    ent.branch = msg.branch ?? null;
    _liveByLeaf.set(msg.name, ent);
    _render();
  }
}

// ── DOM wiring ─────────────────────────────────────────────────────────
function _initDOM() {
  _summaryEl     = document.getElementById("scheduleSummary");
  _modalEl       = document.getElementById("scheduleModalOverlay");
  _ganttEl       = document.getElementById("ganttContainer");
  _modalStatsEl  = document.getElementById("scheduleModalStats");

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
  if (!_plan) {
    _summaryEl.textContent = "—";
    return;
  }
  const actionCount = (_plan.actions || []).length;
  const swapCount   = (_plan.swaps   || []).length;
  const makespan    = _plan.makespan || 0;
  let progress = "";
  const running = [..._liveByLeaf.values()].some(e => e.status === "running");
  const doneCount = [..._liveByLeaf.values()].filter(e => e.status === "done").length;
  if (running) {
    const active = [..._liveByLeaf.entries()].find(([, v]) => v.status === "running");
    progress = ` • <span class="sched-active">${_esc(active?.[0] || "")}</span>`;
  } else if (doneCount === actionCount + swapCount && actionCount > 0) {
    progress = " • <span class=\"sched-done\">complete</span>";
  }
  _summaryEl.innerHTML =
    `${actionCount} action${actionCount === 1 ? "" : "s"}` +
    ` • ${_fmtDur(makespan)} makespan` +
    ` • <span class="sched-replan">#${_replanId}</span>${progress}`;
}

function _esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({
    "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"
  }[c]));
}

function _fmtDur(secs) {
  if (secs < 60)  return `${secs.toFixed(0)}s`;
  if (secs < 3600) return `${(secs / 60).toFixed(1)}m`;
  return `${(secs / 3600).toFixed(2)}h`;
}

// ── SVG Gantt ──────────────────────────────────────────────────────────
function _renderGantt() {
  if (!_ganttEl) return;
  if (!_plan) {
    _ganttEl.innerHTML = `<div class="sched-empty">No plan yet — schedule arrives on workspace launch.</div>`;
    _renderModalStats();
    return;
  }
  _renderModalStats();

  const actions = _plan.actions || [];
  const swaps   = _plan.swaps   || [];
  const tres    = _plan.tool_resource || "robot";

  // Collect resource rows: union of every action.resources[] + tool_resource.
  const resourceSet = new Set([tres]);
  for (const a of actions) {
    for (const r of (a.resources || [])) resourceSet.add(r);
  }
  const resourceRows = [...resourceSet];
  // Move tool_resource (robot) to the top.
  resourceRows.sort((a, b) => (a === tres ? -1 : b === tres ? 1 : a.localeCompare(b)));

  // Compute layout dimensions.
  const ROW_H = 36;
  const HEAD_H = 32;
  const LEFT_W = 110;
  const PAD = 12;
  const horizon = Math.max(_plan.makespan || 0, 60);
  const containerWidth = _ganttEl.clientWidth - PAD * 2;
  const minScale = Math.max(0.6, (containerWidth - LEFT_W) / horizon);
  const pxPerSec = Math.max(minScale, 1.0);
  const totalWidth = LEFT_W + horizon * pxPerSec + 60;
  const totalHeight = HEAD_H + resourceRows.length * ROW_H + 16;

  // Build SVG.
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width", String(totalWidth));
  svg.setAttribute("height", String(totalHeight));
  svg.setAttribute("class", "sched-svg");

  // Background row stripes.
  resourceRows.forEach((r, i) => {
    const rect = document.createElementNS(svgNS, "rect");
    rect.setAttribute("x", "0");
    rect.setAttribute("y", String(HEAD_H + i * ROW_H));
    rect.setAttribute("width", String(totalWidth));
    rect.setAttribute("height", String(ROW_H));
    rect.setAttribute("class", `sched-row-bg ${i % 2 ? "alt" : ""}`);
    svg.appendChild(rect);
  });

  // Resource labels (left gutter, sticky-feel via fixed x).
  resourceRows.forEach((r, i) => {
    const text = document.createElementNS(svgNS, "text");
    text.setAttribute("x", String(LEFT_W - 12));
    text.setAttribute("y", String(HEAD_H + i * ROW_H + ROW_H / 2 + 4));
    text.setAttribute("text-anchor", "end");
    text.setAttribute("class", "sched-resource-label");
    text.textContent = r;
    svg.appendChild(text);
  });

  // Time axis at top.
  const tickStep = _chooseTickStep(horizon, pxPerSec);
  for (let t = 0; t <= horizon; t += tickStep) {
    const x = LEFT_W + t * pxPerSec;
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", String(x));
    line.setAttribute("x2", String(x));
    line.setAttribute("y1", String(HEAD_H));
    line.setAttribute("y2", String(totalHeight - 8));
    line.setAttribute("class", "sched-tick");
    svg.appendChild(line);
    const label = document.createElementNS(svgNS, "text");
    label.setAttribute("x", String(x));
    label.setAttribute("y", String(HEAD_H - 10));
    label.setAttribute("class", "sched-tick-label");
    label.setAttribute("text-anchor", "middle");
    label.textContent = _fmtDur(t);
    svg.appendChild(label);
  }

  // Actions.
  for (const a of actions) {
    const rowIdx = _primaryRow(resourceRows, a.resources, tres);
    if (rowIdx < 0) continue;
    const x = LEFT_W + a.start_t * pxPerSec;
    const w = Math.max(2, a.duration * pxPerSec);
    const y = HEAD_H + rowIdx * ROW_H + 4;
    const h = ROW_H - 8;
    _appendActionBlock(svg, x, y, w, h, a, _liveByLeaf.get(a.leaf_name));
  }

  // Tool swaps (on tool_resource row).
  const swapRowIdx = resourceRows.indexOf(tres);
  if (swapRowIdx >= 0) {
    for (const s of swaps) {
      const x = LEFT_W + s.start_t * pxPerSec;
      const w = Math.max(2, s.duration * pxPerSec);
      const y = HEAD_H + swapRowIdx * ROW_H + 8;
      const h = ROW_H - 16;
      _appendSwapBlock(svg, x, y, w, h, s, _liveByLeaf.get(s.leaf_name));
    }
  }

  // Now-cursor.
  const cursor = document.createElementNS(svgNS, "line");
  cursor.setAttribute("class", "sched-now");
  cursor.setAttribute("y1", String(HEAD_H - 4));
  cursor.setAttribute("y2", String(totalHeight - 8));
  cursor.id = "schedNowCursor";
  svg.appendChild(cursor);

  _ganttEl.innerHTML = "";
  _ganttEl.appendChild(svg);
  _ganttEl.dataset.pxPerSec = String(pxPerSec);
  _ganttEl.dataset.leftW    = String(LEFT_W);
  _updateNowCursor();
}

function _primaryRow(rows, resources, toolRes) {
  if (!resources || !resources.length) return rows.indexOf(toolRes);
  // Prefer the non-tool resource if present (a shaker action with
  // resource=shaker_1 should sit on shaker_1's row, not robot's).
  for (const r of resources) {
    if (r !== toolRes && rows.includes(r)) return rows.indexOf(r);
  }
  return rows.indexOf(resources[0]);
}

function _chooseTickStep(horizon, pxPerSec) {
  // Aim for ticks every ~80 px.
  const target = 80 / pxPerSec;
  for (const cand of [5, 10, 15, 30, 60, 120, 300, 600]) {
    if (cand >= target) return cand;
  }
  return 600;
}

function _appendActionBlock(svg, x, y, w, h, a, live) {
  const svgNS = "http://www.w3.org/2000/svg";
  const g = document.createElementNS(svgNS, "g");
  g.setAttribute("class", "sched-block");

  // Predicted (translucent) bar
  const pred = document.createElementNS(svgNS, "rect");
  pred.setAttribute("x", String(x));
  pred.setAttribute("y", String(y));
  pred.setAttribute("width", String(w));
  pred.setAttribute("height", String(h));
  pred.setAttribute("rx", "4");
  pred.setAttribute("fill", toolColor(a.tool));
  pred.setAttribute("class", "sched-pred");
  g.appendChild(pred);

  // Actual overlay if started
  if (live?.startedAt) {
    const actualStartOffset = live.startedAt - _scheduleStartWall;
    const actualEndOffset = (live.endedAt ?? (Date.now() / 1000)) - _scheduleStartWall;
    const ax = parseFloat(_ganttEl.dataset.leftW || "110")
             + actualStartOffset * parseFloat(_ganttEl.dataset.pxPerSec || "1");
    const aw = Math.max(2, (actualEndOffset - actualStartOffset)
             * parseFloat(_ganttEl.dataset.pxPerSec || "1"));
    const actual = document.createElementNS(svgNS, "rect");
    actual.setAttribute("x", String(ax));
    actual.setAttribute("y", String(y));
    actual.setAttribute("width", String(aw));
    actual.setAttribute("height", String(h));
    actual.setAttribute("rx", "4");
    actual.setAttribute("fill", toolColor(a.tool));
    actual.setAttribute(
      "class",
      `sched-actual sched-${live.status || "running"}`
    );
    g.appendChild(actual);
  }

  // Label
  const label = document.createElementNS(svgNS, "text");
  label.setAttribute("x", String(x + 6));
  label.setAttribute("y", String(y + h / 2 + 4));
  label.setAttribute("class", "sched-block-label");
  label.textContent = w > 50 ? `${a.name}(${a.item})` : "";
  g.appendChild(label);

  // Tooltip via <title>
  const title = document.createElementNS(svgNS, "title");
  const status = live?.status ? ` [${live.status}]` : "";
  title.textContent = `${a.name}(${a.item})${status}\n`
                    + `tool: ${a.tool ?? "—"}\n`
                    + `predicted: t=${a.start_t.toFixed(1)}s, dur=${a.duration}s\n`
                    + (live?.startedAt ? `started: +${(live.startedAt - _scheduleStartWall).toFixed(1)}s\n` : "")
                    + (live?.endedAt ? `ended: +${(live.endedAt - _scheduleStartWall).toFixed(1)}s` : "");
  g.appendChild(title);

  svg.appendChild(g);
}

function _appendSwapBlock(svg, x, y, w, h, s, live) {
  const svgNS = "http://www.w3.org/2000/svg";
  const g = document.createElementNS(svgNS, "g");
  g.setAttribute("class", "sched-swap");

  const rect = document.createElementNS(svgNS, "rect");
  rect.setAttribute("x", String(x));
  rect.setAttribute("y", String(y));
  rect.setAttribute("width", String(w));
  rect.setAttribute("height", String(h));
  rect.setAttribute("rx", "2");
  rect.setAttribute("fill", "url(#swapPattern)");
  rect.setAttribute("class", `sched-swap-rect${live?.status === "done" ? " done" : ""}`);
  g.appendChild(rect);

  const title = document.createElementNS(svgNS, "title");
  title.textContent = `swap ${s.from ?? "∅"} → ${s.to ?? "∅"}\nt=${s.start_t.toFixed(1)}s, dur=${s.duration}s`;
  g.appendChild(title);

  svg.appendChild(g);

  // Define the diagonal-stripe pattern once.
  if (!svg.querySelector("#swapPattern")) {
    const defs = document.createElementNS(svgNS, "defs");
    const pattern = document.createElementNS(svgNS, "pattern");
    pattern.id = "swapPattern";
    pattern.setAttribute("patternUnits", "userSpaceOnUse");
    pattern.setAttribute("width", "6");
    pattern.setAttribute("height", "6");
    pattern.setAttribute("patternTransform", "rotate(45)");
    const stripe = document.createElementNS(svgNS, "rect");
    stripe.setAttribute("width", "3");
    stripe.setAttribute("height", "6");
    stripe.setAttribute("fill", SWAP_STRIPE);
    pattern.appendChild(stripe);
    defs.appendChild(pattern);
    svg.insertBefore(defs, svg.firstChild);
  }
}

function _renderModalStats() {
  if (!_modalStatsEl) return;
  if (!_plan) { _modalStatsEl.textContent = ""; return; }
  const a = (_plan.actions || []).length;
  const s = (_plan.swaps || []).length;
  const m = _plan.makespan || 0;
  _modalStatsEl.innerHTML =
    `<span>Plan #${_replanId}</span>` +
    `<span>${a} action${a === 1 ? "" : "s"}</span>` +
    `<span>${s} swap${s === 1 ? "" : "s"}</span>` +
    `<span>makespan ${_fmtDur(m)}</span>`;
}

// ── now-cursor animation ───────────────────────────────────────────────
function _startCursorLoop() {
  if (_rafHandle) return;
  const tick = () => {
    _updateNowCursor();
    _rafHandle = requestAnimationFrame(tick);
  };
  _rafHandle = requestAnimationFrame(tick);
}

function _stopCursorLoop() {
  if (_rafHandle) cancelAnimationFrame(_rafHandle);
  _rafHandle = null;
}

function _updateNowCursor() {
  const cursor = document.getElementById("schedNowCursor");
  if (!cursor || !_plan || !_ganttEl) return;
  const pxPerSec = parseFloat(_ganttEl.dataset.pxPerSec || "1");
  const leftW    = parseFloat(_ganttEl.dataset.leftW || "110");
  // "Now" = wall-clock elapsed since the schedule was issued.
  const nowOffset = (Date.now() / 1000) - _scheduleStartWall;
  const horizon = Math.max(_plan.makespan || 0, 60);
  const clamped = Math.max(0, Math.min(horizon, nowOffset));
  const x = leftW + clamped * pxPerSec;
  cursor.setAttribute("x1", String(x));
  cursor.setAttribute("x2", String(x));

  // Update actual-duration overlays for any in-flight action — its
  // right edge grows with wall-clock until the action_end event fires.
  _ganttEl.querySelectorAll(".sched-running").forEach(el => {
    const ax = parseFloat(el.getAttribute("data-actual-start") || "");
    if (Number.isFinite(ax)) {
      const px = leftW + (nowOffset - ax) * pxPerSec;
      el.setAttribute("width", String(Math.max(2, px - parseFloat(el.getAttribute("x")))));
    }
  });
}
