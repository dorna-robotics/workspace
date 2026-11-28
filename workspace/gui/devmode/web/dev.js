// Developer mode — /dev/?project=<name>
// One project, a rail on the left (Scene · Workflow · HMI · Calibration).
// Scene is the embedded builder with its Settings windows (a camera's is
// camwin.js: connection, live picture, ROI, exposure).
// Workflow is the project page (with simulation switches on its devices)
// plus the AI builder's Flowchart and the builder's Recipes and Replay.
// HMI shows the operator's screen; Calibration is its own page.
import { apiFetch, esc, stateVariant, stateLabel, isLaunched, wireCommand, toast, notify, unnotify } from "/orchestrator/api.js";
import { openFlowchart, closeFlowchart, preloadFlowchart } from "/dev/flow.js";
import { openCameraWindow } from "/dev/camwin.js";

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

// ── the rail ─────────────────────────────────────────────────────────
function showTab(t) {
  if (!TABS.includes(t)) t = "scene";
  tab = t;
  $$("#dvRail button").forEach(b => { b.classList.toggle("on", b.dataset.tab === t); b.setAttribute("aria-selected", b.dataset.tab === t); });
  $("#dvMain").dataset.tab = t;
  try { localStorage.setItem("dv_tab", t); } catch (_) {}
  history.replaceState(null, "", `?project=${encodeURIComponent(PROJECT)}#${t}`);
  setAiContext();
  ({ scene: openScene, workflow: openWorkflow, hmi: openHmi, calibration: openCalibration })[t]();
  syncFrame(); placeView();
  renderRun(); showWfMsg();
}
$$("#dvRail button").forEach(b => b.onclick = () => showTab(b.dataset.tab));

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
  // a simulated device can't be switched to real until it has an address
  const wb = w.querySelector(".dv-win-body");
  new MutationObserver(() => simGate(w)).observe(wb, { childList: true, subtree: true });
  wb.addEventListener("input", () => simGate(w));
  wb.addEventListener("change", () => simGate(w));
  const n = $("#dvWins").children.length;
  w.style.right = `calc(var(--aiw-on, 0px) + ${16 + n * 24}px)`;
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
      w.style.left = `${Math.max(0, Math.min(pageRight() - 80, ev.clientX - dx))}px`;
      w.style.top = `${Math.max(0, Math.min(innerHeight - 60, ev.clientY - dy))}px`;
    };
    const up = () => { head.removeEventListener("pointermove", mv); head.removeEventListener("pointerup", up); };
    head.addEventListener("pointermove", mv);
    head.addEventListener("pointerup", up);
  });
  return w;
}

// The page's right edge: the assistant's panel, while it is open, is
// beyond it. A dragged window the panel would cover moves back in.
const pageRight = () => document.body.getBoundingClientRect().right;
addEventListener("resize", () => {
  for (const w of $("#dvWins").children) {
    if (w.style.right !== "auto") continue;
    const r = w.getBoundingClientRect(), over = r.right - (pageRight() - 8);
    if (over > 0) w.style.left = `${Math.max(0, r.left - over)}px`;
  }
});

// ── 1 · Scene ───────────────────────────────────────────────────────
// The builder is the 3D view of Scene and of Workflow's Flowchart,
// Recipes and Replay: loaded once, for whichever opens first. On Scene it
// shows its panel (Objects, controls); on Workflow its Recipes / Replay
// panel (side), or the 3D view alone (.sb-viewonly) behind the chart.
let sbReady = false, sbSel = null, thumbDone = false;
let baseSig = "";         // the scene files as the builder loaded them (Save refuses on a mismatch)
let side = null;          // "recipes" | "replay" — opened from the Workflow panel

async function openScene() {
  if (sbReady || !info) return;
  sbReady = true;
  if (info.components.some(c => c.settings?.fields.some(f => f.kind === "serial"))) loadPorts();
  await loadScene();
}

function syncFrame() {
  const w = $("#sbFrame").contentWindow;
  const doc = w && w.document;
  if (!doc || !doc.getElementById("sbTabs")) return;      // not loaded yet: its load handler calls again
  const want = tab === "workflow" ? (side || "scene") : "scene";
  if (doc.documentElement.getAttribute("data-sbtab") !== want) doc.querySelector(`#sbTabs button[data-tab="${want}"]`)?.click();
  doc.documentElement.classList.toggle("sb-viewonly", tab === "workflow" && !side);
  w.dispatchEvent(new Event("resize"));
}

// One scene for Scene and Workflow. The project page's 3D area shows the
// launched runtime's live viewer, or a "Launch" placeholder: not launched,
// the builder's own view — this same iframe, so the same objects and
// camera — lies over that area; launched, the live viewer shows and the
// camera carries across, both ways (its camera_set / camera_get messages).
// Off while the page shows anything over its 3D area (launch card, dialogs).
const PAGE_OVERLAYS = ".launch-card, .modal-overlay, .confirm-overlay, .acct-overlay, .pendant-overlay";
let overOn = false, liveOn = false;
const shown = (el) => { const cs = el.ownerDocument.defaultView.getComputedStyle(el); return cs.display !== "none" && cs.visibility !== "hidden"; };
function builderCam() {
  const t = $("#sbFrame").contentWindow?.__three;
  return t && { type: "camera_set", p: t.camera.position.toArray(), t: t.controls.target.toArray(), u: t.camera.up.toArray() };
}
window.addEventListener("message", (e) => {
  const t = $("#sbFrame").contentWindow?.__three, live = liveViewer();
  if (e.data?.type !== "camera" || !t || !live || e.source !== live.contentWindow || !Array.isArray(e.data.p)) return;
  t.camera.position.fromArray(e.data.p);
  t.camera.up.fromArray(e.data.u || [0, 0, 1]);
  t.controls.target.fromArray(e.data.t);
  t.camera.lookAt(t.controls.target);
  t.controls.update();
});
function liveViewer() {
  try { return $("#wfFrame").contentDocument?.getElementById("ws3dFrame") || null; } catch (_) { return null; }
}
function placeView() {
  const view = $("#dvView"), wf = $("#wfFrame");
  let d = null;
  try { d = wf.contentDocument; } catch (_) {}
  const area = d?.querySelector(".viewer-area"), ph = d?.getElementById("viewerPlaceholder");
  const onPage = !!(tab === "workflow" && !wv && area && ph);
  const live = onPage && ph.style.display === "none";
  const over = onPage && !live && ![...d.querySelectorAll(PAGE_OVERLAYS)].some(shown);
  if (live !== liveOn) {
    liveOn = live;
    const lf = liveViewer();
    if (live) {                     // the builder's camera to the live viewer, now and once it (re)loads
      const send = () => { const c = builderCam(); c && lf?.contentWindow?.postMessage(c, "*"); };
      send();
      lf?.addEventListener("load", () => setTimeout(send, 300), { once: true });
    } else {
      lf?.contentWindow?.postMessage({ type: "camera_get" }, "*");
    }
  }
  if (over) {
    if (!sbReady) openScene();
    const fr = wf.getBoundingClientRect(), r = area.getBoundingClientRect();
    const left = fr.left + Math.max(0, r.left), top = fr.top + Math.max(0, r.top);
    Object.assign(view.style, {
      left: `${left}px`, top: `${top}px`,
      width: `${Math.max(0, Math.min(fr.left + r.right, fr.right) - left)}px`,
      height: `${Math.max(0, Math.min(fr.top + r.bottom, fr.bottom) - top)}px`,
    });
  }
  if (over !== overOn) {
    overOn = over;
    view.classList.toggle("dv-over", over);
    if (!over) view.removeAttribute("style");
    syncFrame();
  }
}
setInterval(placeView, 250);
window.addEventListener("resize", placeView);

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
    w.document.getElementById("sbDevClose")?.addEventListener("click", () => setView(""));
    relayBuilder(w);
    pushMenus();
    syncFrame();
    // The project's picture for the Projects page, once the scene is in.
    waitForScene(w).then(ok => {
      syncFrame();                                // the import put the builder back on Scene
      setSaveReady(ok);
      if (!ok) return banner("The scene did not finish loading in the builder — Reload the page to try again.", true);
      meshesSettled(w).then((ok) => { if (ok && !thumbDone) saveThumb(true); });
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

// Which parts have a settings menu (pins, ports, simulation, a rack's
// collision box…): the builder's list shows them a settings button;
// plain parts (fixture plates) have none and open no window.
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
  $("#sbSel").textContent = !name ? "Components with settings show a settings button in the list."
    : `${name} · ${type || "component"}` + (menu ? "" : " — no settings");
  $("#sbParams").hidden = !menu;
  $("#sbEditPart").hidden = !info?.own_types.includes(type);
  if (menu) openParams(name);
  else { endColEdit(); document.getElementById("dvParams")?.querySelector("[data-close]")?.click(); }
}

$("#sbNew").onclick = () => {
  const w = $("#sbFrame").contentWindow;
  if (w && w.__sbOpenInsert) w.__sbOpenInsert();
};
$("#sbParams").onclick = () => sbSel && openParams(sbSel.name, true);
$("#sbBuild").onclick = () => buildComponent(null);
$("#sbEditPart").onclick = () => sbSel && buildComponent(sbSel.type);
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

// The component builder (compbuilder.js, loaded on first use — it brings
// three.js): a dialog over the Scene tab. A saved part is registered with
// the builder at once (set_project re-scans components/) and its catalog
// opens on it; a deleted one leaves the catalog.
async function buildComponent(type) {
  const { openComponentBuilder } = await import("/dev/compbuilder.js");
  openComponentBuilder({ api: API, type, onSaved: async (r) => {
    try {
      await fetch("/scene-builder/api/set_project", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: info.dir }),
      });
      await loadInfo();
    } catch (_) {}
    onSelect(sbSel || {});
    if (r.deleted) return banner(`Deleted the component ${esc(r.deleted)} (components/${esc(r.deleted)}/).`);
    banner(`Saved ${esc(r.type)} to components/${esc(r.type)}/ — it is in New component now.`
      + (type ? " Parts already placed show the change when the Scene builder reloads." : ""));
    $("#sbFrame").contentWindow?.__sbOpenInsert?.();
  }, inUse: (t) => Object.entries($("#sbFrame").contentWindow?.builderState?.components || {})   // saved or not
    .filter(([, m]) => m && m.type === t).map(([n]) => n) });
}

// The project's picture, the AI builder's standard view (scene builder
// __sbSnapshot): the same angle, framing and backdrop on every card.
function snapshot() {
  const w = $("#sbFrame").contentWindow;
  try { return w && w.__sbSnapshot ? w.__sbSnapshot(640, 400) : null; }
  catch (_) { return null; }                        // the 3D view is not on screen (another tab)
}

// models stream in after the import: wait until the mesh count is still
async function meshesSettled(w, ms = 180000, still = 3) {
  const t0 = Date.now();
  let last = -1, quiet = 0;
  while (Date.now() - t0 < ms) {
    const n = w.__sbMeshCount ? w.__sbMeshCount() : 0;
    quiet = n === last && n > 0 ? quiet + 1 : 0;
    last = n;
    if (quiet >= still) return true;
    await new Promise((r) => setTimeout(r, 700));
  }
  return false;
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
const camKinds = new Map();                    // name → true: a camera part not in /info, its window is camwin.js
function loadPorts() {
  fetch("/dev/api/ports").then(r => r.json()).then(j => { ports = j.ports || []; }).catch(() => {});
}

async function openParams(name, force = false) {
  const open = document.getElementById("dvParams");
  if (open && open.dataset.comp === name && !force) return;
  endColEdit();
  const w = makeWin("dvParams", name);
  w.dataset.comp = name;
  w._stop?.();                                    // a camera window's picture stops
  w._stop = null;
  w.classList.remove("dv-camwin");
  const body = w.querySelector(".dv-win-body"), foot = w.querySelector(".dv-win-foot");
  let s = schemas.get(name);
  if (s?.kind === "camera" || camKinds.get(name)) {                     // cameras + vision servers: their own window
    body.innerHTML = `<div class="muted">Loading…</div>`;
    return openCameraWindow(w, name, { api, post, API, fieldHtml: (f) => fieldHtml(f, ports), saved: paramsSaved });
  }
  if (!s) {                                       // not in /info (yet): ask
    body.innerHTML = `<div class="muted">Loading…</div>`;
    foot.hidden = true;
    try { s = await api(`/params?component=${encodeURIComponent(name)}`); }
    catch (e) { body.innerHTML = `<div class="dv-msg bad">${esc(e.message)}</div>`; return; }
    if (w.dataset.comp !== name) return;          // another click won
    if (s.kind === "camera") { camKinds.set(name, true); return openParams(name, true); }
  }
  if (!s.fields.length) {
    body.innerHTML = `<div class="muted">${esc(s.type)} has no settings — it is placed, not connected.</div>`;
    foot.hidden = true;
    return;
  }
  const io = s.fields.filter(f => f.group === "io"), rest = s.fields.filter(f => f.group !== "io");
  const nc = info?.components.find(c => c.name === name)?.no_connection;
  body.innerHTML = `<div class="muted dv-small">${esc(s.type)}</div>`
    + (nc ? `<div class="dv-msg bad" role="alert">${esc(nc.text)}</div>` : "") + rest.map(f => fieldHtml(f, ports)).join("")
    + (io.length ? `<div class="dv-grp">I/O wiring</div>` + io.map(f => fieldHtml(f, ports)).join("") : "");
  foot.hidden = false;
  foot.innerHTML = `<button class="btn btn-primary" data-save>Save</button><span class="dv-msg" data-msg></span>`;
  const msg = foot.querySelector("[data-msg]");
  const cb = body.querySelector(".dv-colbox");
  if (cb) wireColbox(cb, name, s.fields.find(f => f.kind === "colbox"), w);
  foot.querySelector("[data-save]").onclick = async () => {
    const values = {};
    for (const f of s.fields) {
      const el = body.querySelector(`[data-key="${f.key}"]`);
      values[f.key] = f.kind === "bool" ? el.checked : f.kind === "io" ? ioValue(el)
        : f.kind === "colbox" ? (el._dirty ? el._boxes : f.value) : el.value;
    }
    msg.className = "dv-msg"; msg.textContent = "Saving…";
    try {
      const r = await post("/params", { component: name, values });
      if (r.changed.includes("collision_box")) {
        // the builder takes the saved boxes now: no reload for this key
        try { $("#sbFrame").contentWindow.__sbColBoxCommit?.(name, values.collision_box); } catch (_) {}
        r.builder_keys = (r.builder_keys || []).filter(k => k !== "collision_box");
        const f = s.fields.find(x => x.kind === "colbox");
        f.value = values.collision_box;
        cb._dirty = false;
      }
      paramsSaved(r);
      msg.className = "dv-msg ok";
      msg.textContent = !r.changed.length ? "No changes." :
        `Saved to ${Object.keys(r.files).map(f => f.split("/").pop()).join(", ")}.` + (r.relaunch ? " Relaunch to apply." : "");
    } catch (e) {
      msg.className = "dv-msg bad"; msg.textContent = e.message;
    }
  };
}

// A rack's or a cap holder's collision box: the switch shows it in the
// 3D view with a handle on each face (the builder's __sbColBoxEdit);
// dragging and the size / center fields edit the same boxes.
let colEditEnd = null;
function endColEdit() { const f = colEditEnd; colEditEnd = null; f?.(); }
function colboxHtml(f, id) {
  return `<div class="dv-colbox" id="${id}" data-key="${esc(f.key)}">
    <label class="dv-switch"><input type="checkbox" data-colbox-on/><span>Off</span></label>
    <div class="dv-colbox-edit" data-colbox-boxes hidden></div></div>`;
}
function wireColbox(el, name, f, win) {
  const sb = () => $("#sbFrame").contentWindow;
  const on = el.querySelector("[data-colbox-on]"), list = el.querySelector("[data-colbox-boxes]");
  let boxes = f.value || f.default;
  if (!boxes || !Object.keys(boxes).length) {
    const m = sb()?.__sbModelBox?.(name);              // a class with no box: one around its model
    boxes = m ? { [m.solid]: [m.box] } : { body: [{ pose: [0, 0, 25, 0, 0, 0], scale: [100, 100, 50], padding_enabled: true }] };
    el._dirty = !f.value;
  }
  el._boxes = JSON.parse(JSON.stringify(boxes));
  const AX = ["X", "Y", "Z"];
  const row = (label, key, sol, bi) => `<div class="dv-colbox-row"><span class="muted dv-small">${label}</span>${AX.map((a, k) =>
    `<label class="dv-colbox-num"><span class="dv-small">${a}</span><input class="input" inputmode="decimal" data-cb="${key}" data-solid="${esc(sol)}" data-i="${bi}" data-k="${k}" aria-label="${label} ${a}"/></label>`).join("")}</div>`;
  const multi = Object.values(el._boxes).flat().length > 1;
  list.innerHTML = Object.entries(el._boxes).map(([sol, bs]) => bs.map((_, i) =>
    (multi ? `<div class="section-label">${esc(sol)} · box ${i + 1}</div>` : "")
    + row("Size (mm)", "scale", sol, i) + row("Center (mm)", "pose", sol, i)).join("")).join("")
    + `<button class="btn btn-ghost btn-sm" data-colbox-reset>Use the ${f.default && Object.keys(f.default).length ? "library's" : "model's"} box</button>`;
  const fill = () => {
    for (const inp of list.querySelectorAll("input[data-cb]")) {
      if (inp === document.activeElement) continue;
      const v = el._boxes[inp.dataset.solid]?.[+inp.dataset.i]?.[inp.dataset.cb]?.[+inp.dataset.k];
      inp.value = v == null ? "" : +(+v).toFixed(2);
    }
  };
  const changed = (b) => { el._boxes = b; el._dirty = true; fill(); };
  const show = (frame) => {
    // framed in the 3D view's part this window leaves free
    const fr = $("#sbFrame").getBoundingClientRect(), wr = win.getBoundingClientRect();
    sb()?.__sbColBoxEdit?.(name, on.checked ? el._boxes : null, changed, frame ? { from: wr.right - fr.left, to: fr.width } : null);
  };
  on.onchange = () => {
    on.nextElementSibling.textContent = on.checked ? "Shown in the 3D view — drag a face" : "Off";
    list.hidden = !on.checked;
    if (on.checked) {
      colEditEnd = () => sb()?.__sbColBoxEdit?.(null);
      fill();
      // over the builder's list, not the 3D view the box is dragged in
      // (a window the user placed stays where it is)
      if (win.style.right !== "auto") {
        win.style.right = "auto";
        win.style.left = `${Math.round($("#dvMain").getBoundingClientRect().left) + 8}px`;
      }
    }
    show(on.checked);
  };
  list.addEventListener("input", e => {
    const inp = e.target.closest("input[data-cb]");
    const v = parseFloat(inp?.value);
    if (!inp || !isFinite(v) || (inp.dataset.cb === "scale" && v <= 0)) return;
    el._boxes[inp.dataset.solid][+inp.dataset.i][inp.dataset.cb][+inp.dataset.k] = v;
    el._dirty = true;
    show();
  });
  list.addEventListener("click", e => {
    if (!e.target.closest("[data-colbox-reset]")) return;
    const m = !(f.default && Object.keys(f.default).length) && sb()?.__sbModelBox?.(name);
    changed(JSON.parse(JSON.stringify(m ? { [m.solid]: [m.box] } : f.default)));
    show();
  });
  win.addEventListener("closed", endColEdit, { once: true });
}

// After a Settings save. Connection + I/O keys: the builder never writes
// them, so its copy is still current. Rail / critical: the builder writes
// those too and would save its old values back — it must reload first
// (baseSig stays old, so a Save before that is refused with a 409).
function paramsSaved(r) {
  if (!(r.builder_keys || []).length) baseSig = r.scene_sig;
  else {
    banner(`Saved ${esc(r.builder_keys.join(", "))}. The Scene builder still shows the old value — reload it before you move anything (its unsaved changes are dropped). <button class="btn btn-sm" id="sbReload">Reload the Scene builder</button>`);
    $("#sbReload")?.addEventListener("click", () => { banner(""); loadScene(); });
  }
  if (r.changed.length) loadInfo();
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

// The Settings windows' simulation switches: while the device's address
// field (as the server's params.missing_connection reads it — ip, else a
// USB port, else a serial number; a camera's vision server and serial or
// PoE ip) is empty in the window, a simulated device stays simulated. The
// server refuses it too; this says so before Save.
function simGate(w) {
  const val = (q) => (w.querySelector(q)?.value || "").trim();
  const gate = (box, why) => {
    if (!box) return;
    const lock = box.checked && !!why, label = box.closest("label");
    if (box.disabled !== lock) box.disabled = lock;
    const tip = lock ? `Can't switch to real: ${why}` : "";
    if (label && label.title !== tip) label.title = tip;
    let hint = label?.parentElement.querySelector(":scope > [data-simgate]");
    if (lock && !hint && label) {
      hint = document.createElement("div");
      hint.className = "hint"; hint.dataset.simgate = "";
      label.after(hint);
    }
    if (hint && !lock) hint.remove();
    else if (hint && hint.textContent !== `${why} before switching to real.`) hint.textContent = `${why} before switching to real.`;
  };
  const box = w.querySelector('.dv-win-body input[type="checkbox"][data-key="simulation"]');
  if (w.querySelector('[data-key="camera_cfg.server"]')) {
    const ipEl = w.querySelector('[data-key="camera_cfg.ip"]');
    const ipOk = ipEl && !ipEl.closest("[hidden]") && ipEl.value.trim();
    gate(box, !val('[data-key="camera_cfg.server"]') ? "Pick the vision server this camera is plugged into"
      : !val('[data-key="camera_cfg.serial_number"]') && !ipOk ? `Enter its serial number${ipEl ? " or IP address" : ""}` : "");
    gate(w.querySelector('input[type="checkbox"][data-key="srv.simulation"]'), w.querySelector("#cwSrvIp") && !val("#cwSrvIp") ? "Enter the vision server's IP address" : "");
    return;
  }
  const ip = w.querySelector('[data-key="ip"]'), port = w.querySelector('input[data-key="port"][list]'), sn = w.querySelector('[data-key="serial_number"]');
  gate(box, ip ? (ip.value.trim() ? "" : "Enter its IP address") : port ? (port.value.trim() ? "" : "Choose its USB port")
    : sn ? (sn.value.trim() ? "" : "Enter its serial number") : "");
}

function fieldHtml(f, portList = []) {
  const id = `f_${f.key.replace(/\W/g, "_")}`;
  const v = f.value ?? (f.group === "io" ? f.default ?? "" : "");
  let input;
  if (f.kind === "colbox") {
    input = colboxHtml(f, id);
  } else if (f.kind === "io") {
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
    el.nextElementSibling.textContent = /(^|\.)simulation$/.test(el.dataset.key) ? (el.checked ? "Simulated" : "Real device") : (el.checked ? "On" : "Off");
  }
});

// ── 2 · Workflow ────────────────────────────────────────────────────
// The project page itself (orchestrator workspace.html, ?embed=1): run
// controls, status, steps, devices with Recover / Connect and their
// simulation switches, run parameters, operator controls, logs, its 3D
// view. Above it, the AI builder's Flowchart and the builder's Recipes
// and Replay panels, each in place of the page until Project is picked.
let wf = null;            // /workflow: the project's charts (for the flowchart)
let wv = "";              // "" (the project page) | "flow" | "recipes" | "replay"
const pageUrl = () => `/orchestrator/workspace.html?name=${encodeURIComponent(PROJECT)}`;

function loadWorkflowPage() {
  const f = $("#wfFrame");
  if (f.getAttribute("src")) return;
  f.addEventListener("load", relayWorkflow);
  f.src = `${pageUrl()}&embed=1`;
}
function openWorkflow() {
  loadWorkflowPage();
  setTimeout(preloadFlowchart, 3000);
}

async function setView(v) {
  if (v === wv) return;
  const was = wv;
  wv = v;
  if (was === "flow" && !(await closeFlowchart())) { wv = was; return; }      // an unsaved chart stays open
  side = v === "recipes" || v === "replay" ? v : null;
  if (v) { $("#dvMain").dataset.wv = v; openScene(); } else delete $("#dvMain").dataset.wv;
  $$("#wfSeg button").forEach(b => b.classList.toggle("on", b.dataset.wv === v));
  syncFrame(); placeView();
  renderRun(); showWfMsg();
  if (v === "flow") openChart();
}
$$("#wfSeg button").forEach(b => b.onclick = () => setView(b.dataset.wv));

async function openChart() {
  try { wf = await api("/workflow"); } catch (e) { banner(`Flowchart: ${esc(e.message)}`, true); }
  if (wv !== "flow") return;
  await openFlowchart({
    view: $("#flowView"), project: PROJECT, charts: wf?.charts || [],
    api: { get: (path) => api(path), post },
    onClose: () => wv === "flow" && setView(""),
  });
}

// The theme toggle is the host's: the embedded pages follow it.
addEventListener("storage", e => {
  if (e.key !== "orch_theme") return;
  for (const f of $$("iframe")) {
    try { f.contentDocument?.documentElement.setAttribute("data-theme", e.newValue || "light"); } catch (_) {}
  }
});

// ── 3 · HMI ─────────────────────────────────────────────────────────
// The operator's screen itself (the project page's pendant view): what
// Open HMI shows, before launch and while the project runs.
function openHmi() {
  const f = $("#hmiFrame");
  if (!f.src) f.src = `${pageUrl()}&pendant=1&hosted=1`;
}
$("#hmiOpen").href = `${pageUrl()}&pendant=1`;
$("#hmiReload").onclick = () => { $("#hmiFrame").src = `${pageUrl()}&pendant=1&hosted=1`; };

// ── 4 · Calibration ─────────────────────────────────────────────────
function openCalibration() {
  const f = $("#calFrame");
  if (!f.src) f.src = `/dev/calibration.html?project=${encodeURIComponent(PROJECT)}`;
}

// The calibration page's bench map: the builder's straight-down renders
// over the box it asks for — the bench (robot hidden) and the bare plates.
// Taken once the builder has imported the scene and its models stopped
// arriving — however long a big project takes on this unit (no time limit:
// a slow import is no reason for a map without its items). The page asks
// again when the count moves on (__dvMeshCount): models that came late.
const builderWin = () => $("#sbFrame").contentWindow;
window.__dvMeshCount = () => builderWin()?.__sbMeshCount?.() || 0;
window.__dvTopShot = async (b) => {
  openScene();
  for (;;) {
    const w = builderWin();
    if (w?.__sbTopShot && w.__projectBundle && w.__importBusy === false && await meshesSettled(w, 60000, 7)) {
      return { prod: w.__sbTopShot(b.x0, b.y0, b.x1, b.y1, "prod"), cal: w.__sbTopShot(b.x0, b.y0, b.x1, b.y1, "cal"),
               meshes: w.__sbMeshCount() };
    }
    await new Promise(r => setTimeout(r, 1500));
  }
};

// A part's Settings window on the Scene tab (the calibration page's rail offset).
window.__dvOpenSettings = (name) => { showTab("scene"); openParams(name, true); };

// ── the embedded pages' messages and run controls ───────────────────
// The project page (Workflow) and the builder (Scene) post their messages
// into their own top bars, hidden in here: the host's line carries them.
// What the project page asks or reports (a pause's question, an alarm,
// a device down) shows on every tab, with the way to it; the run
// controls of its sidebar are in the top bar wherever it isn't on screen.
function watchLine(line, onChange) {
  if (!line) return;
  const read = () => onChange(line.hidden ? null : {
    level: line.dataset.level || "info", urgent: line.classList.contains("urgent"),
    text: line.querySelector(".msg-first")?.textContent || "",
  });
  new MutationObserver(read).observe(line, { attributes: true, childList: true, subtree: true, characterData: true });
  read();
}

// The builder's messages: each card its stack makes (vendor/messages.js;
// stack and line are hidden in here) is posted here, then dropped there —
// an error there would otherwise stay on top of its line for good.
function relayBuilder(w) {
  const doc = w.document;
  const take = (card) => {
    const text = [card.querySelector(".msg-title")?.textContent, card.querySelector(".msg-text")?.textContent].filter(Boolean).join(" — ");
    if (text) toast(text, card.dataset.level || "info");
    card.querySelector(".msg-x")?.click();
  };
  const watch = (stack) => {
    [...stack.children].forEach(take);
    new MutationObserver((ms) => ms.forEach(m => m.addedNodes.forEach(n => n.classList?.contains("msg-card") && take(n))))
      .observe(stack, { childList: true });
  };
  const stack = doc.getElementById("msgStack");
  if (stack) watch(stack);
  else {
    const mo = new MutationObserver(() => {
      const st = doc.getElementById("msgStack");
      if (st) { mo.disconnect(); watch(st); }
    });
    mo.observe(doc.body, { childList: true });
  }
  const ls = w.document.getElementById("sbLoadStatus");       // "Importing scene n/N"
  if (ls) new MutationObserver(() => {
    const t = ls.style.display === "none" ? "" : ls.textContent.trim();
    if (t) notify({ id: "sbload", level: "info", label: "Scene", text: t }); else unnotify("sbload");
  }).observe(ls, { attributes: true, childList: true, subtree: true, characterData: true });
}

let wfMsg = null, wfKey = "";
function relayWorkflow() {
  const d = $("#wfFrame").contentDocument;
  watchLine(d?.getElementById("deskMsg"), (m) => { wfMsg = m; showWfMsg(); });
  const ctl = d?.getElementById("controls");
  if (ctl) new MutationObserver(renderRun).observe(ctl, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled"] });
  renderRun();
}
const onProjectPage = () => tab === "workflow" && !wv;
// To the project page; false when it couldn't go (an unsaved flowchart kept open).
async function goProjectPage() {
  if (tab !== "workflow") showTab("workflow");
  await setView("");
  return onProjectPage();
}
function showWfMsg() {
  const m = wfMsg && (wfMsg.urgent || wfMsg.level === "warn") ? wfMsg : null;
  $('#dvRail [data-tab="workflow"]').classList.toggle("attn", !!m && !onProjectPage());
  const k = m && !onProjectPage() ? `${m.level}|${m.text}` : "";
  if (k === wfKey) return;
  wfKey = k;
  if (!k) return unnotify("wf");
  notify({ id: "wf", level: m.level, label: "Workflow", text: m.text,
           actions: [{ label: "Open Workflow", primary: true, wire: (b) => { b.onclick = () => { unnotify("wf"); goProjectPage(); }; } }] });
}

// The strip mirrors the sidebar's buttons. A tap presses the sidebar's
// own (its gates, run parameters and sounds); Park and Kill are held here
// (a hold can't be passed on) and send the command. Start with a device
// down, and Replan, open their dialogs — on the project page.
function renderRun() {
  const box = $("#dvRun");
  const ctl = $("#wfFrame").contentDocument?.getElementById("controls");
  box.hidden = !ctl || !ctl.children.length || onProjectPage();
  if (box.hidden) return;
  const src = [...ctl.children].filter(el => el.matches("button, .ctrl-starting") && el.textContent.trim() !== "Parameters");
  const key = src.map(el => `${el.textContent}|${el.disabled}`).join(";");
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.innerHTML = "";
  for (const el of src) {
    const label = el.textContent.trim();
    if (!el.matches("button")) { box.insertAdjacentHTML("beforeend", `<span class="muted dv-small">${esc(label)}</span>`); continue; }
    const verb = label.toLowerCase();
    const b = document.createElement("button");
    b.className = el.className.split(/\s+/).filter(c => /^btn/.test(c)).join(" ");
    b.textContent = label;
    b.disabled = el.disabled;
    if (el.title && !/^Hold /.test(el.title)) b.title = el.title;
    if (verb === "park" || verb === "kill") {
      wireCommand(b, verb, async () => {
        await apiFetch(`/workspace/${encodeURIComponent(PROJECT)}/cmd`, { method: "POST", body: JSON.stringify({ cmd: verb }) });
        pollStatus();
      });
    } else {
      b.onclick = async () => {
        let there = verb === "replan";
        if (verb === "start" || verb === "resume") {
          const st = await apiFetch(`/workspace/${encodeURIComponent(PROJECT)}/status`).catch(() => ({}));
          there = !!st.devices_summary?.blocking;
        }
        if (there && !(await goProjectPage())) return;    // its dialog opens where it shows
        el.click();
      };
    }
    box.appendChild(b);
  }
}

// Tools: the bench's other programs, as the old start page linked them —
// Jupyter here; Dorna Lab at the robot's address (the scene's, else the
// bench subnet's .10); the vision server a camera names (else .40).
function setTools() {
  const h = location.hostname, net = /^(\d{1,3}\.\d{1,3}\.\d{1,3})\.\d{1,3}$/.exec(h)?.[1];
  const field = (c, k) => c?.settings?.fields.find(f => f.key === k)?.value;
  const robot = info?.components.find(c => c.kind === "robot");
  // the vision Pi a camera names (its server's address), else the bench's .40
  const servers = (info?.components || []).filter(c => c.type === "vision_server" && field(c, "ip"));
  const used = info?.components.find(c => c.camera && servers.some(v => v.name === field(c, "camera_cfg.server")));
  const srv = used ? servers.find(v => v.name === field(used, "camera_cfg.server")) : servers[0];
  const link = (id, url, why) => {
    const a = $(id);
    if (url) { a.href = url; a.removeAttribute("aria-disabled"); a.title = url; }
    else { a.removeAttribute("href"); a.setAttribute("aria-disabled", "true"); a.title = why; }
  };
  link("#toolJupyter", `http://${h}:8888/doc/workspaces/auto`);
  const lab = field(robot, "ip") || (net && `${net}.10`);
  link("#toolLab", lab && `http://${lab}/`, "Set the robot's ip in its Settings (Scene)");
  link("#toolVision", srv ? `http://${field(srv, "ip")}:${field(srv, "port") || 80}/playground` : net && `http://${net}.40/`,
       "No vision server: add a vision_server component and set its IP address (Scene)");
}
document.addEventListener("click", (e) => { if (!e.target.closest("#sbTools")) $("#sbTools").open = false; });

// ── project + status ────────────────────────────────────────────────
async function loadInfo() {
  info = await api("/info");
  $("#dvProject").textContent = info.label || info.name;
  document.title = `${info.name} · Developer mode`;
  if (info.scene_error) banner(`The scene does not load: ${esc(info.scene_error)}`, true);
  schemas.clear();
  for (const c of info.components) if (c.settings) schemas.set(c.name, c.settings);
  pushMenus();
  setTools();
  noConnection();
}

window.__dvInfoChanged = () => loadInfo();

// Real devices (simulation off) with nothing to reach them by: one card
// on the top bar, naming them, with the first one's Settings a click away.
function noConnection() {
  try { $("#wfFrame").contentWindow.__wfSimReload?.(); } catch (_) {}
  const bad = info.components.filter(c => c.no_connection);
  if (!bad.length) return unnotify("noconn");
  const one = bad[0];
  notify({ id: "noconn", level: "bad", label: "Devices",
           title: bad.length === 1 ? `${one.name} has no connection set` : `${bad.length} devices have no connection set`,
           text: bad.map(c => `${c.name} — ${c.no_connection.text}`).join(" "),
           actions: [{ label: `Open ${one.name}'s Settings`, primary: true,
                       wire: (b) => { b.onclick = () => window.__dvOpenSettings(one.name); } }] });
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
  loadWorkflowPage();
  let t = location.hash.slice(1);
  if (!TABS.includes(t)) { try { t = localStorage.getItem("dv_tab"); } catch (_) {} }
  showTab(t);
}
boot();
