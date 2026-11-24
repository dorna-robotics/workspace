// The project's workflow flowchart — ONE per project, describing one
// sample — over the 3D view, opened from the Workflow panel. Ported from
// the AI builder (builder/web/builder/ai_flow.js).
//
// JointJS (vendor/joint.min.js, MPL-2.0) draws and edits it; dagre
// (vendor/dagre.min.js, MIT) lays it out. The document is the project's
// flow/<name>.json, the same JSON the AI builder writes: Save runs the
// chart's checks (devmode/workflow.py) and stores it only when it checks
// out. Positions are kept in the chart as a hint ("at"). Building
// actions.py from the chart is the AI builder's — not done here.
import { esc, toast } from "/orchestrator/api.js";

const KINDS = {
  start:    { label: "Start",     shape: "round",  fill: "#8f3a34", text: "#fff" },
  end:      { label: "End",       shape: "round",  fill: "#5b8a3a", text: "#fff" },
  step:     { label: "Step",      shape: "rect",   fill: "#e8f1fb", stroke: "#3a7bd5" },
  measure:  { label: "Measure",   shape: "para",   fill: "#efe7fb", stroke: "#7a4fd6" },
  decision: { label: "Decision",  shape: "diamond", fill: "#e6f4f1", stroke: "#2e8b7a" },
  hmi:      { label: "Screen",    shape: "double", fill: "#fdf1e3", stroke: "#d68a2e" },
  manual:   { label: "Manual",    shape: "trap",   fill: "#f6e9e6", stroke: "#b5654a" },
  external: { label: "External",  shape: "trap",   fill: "#f6e9e6", stroke: "#b5654a" },
};
// the chart follows the theme: in dark, a node is its hue at a quarter over
// the canvas with light text; edge labels sit on the canvas colour
const dark = () => document.documentElement.getAttribute("data-theme") !== "light";
const mixHex = (a, b, t) => {
  const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const A = p(a), B = p(b);
  return "#" + A.map((v, i) => Math.round(v * t + B[i] * (1 - t)).toString(16).padStart(2, "0")).join("");
};
const FONT = "Inter, -apple-system, system-ui, sans-serif";
function colors(k) {
  if (!dark()) return { fill: k.fill, stroke: k.stroke || k.fill, text: k.text || "#1c2430" };
  if (k.text) return { fill: k.fill, stroke: k.fill, text: k.text };
  return { fill: mixHex(k.stroke, "#121726", 0.24), stroke: mixHex(k.stroke, "#ffffff", 0.8), text: "#e9edf5" };
}
const EDGE = () => dark() ? { line: "#7d8797", text: "#b4bccb", box: "#141a28" } : { line: "#8a94a0", text: "#4f5a68", box: "#f4f6fa" };
const edgeLabelAttrs = (t) => ({ attrs: { text: { text: t, fontSize: 12, fontFamily: FONT, fontWeight: 500, fill: EDGE().text },
  rect: { fill: EDGE().box, rx: 4, ry: 4 } } });
const ROUTER = { name: "manhattan", args: { step: 10, padding: 14, maxAllowedDirectionChange: 90 } };
const SIZE = { round: [160, 44], rect: [230, 70], para: [240, 66], diamond: [200, 110], double: [230, 70], trap: [240, 66] };
const POINTS = { diamond: "50,0 100,50 50,100 0,50", para: "12,0 100,0 88,100 0,100", trap: "0,0 100,0 88,100 12,100" };
const ico = (d, w = 16) => `<svg width="${w}" height="${w}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;

let view = null, api = null, project = "", onSaved = null;
let userView = false, paper = null, graph = null, doc = null, name = null, selected = null, dirty = false;

let libsReady = null;
function loadLibs() {
  const one = (src) => new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = src; s.onload = res; s.onerror = () => rej(new Error("couldn't load " + src));
    document.head.appendChild(s);
  });
  libsReady = libsReady || one("/dev/vendor/dagre.min.js").then(() => one("/dev/vendor/joint.min.js"));
  return libsReady;
}

// the editor's libraries are large for the unit: fetched while the
// Workflow panel is idle, so the first open is quick
export const preloadFlowchart = () => loadLibs().catch(() => {});

// ── open / close ─────────────────────────────────────────────────────
// opts: {view, api: {get, post}, project, charts, onSaved}
export async function openFlowchart(opts) {
  ({ view, api, project, onSaved } = opts);
  view.hidden = false;
  document.documentElement.classList.add("dv-flow-open");
  view.innerHTML = `<div class="dv-flow-empty">Loading the flowchart…</div>`;
  try { await loadLibs(); } catch (e) { view.innerHTML = `<div class="dv-flow-empty">${esc(e.message)}</div>`; return; }
  const mine = (opts.charts || [])[0];
  if (mine) await load(mine.name);
  else chooseStart();
}
export async function closeFlowchart() {
  if (!view || view.hidden) return true;
  if (dirty && !(await window.confirmDialog({ title: "Close without saving the flowchart?", confirm: "Close", cancel: "Keep editing" }))) return false;
  view.hidden = true;
  document.documentElement.classList.remove("dv-flow-open");
  view.innerHTML = "";
  paper = graph = null;
  dirty = false;
  return true;
}
export const flowchartOpen = () => !!view && !view.hidden;

function chooseStart() {
  view.innerHTML = `<div class="dv-flow-empty"><h3>No flowchart yet</h3>
    <p>A project has one workflow, for one sample — the scheduler runs the batch. Describe it to the AI, or start a blank chart here.</p>
    <div class="dv-flow-row dv-flow-center">
      <button class="btn btn-primary" id="afBlank" type="button">Blank chart</button>
      <button class="btn btn-ghost" id="afClose" type="button">Close</button></div></div>`;
  view.querySelector("#afClose").onclick = closeFlowchart;
  view.querySelector("#afBlank").onclick = () => {
    name = (project || "workflow").replace(/[^A-Za-z0-9_-]+/g, "_");
    doc = { id: name, title: (project || "Workflow").replace(/_/g, " "), item: { name: "sample", label: "Samples" },
            lanes: [], nodes: [{ id: "start", kind: "start", label: "Start" }, { id: "end", kind: "end", label: "End" }],
            edges: [{ from: "start", to: "end" }], notes: [] };
    build(); dirty = true; status();
  };
}

async function load(n) {
  let r;
  try { r = await api.get(`/flowchart?name=${encodeURIComponent(n)}`); }
  catch (e) { view.innerHTML = `<div class="dv-flow-empty">${esc(e.message)}</div>`; return; }
  name = n; doc = r.doc; dirty = false;
  build();
  status(r.check);
}

// ── drawing ──────────────────────────────────────────────────────────
function frame() {
  // two rows: the title, its state and Close; then Add ▾ | Arrange, Delete … Save
  view.innerHTML = `<div class="dv-flow-bar">
      <div class="dv-flow-row"><b id="afTitle" class="dv-flow-title"></b><span class="dv-flow-pill" id="afState"></span><span class="spacer"></span>
        <button class="btn btn-ghost btn-icon dv-flow-x" id="afClose" type="button" title="Close" aria-label="Close">${ico('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>')}</button></div>
      <div class="dv-flow-row dv-flow-tools">
        <div class="dv-flow-add"><button class="btn" id="afAdd" type="button" aria-haspopup="menu" aria-expanded="false">${ico('<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>', 14)}Add${ico('<polyline points="6 9 12 15 18 9"/>', 12)}</button>
          <div class="dv-flow-menu" id="afAddMenu" role="menu" hidden>${["step", "measure", "decision", "hmi", "manual"].map((k) => `<button role="menuitem" type="button" data-add="${k}"><i class="dv-flow-sw" style="--sw:${KINDS[k].stroke}"></i>${KINDS[k].label}</button>`).join("")}</div></div>
        <span class="dv-flow-sep" aria-hidden="true"></span>
        <button class="btn btn-ghost" id="afArrange" type="button" title="Lay the chart out again">Arrange</button>
        <button class="btn btn-ghost" id="afDelete" type="button" title="Delete the selection (Delete key)">Delete</button>
        <span class="spacer"></span>
        <button class="btn btn-ghost dv-flow-sidebtn" id="afSideBtn" type="button" aria-pressed="false" title="Show the details panel">${ico('<rect x="3" y="4" width="18" height="16" rx="3"/><line x1="15" y1="4" x2="15" y2="20"/>')}<span>Details</span></button>
        <button class="btn btn-primary" id="afSave" type="button" title="Check the chart and save it to the project's flow/ folder">Save</button></div></div>
    <div class="dv-flow-body"><div class="dv-flow-paper" id="afPaper"></div><aside class="dv-flow-side" id="afSide"></aside></div>
    <div class="dv-flow-msgs" id="afMsgs"></div>`;
  view.querySelector("#afClose").onclick = closeFlowchart;
  const addBtn = view.querySelector("#afAdd"), menu = view.querySelector("#afAddMenu");
  const menuOpen = (on) => {
    menu.hidden = !on; addBtn.setAttribute("aria-expanded", String(on));
    if (on) menu.querySelector("button")?.focus();
  };
  addBtn.onclick = (e) => { e.stopPropagation(); menuOpen(menu.hidden); };
  menu.addEventListener("keydown", (e) => {
    const items = [...menu.querySelectorAll("button")], i = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); items[(i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length].focus(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); menuOpen(false); addBtn.focus(); }
    else if (e.key === "Tab") menuOpen(false);
  });
  view.addEventListener("pointerdown", (e) => { if (!menu.hidden && !e.target.closest(".dv-flow-add")) menuOpen(false); });
  const sideBtn = view.querySelector("#afSideBtn");
  sideBtn.onclick = () => { const on = view.classList.toggle("side-open"); sideBtn.setAttribute("aria-pressed", String(on)); };
  view.querySelector("#afArrange").onclick = () => { arrange(true); dirty = true; status(); };
  view.querySelector("#afDelete").onclick = removeSelected;
  view.querySelector("#afSave").onclick = save;
  for (const b of view.querySelectorAll("[data-add]")) b.onclick = () => { menuOpen(false); addNode(b.dataset.add); };
}

function shapeFor(n) {
  const k = KINDS[n.kind] || KINDS.step;
  const [w, h] = SIZE[k.shape];
  const J = window.joint;
  const common = { size: { width: w, height: h }, nodeId: n.id };
  const c = colors(k);
  const label = { text: n.label || n.id, fontSize: 13, fontFamily: FONT, fontWeight: 500, fill: c.text,
                  textWrap: { width: /^(rect|round|double)$/.test(k.shape) ? -20 : k.shape === "diamond" ? -90 : -48, height: k.shape === "diamond" ? -50 : -10, ellipsis: true } };
  const ports = { groups: {
    out: { position: "bottom", markup: [{ tagName: "circle", selector: "portBody" }],
           attrs: { portBody: { r: 5, magnet: true, fill: dark() ? "#1b2232" : "#fff", stroke: EDGE().line, cursor: "crosshair" } } } },
    items: n.kind === "end" ? [] : [{ group: "out", id: "out" }] };
  let el;
  if (k.shape === "rect" || k.shape === "round" || k.shape === "double") {
    el = new J.shapes.standard.Rectangle({ ...common, ports, attrs: {
      body: { fill: c.fill, stroke: c.stroke, strokeWidth: k.shape === "double" ? 3 : 1.5,
              rx: k.shape === "round" ? h / 2 : 6, ry: k.shape === "round" ? h / 2 : 6,
              strokeDasharray: n.kind === "manual" ? "4 3" : "" }, label } });
  } else {
    el = new J.shapes.standard.Polygon({ ...common, ports, attrs: {
      body: { fill: c.fill, stroke: c.stroke, strokeWidth: 1.5, refPoints: POINTS[k.shape] }, label } });
  }
  el.set({ nodeId: n.id, z: 2 });          // above the arrows, so its dot stays grabbable
  return el;
}

const edgeLabel = (e) => e.label || (Array.isArray(e.when) ? e.when.join(", ") : "");

function linkFor(e, byId) {
  const J = window.joint;
  const l = new J.shapes.standard.Link({ source: { id: byId[e.from].id }, target: { id: byId[e.to].id },
    router: ROUTER, connector: { name: "rounded" },
    attrs: { line: { stroke: EDGE().line, strokeWidth: 1.4, targetMarker: { type: "path", d: "M 8 -4 0 0 8 4 z" } } } });
  const t = edgeLabel(e);
  if (t) l.labels([edgeLabelAttrs(t)]);
  l.set({ edge: { ...e }, z: 1 });
  return l;
}

function build() {
  frame();
  const J = window.joint;
  graph = new J.dia.Graph({}, { cellNamespace: J.shapes });
  const host = view.querySelector("#afPaper");
  paper = new J.dia.Paper({
    el: host, model: graph, width: "100%", height: "100%", gridSize: 10,
    cellViewNamespace: J.shapes, background: { color: "transparent" },
    linkPinning: false, snapLinks: { radius: 30 }, markAvailable: true,
    defaultLink: () => new J.shapes.standard.Link({ router: ROUTER, connector: { name: "rounded" },
      attrs: { line: { stroke: EDGE().line, strokeWidth: 1.4, targetMarker: { type: "path", d: "M 8 -4 0 0 8 4 z" } } } }),
    validateConnection: (vs, ms, vt) => !!vt && vs !== vt && vt.model.isElement(),
    interactive: { linkMove: false, labelMove: false },
  });
  const byId = {};
  for (const n of doc.nodes || []) {
    if (n.kind === "note") continue;
    const el = shapeFor(n);
    byId[n.id] = el;
    graph.addCell(el);
  }
  for (const e of doc.edges || []) if (byId[e.from] && byId[e.to]) graph.addCell(linkFor(e, byId));
  const placed = (doc.nodes || []).every((n) => n.kind === "note" || Array.isArray(n.at));
  if (placed) for (const n of doc.nodes || []) { if (byId[n.id]) byId[n.id].position(n.at[0], n.at[1]); }
  else arrange(false);
  fit();
  // the canvas changes size: fit again, unless the chart was panned or zoomed by hand
  userView = false;
  if (window.ResizeObserver && !host.__afRO) {
    let w0 = host.clientWidth;
    host.__afRO = new ResizeObserver(() => { if (host.clientWidth !== w0) { w0 = host.clientWidth; if (!userView) fit(); } });
    host.__afRO.observe(host);
  }

  paper.on("element:pointerclick", (v) => select(v.model));
  paper.on("link:pointerclick", (v) => select(v.model));
  paper.on("blank:pointerclick", () => select(null));
  paper.on("element:pointerup", () => { dirty = true; status(); });
  paper.on("link:connect", (v) => {
    const src = v.model.getSourceElement(), tgt = v.model.getTargetElement();
    const twice = graph.getLinks().some((l) => l !== v.model && l.getSourceElement() === src && l.getTargetElement() === tgt);
    if (twice) { v.model.remove(); toast("Those two are already connected", "warn"); return; }
    v.model.set("edge", { from: src.get("nodeId"), to: tgt.get("nodeId") });
    v.model.router(ROUTER); v.model.connector("rounded");
    dirty = true; status(); select(v.model);
  });
  // pan with the empty canvas, zoom with the wheel
  let pan = null;
  paper.on("blank:pointerdown", (evt) => { pan = { x: evt.clientX, y: evt.clientY, t: paper.translate() }; });
  host.addEventListener("pointermove", (e) => { if (pan) { userView = true; paper.translate(pan.t.tx + e.clientX - pan.x, pan.t.ty + e.clientY - pan.y); } });
  window.addEventListener("pointerup", () => { pan = null; });
  host.addEventListener("wheel", (e) => {
    e.preventDefault();
    userView = true;
    const s = Math.max(0.2, Math.min(2.5, paper.scale().sx * (e.deltaY < 0 ? 1.1 : 0.9)));
    paper.scale(s, s);
  }, { passive: false });
  select(null);
}

function arrange(refit) {
  const g = new window.dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 36, ranksep: 46, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const el of graph.getElements()) g.setNode(el.id, { width: el.size().width, height: el.size().height });
  for (const l of graph.getLinks()) {
    const s = l.getSourceElement(), t = l.getTargetElement();
    if (s && t) g.setEdge(s.id, t.id);
  }
  window.dagre.layout(g);
  for (const el of graph.getElements()) {
    const p = g.node(el.id);
    el.position(p.x - p.width / 2, p.y - p.height / 2);
  }
  if (refit) fit();
}

// fit the chart: all of its width (down to 45 %); a tall chart stops at 80 % and pans
function fit() {
  if (!paper || !graph.getElements().length) return;
  const host = view.querySelector("#afPaper");
  if (!host || !host.clientWidth) return;
  const box = graph.getBBox();
  if (!box) return;
  const s = Math.max(0.45, Math.min(1, (host.clientWidth - 48) / box.width, Math.max(0.8, (host.clientHeight - 48) / box.height)));
  paper.scale(s, s);
  paper.translate(Math.max(24, (host.clientWidth - box.width * s) / 2) - box.x * s, 24 - box.y * s);
}
// the theme changed while the chart is open: repaint nodes, arrows and labels
function repaint() {
  if (!graph) return;
  for (const el of graph.getElements()) {
    const n = (doc.nodes || []).find((x) => x.id === el.get("nodeId"));
    const c = colors(KINDS[(n || {}).kind] || KINDS.step);
    el.attr({ body: { fill: c.fill, stroke: c.stroke }, label: { fill: c.text } });
    for (const p of el.getPorts()) el.portProp(p.id, "attrs/portBody", { fill: dark() ? "#1b2232" : "#fff", stroke: EDGE().line });
  }
  for (const l of graph.getLinks()) {
    l.attr("line/stroke", EDGE().line);
    const t = edgeLabel(l.get("edge") || {});
    if (t) l.labels([edgeLabelAttrs(t)]);
  }
}
new MutationObserver(() => { if (flowchartOpen()) repaint(); })
  .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

// ── selection + the side panel ───────────────────────────────────────
const nodeOf = (el) => doc.nodes.find((n) => n.id === el.get("nodeId"));

function select(cell) {
  selected = cell;
  if (cell) { view.classList.add("side-open"); view.querySelector("#afSideBtn")?.setAttribute("aria-pressed", "true"); }
  for (const el of graph.getCells()) {
    const v = paper.findViewByModel(el);
    if (v) v.el.classList.toggle("af-sel", el === cell);
  }
  side();
}

function side() {
  const box = view.querySelector("#afSide");
  if (!selected) {
    box.innerHTML = `<div class="section-label">Flowchart</div>
      <label class="dv-kw"><span>Title</span><input class="input" id="afDocTitle" value="${esc(doc.title || "")}"></label>
      <label class="dv-kw"><span>One item is a</span><input class="input" id="afItem" value="${esc((doc.item || {}).name || "sample")}"></label>
      <div class="muted">A project has one workflow, for one sample — the scheduler runs the batch. Drag from a node's dot to connect; click to edit; drag the canvas to pan, wheel to zoom.</div>
      ${(doc.notes || []).length ? `<div class="section-label">Notes</div>${doc.notes.map((n) => `<div class="muted">${esc(n.text)}</div>`).join("")}` : ""}`;
    box.querySelector("#afDocTitle").oninput = (e) => { doc.title = e.target.value; dirty = true; status(); };
    box.querySelector("#afItem").oninput = (e) => { doc.item = { ...(doc.item || {}), name: e.target.value.trim() || "sample" }; dirty = true; status(); };
    return;
  }
  if (selected.isLink()) {
    const e = selected.get("edge") || {};
    const src = doc.nodes.find((n) => n.id === e.from) || {};
    const m = src.kind === "decision" && (src.on || {}).measure ? doc.nodes.find((n) => n.id === src.on.measure) : null;
    box.innerHTML = `<div class="section-label">Arrow</div>
      <div class="muted">${esc(src.label || e.from)} → ${esc((doc.nodes.find((n) => n.id === e.to) || {}).label || e.to)}</div>
      <label class="dv-kw"><span>Label</span><input class="input" id="afEdgeLabel" value="${esc(e.label || "")}" placeholder="Yes / No"></label>
      ${m ? `<div class="section-label">When the reading is</div>${(m.outcomes || []).map((o) => `<label class="dv-check"><input type="checkbox" data-when="${esc(o)}" ${(e.when || []).includes(o) ? "checked" : ""}> ${esc(o)}</label>`).join("")}` : ""}`;
    box.querySelector("#afEdgeLabel").oninput = (ev) => { e.label = ev.target.value; selected.set("edge", e); relabel(selected); };
    for (const c of box.querySelectorAll("[data-when]")) c.onchange = () => {
      e.when = [...box.querySelectorAll("[data-when]:checked")].map((x) => x.dataset.when);
      selected.set("edge", e); relabel(selected);
    };
    return;
  }
  const n = nodeOf(selected);
  const lanes = doc.lanes || [];
  const f = (key, label, v, type = "text", extra = "") =>
    `<label class="dv-kw"><span>${label}</span><input class="input" data-k="${key}" type="${type}" value="${esc(v ?? "")}" ${extra}></label>`;
  const measures = doc.nodes.filter((x) => x.kind === "measure");
  const on = n.on || {};
  const onKind = ["measure", "field", "answer", "items"].find((k) => k in on) || "measure";
  box.innerHTML = `<div class="section-label">${esc((KINDS[n.kind] || {}).label || n.kind)}</div>
    <label class="dv-kw"><span>Text</span><textarea class="input" data-k="label" rows="3">${esc(n.label || "")}</textarea></label>
    <label class="dv-kw"><span>Kind</span><select class="input" data-k="kind">${Object.keys(KINDS).map((k) => `<option value="${k}" ${k === n.kind ? "selected" : ""}>${KINDS[k].label}</option>`).join("")}</select></label>
    <label class="dv-kw"><span>Lane</span><select class="input" data-k="lane"><option value="">—</option>${lanes.map((l) => `<option value="${esc(l.id)}" ${l.id === n.lane ? "selected" : ""}>${esc(l.label)}</option>`).join("")}<option value="__new">New lane…</option></select></label>
    ${n.kind === "step" || n.kind === "measure" ? f("duration", "Time estimate (s)", n.duration, "number", 'min="1"')
      + f("tool", "Tool", n.tool) + f("device", "Device (when not the robot)", n.device)
      + `<label class="dv-kw"><span>Runs</span><select class="input" data-k="scope"><option value="item" ${n.scope !== "run" ? "selected" : ""}>once per ${esc((doc.item || {}).name || "sample")}</option><option value="run" ${n.scope === "run" ? "selected" : ""}>once per run</option></select></label>` : ""}
    ${n.kind === "measure" ? f("outcomes", "Outcomes (first = expected)", (n.outcomes || []).join(", ")) : ""}
    ${n.kind === "decision" ? `<label class="dv-kw"><span>Decided by</span><select class="input" id="afOnKind">
        <option value="measure" ${onKind === "measure" ? "selected" : ""}>a reading</option>
        <option value="field" ${onKind === "field" ? "selected" : ""}>the sample's data</option>
        <option value="items" ${onKind === "items" ? "selected" : ""}>more samples?</option>
        <option value="answer" ${onKind === "answer" ? "selected" : ""}>the operator (before the run)</option></select></label>
      ${onKind === "measure" ? `<label class="dv-kw"><span>Reading</span><select class="input" id="afOnMeasure">${measures.map((m) => `<option value="${esc(m.id)}" ${m.id === on.measure ? "selected" : ""}>${esc(m.label)}</option>`).join("")}</select></label>` : ""}
      ${onKind === "field" ? f("on_field", "Field", on.field) + f("on_is", "Is", on.is) : ""}
      ${f("max_attempts", "Loop limit (tries)", n.max_attempts, "number", 'min="1"')}
      <label class="dv-kw"><span>When the tries run out</span><select class="input" data-k="exhausted"><option value="">—</option>${doc.nodes.filter((x) => x.id !== n.id).map((x) => `<option value="${esc(x.id)}" ${x.id === n.exhausted ? "selected" : ""}>${esc(String(x.label || x.id).slice(0, 60))}</option>`).join("")}</select></label>` : ""}
    ${n.filled ? `<div class="section-label">Filled in</div>${n.filled.map((x) => `<div class="muted">${esc(x.was)} → <b>${esc(x.now)}</b> · ${esc(x.from)}</div>`).join("")}` : ""}
    <div class="muted">id: ${esc(n.id)}</div>`;
  const setVal = (k, v) => {
    if (v === "" || v == null) delete n[k]; else n[k] = v;
    dirty = true; status();
  };
  for (const el of box.querySelectorAll("[data-k]")) {
    const handler = () => {
      const k = el.dataset.k;
      let v = el.value;
      if (k === "lane" && v === "__new") {
        const label = window.prompt("Lane name");
        if (!label) { el.value = n.lane || ""; return; }
        const id = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "lane";
        doc.lanes = [...(doc.lanes || []), { id, label, kind: "process" }];
        v = id;
      }
      if (k === "duration" || k === "max_attempts") v = v === "" ? "" : Math.max(1, parseInt(v, 10) || 1);
      if (k === "outcomes") v = v.split(",").map((x) => x.trim()).filter(Boolean);
      if (k === "on_field") { n.on = { ...(n.on || {}), field: v }; delete n.on.measure; dirty = true; status(); return; }
      if (k === "on_is") { n.on = { ...(n.on || {}), is: v === "true" ? true : v === "false" ? false : v }; dirty = true; status(); return; }
      if (k === "scope" && v === "item") v = "";
      setVal(k, v);
      if (k === "label") selected.attr("label/text", v || n.id);
      if (k === "kind" || k === "lane") redraw(n);
    };
    el.addEventListener(el.tagName === "SELECT" ? "change" : "input", handler);
  }
  const ok = box.querySelector("#afOnKind");
  if (ok) ok.onchange = () => {
    const kind = ok.value;
    n.on = kind === "measure" ? { measure: (measures[0] || {}).id } : kind === "items" ? { items: (doc.item || {}).name || "sample" }
      : kind === "answer" ? { answer: n.id } : { field: "", is: "" };
    dirty = true; status(); side();
  };
  const om = box.querySelector("#afOnMeasure");
  if (om) om.onchange = () => { n.on = { measure: om.value }; dirty = true; status(); };
}

function relabel(link) {
  const t = edgeLabel(link.get("edge") || {});
  link.labels(t ? [edgeLabelAttrs(t)] : []);
  dirty = true; status();
}

// a node's kind changed: swap its shape, keep its place and arrows
function redraw(n) {
  const old = selected, pos = old.position();
  const links = graph.getConnectedLinks(old).map((l) => ({ edge: l.get("edge"), out: l.getSourceElement() === old, other: l.getSourceElement() === old ? l.getTargetElement() : l.getSourceElement() }));
  old.remove();
  const el = shapeFor(n);
  el.position(pos.x, pos.y);
  graph.addCell(el);
  for (const L of links) {
    if (!L.other) continue;
    const map = { [n.id]: el, [L.other.get("nodeId")]: L.other };
    graph.addCell(linkFor(L.edge || { from: L.out ? n.id : L.other.get("nodeId"), to: L.out ? L.other.get("nodeId") : n.id }, map));
  }
  select(el);
}

function addNode(kind) {
  const base = kind + "_";
  let i = 1;
  while (doc.nodes.some((n) => n.id === base + i)) i++;
  const n = { id: base + i, kind, label: `New ${KINDS[kind].label.toLowerCase()}` };
  if (kind === "measure") n.outcomes = ["ok", "not_ok"];
  if (kind === "decision") n.on = { measure: (doc.nodes.find((x) => x.kind === "measure") || {}).id };
  if (kind === "step" || kind === "measure") n.duration = 30;
  if (selected && selected.isElement()) n.lane = nodeOf(selected).lane;
  doc.nodes.push(n);
  const el = shapeFor(n);
  const r = view.getBoundingClientRect();
  const c = paper.clientToLocalPoint({ x: r.left + 200, y: r.top + 120 });
  el.position(c.x, c.y);
  graph.addCell(el);
  dirty = true; status(); select(el);
}

function removeSelected() {
  if (!selected) return;
  if (selected.isElement()) {
    const id = selected.get("nodeId");
    doc.nodes = doc.nodes.filter((n) => n.id !== id);
  }
  selected.remove();
  dirty = true; status(); select(null);
}
document.addEventListener("keydown", (e) => {
  if (!flowchartOpen() || !selected) return;
  if ((e.key === "Delete" || e.key === "Backspace") && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
    e.preventDefault(); removeSelected();
  }
});

// ── save ─────────────────────────────────────────────────────────────
function toDoc() {
  const byEl = {};
  for (const el of graph.getElements()) byEl[el.id] = el.get("nodeId");
  const kept = new Set(Object.values(byEl));
  for (const n of doc.nodes) {
    const el = graph.getElements().find((x) => x.get("nodeId") === n.id);
    if (el) { const p = el.position(); n.at = [Math.round(p.x), Math.round(p.y)]; }
  }
  doc.nodes = doc.nodes.filter((n) => n.kind === "note" || kept.has(n.id));
  doc.edges = graph.getLinks().map((l) => {
    const e = { ...(l.get("edge") || {}) };
    e.from = byEl[l.getSourceElement() && l.getSourceElement().id] || e.from;
    e.to = byEl[l.getTargetElement() && l.getTargetElement().id] || e.to;
    if (!e.label) delete e.label;
    if (!e.when || !e.when.length) delete e.when;
    return e;
  }).filter((e) => e.from && e.to);
  return doc;
}

async function save() {
  let r;
  try { r = await api.post("/flowchart", { name, doc: toDoc() }); }
  catch (e) { toast(e.message || "couldn't save", "bad"); return; }
  if (r.saved) { dirty = false; toast("Flowchart saved"); onSaved && onSaved(); }
  else toast("Not saved — the chart doesn't check out (see below)", "bad");
  status(r.check);
}

function status(check) {
  const t = view.querySelector("#afTitle"), s = view.querySelector("#afState"), m = view.querySelector("#afMsgs");
  if (t) { t.textContent = doc ? (doc.title || name) : ""; t.title = t.textContent; }
  if (s) { s.textContent = dirty ? "Unsaved changes" : "Saved"; s.classList.toggle("dirty", !!dirty); }
  if (check && m) {
    m.innerHTML = (check.errors || []).map((e) => `<div class="dv-msg bad">✗ ${esc(e)}</div>`).join("")
      + ((check.warnings || []).length ? `<details><summary class="muted">${check.warnings.length} warning(s)</summary>${check.warnings.map((w) => `<div class="muted">${esc(w)}</div>`).join("")}</details>` : "")
      + (check.ok ? `<div class="muted">✓ The chart checks out.</div>` : "");
  }
}
