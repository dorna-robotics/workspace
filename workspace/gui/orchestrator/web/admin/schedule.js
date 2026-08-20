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

// ── X-only zoom ──────────────────────────────────────────────────────
// Scales HORIZONTAL layout only: block widths and the gaps between
// them. Row height, block height, corner radius and font size are
// fixed, because shrinking those moves the rows and the type — exactly
// what an operator scanning for the running block does not want.
// design-system.md §3.9.
// Label metrics — module-scope because BOTH the layout pass and
// _appendBlock's label-drop test must measure with the same numbers.
// They were duplicated once; that is how a drop rule and a width rule
// drift apart.
const FONT_PX   = 12;
const CHAR_W    = FONT_PX * 0.62;
const LABEL_PAD = 22;

let _zoom = 1;
const ZOOM_MIN = 0.10, ZOOM_MAX = 2.5, ZOOM_STEP = 1.25;

export function setZoom(z) {
  const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
  if (next === _zoom) return;
  _zoom = next;
  _syncZoomControls();
  _render();
}
export function zoomIn()    { setZoom(_zoom * ZOOM_STEP); }
export function zoomOut()   { setZoom(_zoom / ZOOM_STEP); }
export function zoomReset() { setZoom(1); }
export function zoomLevel() { return _zoom; }

// Compose the (replan_id, leaf_name) lookup key. Defaults replan_id to
// 0 for safety against older publishers; new publishers always supply
// it. Keeps every site that hashes a leaf consistent — change here
// once, never inline this format string.
function _leafKey(replan_id, leaf_name) {
  return `${replan_id || 0}|${leaf_name}`;
}

let _ganttEl = null;

// ── public entrypoints ─────────────────────────────────────────────────

// Initialize the modal DOM (once per page load). Was bundled into
// connectScheduleWS when this module owned its own WS; now exposed
// separately because workspace.js handles transport.


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
  // Force a re-render so a visible pane blanks out immediately.
  _render();
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

// Patch one block in place — only the block whose state changed gets
// its class flipped and (if just-completed) its duration text added.
// Avoids the full SVG rebuild on every action_start / action_end /
// swap_start / swap_end, which was the schedule modal's hottest
// path during a run. Falls back to a full re-render only when the
// block can't be located (e.g. modal just opened, SVG not yet built
// — _render() builds it).
function _patchBlockState(leafKey) {
  if (!_visible()) return;
  const g = _ganttEl.querySelector(`g.sched-block[data-leaf-key="${leafKey}"]`);
  if (!g) { _render(); return; }
  const state = _leafState.get(leafKey) || "pending";
  g.setAttribute("class", `sched-block sched-${state}`);
  // If this transition just produced a finished block, add its
  // elapsed-time label under the rect. (Done before only via a full
  // re-render — we replicate just the duration text here.)
  if ((state === "done" || state === "skipped")
      && g.querySelector("text.sched-blocklabel")       // label shown => wide enough
      && !g.querySelector(".sched-block-elapsed")) {
    const timing = _leafTiming.get(leafKey) || {};
    if (timing.startedAt != null && timing.endedAt != null) {
      const elapsed = timing.endedAt - timing.startedAt;
      const rect = g.querySelector("rect.sched-block-fill");
      if (rect) {
        const x = parseFloat(rect.getAttribute("x"));
        const y = parseFloat(rect.getAttribute("y"));
        const w = parseFloat(rect.getAttribute("width"));
        const h = parseFloat(rect.getAttribute("height"));
        const dur = document.createElementNS("http://www.w3.org/2000/svg", "text");
        dur.setAttribute("x", String(x + w / 2));
        dur.setAttribute("y", String(y + h + 11));
        dur.setAttribute("text-anchor", "middle");
        dur.setAttribute("class", "sched-block-elapsed");
        dur.textContent = `${elapsed.toFixed(1)}s`;
        g.appendChild(dur);
      }
    }
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

// Attach the gantt to an inline host element (the viewport's
// Schedule pane). Renders happen only while the host is visible.
export function attachSchedule(el) {
  if (!el) { _ganttEl = null; _gutterEl = null; _stickyEl = null; return; }
  // FOUR ELEMENTS, EACH WITH ONE JOB. A single scrolling SVG loses the
  // row labels and the phase name the moment you pan, and lets blocks
  // slide under the floating controls.
  //
  //   el .gantt-panel            positions, never scrolls
  //     ├ .sched-topbar          phase readout + zoom controls
  //     └ .gantt-body
  //         ├ .gantt-gutter      row labels — FROZEN
  //         └ .gantt-container   the plot — scrolls
  //
  // The controls live in the topbar, not over the plot, so nothing can
  // pass beneath them.
  el.classList.add("gantt-panel");
  let bar = el.querySelector(":scope > .sched-topbar");
  if (!bar) {
    bar = document.createElement("div");
    bar.className = "sched-topbar";

    el.appendChild(bar);
  }
  let body = el.querySelector(":scope > .gantt-body");
  if (!body) {
    body = document.createElement("div");
    body.className = "gantt-body";
    body.innerHTML = '<div class="gantt-gutter"></div>' +
                     '<div class="gantt-plot">' +
                       '<span class="sched-phase-sticky" hidden></span>' +
                       '<div class="gantt-container"></div>' +
                     '</div>';
    el.appendChild(body);
  }
  if (!el.querySelector(":scope > .sched-tip")) {
    const tip = document.createElement("div");
    tip.className = "sched-tip";
    tip.hidden = true;
    el.appendChild(tip);
  }
  _tipEl    = el.querySelector(":scope > .sched-tip");
  _gutterEl = body.querySelector(".gantt-gutter");
  _ganttEl  = body.querySelector(".gantt-container");
  _stickyEl = body.querySelector(".sched-phase-sticky");
  _ganttEl.addEventListener("scroll", _syncStickyBands, { passive: true });
  _wireTip(_ganttEl, el);
  _mountZoomControls(bar);
}

// Minus / plus / home, floating top-right over the chart. Built once
// per host; the SVG is re-rendered underneath them, so they live on the
// host rather than inside the chart. design-system.md §3.9.
const _ICON = {
  out:  '<line x1="5" y1="12" x2="19" y2="12"/>',
  in:   '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  // Same house path the sidebar uses — one glyph, one meaning.
  home: '<path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>' +
        '<polyline points="9 22 9 12 15 12 15 22"/>',
};

function _svgIcon(paths) {
  return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" ' +
         'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
         'stroke-linejoin="round">' + paths + '</svg>';
}

let _zoomPctEl = null, _zoomInEl = null, _zoomOutEl = null;
let _gutterEl = null, _tipEl = null, _tipPinned = null, _stickyEl = null;
// Bands of the CURRENT render, in plot coordinates, for the readout.

// THE PHASE LABEL DOES NOT MOVE. It used to be an SVG <text> inside the
// scroller, slid along its band each frame to stay at the left edge.
// That fights the scroll: the compositor moves the layer, then the main
// thread moves the label back, and the two are not synchronised — so
// every frame the label is briefly in the wrong place. It reads as
// blinking, and it settles only when scrolling stops. rAF tightens the
// timing but cannot fix it, because the compositor scrolls without the
// main thread at all.
//
// So the label is now an HTML element OUTSIDE the scroller, parked at
// the plot's left edge. It never moves; only its TEXT changes, and only
// when the phase under that edge changes — a handful of times per run
// instead of sixty times a second.
let _bands = [];          // [{phase, x0, x1}] in plot coordinates
let _stickyRaf = 0;
let _stickyIdx = -2;   // -2 = never synced; -1 = no bands

function _syncStickyBands() {
  if (_stickyRaf) return;                 // one update per frame, no more
  _stickyRaf = requestAnimationFrame(() => {
    _stickyRaf = 0;
    _applyStickyBands();
  });
}

function _applyStickyBands() {
  if (!_ganttEl) return;
  // Keep the frozen column vertically in step. It has overflow:hidden,
  // but scrollTop is still settable.
  if (_gutterEl) _gutterEl.scrollTop = _ganttEl.scrollTop;
  if (!_stickyEl) return;
  const x = _ganttEl.scrollLeft;
  // THE LAST BAND YOU HAVE REACHED, not the one strictly under the edge.
  // Containment leaves two holes: at rest the viewport sits at x=0,
  // which is before the first band starts, and between two bands there
  // is a slice gap. Both made the label blank out — at rest it looked
  // like it only appeared once you scrolled, and crossing a boundary
  // flashed. A band owns everything from its left edge until the next
  // one begins, so there are no holes to fall into.
  let idx = _bands.length ? 0 : -1;
  for (let i = 0; i < _bands.length; i++) { if (x >= _bands[i].x0) idx = i; else break; }
  const cur = idx >= 0 ? _bands[idx].phase : null;
  if (idx === _stickyIdx) return;         // nothing changed — no DOM write
  _stickyIdx = idx;
  _stickyEl.textContent = cur || "";
  _stickyEl.hidden = !cur;
  // The pinned label STANDS IN FOR one band's own label — hide just
  // that one, or the two collide at the left edge showing the same word
  // twice. Every other band keeps its label. Runs on phase change only,
  // a handful of times per run, never per frame.
  for (let i = 0; i < _bands.length; i++) {
    const el = _bands[i].el;
    if (el) el.style.display = (i === idx) ? "none" : "";
  }
}

// ── Block detail: hover on a mouse, TAP on glass ──────────────────────
// The pill labels drop as you zoom out, which is the point — shape over
// text. The detail has to come back on demand, and on a tablet there is
// no hover, so tap is the primary path and hover only a desktop
// convenience. An SVG <title> is neither: it never fires on touch.
function _hideTip() { if (_tipEl) { _tipEl.hidden = true; } _tipPinned = null; }

function _showTip(g, pinned) {
  if (!_tipEl || !_ganttEl) return;
  const d = g.dataset;
  const st = (g.getAttribute("class") || "").split(/\s+/)
    .find(c => c.startsWith("sched-") && c !== "sched-block");
  const rows = [];
  if (d.res)   rows.push(["resource", d.res]);
  if (d.plan)  rows.push(["planned", `${d.plan}s`]);
  const el = g.querySelector(".sched-block-elapsed");
  if (el)      rows.push(["actual", el.textContent]);
  if (d.phase) rows.push(["phase", d.phase]);
  if (st)      rows.push(["state", st.replace("sched-", "")]);
  _tipEl.innerHTML =
    `<div class="sched-tip-title"></div>` +
    rows.map(([k, v]) => `<div class="sched-tip-row"><span></span><b></b></div>`).join("");
  _tipEl.querySelector(".sched-tip-title").textContent = d.label || "";
  _tipEl.querySelectorAll(".sched-tip-row").forEach((r, i) => {
    r.querySelector("span").textContent = rows[i][0];
    r.querySelector("b").textContent    = rows[i][1];
  });
  _tipEl.hidden = false;
  // Position against the PANEL, clamped inside it — the block lives in a
  // scrolled child, so its viewport rect is the only honest source.
  const panel = _tipEl.parentElement;
  const pr = panel.getBoundingClientRect();
  const br = g.getBoundingClientRect();
  const tw = _tipEl.offsetWidth, th = _tipEl.offsetHeight;
  let left = br.left - pr.left + br.width / 2 - tw / 2;
  let top  = br.top  - pr.top  - th - 10;
  if (top < 0) top = br.bottom - pr.top + 10;         // flip below
  left = Math.max(6, Math.min(left, pr.width - tw - 6));
  _tipEl.style.left = `${Math.round(left)}px`;
  _tipEl.style.top  = `${Math.round(top)}px`;
  _tipPinned = pinned ? g : null;
}

function _wireTip(host, panel) {
  host.addEventListener("pointerover", (e) => {
    if (e.pointerType === "touch" || _tipPinned) return;
    const g = e.target.closest?.(".sched-block");
    if (g) _showTip(g, false);
  });
  host.addEventListener("pointerout", (e) => {
    if (_tipPinned) return;
    const g = e.target.closest?.(".sched-block");
    if (g && !g.contains(e.relatedTarget)) _hideTip();
  });
  // Tap / click pins it, so it survives the finger leaving the pill.
  host.addEventListener("click", (e) => {
    const g = e.target.closest?.(".sched-block");
    if (!g) return;
    if (_tipPinned === g) { _hideTip(); return; }
    _showTip(g, true);
    e.stopPropagation();
  });
  panel.addEventListener("click", () => { if (_tipPinned) _hideTip(); });
  // A pinned card would otherwise float away from its pill.
  host.addEventListener("scroll", () => { if (_tipPinned) _showTip(_tipPinned, true); },
                        { passive: true });
}

// Clear the CHART. The zoom bar lives on the PANEL, not on this
// scroller, so emptying the scroller can no longer unmount it — the
// guard is kept anyway because _clearChart is also called with the
// panel in older paths and losing the controls is silent.
function _clearChart(host) {
  if (!host) return;
  for (const child of [...host.children]) {
    if (!child.classList.contains("sched-zoom")) child.remove();
  }
}

function _mountZoomControls(host) {
  if (host.querySelector(".sched-zoom")) return;   // already mounted
  if (getComputedStyle(host).position === "static") host.style.position = "relative";
  const bar = document.createElement("div");
  bar.className = "sched-zoom";
  bar.innerHTML =
    '<span class="sched-zoom-pct"></span>' +
    '<button class="sched-zoom-btn" data-z="out"  title="Zoom out"   aria-label="Zoom out">'  + _svgIcon(_ICON.out)  + '</button>' +
    '<button class="sched-zoom-btn" data-z="in"   title="Zoom in"    aria-label="Zoom in">'   + _svgIcon(_ICON.in)   + '</button>' +
    '<button class="sched-zoom-btn" data-z="home" title="Reset zoom and jump to the running action" aria-label="Reset zoom and jump to the running action">'+ _svgIcon(_ICON.home) + '</button>';
  host.appendChild(bar);
  _zoomPctEl = bar.querySelector(".sched-zoom-pct");
  _zoomOutEl = bar.querySelector('[data-z="out"]');
  _zoomInEl  = bar.querySelector('[data-z="in"]');
  bar.addEventListener("click", (e) => {
    const b = e.target.closest("[data-z]");
    if (!b) return;
    if (b.dataset.z === "in")   zoomIn();
    if (b.dataset.z === "out")  zoomOut();
    if (b.dataset.z === "home") {
      // HOME IS "TAKE ME BACK TO THE WORK", not merely 100%. Resetting
      // the zoom alone leaves you wherever you had panned to, which on a
      // 500-action plan is nowhere useful. zoomReset() re-renders (and
      // no-ops when already 1:1), then we centre the live block.
      zoomReset();
      _jumpToCurrent();
    }
  });
  _syncZoomControls();
}

// Buttons disable at the limits — §8 wants every state shipped, and
// that applies to zoom controls like anything else.
function _syncZoomControls() {
  if (_zoomPctEl) _zoomPctEl.textContent = Math.round(_zoom * 100) + "%";
  if (_zoomInEl)  _zoomInEl.disabled  = _zoom >= ZOOM_MAX - 1e-6;
  if (_zoomOutEl) _zoomOutEl.disabled = _zoom <= ZOOM_MIN + 1e-6;
}

// Called when the Schedule tab activates: render and auto-centre on
// the live block. Deferred a frame so layout exists — clientWidth is
// 0 until then.
export function showSchedule() {
  if (!_ganttEl) return;
  _renderGantt();
  requestAnimationFrame(_jumpToCurrent);
}

function _visible() {
  return !!(_ganttEl && _ganttEl.offsetParent !== null);
}

function _render() {
  if (_visible()) _renderGantt();
}

// ── SVG Gantt ──────────────────────────────────────────────────────────
function _renderGantt() {
  if (!_ganttEl) return;
  if (_slices.length === 0) {
    if (_gutterEl) _gutterEl.textContent = "";
    _bands = [];
    _stickyIdx = -2;
    if (_stickyEl) { _stickyEl.textContent = ""; _stickyEl.hidden = true; }
    _clearChart(_ganttEl);
    const empty = document.createElement("div");
    empty.className = "sched-empty";
    empty.textContent = "No plan yet.";
    _ganttEl.appendChild(empty);
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
  // SCALED by zoom (horizontal only).
  const BLOCK_GAP = 18 * _zoom;   // gap between consecutive blocks on a row
  const SLICE_GAP = 38 * _zoom;   // gap between slices (divider sits at the midpoint)
  // A band is drawn BAND_PAD wider than the blocks it wraps, on both
  // sides. The plot has to reserve that much at each end, or the first
  // band's left edge is clipped by the viewport and the margins read
  // lopsided — 8px on the left, 16 + a band pad on the right.
  const BAND_PAD  = 8 * _zoom;
  const ROW_H     = 48;
  const ROW_PAD   = 8;
  // 10px mono, letter-spacing .10em — measured, not guessed.
  const ROWLABEL_CHAR_W = 7.0;
  const TOP_PAD   = 18;
  const BOT_PAD   = 18;

  function labelOf(a)     {
    // Parameterless actions (parametrized: false) — e.g. Start / Park
    // — appear once per plan regardless of item count, so the "(0)"
    // suffix is misleading. Show just the class name for those.
    const base = a.class_name || a.name;
    return (a.parametrized === false) ? base : `${base}(${a.item})`;
  }
  // Natural width at 100% — the label-drop rule below compares against
  // this, never a second measurement.
  function naturalWidth(a) { return labelOf(a).length * CHAR_W + LABEL_PAD; }
  function neededWidth(a)  { return Math.max(naturalWidth(a) * _zoom, 10); }

  // ── Flow layout, per-slice column ─────────────────────────────────
  // X is NOT proportional to wall-clock time. Block widths come from
  // label length so every action's name is readable. Time data is
  // shown separately: as a small "Δ s" line under each *done* block
  // and in the hover tooltip.
  const placements = new Map();    // composite key -> {a, x, w, y, h, rowIdx, replan_id}
  const sliceDividerXs = [];        // x positions of dividers BETWEEN slices
  // Per-slice x-extent + phase name, merged into bands after layout.
  const sliceSpans = [];
  let xBase = BAND_PAD + 8;   // the gutter owns the label column now
  _leafOrder.length = 0;
  _leafGeom.clear();

  for (let sliceIdx = 0; sliceIdx < _slices.length; sliceIdx++) {
    const slice = _slices[sliceIdx];
    const sliceActions = slice.actions || [];
    const sliceReplanId = slice.replan_id || 0;

    // Chart time = planner's predicted ``start_t`` / ``duration``.
    // The planner already enforces causal ordering (and same-resource
    // mutex), so we use *its* numbers for layout decisions. Actual
    // wall-clock timing surfaces separately — as the elapsed-time
    // text under done blocks and in the hover tooltip.
    const chartStart = (a) => a.start_t || 0;
    const chartEnd   = (a) => (a.start_t || 0) + (a.duration || 0);

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
        const sameRow = other.rowIdx === p.rowIdx;
        // Two reasons to push past ``other``:
        //   1. Same-row: physically the same resource, must be
        //      sequential regardless of how the planner's start_t
        //      values look — defensive against quirks in the chart
        //      time fields.
        //   2. Causal predecessor: ``other`` ends at or before this
        //      block's chartStart, so it's strictly earlier in time.
        if (sameRow || chartEnd(other.a) <= ps) {
          const right = other.x + other.w + (sameRow ? BLOCK_GAP : 8);
          if (right > nx) nx = right;
        }
      }
      p.x = nx;
      placed.push(p);
    }

    const sliceMinX = xBase;
    let sliceMaxX = xBase;
    const sliceByStart = [...local.values()].sort(
      (a, b) => chartStart(a.a) - chartStart(b.a),
    );
    for (const p of sliceByStart) {
      if (p.x + p.w > sliceMaxX) sliceMaxX = p.x + p.w;
      p.phase = slice.phase || null;
      const key = _leafKey(sliceReplanId, p.a.leaf_name);
      placements.set(key, p);
      _leafOrder.push(key);
      _leafGeom.set(key, { x: p.x, w: p.w });
    }

    if (sliceIdx + 1 < _slices.length) {
      sliceDividerXs.push(sliceMaxX + SLICE_GAP / 2);
    }
    sliceSpans.push({
      phase: slice.phase || null, x0: sliceMinX, x1: sliceMaxX,
    });
    xBase = sliceMaxX + SLICE_GAP;
  }

  const W = Math.max(200, xBase - SLICE_GAP + BAND_PAD + 8);
  const H = TOP_PAD + rows.length * ROW_H + BOT_PAD;

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width",  String(W));
  svg.setAttribute("height", String(H));
  svg.setAttribute("class",  "sched-svg");

  const gBands = document.createElementNS(svgNS, "g");
  const gRows = document.createElementNS(svgNS, "g");
  const gSlices = document.createElementNS(svgNS, "g");
  const gConn = document.createElementNS(svgNS, "g");
  const gBlocks = document.createElementNS(svgNS, "g");
  svg.appendChild(gBands);   // behind everything — structural, not stateful
  svg.appendChild(gRows);
  svg.appendChild(gSlices);
  svg.appendChild(gConn);
  svg.appendChild(gBlocks);

  // ── Phase bands ────────────────────────────────────────────────────
  // Consecutive slices sharing a phase merge into one rounded rect
  // behind the pills, named above its left edge. Projects that declare
  // no phases send phase=null and get no bands at all.
  // design-system.md §3.9.
  _bands = [];
  const bands = [];
  for (const sp of sliceSpans) {
    if (!sp.phase) continue;
    const last = bands[bands.length - 1];
    if (last && last.phase === sp.phase) last.x1 = sp.x1;
    else bands.push({ phase: sp.phase, x0: sp.x0, x1: sp.x1 });
  }
  bands.forEach((b, bandIdx) => {
    const w = b.x1 - b.x0;
    const r = document.createElementNS(svgNS, "rect");
    r.setAttribute("x", String(b.x0 - BAND_PAD));
    r.setAttribute("y", String(TOP_PAD - 6));
    r.setAttribute("width",  String(w + 2 * BAND_PAD));
    r.setAttribute("height", String(rows.length * ROW_H + 8));
    r.setAttribute("rx", "12");
    // Alternate the wash so a boundary is visible without reading the
    // label; the outline is on every band either way.
    r.setAttribute("class",
                   "sched-phase-band" + (bandIdx % 2 ? " is-alt" : ""));
    gBands.appendChild(r);
    // Same drop rule as the pills: no clipping, no ellipsis.
    // EVERY BAND NAMES ITSELF, at its own left edge. This label scrolls
    // WITH its band, which is why it cannot flicker — only a label
    // moving AGAINST the scroll fights the compositor. The pinned
    // overlay handles the band under the viewport edge, whose own label
    // has scrolled out of reach; this handles every other visible band,
    // which the overlay alone left anonymous.
    let lab = null;
    if (w > b.phase.length * 6.2 + 14) {
      lab = document.createElementNS(svgNS, "text");
      lab.setAttribute("x", String(b.x0 - BAND_PAD + 8));
      lab.setAttribute("y", String(TOP_PAD - 12));
      lab.setAttribute("class", "sched-phase-label");
      lab.textContent = b.phase;
      gBands.appendChild(lab);
    }
    _bands.push({ phase: b.phase, x0: b.x0 - BAND_PAD, x1: b.x1 + BAND_PAD,
                  el: lab });
  });

  // Horizontal dividers stay in the plot; the LABELS go to the frozen
  // gutter so panning cannot take them off screen.
  rows.forEach((r, i) => {
    if (i > 0) {
      const line = document.createElementNS(svgNS, "line");
      line.setAttribute("x1", "0");
      line.setAttribute("x2", String(W - 16));
      line.setAttribute("y1", String(TOP_PAD + i * ROW_H));
      line.setAttribute("y2", String(TOP_PAD + i * ROW_H));
      line.setAttribute("class", "sched-rowline");
      gRows.appendChild(line);
    }
  });

  if (_gutterEl) {
    _gutterEl.textContent = "";
    // FLUSH LEFT, and the column is only as wide as the longest name.
    // Right-aligned labels left a ragged left edge and a pool of dead
    // space beside the short ones, so the component read as three
    // different left margins. Now every label starts on the same line
    // as the panel's own inset, and one fixed gap separates them from
    // the plot.
    const GUTTER_GAP = 16;
    const gutterW = Math.ceil(
      Math.max(...rows.map(r => r.length * ROWLABEL_CHAR_W))) + GUTTER_GAP;
    const gsvg = document.createElementNS(svgNS, "svg");
    gsvg.setAttribute("width",  String(gutterW));
    gsvg.setAttribute("height", String(H));
    gsvg.setAttribute("class",  "sched-svg");
    rows.forEach((r, i) => {
      const t = document.createElementNS(svgNS, "text");
      t.setAttribute("x", "0");
      t.setAttribute("y", String(TOP_PAD + i * ROW_H + ROW_H / 2 + 4));
      t.setAttribute("text-anchor", "start");
      t.setAttribute("class", "sched-rowlabel");
      t.textContent = r;
      gsvg.appendChild(t);
    });
    _gutterEl.appendChild(gsvg);
  }

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

  _clearChart(_ganttEl);
  _ganttEl.appendChild(svg);
  // Synchronous: a rAF here would paint the labels at their band-start
  // x for one frame and then snap them, which is the very flash this
  // whole mechanism exists to avoid.
  _applyStickyBands();
}

function _appendBlock(parent, p, state) {
  const svgNS = "http://www.w3.org/2000/svg";
  const { a, x, y, w, h } = p;
  const g = document.createElementNS(svgNS, "g");
  g.setAttribute("class", `sched-block sched-${state}`);
  // Stable key so surgical state transitions (action_start /
  // action_end / swap_start / swap_end) can locate and patch this
  // block in place instead of forcing a full SVG rebuild.
  g.setAttribute("data-leaf-key", _leafKey(p.replan_id, p.a.leaf_name));

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
  // LABEL-DROP: a pill that is narrower than its own text simply has
  // no text. Never clipped, never ellipsised. design-system.md §3.9.
  // Facts for the detail popover, read straight off the element so the
  // handler needs no parallel index that could fall out of step. MUST
  // come after `label` — it is a const in this scope.
  g.dataset.label = label;
  if (a.resources && a.resources.length) g.dataset.res = a.resources.join(", ");
  if (a.duration != null) g.dataset.plan = String(a.duration);
  if (p.phase) g.dataset.phase = p.phase;
  const _natural = label.length * CHAR_W + LABEL_PAD;
  const lab = (w >= _natural * 0.92) ? document.createElementNS(svgNS, "text") : null;
  if (lab) {
    lab.setAttribute("x", String(x + w / 2));
    lab.setAttribute("y", String(y + h / 2 + 4));
    lab.setAttribute("text-anchor", "middle");
    lab.setAttribute("class", "sched-blocklabel");
    lab.textContent = label;
    g.appendChild(lab);
  }

  // Duration text under DONE blocks only — pending / running blocks
  // have nothing useful to show yet.
  const timing = _leafTiming.get(_leafKey(p.replan_id, p.a.leaf_name)) || {};
  const elapsed = (timing.startedAt != null && timing.endedAt != null)
    ? (timing.endedAt - timing.startedAt)
    : null;
  // SAME DROP RULE AS THE LABEL. When the pill is too narrow to name
  // itself, a duration underneath is noise at exactly the zoom level
  // where the operator wants shape, not numbers. One rule, not two.
  if (elapsed != null && lab && (state === "done" || state === "skipped")) {
    const dur = document.createElementNS(svgNS, "text");
    dur.setAttribute("x", String(x + w / 2));
    dur.setAttribute("y", String(y + h + 11));
    dur.setAttribute("text-anchor", "middle");
    dur.setAttribute("class", "sched-block-elapsed");
    dur.textContent = `${elapsed.toFixed(1)}s`;
    g.appendChild(dur);
  }

  // NO <title> HERE. The native tooltip never fires on touch and cannot
  // be styled; _showTip covers both pointer kinds. See _wireTip.

  parent.appendChild(g);
}

function _primaryRow(rows, resources, toolRes) {
  if (!resources || !resources.length) return rows.indexOf(toolRes);
  for (const r of resources) {
    if (r !== toolRes && rows.includes(r)) return rows.indexOf(r);
  }
  return rows.indexOf(resources[0]);
}

// ── Pendant hero feed ──────────────────────────────────────────────────
// Pure read over the ingested plan: the LAST slice is the current
// routine. Returns step counts, the label chain, the running index,
// a live within-step fraction (wall clock vs planned duration), and
// the NEXT action + its ETA. Null until a plan exists.
export function getScheduleCounts() {
  if (!_slices.length) return null;
  const slice = _slices[_slices.length - 1];
  const acts = slice.actions || [];
  if (!acts.length) return null;
  const rid = slice.replan_id || 0;
  const labelOf = (a) => (a.parametrized === false)
    ? (a.class_name || a.name) : `${a.class_name || a.name}(${a.item})`;
  let done = 0, curIdx = -1;
  acts.forEach((a, i) => {
    const st = _leafState.get(_leafKey(rid, a.leaf_name || a.name)) || "pending";
    if (st === "done" || st === "skipped") done++;
    else if (st === "running" && curIdx < 0) curIdx = i;
  });
  let frac = 0, nextLabel = "", nextEta = null;
  if (curIdx >= 0) {
    const a = acts[curIdx];
    const t = _leafTiming.get(_leafKey(rid, a.leaf_name || a.name)) || {};
    const now = Date.now() / 1000;
    if (t.startedAt != null && a.duration) {
      frac = Math.min(0.95, Math.max(0, (now - t.startedAt) / a.duration));
      nextEta = Math.max(0, a.duration - (now - t.startedAt));
    }
    if (curIdx + 1 < acts.length) nextLabel = labelOf(acts[curIdx + 1]);
  }
  return { total: acts.length, done, curIdx, frac,
           labels: acts.map(labelOf), nextLabel, nextEta };
}
