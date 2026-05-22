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
    }
    // Append this slice. Leaf state from earlier slices stays intact —
    // they've already been marked done/skipped by their action_end
    // events. New leaf names land in "pending" by default.
    _slices.push(msg);
    _render();
  } else if (msg.type === "action_start" || msg.type === "swap_start") {
    _leafState.set(_leafKey(msg.replan_id, msg.name), "running");
    _render();
  } else if (msg.type === "action_end" || msg.type === "swap_end") {
    _leafState.set(
      _leafKey(msg.replan_id, msg.name),
      msg.skipped ? "skipped" : "done",
    );
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

  // ── Lay out each slice in its own horizontal column ────────────────
  // Cursor ``xBase`` tracks where the next slice starts. Each slice
  // runs per-row flow + cross-row alignment locally; the cursor
  // advances by the slice's actual width + SLICE_GAP.
  //
  // ``placements`` keys by ``${replan_id}|${leaf_name}`` so that
  // parameterless actions (Start / Park / ShakerOne / ShakerTwo)
  // can appear in every slice they belong to — keying by leaf_name
  // alone caused later slices to overwrite earlier ones' positions.
  const placements = new Map();    // composite key -> {a, x, w, y, h, rowIdx, replan_id}
  const sliceDividerXs = [];        // x positions of dividers BETWEEN slices
  let xBase = LEFT_W + 8;
  // Reset before populating — the auto-focus logic reads these
  // lookups, and stale entries from a previous render would point at
  // x-coordinates that no longer match the current SVG.
  _leafOrder.length = 0;
  _leafGeom.clear();

  for (let sliceIdx = 0; sliceIdx < _slices.length; sliceIdx++) {
    const slice = _slices[sliceIdx];
    const sliceActions = slice.actions || [];
    const sliceReplanId = slice.replan_id || 0;

    // Per-row left-to-right flow within this slice. The local map's
    // keys can stay ``leaf_name`` — within a single slice leaf names
    // are unique (the planner generates at most one grounding per
    // action class per parameter tuple).
    const local = new Map();
    for (let rowIdx = 0; rowIdx < rows.length; rowIdx++) {
      const arr = sliceActions
        .filter(a => _primaryRow(rows, a.resources, tres) === rowIdx)
        .sort((x, y) => x.start_t - y.start_t);
      let cursor = xBase;
      for (const a of arr) {
        const w = neededWidth(a);
        const y = TOP_PAD + rowIdx * ROW_H + ROW_PAD;
        const h = ROW_H - ROW_PAD * 2;
        local.set(a.leaf_name, {
          a, x: cursor, w, y, h, rowIdx,
          replan_id: sliceReplanId,
        });
        cursor += w + BLOCK_GAP;
      }
    }

    // Cross-row alignment within the slice. Non-robot blocks shift to
    // sit AFTER the robot block whose end matches their start_t.
    const robotRowIdx = rows.indexOf(tres);
    if (robotRowIdx >= 0) {
      const robotByStart = [...local.values()]
        .filter(p => p.rowIdx === robotRowIdx)
        .sort((a, b) => a.a.start_t - b.a.start_t);
      for (const p of local.values()) {
        if (p.rowIdx === robotRowIdx) continue;
        let anchor = null;
        for (const rp of robotByStart) {
          const rpEnd = rp.a.start_t + rp.a.duration;
          if (rpEnd > p.a.start_t) break;
          anchor = rp;
        }
        if (anchor) p.x = anchor.x + anchor.w + 8;
      }
    }

    // Slice width = right edge of the furthest block.
    let sliceMaxX = xBase;
    // Record chronological order within this slice (by solver start_t)
    // for the auto-focus lookup. Slices append in plan order so
    // ``_leafOrder`` ends up globally chronological.
    const sliceByStart = [...local.values()].sort(
      (a, b) => a.a.start_t - b.a.start_t,
    );
    for (const p of sliceByStart) {
      if (p.x + p.w > sliceMaxX) sliceMaxX = p.x + p.w;
      const key = _leafKey(sliceReplanId, p.a.leaf_name);
      placements.set(key, p);
      _leafOrder.push(key);
      _leafGeom.set(key, { x: p.x, w: p.w });
    }

    // Next slice starts after a gap; a divider sits at the midpoint.
    if (sliceIdx + 1 < _slices.length) {
      sliceDividerXs.push(sliceMaxX + SLICE_GAP / 2);
    }
    xBase = sliceMaxX + SLICE_GAP;
  }

  // SVG dimensions. xBase ended one SLICE_GAP past the last slice's
  // right edge — subtract it back out for the final width.
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

  // Vertical slice dividers — thin, subtle, span the full row band.
  for (const x of sliceDividerXs) {
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", String(x));
    line.setAttribute("x2", String(x));
    line.setAttribute("y1", String(TOP_PAD - 4));
    line.setAttribute("y2", String(TOP_PAD + rows.length * ROW_H + 4));
    line.setAttribute("class", "sched-slice-divider");
    gSlices.appendChild(line);
  }

  // Connector lines between consecutive blocks on the same row WITHIN
  // a slice. Filter placements by replan_id so we don't draw a
  // connector that spans the slice divider.
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
