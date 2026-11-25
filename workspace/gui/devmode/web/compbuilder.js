// Developer mode — the component builder, a dialog over the Scene tab
// (the AI builder's New component editor, ai_editor.js, on the unit).
// Upload a GLB → it's measured (mm, bounding box) and looked at from above
// (its holes and pegs: centre, diameter, floor / top) → the kind's anchors
// (a tube's cap_seat, a rack's A1… at the hole floors) → adjust them by
// hand or ask the assistant, with a live 3D preview → Save writes
// components/<type>/ (<type>.py, <type>.glb, __init__.py) into the project
// (handlers.py PartHandler → parts.py). Editing a project part loads it back.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const r1 = (v) => Math.round(v * 10) / 10;
const jpost = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
  .then((x) => x.json()).catch((e) => ({ ok: false, error: e.message }));
const confirmIt = (title, message, confirm, variant = "danger") =>
  window.confirmDialog ? window.confirmDialog({ title, message, confirm, variant }) : Promise.resolve(window.confirm(message));

const ICON = {
  grid: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>',
  boxes: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>',
  edges: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5 12 2"/><line x1="12" y1="2" x2="12" y2="22"/><line x1="2" y1="8.5" x2="22" y2="8.5"/><line x1="2" y1="15.5" x2="22" y2="15.5"/></svg>',
  tags: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.6 13.4l-7.2 7.2a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/><circle cx="7.5" cy="7.5" r="1.5"/></svg>',
  x: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  chev: '<svg class="cb-chev" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>',
};

let st = null;   // the open builder's state

// openComponentBuilder({api, type?, onSaved, inUse}) — api is the project's
// /dev/api/projects/<name> base; type edits that project part; inUse(type)
// names the parts of that type in the Scene builder's scene, saved or not.
export function openComponentBuilder({ api, type: editType = null, onSaved = null, inUse = () => [] }) {
  close();
  const draco = new DRACOLoader();
  draco.setDecoderPath("/vendor/three-addons/draco/");
  st = {
    api, onSaved, inUse, draco, glbB64: null, dims: null, sel: null, top: null, holes: [], pegs: [], levels: [],
    anchors: [], boxes: [], history: [], editing: editType, kind: null, kindSet: false, settings: {}, touched: false,
    showBoxes: true, showGrid: true, showEdges: true, showTags: true,
  };
  const bg = document.createElement("div");
  bg.className = "cb-bg";
  bg.innerHTML = `
    <div class="cb" role="dialog" aria-modal="true" aria-labelledby="cbTitle">
      <div class="cb-head"><h3 id="cbTitle">${editType ? `Edit component · ${esc(editType)}` : "Build a component"}</h3>
        <button class="btn btn-ghost btn-icon" data-close title="Close" aria-label="Close">${ICON.x}</button></div>
      <div class="cb-body">
        <div class="cb-view"><canvas></canvas><div class="cb-drop">Drop a .glb here, or choose a file on the right</div>
          <div class="cb-tags"></div>
          <div class="cb-ctrl">
            <button class="on" id="cbGrid" type="button" title="Grid" aria-label="Grid" aria-pressed="true">${ICON.grid}</button>
            <button class="on" id="cbCol" type="button" title="Collision boxes" aria-label="Collision boxes" aria-pressed="true">${ICON.boxes}</button>
            <button class="on" id="cbEdges" type="button" title="Edge outlines" aria-label="Edge outlines" aria-pressed="true">${ICON.edges}</button>
            <button class="on" id="cbTagsBtn" type="button" title="Anchor names" aria-label="Anchor names" aria-pressed="true">${ICON.tags}</button></div></div>
        <div class="cb-panel">
          <div class="dv-kw"><span>Model (.glb, mm, origin at the base center)</span>
            <div class="cb-row"><label class="btn btn-primary" id="cbFileBtn">Choose a .glb…<input type="file" accept=".glb" id="cbFile" hidden></label>
              <span class="muted dv-small" id="cbDims"></span></div></div>
          <div class="cb-row">
            <label class="dv-kw"><span>Units in the file</span><select class="input" id="cbUnits">
              <option value="1">millimetres</option><option value="10">centimetres</option><option value="1000">metres</option><option value="25.4">inches</option></select></label>
            <label class="dv-kw"><span>Up axis in the file</span><select class="input" id="cbUp">
              <option value="z">Z up</option><option value="y">Y up (glTF standard)</option><option value="x">X up</option></select></label></div>
          <label class="dv-kw"><span>Part id</span><input class="input" id="cbType" placeholder="e.g. reagent_holder_2x" spellcheck="false"></label>
          <label class="dv-kw"><span>Description</span><input class="input" id="cbDesc" placeholder="What it is and how it's used"></label>
          <div class="cb-row"><label class="dv-kw"><span>Kind</span><select class="input" id="cbKind"></select></label>
            <div class="cb-row" id="cbSet"></div></div>
          <div class="muted dv-small" id="cbKindNote">The kind is guessed from the model's shape and name — a tube, rack, cap holder… starts with the anchors Workspace's own parts of that kind have.</div>

          <div class="cb-sec"><span class="section-label">Ask the assistant</span></div>
          <div class="muted dv-small" id="cbHoles"></div>
          <div class="cb-measure" id="cbMeasure" aria-live="polite" hidden></div>
          <div class="cb-chat" id="cbChat"><div class="muted dv-small">Describe the anchors and collision boxes you want — e.g. “one slot on top of each peg, A1–H12, rz 45”, “a place anchor at the bottom of each of the 24 holes”, or “one box for the base and one for the post”. You can still edit everything below.</div></div>
          <div class="cb-row cb-ask"><textarea class="input" id="cbAsk" rows="2" placeholder="Describe what the part needs…"></textarea>
            <button class="btn btn-primary" id="cbSend" type="button">Send</button></div>

          <div class="cb-sec"><span class="section-label">Anchors</span><span class="muted dv-small">mm · deg, from the part's center</span><span class="spacer"></span>
            <button class="btn btn-primary btn-sm" id="cbAddA" type="button">+ Anchor</button></div>
          <div class="cb-tbl"><table><thead><tr><th><div class="cb-r2"><span>x</span><span>y</span><span>z</span><span>rx</span><span>ry</span><span>rz</span></div></th></tr></thead><tbody id="cbA"></tbody></table></div>
          <div class="cb-empty" data-empty-for="cbA">No anchors yet. Load a model, or add one.</div>
          <div class="cb-sec"><span class="section-label">Collision boxes</span><span class="muted dv-small">center + size, mm</span><span class="spacer"></span>
            <button class="btn btn-primary btn-sm" id="cbAddB" type="button">+ Box</button></div>
          <div class="cb-tbl"><table><thead><tr><th><div class="cb-r2"><span>x</span><span>y</span><span>z</span><span>lx</span><span>ly</span><span>lz</span></div></th></tr></thead><tbody id="cbB"></tbody></table></div>
          <div class="cb-empty" data-empty-for="cbB">No collision boxes yet. Load a model, or add one.</div>
        </div>
      </div>
      <div class="cb-foot"><span class="dv-msg" id="cbMsg">Upload the model to start.</span><span class="spacer"></span>
        ${editType ? `<button class="btn btn-danger" id="cbDelete" type="button">Delete component</button>` : ""}
        <button class="btn" data-close type="button">Cancel</button><button class="btn btn-primary" id="cbSave" type="button">Save component</button></div>
    </div>`;
  document.body.appendChild(bg);
  st.bg = bg;
  const $ = (s) => bg.querySelector(s);
  bg.querySelectorAll("[data-close]").forEach((b) => b.onclick = close);
  bg.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  $("#cbFile").onchange = (e) => e.target.files[0] && loadFile(e.target.files[0]);
  const view = $(".cb-view");
  view.addEventListener("dragover", (e) => { e.preventDefault(); view.classList.add("over"); });
  view.addEventListener("dragleave", () => view.classList.remove("over"));
  view.addEventListener("drop", (e) => { e.preventDefault(); view.classList.remove("over"); const f = e.dataTransfer.files[0]; if (f) loadFile(f); });
  $("#cbAddA").onclick = () => { st.touched = true; st.anchors.push({ name: `hole_${st.anchors.filter((a) => a.name.startsWith("hole_")).length}`, p: [0, 0, 0, 0, 0, 0] }); renderTables(); sync(); };
  $("#cbAddB").onclick = () => { st.touched = true; const d = st.dims || [50, 50, 50]; st.boxes.push({ c: [0, 0, d[2] / 2], s: [d[0] / 2, d[1] / 2, d[2] / 2] }); renderTables(); sync(); };
  $("#cbSave").onclick = save;
  const toggle = (id, key, after) => {
    $(id).onclick = (e) => {
      st[key] = !st[key];
      e.currentTarget.classList.toggle("on", st[key]); e.currentTarget.setAttribute("aria-pressed", st[key]);
      after(); redraw();
    };
  };
  toggle("#cbGrid", "showGrid", () => { if (st.grid) st.grid.visible = st.showGrid; });
  toggle("#cbCol", "showBoxes", sync);
  toggle("#cbTagsBtn", "showTags", () => {});
  toggle("#cbEdges", "showEdges", () => {
    if (st.model) st.model.traverse((o) => { if (o.userData.edges) o.userData.edges.visible = st.showEdges; });
  });
  // anything done in the dialog (a table edit, a menu, a button) redraws once — the
  // view renders only when something changed, never 60 times a second for nothing
  for (const ev of ["input", "change", "click"]) bg.addEventListener(ev, () => redraw(), true);
  $("#cbSend").onclick = ask;
  $("#cbAsk").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } });
  $("#cbUnits").onchange = $("#cbUp").onchange = () => st.raw && onModel(st.raw, st.fname, true);
  // the kind: guessed from the id / description / model until the user picks one
  jpost("/dev/api/component/template", {}).then((r) => {
    if (!st || !r.kinds) return;
    $("#cbKind").innerHTML = `<option value="">Auto: guessed from the model</option>`
      + r.kinds.map((k) => `<option value="${k.key}">${esc(k.label)}</option>`).join("");
    showKind();
  });
  $("#cbKind").onchange = async (e) => {
    if (!e.target.value) {                          // back to the guess
      st.kindSet = false;
      if (st.dims && !(st.keep || st.touched)) applyTemplate(null);
      else showKind();
      return;
    }
    st.kindSet = true;
    if (!st.dims) { st.kind = e.target.value; renderSettings(); return; }
    if ((st.keep || st.touched) && !(await confirmIt("Start the anchors over?",
      "Start this part's anchors over from the new kind's? Cancel keeps your anchors and only changes the kind.", "Start over", "primary"))) {
      st.kind = e.target.value; renderSettings(); return;
    }
    applyTemplate(e.target.value);
  };
  for (const q of ["#cbType", "#cbDesc"]) $(q).addEventListener("change", () => {
    if (st.dims && !st.kindSet && !st.keep && !st.touched) applyTemplate();
  });
  setupView($("canvas"));
  renderTables();
  if (editType) { $("#cbDelete").onclick = () => remove(editType); loadExisting(editType); }
  else $("#cbFileBtn").focus();
}

function say(text, bad = false) {
  const m = st && st.bg.querySelector("#cbMsg");
  if (!m) return;
  m.textContent = text;
  m.className = "dv-msg" + (bad ? " bad" : "");
}

// ── editing a project part: its anchors, boxes, description and model back ──
async function loadExisting(type) {
  const $ = (q) => st.bg.querySelector(q), me = st;
  let r;
  try {
    const res = await fetch(`${st.api}/part?type=${encodeURIComponent(type)}`, { cache: "no-store" });
    r = await res.json();
  } catch (e) { r = { ok: false, error: e.message }; }
  if (me !== st) return;
  if (!r.ok) { say(r.error || "Couldn't open that component", true); $("#cbSave").disabled = true; return; }
  $("#cbType").value = type; $("#cbType").disabled = true;          // the scene refers to it by this name
  $("#cbDesc").value = r.description || "";
  st.kind = r.kind || "other"; st.kindSet = true; st.settings = r.settings || {};
  showKind(); renderSettings();
  st.keep = true;                                                    // its own anchors, not fresh ones from the geometry
  st.anchors = Object.entries(r.anchors || {}).map(([name, p]) => ({ name, p: p.map(Number), fixed: name === "center" }));
  st.boxes = (r.collision_boxes || []).map((b) => ({ c: b.pose.slice(0, 3).map(Number), s: b.scale.map(Number) }));
  renderTables(); sync();
  if (!r.glb) { say("This component has no model yet — choose its .glb."); return; }
  // the stored model is already in Workspace's frame — millimetres, Z up — and
  // stays as it is: units and up axis apply to a newly chosen file only
  $("#cbUnits").value = "1"; $("#cbUp").value = "z";
  $("#cbUnits").disabled = $("#cbUp").disabled = true;
  const url = `${st.api}/part/${encodeURIComponent(type)}/${encodeURIComponent(type)}.glb`;
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`the model didn't load (${res.status})`);
    const buf = await res.arrayBuffer();
    if (me !== st) return;
    // the model's folder as the base: a compressed model's <type>.glb.bin sits next to it
    loader(me).parse(buf, url.replace(/[^/]*$/, ""), (gltf) => me === st && onModel(gltf.scene, `${type}.glb`),
      (err) => say("Couldn't read its model: " + err.message, true));
  } catch (e) { say(e.message, true); }
}

async function remove(type) {
  const used = st.inUse(type);
  if (used.length) {
    say(`${type} is in the Scene builder's scene (${used.slice(0, 6).join(", ")}${used.length > 6 ? "…" : ""}) — remove it there and save the scene first`, true);
    return;
  }
  const me = st;
  if (!(await confirmIt(`Delete ${type}?`, `Its folder components/${type}/ — class and model — is removed from this project.`, "Delete"))) return;
  const d = await jpost(`${me.api}/part`, { type, delete: true });
  if (!d.ok) { if (me === st) say(d.error || "Couldn't delete it", true); return; }
  if (me === st) close();
  if (me.onSaved) me.onSaved({ deleted: type });
}

export function close() {
  if (!st) return;
  cancelAnimationFrame(st.raf);
  st.observers.forEach((o) => o.disconnect());
  st.controls.dispose();
  st.scene.traverse((o) => {                       // the model, edges, marks, grid: their GPU memory
    if (o.geometry) o.geometry.dispose();
    for (const m of [].concat(o.material || [])) { for (const v of Object.values(m)) if (v && v.isTexture) v.dispose(); m.dispose(); }
  });
  st.scene.environment.dispose();
  st.pmrem.dispose();
  st.draco.dispose();                              // its decoder workers
  st.renderer.dispose();
  st.renderer.forceContextLoss();                  // the WebGL context now, not at garbage collection
  st.bg.remove();
  st = null;
}

// a GLTF loader on the dialog's one DRACOLoader (disposed with it)
function loader(s) {
  const l = new GLTFLoader();
  l.setDRACOLoader(s.draco);
  return l;
}

// ── 3D preview: the scene builder's look (app.js) ───────────────────
// Same renderer settings, gradient environment light, lights, theme-aware
// background and grid, edge outlines and controls (right-drag pans).
const BG = { dark: new THREE.Color("#080c12"), light: new THREE.Color("#f0f4f8") };
const GRID = { dark: { minor: 0x182030, major: 0x253548 }, light: { minor: 0xbbbbbb, major: 0x888888 } };
const theme = () => document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";

function rectGrid(width, step, major, cMinor, cMajor) {
  const verts = [], colors = [], half = width / 2, mn = new THREE.Color(cMinor), mj = new THREE.Color(cMajor);
  for (let v = -half; v <= half + 1e-6; v += step) {
    const c = Math.abs(v % major) < 1e-6 ? mj : mn;
    verts.push(v, -half, 0, v, half, 0, -half, v, 0, half, v, 0);
    for (let k = 0; k < 4; k++) colors.push(c.r, c.g, c.b);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
  g.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  return new THREE.LineSegments(g, new THREE.LineBasicMaterial({ vertexColors: true, toneMapped: false }));
}

function setupView(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.9;
  const scene = new THREE.Scene();
  // the procedural gradient environment (zenith → horizon → floor), Z up
  const pmrem = new THREE.PMREMGenerator(renderer);
  const env = document.createElement("canvas"); env.width = 64; env.height = 256;
  const g2 = env.getContext("2d"), grad = g2.createLinearGradient(0, 0, 0, 256);
  grad.addColorStop(0, "#ffffff"); grad.addColorStop(0.5, "#b9bfc9"); grad.addColorStop(1, "#6d737b");
  g2.fillStyle = grad; g2.fillRect(0, 0, 64, 256);
  const tex = new THREE.CanvasTexture(env); tex.colorSpace = THREE.SRGBColorSpace;
  const envScene = new THREE.Scene(), sphere = new THREE.Mesh(new THREE.SphereGeometry(100, 64, 64), new THREE.MeshBasicMaterial({ map: tex, side: THREE.BackSide }));
  sphere.rotation.x = Math.PI / 2; envScene.add(sphere);
  scene.environment = pmrem.fromScene(envScene).texture;
  tex.dispose(); sphere.geometry.dispose(); sphere.material.dispose();
  scene.environmentIntensity = 0.9;
  scene.add(new THREE.HemisphereLight(0xdde8ff, 0x39414f, 0.85));
  const key = new THREE.DirectionalLight(0xfff4e0, 0.65); key.position.set(1200, 900, 1500); scene.add(key);
  const fill = new THREE.DirectionalLight(0xb0ccff, 0.4); fill.position.set(-800, -600, 600); scene.add(fill);
  const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100000);
  camera.up.set(0, 0, 1);
  camera.position.set(300, 300, 300);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = false;
  controls.minDistance = 5; controls.maxDistance = 50000;
  controls.mouseButtons.LEFT = THREE.MOUSE.ROTATE; controls.mouseButtons.RIGHT = THREE.MOUSE.PAN; controls.mouseButtons.MIDDLE = THREE.MOUSE.DOLLY;
  const marks = new THREE.Group(); scene.add(marks);
  Object.assign(st, { renderer, scene, camera, controls, marks, pmrem, model: null, grid: null, gridR: 0, dirty: true,
                      tags: canvas.parentElement.querySelector(".cb-tags") });
  controls.addEventListener("change", redraw);
  controls.addEventListener("start", () => { if (st) st.camMoved = true; });
  applyTheme(200);
  const themeObs = new MutationObserver(() => st && applyTheme(st.gridR || 200));
  themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  const resize = () => {
    const p = canvas.parentElement, w = p.clientWidth, h = p.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
    if (st.home && !st.camMoved) fitView();             // still the fitted view: keep the part in frame
    redraw();
  };
  const sizeObs = new ResizeObserver(resize);
  sizeObs.observe(canvas.parentElement);
  st.observers = [themeObs, sizeObs];
  const loop = () => {                                  // a frame only when something changed
    if (!st) return;
    if (st.dirty) { st.dirty = false; renderer.render(scene, camera); placeTags(); }
    st.raf = requestAnimationFrame(loop);
  };
  resize(); loop();
}

function redraw() { if (st) st.dirty = true; }

// isometric and the whole part in frame whatever the viewer's shape: the
// bounding sphere against the narrower of the two fields of view
function fitView() {
  const { c, rs } = st.home, cam = st.camera;
  const v = THREE.MathUtils.degToRad(cam.fov), hf = 2 * Math.atan(Math.tan(v / 2) * (cam.aspect || 1));
  const dist = rs / Math.sin(Math.min(v, hf) / 2) * 1.04;
  cam.position.copy(c).add(new THREE.Vector3(1, 1, 1).normalize().multiplyScalar(dist));
  st.controls.target.copy(c);
  st.controls.update(); redraw();
}

// the anchors' names, as tags over the view: placed on each frame drawn, the
// overlapping ones hidden (the selected one always shows); a tag selects its row.
// Many anchors (a 96-slot grid): only the grid's corners and the named anchors
// are tagged — the rest when their row, or the grid's row, is hovered or selected
const MANY_TAGS = 24;
function placeTags() {
  const layer = st.tags;
  layer.hidden = !st.showTags || !st.anchors.length;
  if (layer.hidden) return;
  const w = layer.clientWidth, h = layer.clientHeight, v = new THREE.Vector3();
  while (layer.children.length < st.anchors.length) {
    const el = document.createElement("button");
    el.type = "button"; el.className = "cb-tag"; el.tabIndex = -1;
    el.onclick = () => {
      const i = +el.dataset.i;
      st.sel = "a" + i;
      if (st.slotG && st.slotG.idx.has(i)) st.gridOpen = true;
      renderTables(); sync();
      const row = st.bg.querySelector(`#cbA tr[data-k="a${i}"]`);
      if (row) { row.scrollIntoView({ block: "nearest", behavior: "smooth" }); row.querySelector("input")?.focus({ preventScroll: true }); }
    };
    layer.appendChild(el);
  }
  while (layer.children.length > st.anchors.length) layer.lastChild.remove();
  const g = st.slotG, many = st.anchors.length > MANY_TAGS, gridOn = st.sel === "g" || st.hover === "g";
  const wanted = (a, i) => {
    const k = "a" + i;
    if (!many || st.sel === k || st.hover === k) return true;
    if (g && g.idx.has(i)) return g.corners.has(a.name) || (gridOn && /^(A\d+|[A-Z]+1)$/.test(a.name));
    return !SLOT_RE.test(a.name);
  };
  // the viewer's own buttons are a no-go zone: a tag never hides under them
  const L = layer.getBoundingClientRect(), C = st.bg.querySelector(".cb-ctrl").getBoundingClientRect();
  const placed = [[C.left - L.left - 6, C.top - L.top - 6, C.right - L.left + 6, C.bottom - L.top + 6]];
  const order = st.anchors.map((a, i) => i).sort((a, b) => (st.sel === "a" + b || st.hover === "a" + b) - (st.sel === "a" + a || st.hover === "a" + a));
  for (const i of order) {
    const a = st.anchors[i], el = layer.children[i];
    if (el.textContent !== a.name) el.textContent = a.name;
    el.dataset.i = i;
    el.classList.toggle("sel", st.sel === "a" + i);
    if (!wanted(a, i)) { el.style.display = "none"; continue; }
    v.set(a.p[0], a.p[1], a.p[2]).project(st.camera);
    const x = (v.x + 1) / 2 * w, y = (1 - v.y) / 2 * h;
    const bw = el.offsetWidth || a.name.length * 7 + 16, bh = el.offsetHeight || 22;
    const box = [x + 8, y - bh - 6, x + 8 + bw, y - 6];
    // a tag shows whole or not at all: never cut by the viewer's edge
    let show = v.z < 1 && box[0] >= 8 && box[2] <= w - 8 && box[1] >= 8 && box[3] <= h - 8;
    if (show && placed.some((p, k) => (k === 0 || st.sel !== "a" + i) && box[0] < p[2] && p[0] < box[2] && box[1] < p[3] && p[1] < box[3])) show = false;
    if (show) placed.push(box);
    el.style.display = show ? "" : "none";
    if (show) el.style.transform = `translate(${Math.round(box[0])}px, ${Math.round(box[1])}px)`;
  }
}

// background + grid for the theme; the grid sized to the part (10 mm squares,
// 50 mm majors for a part, the bench's 50 / 500 for something bench-sized)
function applyTheme(R) {
  const t = theme();
  st.scene.background = BG[t];
  if (st.grid) { st.scene.remove(st.grid); st.grid.geometry.dispose(); st.grid.material.dispose(); }
  const big = R > 400, step = big ? 50 : 10, major = big ? 500 : 50;
  const width = Math.max(major * 2, Math.ceil(R * 4 / major) * major);
  st.grid = rectGrid(width, step, major, GRID[t].minor, GRID[t].major);
  st.grid.visible = st.showGrid;
  st.scene.add(st.grid);
  st.gridR = R;
  redraw();
}

// the black edge outlines parts have in the scene builder (app.js addEdgeOverlay)
function addEdges(obj) {
  obj.traverse((o) => {
    if (!o.isMesh || o.userData.edges) return;
    const lines = new THREE.LineSegments(new THREE.EdgesGeometry(o.geometry, 75), new THREE.LineBasicMaterial({ color: 0x555555, toneMapped: false }));
    lines.renderOrder = 10;
    lines.visible = st.showEdges;
    o.userData.edges = lines;
    o.add(lines);
  });
}

function loadFile(file) {
  if (!/\.glb$/i.test(file.name)) { say("Choose a .glb file", true); return; }
  const reader = new FileReader(), me = st;
  reader.onload = () => {
    if (me !== st) return;                        // the dialog closed (or another opened) meanwhile
    const buf = reader.result, bytes = new Uint8Array(buf);
    let bin = "";
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    st.glbB64 = btoa(bin);
    // a new file: its own units and up axis (millimetres, Z up by default — Workspace's frame)
    st.bg.querySelector("#cbUnits").disabled = st.bg.querySelector("#cbUp").disabled = false;
    loader(me).parse(buf, "", (gltf) => me === st && onModel(gltf.scene, file.name),
      (err) => me === st && say("Couldn't read that model: " + err.message, true));
  };
  reader.readAsArrayBuffer(file);
  const idEl = st.bg.querySelector("#cbType");
  if (!idEl.value) idEl.value = file.name.replace(/\.glb$/i, "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

function onModel(obj, fname, refit) {
  const $ = (q) => st.bg.querySelector(q);
  st.raw = obj; st.fname = fname;
  if (st.model) { st.model.remove(obj); st.scene.remove(st.model); }
  if (!refit) addEdges(obj);
  // the file's frame → Workspace's (Z up, mm), exactly what save bakes in
  const wrap = new THREE.Group();
  st.unitScale = parseFloat($("#cbUnits").value) || 1;
  st.up = $("#cbUp").value;
  wrap.scale.setScalar(st.unitScale);
  if (st.up === "y") wrap.rotation.x = Math.PI / 2;        // file's +Y → +Z
  else if (st.up === "x") wrap.rotation.y = -Math.PI / 2;  // file's +X → +Z
  wrap.add(obj);
  st.model = wrap; st.scene.add(wrap);
  wrap.updateMatrixWorld(true);
  const bb = new THREE.Box3().setFromObject(wrap);
  const size = bb.getSize(new THREE.Vector3()), c = bb.getCenter(new THREE.Vector3());
  st.dims = [r1(size.x), r1(size.y), r1(size.z)];
  const H = r1(bb.max.z);
  const off = Math.hypot(c.x, c.y) > 2 || Math.abs(bb.min.z) > 1;
  $("#cbDims").innerHTML = `${esc(fname)} · ${st.dims[0]} × ${st.dims[1]} × ${st.dims[2]} mm`
    + (off ? ` · <span class="cb-warn">origin isn't at the base center (bottom z ${r1(bb.min.z)}, center ${r1(c.x)}, ${r1(c.y)}) — anchors are relative to the file's origin</span>` : "");
  // start from the geometry: center, top, place; one box around the part (an
  // edited part keeps its own; a place the user set by hand stays where they put it)
  const userPlace = st.anchors.find((a) => a.name === "place" && a.user);
  if (!st.keep) {
    st.anchors = [
      { name: "center", p: [0, 0, 0, 0, 0, 0], fixed: true },
      { name: "top", p: [r1(c.x), r1(c.y), H, 0, 0, 0] },
      userPlace || { name: "place", p: [r1(c.x), r1(c.y), H, 0, 0, 0] },
    ];
    st.boxes = [{ c: [r1(c.x), r1(c.y), r1(c.z)], s: [st.dims[0], st.dims[1], st.dims[2]] }];
  }
  st.xy = [r1(c.x), r1(c.y)];
  const R = Math.max(size.x, size.y, size.z) || 100;
  st.home = { c: c.clone(), R, rs: (size.length() / 2) || 50 };
  st.camMoved = false;
  fitView();
  applyTheme(R);
  $(".cb-drop").hidden = true;
  $("#cbFileBtn").childNodes[0].textContent = "Replace the .glb…";
  say("Describe what the part needs, or check the anchors and collision boxes, then save.");
  const soup = meshSoup(wrap);
  st.soup = soup;
  findHoles(wrap, bb, soup);
  setTimeout(() => uploadMesh(soup), 0);
  renderTables(); sync();
  // then the kind's own anchors (a tube's cap_seat, a rack's A1… at the hole floors…)
  if (!st.keep) applyTemplate(st.kindSet ? st.kind : null);
}

// ── the part's kind → Workspace's anchors for that kind (component_kinds.py) ──
async function applyTemplate(kind) {
  const $ = (q) => st.bg.querySelector(q);
  const keepPlace = st.anchors.find((a) => a.name === "place" && a.user);
  let r = await postTemplate(kind);
  if (!st || !r.ok) return;
  // nothing in the name says what it is, but the model does: a regular grid of raised
  // pegs is a cap holder, a grid of pockets a rack — the measured grid makes the slots
  st.kindFrom = "";
  if (!kind && r.kind === "other") {
    const gp = fitGrid(st.pegs), gh = fitGrid(st.holes);
    const n = (g) => (g ? g.rows * g.cols : 0);
    const best = n(gp) >= n(gh) ? { g: gp, kind: "capholder", what: "pegs" } : { g: gh, kind: "rack", what: "holes" };
    if (n(best.g) >= 4) {
      const r2 = await postTemplate(best.kind);
      if (!st) return;
      if (r2 && r2.ok) { r = r2; st.kindFrom = `from ${best.g.count} ${best.what}`; }
    }
  }
  st.kind = r.kind; st.settings = r.settings || {};
  st.anchors = Object.entries(r.anchors).map(([name, p]) => ({ name, p: p.map(Number), fixed: name === "center" }));
  // a place the user set (z 3 under the cap's rim…) stays, and the slots go to its
  // height: the place anchor and the slots are the same surface
  const notes = (r.notes || []).slice();
  if (keepPlace && st.anchors.some((a) => a.name === "place")) {
    const z = keepPlace.p[2];
    let slots = 0;
    st.anchors = st.anchors.map((a) => {
      if (a.name === "place") return { ...a, p: keepPlace.p.slice(), user: true };
      if (SLOT_RE.test(a.name)) { slots++; return { ...a, p: [a.p[0], a.p[1], z, a.p[3], a.p[4], a.p[5]] }; }
      return a;
    });
    if (slots) {
      for (let k = 0; k < notes.length; k++) notes[k] = notes[k].replace(/, (at the surface the pegs stand on, |floor )z -?[\d.]+\)/, ")");
      notes.push(`slots at your place height, z ${r1(z)}`);
    }
  }
  st.touched = false; st.sel = null;
  showKind();
  const opt = [...$("#cbKind").options].find((o) => o.value === r.kind);
  const label = opt ? opt.textContent.replace(/^Auto: /, "").replace(/ \(from .*\)$/, "") : r.kind;
  $("#cbKindNote").textContent = `${label}${st.kindFrom ? ` (${st.kindFrom})` : ""}: started from Workspace's ${label.toLowerCase()} anchors`
    + (notes.length ? " — " + notes.join("; ") : "") + ". Ask the assistant to adjust anything.";
  renderSettings(); renderTables(); sync();
}

function postTemplate(kind) {
  const $ = (q) => st.bg.querySelector(q);
  return jpost("/dev/api/component/template", {
    kind: kind || null, type: $("#cbType").value.trim(), file_name: st.fname, description: $("#cbDesc").value.trim(),
    dims: st.dims, top: st.top, holes: st.holes, pegs: st.pegs, levels: st.levels, xy: st.xy });
}

// the Kind menu: the kind picked, or "Auto" with what was guessed
function showKind() {
  const sel = st && st.bg.querySelector("#cbKind");
  if (!sel || !sel.options.length) return;
  const guess = st.kind && [...sel.options].find((o) => o.value === st.kind);
  sel.options[0].textContent = !st.kindSet && guess ? `Auto: ${guess.textContent}${st.kindFrom ? ` (${st.kindFrom})` : ""}` : "Auto: guessed from the model";
  sel.value = st.kindSet && st.kind ? st.kind : "";
}

// a cap's screw (the decapper uses it); a tube's size
function renderSettings() {
  const el = st.bg.querySelector("#cbSet"), s = st.settings || {};
  if (st.kind === "cap") {
    el.innerHTML = `<label class="dv-kw"><span>Cap</span><select class="input" data-s="cap_type">
        <option value="screw">screw</option><option value="snap">snap</option></select></label>
      <label class="dv-kw"><span>Twist °</span><input class="input" type="number" data-s="twist" value="${s.twist ?? ""}"></label>
      <label class="dv-kw"><span>Pitch mm/turn</span><input class="input" type="number" step="0.01" data-s="pitch" value="${s.pitch ?? ""}"></label>`;
    el.querySelector("select").value = s.cap_type || "screw";
    el.querySelectorAll("[data-s]").forEach((i) => i.onchange = () => {
      st.settings[i.dataset.s] = i.tagName === "SELECT" ? i.value : parseFloat(i.value);
    });
  } else if (st.kind === "tube" && s.size) {
    el.innerHTML = `<span class="muted dv-small cb-size">size ${s.size.map((v) => r1(v)).join(" × ")} mm</span>`;
  } else el.innerHTML = "";
}

// the model's triangles in the part's frame (mm, Z up — after the file's units
// and up axis): 9 floats a triangle. What the height map reads and what the
// assistant's measuring tools get (uploadMesh).
function meshSoup(obj) {
  obj.updateMatrixWorld(true);
  const chunks = [], v = new THREE.Vector3();
  let count = 0;
  obj.traverse((o) => {
    if (!o.isMesh || !o.geometry || !o.geometry.attributes.position) return;
    const pos = o.geometry.attributes.position, idx = o.geometry.index;
    const m = o.matrixWorld, nt = idx ? idx.count / 3 : pos.count / 3;
    const out = new Float32Array(nt * 9);
    for (let t = 0; t < nt; t++) for (let k = 0; k < 3; k++) {
      v.fromBufferAttribute(pos, idx ? idx.getX(t * 3 + k) : t * 3 + k).applyMatrix4(m);
      out[t * 9 + k * 3] = v.x; out[t * 9 + k * 3 + 1] = v.y; out[t * 9 + k * 3 + 2] = v.z;
    }
    chunks.push(out); count += nt;
  });
  const T = new Float32Array(count * 9);
  let o = 0;
  for (const c of chunks) { T.set(c, o); o += c.length; }
  return T;
}

// the model to the server once per load, so the assistant can measure it itself;
// the state shows under the measurements (renderMeasure), with a Retry when it failed
function uploadMesh(T) {
  st.meshId = null; st.meshErr = ""; st.meshRefused = false;
  if (!T || !T.length) { st.meshP = null; st.meshState = null; renderMeasure(); return; }
  st.meshState = "busy"; renderMeasure();
  const bytes = new Uint8Array(T.buffer, T.byteOffset, T.byteLength);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  const me = st;
  st.meshP = fetch("/dev/api/component/mesh", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tris: btoa(bin) }) })
    .then((r) => r.json().catch(() => ({})).then((j) => ({ j, http: r.ok, status: r.status })))
    .then(({ j, http, status }) => {
      if (me !== st) return;
      if (j && j.ok) { st.meshId = j.id; st.meshState = "ok"; return; }
      // a 200 with an error is the server refusing this model (over its triangle
      // limit…): retrying won't help. Anything else may pass next time.
      st.meshState = "bad";
      st.meshErr = (j && j.error) || `the server answered ${status}`;
      st.meshRefused = http && !!(j && j.error);
    })
    .catch((e) => { if (me === st) { st.meshState = "bad"; st.meshErr = e && e.message ? e.message : "no connection"; } })
    .finally(() => { if (me === st) renderMeasure(); });
}

// The part seen from straight above: the top surface's height at the centre of
// each cell of an n × n grid over its footprint (NaN where nothing is below).
// The triangles are bucketed by the cells their footprint covers, once, and each
// cell's centre is tested only against its own bucket — a ray per cell against
// every triangle of a dense CAD mesh froze the page.
function heightMap(bb, n, T) {
  const sx = (bb.max.x - bb.min.x) / n, sy = (bb.max.y - bb.min.y) / n;
  const hits = new Float32Array(n * n).fill(NaN);
  const count = T.length / 9;
  if (!(sx > 0 && sy > 0) || !count) return hits;
  const cellOf = (x, s0, s) => Math.min(n - 1, Math.max(0, Math.floor((x - s0) / s)));
  // the cells each triangle's footprint covers (two passes: counts, then a flat list)
  const span = new Int32Array(count * 4), cnt = new Int32Array(n * n + 1);
  for (let t = 0; t < count; t++) {
    const b = t * 9, x0 = Math.min(T[b], T[b + 3], T[b + 6]), x1 = Math.max(T[b], T[b + 3], T[b + 6]);
    const y0 = Math.min(T[b + 1], T[b + 4], T[b + 7]), y1 = Math.max(T[b + 1], T[b + 4], T[b + 7]);
    const i0 = cellOf(x0, bb.min.x, sx), i1 = cellOf(x1, bb.min.x, sx), j0 = cellOf(y0, bb.min.y, sy), j1 = cellOf(y1, bb.min.y, sy);
    span[t * 4] = i0; span[t * 4 + 1] = i1; span[t * 4 + 2] = j0; span[t * 4 + 3] = j1;
    for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) cnt[j * n + i + 1]++;
  }
  for (let c = 0; c < n * n; c++) cnt[c + 1] += cnt[c];
  const list = new Int32Array(cnt[n * n]), fill = cnt.slice(0, n * n);
  for (let t = 0; t < count; t++) {
    const i0 = span[t * 4], i1 = span[t * 4 + 1], j0 = span[t * 4 + 2], j1 = span[t * 4 + 3];
    for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) list[fill[j * n + i]++] = t;
  }
  // each cell's centre: the highest triangle whose footprint contains it
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const px = bb.min.x + (i + 0.5) * sx, py = bb.min.y + (j + 0.5) * sy;
    let best = -Infinity;
    for (let q = cnt[j * n + i], e = cnt[j * n + i + 1]; q < e; q++) {
      const b = list[q] * 9;
      const ax = T[b], ay = T[b + 1], bx = T[b + 3], by = T[b + 4], cx = T[b + 6], cy = T[b + 7];
      const d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
      if (Math.abs(d) < 1e-12) continue;                      // seen edge-on from above (a wall)
      const l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d;
      const l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d;
      const l3 = 1 - l1 - l2;
      if (l1 < -1e-6 || l2 < -1e-6 || l3 < -1e-6) continue;
      const z = l1 * T[b + 2] + l2 * T[b + 5] + l3 * T[b + 8];
      if (z > best) best = z;
    }
    if (best > -Infinity) hits[j * n + i] = best;
  }
  return hits;
}

// ── looking down on the part: its holes (centre, diameter, bottom) ──
// Where the height map lands well below the top face (or falls through) the
// cell is part of a hole; connected cells are one hole. Raised pegs (a cap
// holder's pins) are islands standing above the main surface, with their own,
// finer step: a 1 mm pin is a pin.
function scanTop(bb, soup, n, fine) {
  const size = bb.getSize(new THREE.Vector3());
  const sx = size.x / n, sy = size.y / n;
  const hits = heightMap(bb, n, soup);
  const zs = Array.from(hits).filter((z) => !isNaN(z)).sort((a, b) => a - b);
  if (!zs.length) return null;
  const top = zs[Math.floor(zs.length * 0.9)];
  // a hole is this far under the top face; a peg this far over the surface it stands on
  const deep = fine ? Math.max(0.4, size.z * 0.04) : Math.max(1.5, size.z * 0.04);
  const pegStep = Math.max(0.3, Math.min(sx, sy) * 0.25);
  // a miss counts as a through-hole only inside the part's outline (not the margin around it)
  const inside = (i, j) => {
    let a = 0;
    for (const [di, dj] of [[-1, 0], [1, 0], [0, -1], [0, 1]]) {
      for (let k = 1; k < n; k++) {
        const ii = i + di * k, jj = j + dj * k;
        if (ii < 0 || jj < 0 || ii >= n || jj >= n) break;
        if (!isNaN(hits[jj * n + ii])) { a++; break; }
      }
    }
    return a === 4;
  };
  // connected cells where test() holds, that don't touch the map's edge
  const islands = (test, minCells, maxCells) => {
    const seen = new Uint8Array(n * n), out = [];
    for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
      if (seen[j * n + i] || !test(i, j)) continue;
      const cells = [], stack = [[i, j]];
      let edge = false;
      seen[j * n + i] = 1;
      while (stack.length) {
        const [a, b] = stack.pop(); cells.push([a, b]);
        if (a === 0 || b === 0 || a === n - 1 || b === n - 1) edge = true;
        for (const [di, dj] of [[-1, 0], [1, 0], [0, -1], [0, 1]]) {
          const c = a + di, d = b + dj;
          if (c < 0 || d < 0 || c >= n || d >= n || seen[d * n + c] || !test(c, d)) continue;
          seen[d * n + c] = 1; stack.push([c, d]);
        }
      }
      if (edge || cells.length < minCells || cells.length > maxCells) continue;   // the margin / a rim, or noise
      const cx = cells.reduce((s, c) => s + c[0], 0) / cells.length, cy = cells.reduce((s, c) => s + c[1], 0) / cells.length;
      out.push({ cells, x: r1(bb.min.x + (cx + 0.5) * sx), y: r1(bb.min.y + (cy + 0.5) * sy),
                 d: r1(2 * Math.sqrt(cells.length * sx * sy / Math.PI)) });
    }
    return out.sort((p, q) => (Math.abs(p.y - q.y) > 1 ? p.y - q.y : p.x - q.x));
  };
  const holes = islands((i, j) => { const z = hits[j * n + i]; return isNaN(z) ? inside(i, j) : z < top - deep; }, 3, n * n).map((h) => {
    const floor = h.cells.map(([a, b]) => hits[b * n + a]).filter((z) => !isNaN(z));
    return { x: h.x, y: h.y, d: h.d, bottom: floor.length ? r1(Math.min(...floor)) : r1(bb.min.z), through: floor.length < h.cells.length * 0.5 };
  });
  // the surfaces seen from above: the heights that cover the most area. 0.5 mm bins
  // rank them; each level's height is the median of what fell in its bin
  const bins = new Map();
  for (const z of zs) { const k = Math.round(z * 2) / 2; let b = bins.get(k); if (!b) bins.set(k, b = []); b.push(z); }
  const levels = [...bins.values()].sort((p, q) => q.length - p.length).slice(0, 5)
    .map((b) => ({ z: r1(b[b.length >> 1]), area_pct: Math.round(100 * b.length / zs.length) })).filter((l) => l.area_pct >= 2);
  const surface = levels.length ? levels[0].z : r1(top);
  const pegs = islands((i, j) => { const z = hits[j * n + i]; return !isNaN(z) && z > surface + pegStep; }, 2, n * n * 0.25).map((p) => (
    { x: p.x, y: p.y, d: p.d, top: r1(Math.max(...p.cells.map(([a, b]) => hits[b * n + a]))), bottom: surface }));
  // a cell is size / n: features under ~2 cells wide are below this map's resolution
  return { top: r1(top), holes, pegs, levels, resolution: [r1(sx), r1(sy)] };
}

function findHoles(obj, bb, soup) {
  let m = scanTop(bb, soup, 110, false);
  // no grid either way: look again, twice as fine and with shallower steps, and
  // keep it if it finds one
  if (m && !fitGrid(m.holes) && !fitGrid(m.pegs)) {
    const f = scanTop(bb, soup, 220, true);
    if (f && (fitGrid(f.holes) || fitGrid(f.pegs))) m = f;
  }
  if (!m) { st.holes = []; st.pegs = []; st.levels = []; st.top = null; renderMeasure(); return; }
  Object.assign(st, { top: m.top, holes: m.holes, pegs: m.pegs, levels: m.levels, resolution: m.resolution });
  try { refinePegs(soup, st.pegs); } catch (_) {}
  renderMeasure();
}

// The height map counts whole cells, so an 8 mm peg reads 7.9: cut the model's own
// triangles with a plane halfway up the pegs and take each peg's diameter (and
// centre) from where its wall crosses it
function refinePegs(T, pegs) {
  if (!pegs.length) return;
  const tops = pegs.map((p) => p.top).sort((a, b) => a - b), base = pegs[0].bottom;
  const mid = tops[tops.length >> 1];
  if (!(mid - base > 0.2)) return;
  const z0 = base + (mid - base) * 0.5;
  const cell = Math.max(4, Math.max(...pegs.map((p) => p.d)) * 2), buckets = new Map();
  const add = (x, y) => { const k = `${Math.floor(x / cell)},${Math.floor(y / cell)}`; let b = buckets.get(k); if (!b) buckets.set(k, b = []); b.push(x, y); };
  for (let b = 0; b < T.length; b += 9) for (let e = 0; e < 3; e++) {
    const i = b + e * 3, j = b + ((e + 1) % 3) * 3, za = T[i + 2] - z0, zb = T[j + 2] - z0;
    if ((za < 0) === (zb < 0) || za === zb) continue;
    const t = za / (za - zb);
    add(T[i] + (T[j] - T[i]) * t, T[i + 1] + (T[j + 1] - T[i + 1]) * t);
  }
  for (const p of pegs) {
    const R = p.d * 0.75 + 1, gx = Math.floor(p.x / cell), gy = Math.floor(p.y / cell);
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity, n = 0;
    for (let a = gx - 1; a <= gx + 1; a++) for (let c = gy - 1; c <= gy + 1; c++) {
      const b = buckets.get(`${a},${c}`);
      if (!b) continue;
      for (let q = 0; q < b.length; q += 2) {
        const x = b[q], y = b[q + 1];
        if (Math.hypot(x - p.x, y - p.y) > R) continue;
        n++; x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
      }
    }
    if (n < 6) continue;
    const D = ((x1 - x0) + (y1 - y0)) / 2;
    if (!(D > p.d * 0.7 && D < p.d * 1.3)) continue;           // not this peg's wall alone: keep the map's
    p.d = r1(D); p.x = r1((x0 + x1) / 2); p.y = r1((y0 + y1) / 2);
  }
}

// The holes / pegs as rows × columns — component_kinds.fit_grid, here so the
// builder knows whether a grid was found before asking the server
function fitGrid(feats) {
  const hs = (feats || []).filter((h) => h.d).sort((a, b) => a.d - b.d);
  if (!hs.length) return null;
  const groups = [];
  for (const h of hs) { const g = groups[groups.length - 1]; if (g && h.d <= g[0].d * 1.15) g.push(h); else groups.push([h]); }
  const main = groups.reduce((a, b) => (b.length > a.length ? b : a));
  const d = main.reduce((s, h) => s + h.d, 0) / main.length, tol = Math.max(1.5, d * 0.3);
  const clusters = (vals) => {
    const out = [];
    for (const v of vals.sort((a, b) => a - b)) { const c = out[out.length - 1]; if (c && v - c[c.length - 1] <= tol) c.push(v); else out.push([v]); }
    return out.map((c) => c.reduce((s, v) => s + v, 0) / c.length);
  };
  const xs = clusters(main.map((h) => h.x)), ys = clusters(main.map((h) => h.y));
  const cols = xs.length, rows = ys.length;
  if (rows * cols < main.length || rows * cols > main.length * 1.25) return null;
  const px = cols > 1 ? (xs[cols - 1] - xs[0]) / (cols - 1) : 0, py = rows > 1 ? (ys[rows - 1] - ys[0]) / (rows - 1) : 0;
  if (xs.some((x, i) => Math.abs(x - (xs[0] + i * px)) > tol) || ys.some((y, i) => Math.abs(y - (ys[0] + i * py)) > tol)) return null;
  return { rows, cols, pitch: [r1(px), r1(py)], d: r1(d), count: main.length };
}

// What was measured: the features found from above, and whether the
// assistant's measuring tools have the model (uploadMesh)
function renderMeasure() {
  if (!st) return;
  const h = st.bg.querySelector("#cbHoles"), m = st.bg.querySelector("#cbMeasure");
  if (!st.dims) { h.textContent = ""; m.hidden = true; return; }
  const said = [], avg = (a) => r1(a.reduce((s, x) => s + x.d, 0) / a.length);
  const gp = fitGrid(st.pegs), gh = fitGrid(st.holes);
  if (st.pegs.length) said.push(`${st.pegs.length} peg${st.pegs.length === 1 ? "" : "s"} Ø ${avg(st.pegs)} mm` + (gp && gp.rows * gp.cols > 1 ? ` (${gp.rows} × ${gp.cols}, ${gp.pitch[0]} mm pitch)` : ""));
  if (st.holes.length) said.push(`${st.holes.length} hole${st.holes.length === 1 ? "" : "s"} Ø ${avg(st.holes)} mm` + (gh && gh.rows * gh.cols > 1 ? ` (${gh.rows} × ${gh.cols})` : ""));
  h.textContent = said.length ? `Measured: ${said.join(", ")}`
    : "Measured: no holes or pegs seen from above" + (st.meshState === "ok" ? " — the assistant can measure the sides and underside" : "");
  const s = st.meshState;
  m.hidden = !s;
  m.className = "cb-measure" + (s === "bad" ? " bad" : s === "ok" ? " ok" : "");
  if (s === "bad") {
    m.innerHTML = `<span>Couldn't prepare the model for measuring${st.meshErr ? `: ${esc(st.meshErr)}` : "."}</span>`
      + (st.meshRefused ? "" : ' <button class="btn btn-primary btn-sm" type="button" data-retry>Retry</button>');
    const b = m.querySelector("[data-retry]");
    if (b) b.onclick = () => uploadMesh(st.soup);
  } else m.textContent = s === "ok" ? "The assistant can measure the model itself." : "Preparing the model for the assistant's measuring…";
}

// ── the assistant ───────────────────────────────────────────────────
async function ask() {
  const box = st.bg.querySelector("#cbAsk"), msg = box.value.trim();
  if (!msg) return;
  if (!st.dims) { say("Upload the model first", true); return; }
  const me = st, chat = st.bg.querySelector("#cbChat");
  const line = (who, text) => { const d = document.createElement("div"); d.className = "cb-msg " + who; d.textContent = text; chat.appendChild(d); chat.scrollTop = chat.scrollHeight; return d; };
  line("you", msg); box.value = "";
  const wait = line("ai", "…");
  const send = st.bg.querySelector("#cbSend");
  send.disabled = true;
  if (st.meshP) await st.meshP;
  // the measuring upload failed (a dropped connection…): once more before asking
  if (!st.meshId && st.soup && !st.meshRefused) { uploadMesh(st.soup); if (st.meshP) await st.meshP; }
  const r = await jpost("/dev/api/component/ai", {
    message: msg, history: st.history, dims: st.dims, top: st.top, holes: st.holes, pegs: st.pegs, levels: st.levels,
    resolution: st.resolution, xy: st.xy, mesh_id: st.meshId, mesh_state: st.meshState,
    kind: st.kind, settings: st.settings, type: st.bg.querySelector("#cbType").value.trim(),
    description: st.bg.querySelector("#cbDesc").value.trim(),
    anchors: st.anchors.map((a) => ({ name: a.name, pose: a.p })),
    boxes: st.boxes.map((b) => ({ center: b.c, size: b.s })) });
  if (me !== st) return;
  send.disabled = false;
  if (!r.ok) { wait.textContent = r.error || "The assistant didn't answer"; wait.classList.add("bad"); return; }
  wait.textContent = r.reply || (r.changed ? "Set." : "");
  st.history = r.history || st.history;
  if (r.kind && r.kind !== st.kind) { st.kind = r.kind; st.kindSet = true; showKind(); }
  if (r.settings) st.settings = r.settings;
  renderSettings();
  if (r.changed) {
    st.touched = true;
    // a place the assistant moved on the user's word is the user's: a new template keeps it
    const was = st.anchors.find((a) => a.name === "place");
    st.anchors = r.anchors.map((a) => ({ name: a.name, p: a.pose.map(Number), fixed: a.name === "center",
      user: a.name === "place" && (was ? was.user || was.p.some((v, j) => Math.abs(v - a.pose[j]) > 1e-6) : true) }));
    st.boxes = r.boxes.map((b) => ({ c: b.center.map(Number), s: b.size.map(Number) }));
    st.sel = null; renderTables(); sync();
  }
}

function sync() {
  if (!st) return;
  // free the old markers' GPU memory before drawing new ones (sync runs on every keystroke)
  st.marks.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
  st.marks.clear();
  redraw();
  const R = st.dims ? Math.max(...st.dims) : 100;
  st.anchors.forEach((a, i) => {
    const g = new THREE.AxesHelper(Math.max(12, R * 0.12) * (st.sel === "a" + i ? 1.8 : 1));
    g.position.set(a.p[0], a.p[1], a.p[2]);
    g.rotation.set(a.p[3] * Math.PI / 180, a.p[4] * Math.PI / 180, a.p[5] * Math.PI / 180);
    g.add(new THREE.Mesh(new THREE.SphereGeometry(Math.max(1.5, R * 0.012)), new THREE.MeshBasicMaterial({ color: st.sel === "a" + i ? 0xff9f0a : 0x0a84ff })));
    // drawn over the part: an anchor at a hole's bottom sits inside the (opaque) model
    g.traverse((o) => { if (o.material) { o.material.depthTest = false; o.material.transparent = true; } o.renderOrder = 30; });
    st.marks.add(g);
  });
  for (const h of st.holes) {                  // the holes found from above
    const ring = new THREE.Mesh(new THREE.RingGeometry(h.d * 0.42, h.d * 0.5, 24), new THREE.MeshBasicMaterial({ color: 0x34c759, side: THREE.DoubleSide }));
    ring.position.set(h.x, h.y, (st.top || 0) + 0.3); st.marks.add(ring);
  }
  // the scene builder's collision boxes: translucent red; the selected one stronger
  if (st.showBoxes) st.boxes.forEach((b, i) => {
    const box = new THREE.Mesh(new THREE.BoxGeometry(b.s[0], b.s[1], b.s[2]),
      new THREE.MeshBasicMaterial({ color: 0xff3344, transparent: true, opacity: st.sel === "b" + i ? 0.45 : 0.28, depthWrite: false }));
    box.position.set(b.c[0], b.c[1], b.c[2]);
    box.renderOrder = 20;
    st.marks.add(box);
  });
}

// A1… slot anchors that make a full, regular grid (columns along x, rows along y,
// one height and turn) — shown as one "Slots A1–H12 (96)" row, edited as a grid
const SLOT_RE = /^([A-Z]+)(\d+)$/;
// row letters as Workspace's racks name them: A…Z, then AA, AB… (component_kinds.row_name)
const rowName = (r) => { let s = ""; for (r += 1; r; r = Math.floor((r - 1) / 26)) s = String.fromCharCode(65 + (r - 1) % 26) + s; return s; };
const rowIndex = (s) => [...s].reduce((n, c) => n * 26 + c.charCodeAt(0) - 64, 0) - 1;
function slotGrid(anchors) {
  const pos = new Map();
  anchors.forEach((a, i) => { const m = SLOT_RE.exec(a.name); if (m) pos.set(`${rowIndex(m[1])},${+m[2] - 1}`, i); });
  if (pos.size < 4) return null;
  let R = 0, C = 0;
  for (const k of pos.keys()) { const [r, c] = k.split(",").map(Number); R = Math.max(R, r + 1); C = Math.max(C, c + 1); }
  if (R * C !== pos.size || !pos.has("0,0")) return null;
  const at = (r, c) => anchors[pos.get(`${r},${c}`)].p, a1 = at(0, 0);
  const px = C > 1 ? (at(0, C - 1)[0] - a1[0]) / (C - 1) : 0, py = R > 1 ? (at(R - 1, 0)[1] - a1[1]) / (R - 1) : 0;
  for (const [k, i] of pos) {
    const [r, c] = k.split(",").map(Number), p = anchors[i].p;
    if (Math.abs(p[0] - (a1[0] + c * px)) > 0.05 || Math.abs(p[1] - (a1[1] + r * py)) > 0.05 || Math.abs(p[2] - a1[2]) > 0.05
        || [3, 4, 5].some((j) => Math.abs(p[j] - a1[j]) > 0.01)) return null;
  }
  const last = rowName(R - 1);
  return { rows: R, cols: C, first: a1.slice(0, 3), pitch: [Math.round(px * 100) / 100, Math.round(py * 100) / 100],
           rot: a1.slice(3, 6), idx: new Set(pos.values()), last: last + C, corners: new Set(["A1", "A" + C, last + "1", last + C]) };
}

const num = (v, f, label, dis) => `<input class="input" type="number" step="0.5" inputmode="decimal" value="${v}" data-f="${f}" aria-label="${label}" ${dis ? "disabled" : ""}>`;
const AX6 = ["x", "y", "z", "rx", "ry", "rz"], BX6 = ["x", "y", "z", "lx", "ly", "lz"];

function renderTables() {
  const A = st.bg.querySelector("#cbA"), B = st.bg.querySelector("#cbB");
  const g = st.slotG = slotGrid(st.anchors);
  if (st.sel === "g" && !g) st.sel = null;
  const del = (label) => `<button class="cb-del" type="button" data-del aria-label="${label}" title="${label}">${ICON.x}</button>`;
  const row = (a, i) => `<tr data-k="a${i}" class="${st.sel === "a" + i ? "sel" : ""}"><td>
    <div class="cb-r1"><input class="input cb-name" value="${esc(a.name)}" data-f="name" aria-label="Anchor name" ${a.fixed ? "disabled" : ""}>
      ${a.fixed ? '<span class="cb-del" aria-hidden="true"></span>' : del(`Delete anchor ${esc(a.name)}`)}</div>
    <div class="cb-r2">${a.p.map((v, j) => num(v, j, `${a.name} ${AX6[j]}`, a.fixed)).join("")}</div></td></tr>`;
  let html = st.anchors.map((a, i) => (g && g.idx.has(i) ? "" : row(a, i))).join("");
  if (g) {
    const n = g.rows * g.cols, open = !!st.gridOpen;
    html += `<tr data-k="g" class="cb-grp${st.sel === "g" ? " sel" : ""}"><td>
      <div class="cb-r1"><button class="cb-disc" type="button" data-open aria-expanded="${open}">${ICON.chev}<span>Slots A1–${g.last}</span><span class="cb-count">${n}</span></button>
        <span class="cb-grp-sum">${g.rows} × ${g.cols}</span>${del(`Delete all ${n} slots`)}</div>
      <div class="cb-r2 cb-caps" aria-hidden="true"><span>A1 x</span><span>A1 y</span><span>z</span><span title="Pitch along x (column to column)">Δx</span><span title="Pitch along y (row to row)">Δy</span><span>rz</span></div>
      <div class="cb-r2">${num(g.first[0], "x0", "A1 x")}${num(g.first[1], "y0", "A1 y")}${num(g.first[2], "z", "Slot height z")}${num(g.pitch[0], "px", "Pitch along x")}${num(g.pitch[1], "py", "Pitch along y")}${num(g.rot[2], "rz", "Slot turn rz")}</div></td></tr>`;
    if (open) html += st.anchors.map((a, i) => (g.idx.has(i) ? row(a, i).replace("<tr ", '<tr data-slot="1" ') : "")).join("");
  }
  A.innerHTML = html;
  B.innerHTML = st.boxes.map((b, i) => `<tr data-k="b${i}" class="${st.sel === "b" + i ? "sel" : ""}"><td>
    <div class="cb-r1"><span class="cb-name">Box ${i + 1}</span>${del(`Delete box ${i + 1}`)}</div>
    <div class="cb-r2">${[...b.c, ...b.s].map((v, j) => num(v, j, `Box ${i + 1} ${BX6[j]}`)).join("")}</div></td></tr>`).join("");
  for (const tr of [...A.children, ...B.children]) {
    const k = tr.dataset.k, isA = k[0] === "a", i = +k.slice(1);
    // a row is selected by focusing any of its fields, or a click anywhere on it
    const pick = () => { if (st.sel === k) return; st.sel = k; tr.parentElement.querySelectorAll("tr").forEach((t) => t.classList.toggle("sel", t === tr)); sync(); };
    tr.addEventListener("focusin", pick);
    tr.addEventListener("click", (e) => { if (!e.target.closest("[data-del], [data-open]")) pick(); });
    tr.addEventListener("mouseenter", () => { st.hover = k; redraw(); });
    tr.addEventListener("mouseleave", () => { if (st.hover === k) st.hover = null; redraw(); });
    if (k === "g") {
      tr.querySelector("[data-open]").onclick = () => { st.gridOpen = !st.gridOpen; renderTables(); };
      tr.querySelector("[data-del]").onclick = () => { st.touched = true; st.anchors = st.anchors.filter((a, j) => !g.idx.has(j)); st.sel = null; renderTables(); sync(); };
      // the grid edited as a grid: every slot moves with A1 / the pitch / the height / the turn
      tr.querySelectorAll("input").forEach((inp) => {
        inp.addEventListener("input", () => {
          const v = parseFloat(inp.value);
          if (!isFinite(v)) return;
          st.touched = true;
          const f = inp.dataset.f;
          if (f === "x0") g.first[0] = v; else if (f === "y0") g.first[1] = v; else if (f === "z") g.first[2] = v;
          else if (f === "px") g.pitch[0] = v; else if (f === "py") g.pitch[1] = v; else if (f === "rz") g.rot[2] = v;
          st.anchors.forEach((a, j) => {
            if (!g.idx.has(j)) return;
            const m = SLOT_RE.exec(a.name), r = rowIndex(m[1]), c = +m[2] - 1;
            a.p = [g.first[0] + c * g.pitch[0], g.first[1] + r * g.pitch[1], g.first[2], g.rot[0], g.rot[1], g.rot[2]]
              .map((x) => Math.round(x * 100) / 100);
          });
          sync();
        });
        inp.addEventListener("change", () => { if (st.gridOpen) renderTables(); });
      });
      continue;
    }
    tr.querySelectorAll("input").forEach((inp) => inp.addEventListener("input", () => {
      const f = inp.dataset.f;
      st.touched = true;
      if (isA) {
        st.anchors[i].user = true;
        if (f === "name") st.anchors[i].name = inp.value.trim();
        else {
          const was = st.anchors[i].p[+f], v = parseFloat(inp.value) || 0;
          st.anchors[i].p[+f] = v;
          // place and the slots are one surface: the slots that sat at place's height follow it
          if (st.anchors[i].name === "place" && +f === 2 && g && Math.abs(g.first[2] - was) < 0.05) {
            g.first[2] = v;
            st.anchors.forEach((a, j) => { if (g.idx.has(j)) a.p[2] = v; });
            st.slotsMoved = true;
          }
        }
      } else { const j = +f, v = parseFloat(inp.value) || 0; if (j < 3) st.boxes[i].c[j] = v; else st.boxes[i].s[j - 3] = Math.max(0.1, v); }
      sync();
    }));
    // the slot grid's height field shows where the slots went
    tr.querySelectorAll("input").forEach((inp) => inp.addEventListener("change", () => { if (st.slotsMoved) { st.slotsMoved = false; renderTables(); } }));
    const d = tr.querySelector("[data-del]");
    if (d) d.onclick = () => { st.touched = true; (isA ? st.anchors : st.boxes).splice(i, 1); st.sel = null; renderTables(); sync(); };
  }
  // the empty states, and the drop hint once there's something in the view
  for (const [tb, list] of [[A, st.anchors], [B, st.boxes]]) st.bg.querySelector(`[data-empty-for="${tb.id}"]`).hidden = list.length > 0;
  if (st.dims || st.anchors.length || st.boxes.length) st.bg.querySelector(".cb-drop").hidden = true;
}

async function save() {
  const $ = (s) => st.bg.querySelector(s);
  if (!st.glbB64 && !st.editing) { say("Upload the model first", true); return; }
  const names = st.anchors.map((a) => a.name);
  if (new Set(names).size !== names.length) { say("Anchor names must be unique", true); return; }
  const body = {
    type: $("#cbType").value.trim(), edit: !!st.editing, description: $("#cbDesc").value.trim(), dims: st.dims,
    glb_b64: st.glbB64, unit_scale: st.unitScale, up: st.up, kind: st.kind, settings: st.settings, file_name: st.fname,
    anchors: Object.fromEntries(st.anchors.map((a) => [a.name, a.p])),
    collision_boxes: st.boxes.map((b) => ({ pose: [...b.c, 0, 0, 0], scale: b.s })),
  };
  $("#cbSave").disabled = true; say("Saving…");
  const me = st;
  const r = await jpost(`${me.api}/part`, body);
  if (!r.ok) { if (me === st) { $("#cbSave").disabled = false; say(r.error || "Save failed", true); } return; }
  if (me === st) close();
  if (me.onSaved) me.onSaved(r);                 // saved even if the dialog was closed meanwhile
}
