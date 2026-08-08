// Live BT schedule view — clean flow Gantt.
//
// Each row lays its blocks out side-by-side with a fixed gap, sized to
// fit the full action name. Cross-row alignment respects start-time
// order (a shaker block lands underneath the robot action that
// triggered it). Consecutive blocks on the same row are joined by a
// thin connector line. State is driven by explicit framework events.
//
// Transport: this module no longer owns a WebSocket. Schedule events
// arrive via the workspace's multiplexed /ws channel (envelope type
// ``schedule_event``); workspace.js routes them into ``ingestScheduleEvent``
// below. The server keeps /ws/schedule alive for back-compat
// (external monitors, headless scripts) but the admin page reads
// from the mux. See docs/internal/ws-multiplexing-plan.md.

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

let _paneEl = null;   // #viewerSchedule — the viewport tab pane (was a modal)
let _ganttEl = null;

// ── public entrypoints ─────────────────────────────────────────────────

// Initialize the modal DOM (once per page load). Was bundled into
// connectScheduleWS when this module owned its own WS; now exposed
// separately because workspace.js handles transport.
export function initSchedule() {
  _initDOM();
}

// Reset state on workspace teardown — clears the in-memory Gantt so
// the next Launch starts from a blank canvas. Replaces the old
// disconnectScheduleWS behaviour for cache hygiene; no socket to
// close anymore (transport is the mux WS, owned by workspace.js).
export function resetSchedule() {
  _slices.length = 0;
  _leafState.clear();
  _leafTiming.clear();
  _leafOrder.length = 0;
  _leafGeom.clear();
  // Force a re-render so the modal blanks out if currently open.
  if (_paneVisible()) _render();
}

// Public ingest — workspace.js's mux dispatcher calls this for every
// ``schedule_event`` envelope. Accepts the raw event object the
// framework publishes (no envelope wrapper — payload only).
export function ingestScheduleEvent(event) {
  _ingest(event);
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
    _patchBlockState(k);
  } else if (msg.type === "action_end" || msg.type === "swap_end") {
    const k = _leafKey(msg.replan_id, msg.name);
    _leafState.set(k, msg.skipped ? "skipped" : "done");
    const t = _leafTiming.get(k) || {};
    t.endedAt = msg.wall_ts;
    _leafTiming.set(k, t);
    _patchBlockState(k);
  }
}

// Patch one block in place — class + shape glyph + time text. Falls
// back to a full re-render only when the block can't be located.
function _patchBlockState(leafKey) {
  if (!_paneVisible()) return;
  if (!_ganttEl) return;
  const el = _ganttEl.querySelector(`.sched-block[data-leaf-key="${CSS.escape(leafKey)}"]`);
  if (!el) { _render(); return; }
  const state = _leafState.get(leafKey) || "pending";
  el.className = `sched-block sched-${state}`;
  _dressBlock(el, leafKey);
}

function _initDOM() {
  _paneEl = document.getElementById("viewerSchedule");
  _ganttEl = document.getElementById("ganttContainer");
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
      const st = _leafState.get(name);
      return st !== "done" && st !== "skipped";
    });
  }
  if (!target) target = _leafOrder[_leafOrder.length - 1];
  const geom = _leafGeom.get(target);
  if (!geom) return;
  const track = _ganttEl.querySelector(".sched-track");
  if (!track) return;
  const blockCentre = (geom.frac + geom.wfrac / 2) * track.scrollWidth;
  _ganttEl.scrollLeft = Math.max(0, blockCentre - _ganttEl.clientWidth / 2);
}

export function showScheduleView() {
  _initDOM();
  if (!_paneEl) return;
  _paneEl.style.display = "";
  _renderGantt();
  // Always auto-centre on the "live" block (running, else next pending,
  // else the last block once everything's done). ``_jumpToCurrent`` is
  // a no-op when the chart is empty. Deferred a frame so the modal has
  // laid out — clientWidth is 0 until then.
  requestAnimationFrame(_jumpToCurrent);
}

function _paneVisible() {
  return !!(_paneEl && _paneEl.style.display !== "none");
}

// §4.5: the run is estimated while it runs. ETA = the planner's own
// numbers — estimates of pending blocks plus the remainder of the
// running one. Null when there is no plan (pre-launch, no schedule).
export function getScheduleEta() {
  // Pure data walk (no DOM — the pane may never have been opened).
  if (!_slices.length) return null;
  let eta = 0, seen = false;
  for (const slice of _slices) {
    const rid = slice.replan_id || 0;
    for (const a of (slice.actions || [])) {
      const key = _leafKey(rid, a.leaf_name);
      const st = _leafState.get(key) || "pending";
      if (st === "done" || st === "skipped") continue;
      const est = a.duration || 0;
      if (st === "running") {
        const t = _leafTiming.get(key) || {};
        const gone = t.startedAt != null ? (Date.now() / 1000 - t.startedAt) : 0;
        eta += Math.max(0, est - gone);
      } else {
        eta += est;
      }
      seen = true;
    }
  }
  return seen ? eta : 0;
}

export function hideScheduleView() {
  if (!_paneEl) return;
  _paneEl.style.display = "none";
}

function _render() {
  if (_paneVisible()) _renderGantt();
}

// ── SVG Gantt ──────────────────────────────────────────────────────────
function _renderGantt() {
  if (!_ganttEl) return;
  if (_slices.length === 0) {
    _ganttEl.innerHTML = `<div class="sched-empty">No plan yet.</div>`;
    return;
  }

  // Resource rows: union across slices, tool resource first.
  const tres = _slices[_slices.length - 1].tool_resource || "robot";
  const resSet = new Set([tres]);
  for (const slice of _slices) {
    for (const a of (slice.actions || [])) {
      for (const r of (a.resources || [])) resSet.add(r);
    }
  }
  const rows = [...resSet];
  rows.sort((a, b) => (a === tres ? -1 : b === tres ? 1 : a.localeCompare(b)));

  // ── ONE shared time scale (design §6) ─────────────────────────────
  // Blocks are positioned as left/width PERCENTAGES of the total plan
  // duration — a 20s block is the same width in every lane, a lane
  // that starts late shows empty track, idle gaps are visible, and
  // the tick row means something. Slices (replans) are sequential:
  // each gets a cumulative offset equal to the spans before it.
  const spans = _slices.map(sl => Math.max(1,
    ...((sl.actions || []).map(a => (a.start_t || 0) + (a.duration || 0)))));
  const offsets = [];
  let acc = 0;
  for (const sp of spans) { offsets.push(acc); acc += sp; }
  const T = Math.max(1, acc);

  // Track width: time-proportional, floor at the viewport so short
  // plans still fill it; ~5px/s keeps bd's 12min batch scrollable.
  const paneW = Math.max(400, _ganttEl.clientWidth - 140);
  const trackPx = Math.max(paneW, Math.min(T * 5, 20000));

  _leafOrder.length = 0;
  _leafGeom.clear();

  const frag = document.createDocumentFragment();
  const wrap = document.createElement("div");
  wrap.className = "sched-wrap";
  wrap.style.setProperty("--track-w", trackPx + "px");

  // tick row — answers "when"; the in-capsule number answers "how long"
  const ticks = document.createElement("div");
  ticks.className = "sched-ticks";
  const step = _niceStep(T);
  for (let t = 0; t <= T; t += step) {
    const el = document.createElement("span");
    el.style.left = (t / T * 100) + "%";
    el.textContent = _fmtS(t);
    ticks.appendChild(el);
  }
  wrap.appendChild(ticks);

  // slice dividers on the shared scale
  for (let i = 1; i < offsets.length; i++) {
    const d = document.createElement("div");
    d.className = "sched-slice-line";
    d.style.left = (offsets[i] / T * 100) + "%";
    d.title = `replan ${_slices[i].replan_id ?? i}`;
    wrap.appendChild(d);
  }

  for (const row of rows) {
    const lane = document.createElement("div");
    lane.className = "sched-lane";
    const lab = document.createElement("span");
    lab.className = "sched-lane-label";
    lab.textContent = row;
    lab.title = row;
    const track = document.createElement("div");
    track.className = "sched-track";
    lane.appendChild(lab);
    lane.appendChild(track);
    wrap.appendChild(lane);

    for (let si = 0; si < _slices.length; si++) {
      const slice = _slices[si];
      const rid = slice.replan_id || 0;
      for (const a of (slice.actions || [])) {
        if (_primaryRow(rows, a.resources, tres) !== rows.indexOf(row)) continue;
        const key = _leafKey(rid, a.leaf_name);
        const left = (offsets[si] + (a.start_t || 0)) / T * 100;
        const width = Math.max(0.25, (a.duration || 0) / T * 100);
        const el = document.createElement("div");
        el.className = "sched-block sched-" + (_leafState.get(key) || "pending");
        el.dataset.leafKey = key;
        el.dataset.est = String(a.duration || 0);
        el.dataset.label = _labelOf(a);
        el.style.left = left + "%";
        el.style.width = width + "%";
        el.innerHTML = `<span class="sb-fill"></span><span class="sb-shape"></span>` +
          `<span class="sb-label">${_escS(_labelOf(a))}</span><span class="sb-time"></span>`;
        _dressBlock(el, key);
        track.appendChild(el);
        _leafOrder.push(key);
        _leafGeom.set(key, { frac: left / 100, wfrac: width / 100 });
      }
    }
  }

  frag.appendChild(wrap);
  _ganttEl.innerHTML = "";
  _ganttEl.appendChild(frag);
}

// State → shape + time, per §6 and §9 (never colour alone):
//   done: ✓ · elapsed (accent)   running: pulsing dot · elapsed
//   skipped: — (dashed border)   pending: ~estimate (muted)
function _dressBlock(el, key) {
  const state = _leafState.get(key) || "pending";
  const est = Number(el.dataset.est) || 0;
  const timing = _leafTiming.get(key) || {};
  const shape = el.querySelector(".sb-shape");
  const time = el.querySelector(".sb-time");
  const fill = el.querySelector(".sb-fill");
  let timeText = "~" + _fmtS(est);
  if (state === "done" && timing.startedAt != null && timing.endedAt != null) {
    timeText = _fmtS(timing.endedAt - timing.startedAt);
  } else if (state === "running" && timing.startedAt != null) {
    timeText = _fmtS((Date.now() / 1000) - timing.startedAt);
  } else if (state === "skipped") {
    timeText = "—";
  }
  if (shape) shape.textContent = state === "done" ? "✓" : "";
  if (time) time.textContent = timeText;
  // elapsed-vs-planned as the inner fill: a block that ran long reads
  // as overrun without a fifth colour.
  if (fill) {
    let frac = 0;
    if (state === "done" && timing.startedAt != null && timing.endedAt != null && est > 0) {
      frac = Math.min(1, (timing.endedAt - timing.startedAt) / est);
    } else if (state === "running" && timing.startedAt != null && est > 0) {
      frac = Math.min(1, ((Date.now() / 1000) - timing.startedAt) / est);
    }
    fill.style.width = (frac * 100) + "%";
  }
  // full string one hover away — the container query may hide the number
  el.title = `${el.dataset.label} · ` +
    (state === "pending" ? `estimated ${_fmtS(est)}` :
     state === "skipped" ? "skipped" : `elapsed ${timeText}`);
}

function _labelOf(a) {
  const base = a.class_name || a.name;
  return (a.parametrized === false) ? base : `${base}(${a.item})`;
}
function _escS(t) {
  const d = document.createElement("span"); d.textContent = t; return d.innerHTML;
}
function _fmtS(v) {
  if (v >= 90) return `${Math.floor(v / 60)}m${String(Math.round(v % 60)).padStart(2, "0")}`;
  return `${Math.round(v * 10) / 10}s`;
}
function _niceStep(T) {
  const target = T / 8;
  const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200];
  for (const st of steps) if (st >= target) return st;
  return 1800;
}

function _primaryRow(rows, resources, toolRes) {
  const rs = resources || [];
  if (rs.includes(toolRes)) return rows.indexOf(toolRes);
  for (const r of rs) {
    const i = rows.indexOf(r);
    if (i >= 0) return i;
  }
  return rows.indexOf(toolRes);
}
