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

// One ``schedule`` event from the framework = one slice. We append
// rather than replace so the operator sees the full job history grow
// in place across replans.
//
// State is keyed by ``${replan_id}|${leaf_name}`` because parameterless
// actions (Start / Park / ShakerOne / ShakerTwo) reuse the same
// leaf_name across slices — the replan_id scopes the lookup to the
// slice the event belongs to.
const _slices = [];
const _leafState = new Map();   // "replan_id|leaf_name" -> state
// Wall-clock timing per leaf — populated from action_start /
// action_end / swap_start / swap_end events. Shown in the click-info
// panel so the operator can see actual vs planned timing.
//   composite key -> { startedAt: epoch_s, endedAt: epoch_s }
const _leafTiming = new Map();
// Chronological list of placement keys ("replan_id|leaf_name") as drawn —
// populated by ``_renderGantt`` and read by the auto-focus logic to
// centre the running block.
const _leafOrder = [];
// placement key -> { x, w } in the SVG's coordinate system. Same lifecycle
// as ``_leafOrder`` — overwritten each render.
const _leafGeom = new Map();

// Compose the (replan_id, leaf_name) lookup key. Defaults replan_id to
// 0 for safety against older publishers; new publishers always supply
// it. Keeps every site that hashes a leaf consistent — change here
// once, never inline this format string.
function _leafKey(replan_id, leaf_name) {
  return `${replan_id || 0}|${leaf_name}`;
}

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
    // replan_id == 1 = fresh workflow run (the launcher's per-run
    // counter starts there). Clear out the previous run's slices +
    // leaf state so the chart doesn't accumulate forever across
    // Start → complete → Start cycles. Matches the server-side
    // history-reset rule.
    if ((msg.replan_id || 0) === 1) {
      _slices.length = 0;
      _leafState.clear();
      _leafTiming.clear();
    }
    // Append this slice. Leaf state from earlier slices stays intact —
    // they've already been marked done/skipped by their action_end
    // events. New leaf names land in "pending" by default.
    _slices.push(msg);
    _render();
  } else if (msg.type === "action_start" || msg.type === "swap_start") {
    const k = _leafKey(msg.replan_id, msg.name);
    _leafState.set(k, "running");
    const t = _leafTiming.get(k) || {};
    t.startedAt = msg.wall_ts;
    _leafTiming.set(k, t);
    _render();
  } else if (msg.type === "action_end" || msg.type === "swap_end") {
    const k = _leafKey(msg.replan_id, msg.name);
    _leafState.set(k, msg.skipped ? "skipped" : "done");
    const t = _leafTiming.get(k) || {};
    t.endedAt = msg.wall_ts;
    _leafTiming.set(k, t);
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
    _modalEl.addEventListener("click", (e) => {
      if (e.target === _modalEl) closeScheduleModal();
    });
    _modalEl._wired = true;
  }
}

// Centre the scroll viewport on whichever leaf is currently "the live
// one". Prefers a running block; otherwise scrolls to the next pending
// block; otherwise the last leaf in the chart. No-op when the chart is
// empty (nothing to scroll to).
function _jumpToCurrent() {
  if (!_ganttEl || _leafOrder.length === 0) return;
  let target = _leafOrder.find(name => _leafState.get(name) === "running");
  if (!target) {
    target = _leafOrder.find(name => {
      const s = _leafState.get(name);
      return s !== "done" && s !== "skipped";
    });
  }
  if (!target) target = _leafOrder[_leafOrder.length - 1];
  const geom = _leafGeom.get(target);
  if (!geom) return;
  const blockCentre = geom.x + geom.w / 2;
  const viewportW = _ganttEl.clientWidth;
  // Instant scroll — the modal has just opened and a smooth pan would
  // make the chart visibly slide, which reads as a glitch.
  _ganttEl.scrollLeft = Math.max(0, blockCentre - viewportW / 2);
}

export function openScheduleModal() {
  _initDOM();
  if (!_modalEl) return;
  _modalEl.classList.add("show");
  _renderGantt();
  // Always auto-centre on the "live" block (running, else next pending,
  // else the last block once everything's done). ``_jumpToCurrent`` is
  // a no-op when the chart is empty. Deferred a frame so the modal has
  // laid out — clientWidth is 0 until then.
  requestAnimationFrame(_jumpToCurrent);
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
  if (_slices.length === 0) {
    _ganttEl.innerHTML = `<div class="sched-empty">No plan yet.</div>`;
    return;
  }

  // tool_resource comes from the most-recent slice (the project's
  // identity shouldn't change across replans anyway). Resource rows
  // are the union across every slice's actions so a slice that only
  // touches shaker_2 still gets a row drawn for it.
  const tres = _slices[_slices.length - 1].tool_resource || "robot";
  const resSet = new Set([tres]);
  for (const slice of _slices) {
    for (const a of (slice.actions || [])) {
      for (const r of (a.resources || [])) resSet.add(r);
    }
  }
  const rows = [...resSet];
  rows.sort((a, b) => (a === tres ? -1 : b === tres ? 1 : a.localeCompare(b)));

  // ── Layout knobs ───────────────────────────────────────────────────
  const PX_PER_SEC = 10;      // chart scale — pick so typical 10 s actions fit a small label
  const MIN_BLOCK_W = 28;     // floor so very short / pending blocks still hit-test
  const ROW_H     = 48;
  const ROW_PAD   = 8;
  const LEFT_W    = 120;
  const TOP_PAD   = 18;
  const AXIS_H    = 24;       // bottom band for the time-axis ticks
  const BOT_PAD   = 14;

  function labelOf(a)     {
    // Parameterless actions (parametrized: false) — e.g. Start / Park
    // — appear once per plan regardless of item count, so the "(0)"
    // suffix is misleading. Show just the class name for those.
    const base = a.class_name || a.name;
    return (a.parametrized === false) ? base : `${base}(${a.item})`;
  }

  // ── Time-axis layout ───────────────────────────────────────────────
  // Each block sits at x = (its chart-relative start time) * PX_PER_SEC.
  // Position and width come from actual wall-clock feedback when the
  // leaf has run; otherwise we fall back to the planner's prediction
  // for the slice (broadcast ``wall_ts`` + ``start_t``).
  //
  // ``T0`` is the chart's t=0 — anchored at the wall-clock time the
  // first replan published its schedule. All chart times are relative
  // to this so consecutive slices flow naturally on one timeline; the
  // gap between slices reflects real idle time (or replan latency).
  const T0 = _slices[0].wall_ts;
  const placements = new Map();    // composite key -> { a, x, w, y, h, rowIdx, replan_id }
  _leafOrder.length = 0;
  _leafGeom.clear();

  // Track the latest "now" across the chart so we can size the SVG
  // wide enough for a running block that's still growing.
  const nowSec = Date.now() / 1000 - T0;
  let chartMaxT = 0;

  for (let sliceIdx = 0; sliceIdx < _slices.length; sliceIdx++) {
    const slice = _slices[sliceIdx];
    const sliceActions = slice.actions || [];
    const sliceReplanId = slice.replan_id || 0;
    const slicePlanAnchor = (slice.wall_ts || T0) - T0;   // chart-relative slice t=0

    for (const a of sliceActions) {
      const key = _leafKey(sliceReplanId, a.leaf_name);
      const timing = _leafTiming.get(key) || {};
      const state = _leafState.get(key) || "pending";

      // Resolve the block's chart-time interval:
      //   actual start if it ran; planner's predicted start otherwise.
      //   actual end if it finished; "now" while running; planner's
      //   predicted end while still pending.
      const actualStart = timing.startedAt != null ? timing.startedAt - T0 : null;
      const actualEnd   = timing.endedAt   != null ? timing.endedAt   - T0 : null;
      const predStart   = slicePlanAnchor + (a.start_t || 0);
      const predEnd     = predStart + (a.duration || 0);
      const t1 = actualStart != null ? actualStart : predStart;
      let   t2;
      if (actualEnd != null)        t2 = actualEnd;
      else if (state === "running") t2 = nowSec;
      else                          t2 = predEnd;
      if (t2 < t1) t2 = t1;
      if (t2 > chartMaxT) chartMaxT = t2;

      const rowIdx = _primaryRow(rows, a.resources, tres);
      placements.set(key, {
        a,
        x: LEFT_W + 8 + t1 * PX_PER_SEC,
        w: Math.max(MIN_BLOCK_W, (t2 - t1) * PX_PER_SEC),
        y: TOP_PAD + rowIdx * ROW_H + ROW_PAD,
        h: ROW_H - ROW_PAD * 2,
        rowIdx,
        replan_id: sliceReplanId,
        t1, t2,
      });
    }
  }

  // Populate _leafOrder / _leafGeom in chronological order for the
  // auto-focus button.
  const ordered = [...placements.entries()].sort((a, b) => a[1].t1 - b[1].t1);
  for (const [key, p] of ordered) {
    _leafOrder.push(key);
    _leafGeom.set(key, { x: p.x, w: p.w });
  }

  // SVG dimensions. ``chartMaxT`` plus a small lookahead so running
  // blocks grow into visible space.
  const tickEvery = _chooseTickStep(chartMaxT);
  const chartRightT = Math.max(chartMaxT, nowSec) + tickEvery;  // +1 tick padding
  const W = Math.max(LEFT_W + 200, LEFT_W + 8 + chartRightT * PX_PER_SEC + 16);
  const H = TOP_PAD + rows.length * ROW_H + AXIS_H + BOT_PAD;

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width",  String(W));
  svg.setAttribute("height", String(H));
  svg.setAttribute("class",  "sched-svg");

  const gRows = document.createElementNS(svgNS, "g");
  const gAxis = document.createElementNS(svgNS, "g");
  const gBlocks = document.createElementNS(svgNS, "g");
  svg.appendChild(gRows);
  svg.appendChild(gAxis);
  svg.appendChild(gBlocks);

  // Row labels + horizontal dividers.
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

  // Time axis below the rows. Ticks every ``tickEvery`` seconds with
  // an mm:ss label and a faint vertical gridline through the chart.
  const axisY = TOP_PAD + rows.length * ROW_H + 6;
  const gridBottomY = TOP_PAD + rows.length * ROW_H;
  // Top gridline aligned with the first row's top edge.
  for (let t = 0; t <= chartRightT + 0.01; t += tickEvery) {
    const xt = LEFT_W + 8 + t * PX_PER_SEC;
    const grid = document.createElementNS(svgNS, "line");
    grid.setAttribute("x1", String(xt));
    grid.setAttribute("x2", String(xt));
    grid.setAttribute("y1", String(TOP_PAD - 4));
    grid.setAttribute("y2", String(gridBottomY + 4));
    grid.setAttribute("class", "sched-axis-grid");
    gAxis.appendChild(grid);

    const lbl = document.createElementNS(svgNS, "text");
    lbl.setAttribute("x", String(xt));
    lbl.setAttribute("y", String(axisY + 14));
    lbl.setAttribute("text-anchor", "middle");
    lbl.setAttribute("class", "sched-axis-label");
    lbl.textContent = _fmtAxisLabel(t);
    gAxis.appendChild(lbl);
  }
  // Vertical "now" marker — useful while watching a live run.
  if (nowSec > 0 && nowSec <= chartRightT) {
    const xNow = LEFT_W + 8 + nowSec * PX_PER_SEC;
    const now = document.createElementNS(svgNS, "line");
    now.setAttribute("x1", String(xNow));
    now.setAttribute("x2", String(xNow));
    now.setAttribute("y1", String(TOP_PAD - 4));
    now.setAttribute("y2", String(gridBottomY + 4));
    now.setAttribute("class", "sched-axis-now");
    gAxis.appendChild(now);
  }

  // Action blocks.
  for (const p of placements.values()) {
    const state = _leafState.get(_leafKey(p.replan_id, p.a.leaf_name)) || "pending";
    _appendBlock(gBlocks, p, state);
  }

  _ganttEl.innerHTML = "";
  _ganttEl.appendChild(svg);

  // Keep running blocks growing in real-time. The ``setTimeout`` ladder
  // lazily kicks in only while at least one leaf is running; otherwise
  // the chart is static and no timer fires.
  _scheduleLiveTick();
}

// ── Helpers ────────────────────────────────────────────────────────────

function _chooseTickStep(maxT) {
  // Pick a tick interval that yields ~6–12 labels across the chart.
  // Steps are seconds, picked from a "nice" sequence.
  const candidates = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600];
  const target = Math.max(maxT, 30);
  for (const c of candidates) {
    if (target / c <= 12) return c;
  }
  return candidates[candidates.length - 1];
}

function _fmtAxisLabel(t) {
  // ``t`` is seconds from T0. Show as mm:ss for short runs, hh:mm:ss
  // once the chart spans an hour or more.
  t = Math.round(t);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

let _liveTickHandle = null;
function _scheduleLiveTick() {
  if (_liveTickHandle) return;
  const anyRunning = [..._leafState.values()].some(s => s === "running");
  if (!anyRunning) return;
  _liveTickHandle = setTimeout(() => {
    _liveTickHandle = null;
    if (_modalEl?.classList.contains("show")) _renderGantt();
  }, 500);
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

  // Same label rule as ``labelOf`` in the layout above — keep them
  // in sync. Parameterless actions drop the "(item)" suffix.
  const base = a.class_name || a.name;
  const label = (a.parametrized === false) ? base : `${base}(${a.item})`;
  const lab = document.createElementNS(svgNS, "text");
  lab.setAttribute("x", String(x + w / 2));
  lab.setAttribute("y", String(y + h / 2 + 4));
  lab.setAttribute("text-anchor", "middle");
  lab.setAttribute("class", "sched-blocklabel");
  lab.textContent = label;
  g.appendChild(lab);

  // Hover tooltip shows the same info the chart already renders
  // visually (position + width). Useful when blocks are narrow.
  const timing = _leafTiming.get(_leafKey(p.replan_id, p.a.leaf_name)) || {};
  const fmtClock = (ts) =>
    ts == null ? "—" : new Date(ts * 1000).toLocaleTimeString();
  const titleParts = [`${label} — ${state}`];
  if (timing.startedAt != null) titleParts.push(`started ${fmtClock(timing.startedAt)}`);
  if (timing.endedAt   != null) titleParts.push(`Δ${(timing.endedAt - timing.startedAt).toFixed(1)} s`);
  const title = document.createElementNS(svgNS, "title");
  title.textContent = titleParts.join(" · ");
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
