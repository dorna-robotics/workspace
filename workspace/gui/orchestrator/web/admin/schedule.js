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
  const FONT_PX   = 12;
  const CHAR_W    = FONT_PX * 0.62;
  const LABEL_PAD = 22;
  const BLOCK_GAP = 18;       // gap between consecutive blocks on a row
  const SLICE_GAP = 38;       // gap between slices (vertical divider sits at the midpoint)
  const ROW_H     = 48;
  const ROW_PAD   = 8;
  const LEFT_W    = 120;
  const TOP_PAD   = 18;
  const BOT_PAD   = 18;

  function labelOf(a)     {
    // Parameterless actions (parametrized: false) — e.g. Start / Park
    // — appear once per plan regardless of item count, so the "(0)"
    // suffix is misleading. Show just the class name for those.
    const base = a.class_name || a.name;
    return (a.parametrized === false) ? base : `${base}(${a.item})`;
  }
  function neededWidth(a) { return labelOf(a).length * CHAR_W + LABEL_PAD; }

  // ── Flow layout, per-slice column ─────────────────────────────────
  // X is NOT proportional to wall-clock time. Block widths come from
  // label length so every action's name is readable. Time data is
  // shown separately: as a small "Δ s" line under each *done* block
  // and in the hover tooltip.
  const placements = new Map();    // composite key -> {a, x, w, y, h, rowIdx, replan_id}
  const sliceDividerXs = [];        // x positions of dividers BETWEEN slices
  let xBase = LEFT_W + 8;
  _leafOrder.length = 0;
  _leafGeom.clear();

  for (let sliceIdx = 0; sliceIdx < _slices.length; sliceIdx++) {
    const slice = _slices[sliceIdx];
    const sliceActions = slice.actions || [];
    const sliceReplanId = slice.replan_id || 0;

    // Per-action chart time in **slice-local seconds** — actual when
    // known (epoch minus the slice's wall_ts), planner prediction
    // otherwise. Both branches return the same unit so ordering
    // decisions below compare apples-to-apples; mixing epoch values
    // with planner seconds bunches every done block onto the right.
    const sliceT0 = slice.wall_ts || 0;
    const chartStart = (a) => {
      const t = _leafTiming.get(_leafKey(sliceReplanId, a.leaf_name));
      if (t && t.startedAt != null) return t.startedAt - sliceT0;
      return a.start_t || 0;
    };
    const chartEnd = (a) => {
      const t = _leafTiming.get(_leafKey(sliceReplanId, a.leaf_name));
      if (t && t.endedAt != null)   return t.endedAt   - sliceT0;
      if (t && t.startedAt != null) return (t.startedAt - sliceT0) + (a.duration || 0);
      return (a.start_t || 0) + (a.duration || 0);
    };

    // Build the block records first (x left undefined; we'll fill
    // it in the topological pass below).
    const local = new Map();
    for (const a of sliceActions) {
      const rowIdx = _primaryRow(rows, a.resources, tres);
      const w = neededWidth(a);
      const y = TOP_PAD + rowIdx * ROW_H + ROW_PAD;
      const h = ROW_H - ROW_PAD * 2;
      local.set(a.leaf_name, {
        a, x: xBase, w, y, h, rowIdx,
        replan_id: sliceReplanId,
      });
    }

    // Topological flow layout across ALL rows of this slice.
    //
    // Walk blocks in chartStart order. Each block's x is pushed past
    // the right edge of every earlier-placed block whose chartEnd is
    // ≤ this block's chartStart — i.e. every block that fully
    // *precedes* this one in chart time, regardless of row. That
    // means:
    //   * Sequential blocks (A ends before B starts) end up visually
    //     sequential, even when they're on different rows. So if the
    //     autonomous shaker completes before the next robot action
    //     starts, the robot block sits to the right of the shaker
    //     block — no x overlap.
    //   * Parallel blocks (their chart-time intervals overlap) don't
    //     enforce ordering on each other, so they can land at the
    //     same x and stack vertically across rows — which is what
    //     parallelism *should* look like.
    //
    // Chart time uses actual ``wall_ts`` when available so completed
    // runs reflect what really happened, not what the planner
    // predicted.
    const allByTime = [...local.values()].sort(
      (a, b) => chartStart(a.a) - chartStart(b.a),
    );
    const placed = [];
    for (const p of allByTime) {
      let nx = xBase;
      const ps = chartStart(p.a);
      for (const other of placed) {
        if (chartEnd(other.a) <= ps) {
          const right = other.x + other.w + 8;
          if (right > nx) nx = right;
        }
      }
      p.x = nx;
      placed.push(p);
    }

    let sliceMaxX = xBase;
    const sliceByStart = [...local.values()].sort(
      (a, b) => chartStart(a.a) - chartStart(b.a),
    );
    for (const p of sliceByStart) {
      if (p.x + p.w > sliceMaxX) sliceMaxX = p.x + p.w;
      const key = _leafKey(sliceReplanId, p.a.leaf_name);
      placements.set(key, p);
      _leafOrder.push(key);
      _leafGeom.set(key, { x: p.x, w: p.w });
    }

    if (sliceIdx + 1 < _slices.length) {
      sliceDividerXs.push(sliceMaxX + SLICE_GAP / 2);
    }
    xBase = sliceMaxX + SLICE_GAP;
  }

  const W = Math.max(LEFT_W + 200, xBase - SLICE_GAP + 16);
  const H = TOP_PAD + rows.length * ROW_H + BOT_PAD;

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width",  String(W));
  svg.setAttribute("height", String(H));
  svg.setAttribute("class",  "sched-svg");

  const gRows = document.createElementNS(svgNS, "g");
  const gSlices = document.createElementNS(svgNS, "g");
  const gConn = document.createElementNS(svgNS, "g");
  const gBlocks = document.createElementNS(svgNS, "g");
  svg.appendChild(gRows);
  svg.appendChild(gSlices);
  svg.appendChild(gConn);
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

  // Slice dividers — thin, subtle, span the full row band.
  for (const x of sliceDividerXs) {
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", String(x));
    line.setAttribute("x2", String(x));
    line.setAttribute("y1", String(TOP_PAD - 4));
    line.setAttribute("y2", String(TOP_PAD + rows.length * ROW_H + 4));
    line.setAttribute("class", "sched-slice-divider");
    gSlices.appendChild(line);
  }

  // Connector lines between consecutive blocks on the same row within
  // a slice — visual hint at sequencing.
  for (let sliceIdx = 0; sliceIdx < _slices.length; sliceIdx++) {
    const sliceReplanId = _slices[sliceIdx].replan_id || 0;
    for (let rowIdx = 0; rowIdx < rows.length; rowIdx++) {
      const arr = [...placements.values()]
        .filter(p => p.rowIdx === rowIdx && p.replan_id === sliceReplanId)
        .sort((a, b) => a.x - b.x);
      for (let i = 0; i + 1 < arr.length; i++) {
        const A = arr[i], B = arr[i + 1];
        const x1 = A.x + A.w;
        const x2 = B.x;
        if (x2 - x1 < 4) continue;
        const yMid = A.y + A.h / 2;
        const stA = _leafState.get(_leafKey(A.replan_id, A.a.leaf_name)) || "pending";
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
  }

  // Action blocks.
  for (const p of placements.values()) {
    const state = _leafState.get(_leafKey(p.replan_id, p.a.leaf_name)) || "pending";
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

  // Duration text under DONE blocks only — pending / running blocks
  // have nothing useful to show yet.
  const timing = _leafTiming.get(_leafKey(p.replan_id, p.a.leaf_name)) || {};
  const elapsed = (timing.startedAt != null && timing.endedAt != null)
    ? (timing.endedAt - timing.startedAt)
    : null;
  if (elapsed != null && (state === "done" || state === "skipped")) {
    const dur = document.createElementNS(svgNS, "text");
    dur.setAttribute("x", String(x + w / 2));
    dur.setAttribute("y", String(y + h + 11));
    dur.setAttribute("text-anchor", "middle");
    dur.setAttribute("class", "sched-block-elapsed");
    dur.textContent = `${elapsed.toFixed(1)}s`;
    g.appendChild(dur);
  }

  // Hover tooltip — minimal: label + key time data (or state).
  const title = document.createElementNS(svgNS, "title");
  title.textContent = elapsed != null
    ? `${label} · ${elapsed.toFixed(1)}s`
    : `${label} · ${state}`;
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
