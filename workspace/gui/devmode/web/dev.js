// Developer mode — /dev/?project=<name>
// Four tabs over one project: Scene builder (the embedded builder, its
// Parameters + Camera windows), Workflow (run controls, simulation per
// device, the devices' operator buttons), HMI (Main + Parameters
// previews) and Calibration (calibration.html).
import { apiFetch, esc, stateVariant, stateLabel, isLaunched, canStart, isRunning } from "/orchestrator/api.js";

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

function onSelect({ name, type }) {
  sbSel = name ? { name, type } : null;
  $("#sbSel").textContent = name ? `${name} · ${type || "component"}` : "Click a component to set its parameters.";
  $("#sbParams").disabled = !name;
  $("#sbCamera").hidden = !(type || "").startsWith("inspection_");
  if (name) openParams(name);
}

$("#sbNew").onclick = () => {
  const w = $("#sbFrame").contentWindow;
  if (w && w.__sbOpenInsert) w.__sbOpenInsert();
};
$("#sbParams").onclick = () => sbSel && openParams(sbSel.name);
$("#sbCamera").onclick = () => sbSel && openCamera(sbSel.name);
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

// Parameters window
let ports = null;
async function openParams(name) {
  const w = makeWin("dvParams", name);
  const body = w.querySelector(".dv-win-body"), foot = w.querySelector(".dv-win-foot");
  body.innerHTML = `<div class="muted">Loading…</div>`;
  foot.hidden = true;
  let s;
  try { s = await api(`/params?component=${encodeURIComponent(name)}`); }
  catch (e) { body.innerHTML = `<div class="dv-msg bad">${esc(e.message)}</div>`; return; }
  if (w.querySelector("h3").textContent !== name) return;   // another click won
  if (!s.fields.length) {
    body.innerHTML = `<div class="muted">${esc(s.type)} has no device settings — it is placed, not connected.</div>`;
    return;
  }
  if (s.fields.some(f => f.kind === "serial") && !ports) {
    try { ports = (await fetch("/dev/api/ports").then(r => r.json())).ports || []; } catch (_) { ports = []; }
  }
  body.innerHTML = `<div class="muted dv-small">${esc(s.type)}</div>` + s.fields.map(f => fieldHtml(f)).join("");
  foot.hidden = false;
  foot.innerHTML = `<button class="btn btn-primary" data-save>Save</button>
    ${s.type.startsWith("inspection_") ? `<button class="btn" data-cam>Camera</button>` : ""}
    <span class="dv-msg" data-msg></span>`;
  const msg = foot.querySelector("[data-msg]");
  foot.querySelector("[data-cam]")?.addEventListener("click", () => openCamera(name));
  foot.querySelector("[data-save]").onclick = async () => {
    const values = {};
    for (const f of s.fields) {
      const el = body.querySelector(`[data-key="${f.key}"]`);
      values[f.key] = f.kind === "bool" ? el.checked : el.value;
    }
    msg.className = "dv-msg"; msg.textContent = "Saving…";
    try {
      const r = await post("/params", { component: name, values });
      // Connection keys: the builder never writes them, so its copy is
      // still current. Rail / critical: the builder writes those too and
      // would save its old values back — it must reload first (baseSig
      // stays old, so a Save before that is refused with a 409).
      if (!(r.builder_keys || []).length) baseSig = r.scene_sig;
      else {
        banner(`Saved ${esc(r.builder_keys.join(", "))}. The Scene builder still shows the old value — reload it before you move anything (its unsaved changes are dropped). <button class="btn btn-sm" id="sbReload">Reload the Scene builder</button>`);
        $("#sbReload")?.addEventListener("click", () => { banner(""); loadScene(); });
      }
      msg.className = "dv-msg ok";
      msg.textContent = !r.changed.length ? "No changes." :
        `Saved to ${Object.keys(r.files).map(f => f.split("/").pop()).join(", ")}.` + (r.relaunch ? " Relaunch to apply." : "");
      if (r.changed.includes("simulation")) loadInfo();
    } catch (e) {
      msg.className = "dv-msg bad"; msg.textContent = e.message;
    }
  };
}

function fieldHtml(f) {
  const id = `f_${f.key.replace(/\W/g, "_")}`;
  const v = f.value ?? "";
  let input;
  if (f.kind === "bool") {
    input = `<label class="dv-switch"><input type="checkbox" id="${id}" data-key="${esc(f.key)}" ${f.value ? "checked" : ""}/>
      <span>${f.key === "simulation" ? (f.value ? "Simulated" : "Real device") : (f.value ? "On" : "Off")}</span></label>`;
  } else if (f.kind === "enum") {
    const opts = (f.options || []).slice();
    if (!opts.includes(v)) opts.unshift(v);          // unset ("") or a value the list does not know
    input = `<select class="input" id="${id}" data-key="${esc(f.key)}">${opts.map(o => `<option value="${esc(o)}" ${o === v ? "selected" : ""}>${esc(o || "— none —")}</option>`).join("")}</select>`;
  } else if (f.kind === "serial") {
    input = `<input class="input mono" id="${id}" data-key="${esc(f.key)}" list="${id}_l" value="${esc(v)}" placeholder="/dev/serial/by-id/…" spellcheck="false"/>
      <datalist id="${id}_l">${(ports || []).map(p => `<option value="${esc(p.port)}">${esc(p.description)}</option>`).join("")}</datalist>`;
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
let wfOpen = false, rtWs = null, rtUrl = "", opActions = [], opComponents = [], devices = new Map(), replies = {};

function openWorkflow() {
  $("#wfOperator").href = `/orchestrator/workspace.html?name=${encodeURIComponent(PROJECT)}`;
  if (!wfOpen) { wfOpen = true; }
  renderRun();
  renderSim();
  renderDevices();
}

function renderRun() {
  const s = status.state || "NOT_LAUNCHED", launched = isLaunched(s);
  const btn = (cmd, label, cls = "", on = true) => `<button class="btn ${cls}" data-cmd="${cmd}" ${on ? "" : "disabled"}>${label}</button>`;
  $("#wfRun").innerHTML = launched
    ? btn("start", "Start", "btn-primary", canStart(s)) + btn("pause", "Pause", "", isRunning(s)) + btn("park", "Park", "", s !== "PARKING")
      + btn("relaunch", "Relaunch") + btn("kill", "Kill", "btn-danger")
    : btn("launch", "Launch", "btn-primary");
  $("#wfRunMsg").textContent = status.last_error ? `Last error: ${status.last_error}` : launched ? `State: ${stateLabel(s)}` : "Not launched. Launch builds the project with its current scene.";
}
$("#wfRun").addEventListener("click", async e => {
  const b = e.target.closest("[data-cmd]");
  if (!b) return;
  const cmd = b.dataset.cmd;
  if (cmd === "kill") {
    const ok = await window.confirmDialog({ title: "Kill the project?", message: "The program stops and the robot halts.", confirm: "Kill" });
    if (!ok) return;
  }
  b.disabled = true;
  try {
    await apiFetch(`/workspace/${encodeURIComponent(PROJECT)}/cmd`, { method: "POST", body: JSON.stringify({ cmd }) });
    $("#wfSimNote").hidden = true;
  } catch (err) {
    banner(`${esc(cmd)}: ${esc(err.message)}`, true);
  }
  pollStatus();
});

function renderSim() {
  const box = $("#wfSim");
  if (!info) return;
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
$("#wfSim").addEventListener("change", e => {
  const el = e.target.closest("[data-sim]");
  if (el) setSim([el.dataset.sim], el.checked);
});
$("#wfAllSim").onclick = () => info && setSim(info.components.filter(c => c.device).map(c => c.name), true);
$("#wfAllReal").onclick = () => info && setSim(info.components.filter(c => c.device).map(c => c.name), false);

async function setSim(names, on) {
  try {
    const r = await post("/simulation", { components: names, on });
    if (r.scene_sig) baseSig = r.scene_sig;
    if (r.relaunch) {
      const n = $("#wfSimNote");
      n.innerHTML = `<span>The running project still uses the old setting.</span><button class="btn btn-sm" data-cmd-relaunch>Relaunch now</button>`;
      n.hidden = false;
      n.querySelector("[data-cmd-relaunch]").onclick = () => $("#wfRun").querySelector('[data-cmd="relaunch"]')?.click();
    }
  } catch (e) {
    banner(`Simulation: ${esc(e.message)}`, true);
  }
  await loadInfo();
}

// The runtime's own socket: operator_actions, devices, invoke results.
function connectRuntime() {
  const launched = isLaunched(status.state) && status.state !== "LAUNCHED_NOT_READY";
  const url = launched && info ? `http://${location.hostname}:${info.port}` : "";
  if (url === rtUrl) return;
  rtUrl = url;
  if (rtWs) { try { rtWs.onclose = null; rtWs.close(); } catch (_) {} rtWs = null; }
  opActions = []; opComponents = []; devices.clear();
  if (!url) { renderDevices(); return; }
  const ws = new WebSocket(url.replace(/^http/, "ws") + "/ws");
  rtWs = ws;
  ws.onmessage = e => {
    let m; try { m = JSON.parse(e.data); } catch (_) { return; }
    const p = m.payload || {};
    if (m.type === "operator_actions") opActions = p.actions || [];
    else if (m.type === "operator_components") opComponents = p.components || [];
    else if (m.type === "devices_snapshot") { devices.clear(); for (const d of p.devices || []) if (d.id) devices.set(d.id, d); }
    else if (m.type === "device_state") { if (p.id) devices.set(p.id, p); }
    else if (m.type === "invoke_result") replies[p.component] = p;
    else return;
    if (tab === "workflow") renderDevices();
  };
  ws.onclose = () => { if (rtWs === ws) { rtWs = null; rtUrl = ""; setTimeout(connectRuntime, 2000); } };
}

function renderDevices() {
  const box = $("#wfDevices");
  if (!rtUrl) {
    $("#wfDevMsg").textContent = "";
    box.innerHTML = `<div class="dv-empty">Launch the project to use its devices' buttons.<br>Simulated devices answer too — nothing real moves.</div>`;
    return;
  }
  const running = isRunning(status.state);
  $("#wfDevMsg").textContent = running ? "Buttons are off while a run is in progress." : "";
  const comps = opComponents.length ? opComponents
    : [...new Set(opActions.map(a => a.component))].map(n => ({ name: n, label: n }));
  if (!comps.length) { box.innerHTML = `<div class="dv-empty">Connecting to the project…</div>`; return; }
  const simOf = n => info?.components.find(c => c.name === n)?.simulation;
  box.innerHTML = comps.map(c => {
    const acts = opActions.filter(a => a.component === c.name);
    const devs = (c.devices || []).map(id => devices.get(id)).filter(Boolean);
    const down = devs.find(d => d.state === "down" || d.state === "error");
    const st = devs.length ? (down ? `<span class="pill bad"><span class="dot"></span>${esc(down.state)}</span>` : `<span class="pill ok"><span class="dot"></span>ready</span>`) : "";
    const sim = simOf(c.name);
    const mode = sim == null ? "" : `<span class="pill ${sim ? "sim" : "off"}"><span class="dot ${sim ? "sim" : ""}"></span>${sim ? "SIM" : "REAL"}</span>`;
    const rep = replies[c.name];
    return `<div class="dv-dev"><div class="dv-dev-head"><b>${esc(c.label || c.name)}</b>${mode}${st}
        ${down ? `<div class="spacer"></div><button class="btn btn-sm" data-recover="${esc(down.id)}">Recover</button>` : ""}</div>
      ${down?.msg ? `<div class="dv-msg bad">${esc(down.msg)}</div>` : ""}
      <div class="dv-acts">${acts.map(a => actHtml(a, running)).join("") || `<span class="muted dv-small">No buttons.</span>`}</div>
      ${rep ? `<div class="dv-reply ${rep.ok ? "" : "bad"}">${esc(rep.method)}: ${esc(rep.ok ? (rep.text ?? "done") : (rep.msg || "failed"))}</div>` : ""}
    </div>`;
  }).join("");
}
function actHtml(a, running) {
  const ps = (a.params || []).map(p => `<input class="input dv-act-arg" data-arg="${esc(p.name)}" placeholder="${esc(p.name)}${p.unit ? ` (${esc(p.unit)})` : ""}" value="${esc(p.default ?? "")}"/>`).join("");
  return `<span class="dv-act" data-c="${esc(a.component)}" data-m="${esc(a.method)}">${ps}<button class="btn" ${running ? "disabled" : ""}>${esc(a.label || a.method)}</button></span>`;
}
$("#wfDevices").addEventListener("click", async e => {
  const rec = e.target.closest("[data-recover]");
  if (rec) {
    try { await apiFetch(`/workspace/${encodeURIComponent(PROJECT)}/devices/${encodeURIComponent(rec.dataset.recover)}/recover`, { method: "POST", body: "{}" }); }
    catch (err) { banner(`Recover: ${esc(err.message)}`, true); }
    return;
  }
  const b = e.target.closest(".dv-act button");
  if (!b || !rtWs || rtWs.readyState !== 1) return;
  const span = b.parentElement;
  const args = {};
  for (const i of span.querySelectorAll("[data-arg]")) if (i.value !== "") args[i.dataset.arg] = isNaN(+i.value) ? i.value : +i.value;
  const payload = { component: span.dataset.c, method: span.dataset.m };
  if (Object.keys(args).length) payload.args = args;
  replies[span.dataset.c] = { method: span.dataset.m, ok: true, text: "…" };
  renderDevices();
  rtWs.send(JSON.stringify({ type: "invoke", payload }));
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
  if (tab === "workflow") { renderSim(); renderDevices(); }
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
  connectRuntime();
  if (tab === "workflow") { renderRun(); renderDevices(); }
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
