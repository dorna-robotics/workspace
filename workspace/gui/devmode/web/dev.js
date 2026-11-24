// Developer mode — /dev/?project=<name>
// Four tabs over one project: Scene builder (the embedded builder, its
// Settings + Camera windows), Workflow (the project page itself — 3D
// view, run controls, steps, devices — plus the scene's simulation
// switches), HMI (Main + Parameters previews) and Calibration.
import { apiFetch, esc, stateVariant, stateLabel, isLaunched } from "/orchestrator/api.js";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const params = new URLSearchParams(location.search);
const PROJECT = (params.get("project") || "").trim();
const API = `/dev/api/projects/${encodeURIComponent(PROJECT)}`;
const TABS = ["scene", "workflow", "hmi", "calibration"];

let info = null;          // /info
let status = {};          // orchestrator status
let tab = null;

function banner(html, bad = false) {
  const b = $("#dvBanner");
  if (!html) { b.hidden = true; return; }
  b.className = "dv-banner" + (bad ? " bad" : "");
  b.innerHTML = html;
  b.hidden = false;
}

async function api(path, opts = {}) {
  const r = await fetch(API + path, {
    ...opts,
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    cache: "no-store",
  });
  if (r.status === 401) { location.href = "/dev/"; throw new Error("login required"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) throw new Error(j.error || r.statusText);
  return j;
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });

function setAiContext() {
  window.__aiContext = { page: "developer", project: PROJECT, tab };
}

// ── tabs ─────────────────────────────────────────────────────────────
function showTab(t) {
  if (!TABS.includes(t)) t = "scene";
  tab = t;
  $$(".dv-tabs button").forEach(b => { b.classList.toggle("on", b.dataset.tab === t); b.setAttribute("aria-selected", b.dataset.tab === t); });
  $$(".dv-pane").forEach(p => p.hidden = p.dataset.pane !== t);
  try { localStorage.setItem("dv_tab", t); } catch (_) {}
  history.replaceState(null, "", `?project=${encodeURIComponent(PROJECT)}#${t}`);
  setAiContext();
  ({ scene: openScene, workflow: openWorkflow, hmi: openHmi, calibration: openCalibration })[t]();
}
$$(".dv-tabs button").forEach(b => b.onclick = () => showTab(b.dataset.tab));

// ── floating windows ────────────────────────────────────────────────
function makeWin(id, title, cls = "") {
  let w = document.getElementById(id);
  if (w) { w.querySelector("h3").textContent = title; return w; }
  w = document.createElement("div");
  w.id = id;
  w.className = "dv-win " + cls;
  w.innerHTML = `<div class="dv-win-head"><h3></h3>
      <button class="btn btn-ghost btn-sm btn-icon" data-close title="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></div>
    <div class="dv-win-body"></div><div class="dv-win-foot" hidden></div>`;
  w.querySelector("h3").textContent = title;
  const n = $("#dvWins").children.length;
  w.style.right = `${24 + n * 24}px`;
  w.style.top = `${120 + n * 24}px`;
  $("#dvWins").appendChild(w);
  w.querySelector("[data-close]").onclick = () => { w.remove(); w.dispatchEvent(new Event("closed")); };
  // drag by the head
  const head = w.querySelector(".dv-win-head");
  head.addEventListener("pointerdown", e => {
    if (e.target.closest("button")) return;
    const r = w.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
    head.setPointerCapture(e.pointerId);
    const mv = ev => {
      w.style.right = "auto";
      w.style.left = `${Math.max(0, Math.min(innerWidth - 80, ev.clientX - dx))}px`;
      w.style.top = `${Math.max(0, Math.min(innerHeight - 60, ev.clientY - dy))}px`;
    };
    const up = () => { head.removeEventListener("pointermove", mv); head.removeEventListener("pointerup", up); };
    head.addEventListener("pointermove", mv);
    head.addEventListener("pointerup", up);
  });
  return w;
}

// ── 1 · Scene builder ───────────────────────────────────────────────
let sbReady = false, sbSel = null, thumbDone = false;
let baseSig = "";         // the scene files as the builder loaded them (Save refuses on a mismatch)

async function openScene() {
  if (sbReady || !info) return;
  sbReady = true;
  if (info.components.some(c => c.settings?.fields.some(f => f.kind === "serial"))) loadPorts();
  await loadScene();
}

// The builder holds ONE project server-side and keeps its own copy in
// the browser. Developer mode always starts it from the project's
// files: the AI and the Parameters window write those files too, and a
// stale builder copy saved back would undo them.
async function loadScene() {
  const frame = $("#sbFrame");
  try {
    await fetch("/scene-builder/api/reset", { method: "POST" });
    await loadInfo();
    baseSig = info.scene_sig;
    // Only this project's remembered builder state (file slots, tags):
    // other projects' unsaved work in the standalone builder stays.
    try {
      const dir = String(info.dir || "").replace(/\/+$/, "");
      for (const k of [`sb_persist::${dir}`, `sb_persist::${dir}/`]) localStorage.removeItem(k);
      localStorage.setItem("sb_tab", "scene");
    } catch (_) {}
    const r = await fetch("/scene-builder/api/set_project", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: info.dir }),
    }).then(r => r.json());
    if (!r.ok) throw new Error(r.error || "the builder refused the project");
  } catch (e) {
    banner(`Scene builder: ${esc(e.message)}`, true);
  }
  onSelect({});
  setSaveReady(false);
  frame.src = `/scene-builder/index.html?devmode=1&project=${encodeURIComponent(PROJECT)}&t=${Date.now()}`;
  frame.addEventListener("load", () => {
    const w = frame.contentWindow;
    w.addEventListener("sb-select", e => onSelect(e.detail || {}));
    w.addEventListener("sb-menu", e => e.detail?.name && openParams(e.detail.name));
    pushMenus();
    // The project's picture for the Projects page, once the scene is in.
    waitForScene(w).then(ok => {
      setSaveReady(ok);
      if (!ok) return banner("The scene did not finish loading in the builder — Reload the page to try again.", true);
      if (!thumbDone) setTimeout(() => saveThumb(true), 1500);       // meshes stream in after the import
    });
  }, { once: true });
}

// Loaded = the builder imported the project's files completely. Until
// then Save stays off: a half-imported scene saved back would cut every
// component not imported yet.
function waitForScene(w, ms = 120000) {
  const t0 = Date.now();
  return new Promise(res => {
    const tick = () => {
      if (w.closed || $("#sbFrame").contentWindow !== w) return res(false);
      if (w.__three && w.__projectBundle && w.__importBusy === false) return res(true);
      if (Date.now() - t0 > ms) return res(false);
      setTimeout(tick, 400);
    };
    tick();
  });
}

function setSaveReady(ok) {
  const b = $("#sbSave");
  b.disabled = !ok;
  b.title = ok ? "Write the builder's scene into the project's scene files" : "Loading the scene…";
}

// Which parts have a settings menu (pins, ports, simulation…): the
// builder's list shows them a settings button; plain parts (fixture
// plates, racks) have none and open no window.
const hasMenu = (name) => !!info?.components.find(c => c.name === name)?.menu;
function pushMenus() {
  const w = $("#sbFrame").contentWindow;
  if (!w || !info) return;
  try {
    w.__sbMenus = new Set(info.components.filter(c => c.menu).map(c => c.name));
    w.updateObjectList?.();
  } catch (_) {}
}

function onSelect({ name, type }) {
  sbSel = name ? { name, type } : null;
  const menu = !!name && hasMenu(name);
  $("#sbSel").textContent = !name ? "Parts with settings show a settings button in the list."
    : `${name} · ${type || "component"}` + (menu ? "" : " — no settings");
  $("#sbParams").hidden = !menu;
  $("#sbCamera").hidden = !(type || "").startsWith("inspection_");
  $("#sbVision").hidden = !isVisionPart(type);
  if (name && isVisionPart(type)) prefetchVision(name);
  if (menu) openParams(name);
  else document.getElementById("dvParams")?.querySelector("[data-close]")?.click();
}

$("#sbNew").onclick = () => {
  const w = $("#sbFrame").contentWindow;
  if (w && w.__sbOpenInsert) w.__sbOpenInsert();
};
$("#sbParams").onclick = () => sbSel && openParams(sbSel.name, true);
$("#sbCamera").onclick = () => sbSel && openCamera(sbSel.name);
$("#sbVision").onclick = () => sbSel && openVision(sbSel.name);
$("#sbThumb").onclick = () => saveThumb(false);

$("#sbSave").onclick = async () => {
  const w = $("#sbFrame").contentWindow;
  if (!w || !w.__sbConfigByFile) return;
  const btn = $("#sbSave");
  if (!w.__projectBundle || w.__importBusy !== false) return;
  btn.disabled = true;
  try {
    const files = w.__sbConfigByFile();
    const r = await post("/scene/save", { files, base_sig: baseSig });
    baseSig = r.scene_sig;
    const parts = [];
    if (r.added.length) parts.push(`added ${r.added.length}`);
    if (r.changed.length) parts.push(`changed ${r.changed.length}`);
    if (r.removed.length) parts.push(`removed ${r.removed.length}`);
    const what = parts.length ? `Saved to ${r.files.join(", ")}: ${parts.join(", ")}.` : "Nothing to save — the files already match.";
    const skip = r.skipped.length ? ` Not saved, because the scene file computes them with a template (edit the file itself): ${r.skipped.join(", ")}.` : "";
    banner(esc(what + skip) + (r.relaunch && parts.length ? " Relaunch the project to use it." : ""));
    saveThumb(true);
    loadInfo();
  } catch (e) {
    banner(`Save failed: ${esc(e.message)} <button class="btn btn-sm" id="sbReload">Reload the Scene builder</button>`, true);
    $("#sbReload")?.addEventListener("click", () => { banner(""); loadScene(); });
  } finally {
    btn.disabled = false;
  }
};

function snapshot() {
  const w = $("#sbFrame").contentWindow;
  const t = w && w.__three;
  if (!t || !t.renderer) return null;
  t.renderer.render(t.scene, t.camera);            // same task as toDataURL: the buffer is still there
  const src = t.renderer.domElement;
  const W = 640, H = Math.round(W * 3 / 4);
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const g = c.getContext("2d");
  const s = Math.max(W / src.width, H / src.height);
  const sw = W / s, sh = H / s;
  g.drawImage(src, (src.width - sw) / 2, (src.height - sh) / 2, sw, sh, 0, 0, W, H);
  return c.toDataURL("image/png");
}

async function saveThumb(quiet) {
  const png = snapshot();
  if (!png) { if (!quiet) banner("The 3D view is not ready yet.", true); return; }
  try {
    const r = await fetch(`/home/api/projects/${encodeURIComponent(PROJECT)}/thumb`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ image: png }),
    });
    if (!r.ok) throw new Error(r.status);
    thumbDone = true;
    if (!quiet) banner("Saved this view as the project's picture.");
  } catch (_) {
    if (!quiet) banner("The Projects page cannot take a picture on this unit yet.", true);
  }
}

// Settings window. /info carries every part's window (its schema), so
// a click opens it at once — no round trip while the 3D view is busy
// with the selection; a reselect of the part already shown leaves the
// window, and any typing in it, alone. Serial ports are asked for once.
let ports = [];
const schemas = new Map();                     // name → /params schema (from /info)
function loadPorts() {
  fetch("/dev/api/ports").then(r => r.json()).then(j => { ports = j.ports || []; }).catch(() => {});
}

async function openParams(name, force = false) {
  const open = document.getElementById("dvParams");
  if (open && open.dataset.comp === name && !force) return;
  const w = makeWin("dvParams", name);
  w.dataset.comp = name;
  const body = w.querySelector(".dv-win-body"), foot = w.querySelector(".dv-win-foot");
  let s = schemas.get(name);
  if (!s) {                                       // not in /info (yet): ask
    body.innerHTML = `<div class="muted">Loading…</div>`;
    foot.hidden = true;
    try { s = await api(`/params?component=${encodeURIComponent(name)}`); }
    catch (e) { body.innerHTML = `<div class="dv-msg bad">${esc(e.message)}</div>`; return; }
    if (w.dataset.comp !== name) return;          // another click won
  }
  if (!s.fields.length) {
    body.innerHTML = `<div class="muted">${esc(s.type)} has no settings — it is placed, not connected.</div>`;
    foot.hidden = true;
    return;
  }
  const io = s.fields.filter(f => f.group === "io"), rest = s.fields.filter(f => f.group !== "io");
  body.innerHTML = `<div class="muted dv-small">${esc(s.type)}</div>` + rest.map(f => fieldHtml(f, ports)).join("")
    + (io.length ? `<div class="dv-grp">I/O wiring</div>` + io.map(f => fieldHtml(f, ports)).join("") : "");
  foot.hidden = false;
  foot.innerHTML = `<button class="btn btn-primary" data-save>Save</button>
    ${s.type.startsWith("inspection_") ? `<button class="btn" data-cam>Camera</button>` : ""}
    ${isVisionPart(s.type) ? `<button class="btn" data-vision>Vision configurator</button>` : ""}
    <span class="dv-msg" data-msg></span>`;
  const msg = foot.querySelector("[data-msg]");
  foot.querySelector("[data-cam]")?.addEventListener("click", () => openCamera(name));
  foot.querySelector("[data-vision]")?.addEventListener("click", () => openVision(name));
  foot.querySelector("[data-save]").onclick = async () => {
    const values = {};
    for (const f of s.fields) {
      const el = body.querySelector(`[data-key="${f.key}"]`);
      values[f.key] = f.kind === "bool" ? el.checked : f.kind === "io" ? ioValue(el) : el.value;
    }
    msg.className = "dv-msg"; msg.textContent = "Saving…";
    try {
      const r = await post("/params", { component: name, values });
      // Connection + I/O keys: the builder never writes them, so its copy
      // is still current. Rail / critical: the builder writes those too
      // and would save its old values back — it must reload first
      // (baseSig stays old, so a Save before that is refused with a 409).
      if (!(r.builder_keys || []).length) baseSig = r.scene_sig;
      else {
        banner(`Saved ${esc(r.builder_keys.join(", "))}. The Scene builder still shows the old value — reload it before you move anything (its unsaved changes are dropped). <button class="btn btn-sm" id="sbReload">Reload the Scene builder</button>`);
        $("#sbReload")?.addEventListener("click", () => { banner(""); loadScene(); });
      }
      msg.className = "dv-msg ok";
      msg.textContent = !r.changed.length ? "No changes." :
        `Saved to ${Object.keys(r.files).map(f => f.split("/").pop()).join(", ")}.` + (r.relaunch ? " Relaunch to apply." : "");
      if (r.changed.length) loadInfo();
    } catch (e) {
      msg.className = "dv-msg bad"; msg.textContent = e.message;
    }
  };
}

// I/O wiring: [[pin, value, wait], …] as rows of pin · 0/1 · wait (s).
const PINS = 16;
function ioRow([pin, val, wait] = [null, 1, 0.25]) {
  const pinOpts = `<option value="" ${pin == null ? "selected" : ""}>—</option>` +
    Array.from({ length: PINS }, (_, i) => `<option value="${i}" ${pin === i ? "selected" : ""}>Out ${i}</option>`).join("");
  const valOpts = ["", 0, 1].map(v => `<option value="${v}" ${(val ?? "") === v ? "selected" : ""}>${v === "" ? "—" : v ? "1 · on" : "0 · off"}</option>`).join("");
  return `<div class="dv-io-row">
    <select class="input" data-io="pin" aria-label="Output pin">${pinOpts}</select>
    <select class="input" data-io="val" aria-label="Value">${valOpts}</select>
    <input class="input" data-io="wait" inputmode="decimal" value="${esc(wait ?? 0)}" aria-label="Wait (s)"/><span class="muted dv-small">s</span>
    <button class="btn btn-ghost btn-icon" data-io-del title="Remove this step"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
  </div>`;
}
function ioValue(el) {
  return [...el.querySelectorAll(".dv-io-row")].map(r => [
    r.querySelector('[data-io="pin"]').value, r.querySelector('[data-io="val"]').value, r.querySelector('[data-io="wait"]').value]);
}
document.addEventListener("click", e => {
  const del = e.target.closest("[data-io-del]");
  if (del) { const box = del.closest(".dv-io"); if (box.querySelectorAll(".dv-io-row").length > 1) del.closest(".dv-io-row").remove(); return; }
  const add = e.target.closest("[data-io-add]");
  if (add) add.insertAdjacentHTML("beforebegin", ioRow());
});

function fieldHtml(f, portList = []) {
  const id = `f_${f.key.replace(/\W/g, "_")}`;
  const v = f.value ?? (f.group === "io" ? f.default ?? "" : "");
  let input;
  if (f.kind === "io") {
    const rows = Array.isArray(v) && v.length ? v : [[null, 1, 0.25]];
    input = `<div class="dv-io" id="${id}" data-key="${esc(f.key)}">
      <div class="dv-io-head muted dv-small"><span>Pin</span><span>Value</span><span>Wait</span></div>
      ${rows.map(ioRow).join("")}<button class="btn btn-ghost btn-sm" data-io-add>+ Add a step</button></div>`;
  } else if (f.kind === "bool") {
    input = `<label class="dv-switch"><input type="checkbox" id="${id}" data-key="${esc(f.key)}" ${f.value ? "checked" : ""}/>
      <span>${f.key === "simulation" ? (f.value ? "Simulated" : "Real device") : (f.value ? "On" : "Off")}</span></label>`;
  } else if (f.kind === "enum") {
    const opts = (f.options || []).slice();
    if (!opts.includes(v)) opts.unshift(v);          // unset ("") or a value the list does not know
    input = `<select class="input" id="${id}" data-key="${esc(f.key)}">${opts.map(o => `<option value="${esc(o)}" ${o === v ? "selected" : ""}>${esc(o || "— none —")}</option>`).join("")}</select>`;
  } else if (f.kind === "serial") {
    input = `<input class="input mono" id="${id}" data-key="${esc(f.key)}" list="${id}_l" value="${esc(v)}" placeholder="/dev/serial/by-id/…" spellcheck="false"/>
      <datalist id="${id}_l">${portList.map(p => `<option value="${esc(p.port)}">${esc(p.description)}</option>`).join("")}</datalist>`;
  } else {
    const num = f.kind === "int" || f.kind === "float";
    input = `<input class="input${num ? "" : " mono"}" id="${id}" data-key="${esc(f.key)}" value="${esc(v)}" ${num ? `inputmode="decimal"` : ""} spellcheck="false"/>`;
  }
  return `<div class="dv-field"><label for="${id}">${esc(f.label)}</label>${input}${f.hint ? `<div class="hint">${esc(f.hint)}</div>` : ""}</div>`;
}
document.addEventListener("change", e => {
  const el = e.target;
  if (el.matches?.(".dv-switch input[data-key]")) {
    el.nextElementSibling.textContent = el.dataset.key === "simulation" ? (el.checked ? "Simulated" : "Real device") : (el.checked ? "On" : "Off");
  }
});

// The vision configurator: the camera's vision server GUI (dorna_vision),
// in a new window on its Playground page. The server is the one the
// camera names (camera_cfg.server); the GUI takes no camera in its link,
// so the banner names the serial to pick there. In simulation, or with no
// server set, the banner says why instead.
const isVisionPart = (type) => /^inspection_/.test(type || "") || type === "vision_server";
const visionWhere = new Map();                    // name -> promise of /vision/<name> (asked on select)
function visionInfo(name, fresh) {
  if (fresh || !visionWhere.has(name)) visionWhere.set(name, api(`/vision/${encodeURIComponent(name)}`).catch((e) => ({ error: e.message })));
  return visionWhere.get(name);
}
async function openVision(name) {
  // the answer asked for on select is usually here: open the window in the
  // click itself (no popup blocker); else a blank one now, pointed after
  let j = visionWhere.get(name)?.settled;
  const win = j ? (j.available ? window.open(j.url, "_blank", "noopener") : null) : window.open("", "_blank");
  if (!j) {
    j = await visionInfo(name, true);
    if (win && j.available) { try { win.opener = null; } catch (_) {} win.location.href = j.url; }
    else win?.close();
  }
  visionInfo(name, true).then((r) => { visionWhere.get(name).settled = r; });   // fresh for the next click
  if (j.error) { banner(`Vision configurator: ${esc(j.error)}`, true); return; }
  if (!j.available) { banner(`Vision configurator — ${esc(name)}: ${esc(j.reason)}`, true); return; }
  const link = `<a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.host)}:${esc(j.port)}</a>`;
  const cam = j.serial ? ` Pick camera <b class="mono">${esc(j.serial)}</b> there.` : "";
  const sim = j.camera_simulated ? ` ${esc(name)} itself is still simulated in the scene.` : "";
  banner(`Opened the vision configurator of ${esc(j.server)} (${link}).${cam}${sim}`);
}
function prefetchVision(name) {
  const p = visionInfo(name, true);
  p.then((r) => { p.settled = r; });
}

// Camera preview window: a frame every ~0.5 s while it is open.
function openCamera(name) {
  const w = makeWin(`dvCam_${name}`, `Camera · ${name}`, "dv-cam");
  const body = w.querySelector(".dv-win-body");
  if (w._poll) return;
  body.innerHTML = `<div class="dv-cam-view"><span>Connecting…</span></div><div class="muted dv-small" data-st></div>`;
  const view = body.querySelector(".dv-cam-view"), st = body.querySelector("[data-st]");
  let url = null, alive = true, n = 0;
  w.addEventListener("closed", () => { alive = false; if (url) URL.revokeObjectURL(url); });
  w._poll = true;
  const tick = async () => {
    if (!alive) return;
    let wait = 500;
    try {
      const r = await fetch(`${API}/camera/${encodeURIComponent(name)}.jpg`, { cache: "no-store" });
      if (r.ok && (r.headers.get("Content-Type") || "").startsWith("image/")) {
        const b = await r.blob();
        if (url) URL.revokeObjectURL(url);
        url = URL.createObjectURL(b);
        let img = view.querySelector("img");
        if (!img) { view.innerHTML = ""; img = document.createElement("img"); img.alt = name; view.appendChild(img); }
        img.src = url;
        st.textContent = `Live · frame ${++n}`;
      } else {
        const j = await r.json().catch(() => ({}));
        view.innerHTML = `<span>${esc(j.reason || j.error || `No image (${r.status})`)}</span>`;
        st.textContent = j.sim ? "" : "Retrying…";
        wait = j.sim ? 5000 : 3000;
      }
    } catch (e) {
      view.innerHTML = `<span>${esc(e.message)}</span>`;
      wait = 3000;
    }
    setTimeout(tick, wait);
  };
  tick();
}

// ── 2 · Workflow ────────────────────────────────────────────────────
// The project page itself (orchestrator workspace.html, ?embed=1: no
// nav or account of its own) — the 3D view, run controls, steps,
// devices and their buttons, exactly as operators know it. Developer
// mode adds the scene's simulation switches on top.
function openWorkflow() {
  const page = `/orchestrator/workspace.html?name=${encodeURIComponent(PROJECT)}`;
  $("#wfOperator").href = page;
  const f = $("#wfFrame");
  if (!f.getAttribute("src")) f.src = `${page}&embed=1`;
  renderSimSum();
}

function renderSimSum() {
  if (!info) return;
  const devs = info.components.filter(c => c.device), n = devs.filter(c => c.simulation).length;
  $("#wfSimSum").textContent = !devs.length ? "Simulation · no devices"
    : n === devs.length ? "Simulation · all simulated" : !n ? "Simulation · all real" : `Simulation · ${n} of ${devs.length} simulated`;
  if (document.getElementById("dvSim")) renderSim();
}

function openSim() {
  const w = makeWin("dvSim", "Simulation");
  const body = w.querySelector(".dv-win-body"), foot = w.querySelector(".dv-win-foot");
  body.innerHTML = `<div class="muted dv-small">Saved to the project's scene: a simulated device answers, nothing real moves.</div><div class="dv-note" id="wfSimNote" hidden></div><div id="wfSim"></div>`;
  foot.hidden = false;
  foot.innerHTML = `<button class="btn" data-all="1">All simulated</button><button class="btn" data-all="0">All real</button>`;
  foot.onclick = e => {
    const b = e.target.closest("[data-all]");
    if (b && info) setSim(info.components.filter(c => c.device).map(c => c.name), b.dataset.all === "1");
  };
  body.addEventListener("change", e => {
    const el = e.target.closest("[data-sim]");
    if (el) setSim([el.dataset.sim], el.checked);
  });
  renderSim();
}
$("#wfSimBtn").onclick = openSim;

function renderSim() {
  const box = $("#wfSim");
  if (!box || !info) return;
  const devs = info.components.filter(c => c.device);
  if (!devs.length) { box.innerHTML = `<div class="dv-empty">This project's scene has no devices.</div>`; return; }
  const groups = [["robot", "Robot"], ["camera", "Cameras"], ["device", "Devices"]];
  box.innerHTML = groups.map(([k, label]) => {
    const rows = devs.filter(c => c.kind === k);
    if (!rows.length) return "";
    return `<div class="dv-grp">${label}</div>` + rows.map(c => `
      <div class="dv-row"><div class="nm"><b>${esc(c.name)}</b><span>${esc(c.type)}</span></div>
        <label class="dv-switch"><span>${c.simulation ? "Simulated" : "Real"}</span>
          <input type="checkbox" data-sim="${esc(c.name)}" ${c.simulation ? "checked" : ""} title="Simulation"/></label></div>`).join("");
  }).join("");
}

async function setSim(names, on) {
  try {
    const r = await post("/simulation", { components: names, on });
    if (r.scene_sig) baseSig = r.scene_sig;
    const note = $("#wfSimNote");
    if (r.relaunch && note) {
      note.innerHTML = `<span>The running project still uses the old setting — relaunch it to apply.</span><button class="btn btn-sm" data-relaunch>Relaunch now</button>`;
      note.hidden = false;
      note.querySelector("[data-relaunch]").onclick = async () => {
        note.hidden = true;
        try { await apiFetch(`/workspace/${encodeURIComponent(PROJECT)}/cmd`, { method: "POST", body: JSON.stringify({ cmd: "relaunch" }) }); }
        catch (err) { banner(`Relaunch: ${esc(err.message)}`, true); }
        pollStatus();
      };
    }
  } catch (e) {
    banner(`Simulation: ${esc(e.message)}`, true);
  }
  await loadInfo();
}

// The theme toggle is the host's: the embedded pages follow it.
addEventListener("storage", e => {
  if (e.key !== "orch_theme") return;
  for (const f of $$("iframe")) {
    try { f.contentDocument?.documentElement.setAttribute("data-theme", e.newValue || "light"); } catch (_) {}
  }
});

// ── 3 · HMI ─────────────────────────────────────────────────────────
let hmiLaunchedShown = null;
function openHmi() {
  const launched = isLaunched(status.state) && status.state !== "LAUNCHED_NOT_READY";
  if (hmiLaunchedShown === launched && $("#hmiSetup").src) return;
  hmiLaunchedShown = launched;
  const q = `project=${encodeURIComponent(PROJECT)}`;
  // Main: the live pendant while the project runs; before that, the
  // project's own screen mounted from its files (hmi.html).
  $("#hmiMain").src = launched
    ? `/orchestrator/workspace.html?name=${encodeURIComponent(PROJECT)}&pendant=1`
    : `/dev/hmi.html?${q}&screen=main`;
  $("#hmiSetup").src = `/dev/hmi.html?${q}&screen=setup`;
  $("#hmiMsg").textContent = launched
    ? "Main is the live page of the running project. Parameters updates when its files change."
    : "Both pages update when their files change. Launch to see Main live.";
}
$$("#hmiSeg button").forEach(b => b.onclick = () => {
  $$("#hmiSeg button").forEach(x => x.classList.toggle("on", x === b));
  $("#hmiGrid").dataset.v = b.dataset.v;
});
$("#hmiReload").onclick = () => { hmiLaunchedShown = null; $("#hmiSetup").src = ""; openHmi(); };

// ── 4 · Calibration ─────────────────────────────────────────────────
function openCalibration() {
  const f = $("#calFrame");
  if (!f.src) f.src = `/dev/calibration.html?project=${encodeURIComponent(PROJECT)}`;
}

// ── project + status ────────────────────────────────────────────────
async function loadInfo() {
  info = await api("/info");
  $("#dvProject").textContent = info.label || info.name;
  document.title = `${info.name} · Developer mode`;
  if (info.scene_error) banner(`The scene does not load: ${esc(info.scene_error)}`, true);
  schemas.clear();
  for (const c of info.components) if (c.settings) schemas.set(c.name, c.settings);
  pushMenus();
  renderSimSum();
}

async function pollStatus() {
  try {
    status = await apiFetch(`/workspace/${encodeURIComponent(PROJECT)}/status`);
  } catch (_) {
    status = { state: "UNKNOWN" };
  }
  const s = status.state || "NOT_LAUNCHED";
  const pill = $("#dvState");
  pill.className = `pill ${stateVariant(s)}`;
  pill.innerHTML = `<span class="dot ${stateVariant(s)}"></span><span>${esc(stateLabel(s))}</span>`;
  if (tab === "hmi") openHmi();
}

async function boot() {
  if (!PROJECT) {
    banner(`No project named. <a href="/">Pick one on the Projects page</a>.`, true);
    return;
  }
  try {
    await loadInfo();
  } catch (e) {
    banner(`${esc(e.message)} — <a href="/">back to projects</a>`, true);
    return;
  }
  await pollStatus();
  setInterval(pollStatus, 2000);
  let t = location.hash.slice(1);
  if (!TABS.includes(t)) { try { t = localStorage.getItem("dv_tab"); } catch (_) {} }
  showTab(t);
}
boot();
