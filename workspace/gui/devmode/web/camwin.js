// The camera window — Developer mode ▸ Scene, a camera part's Settings.
// One window: how the camera is connected (vision server, type, serial /
// IP, Find cameras, simulation), its live picture (or the reason there is
// none, and the vision Pi's own GUI), the ROI of the detections it runs
// drawn over the picture, and the camera's exposure / white balance.
// The vision_server part gets the matching window: its address,
// simulation, and the cameras it sees.
//
// Every frame is a full-resolution JPEG from …/camera/<name>.jpg; the ROI
// layer is an SVG whose viewBox is the frame's own pixels, so a point on
// the scaled picture maps to full-frame pixels by the SVG itself.
import { esc } from "/orchestrator/api.js";

const FEED_MS = 500, RETRY_MS = 3000;
const SIM_TEXT = (on) => (on ? "Simulated" : "Real device");

const REASON_TITLE = {
  simulated: "Simulated", server_simulated: "Vision server simulated", no_server: "No vision server",
  no_address: "No address", no_camera: "No camera", unreachable: "Not reachable",
};

// ctx: { api(path), post(path, body), API, fieldHtml(f), saved(r) }
export async function openCameraWindow(w, name, ctx, keepFrame = null) {
  const body = w.querySelector(".dv-win-body"), foot = w.querySelector(".dv-win-foot");
  foot.hidden = true;
  w.classList.add("dv-camwin");
  let setup;
  try { setup = await ctx.api(`/camera/${encodeURIComponent(name)}/setup`); }
  catch (e) { body.innerHTML = `<div class="dv-msg bad">${esc(e.message)}</div>`; return; }
  if (w.dataset.comp !== name) return;            // another part was picked meanwhile
  w._stop?.();
  const reopen = (frame) => openCameraWindow(w, name, ctx, frame);
  if (setup.type === "vision_server") return serverWindow(w, body, name, setup, ctx, reopen);
  return cameraWindow(w, body, name, setup, ctx, reopen, keepFrame);
}

const field = (s, k) => s.fields.find(f => f.key === k);
const CONN_KEYS = ["camera_cfg.server", "camera_cfg.type", "camera_cfg.serial_number", "camera_cfg.ip", "simulation"];

function switchHtml(key, on, extra = "") {
  return `<label class="switch"><input type="checkbox" data-key="${esc(key)}" ${on ? "checked" : ""} ${extra}/>
    <span>${SIM_TEXT(on)}</span></label>`;
}

function serverRow(srv) {
  // the selected vision server's own address, edited here, written to it
  if (!srv) return "";
  return `<div class="dv-row dv-srv">
      <div class="dv-field dv-grow"><label for="cwSrvIp">Server IP address</label>
        <input class="input mono" id="cwSrvIp" value="${esc(srv.ip)}" placeholder="10.0.3.40" spellcheck="false"/></div>
      <div class="dv-field dv-port"><label for="cwSrvPort">Port</label>
        <input class="input mono" id="cwSrvPort" value="${esc(srv.port)}" inputmode="numeric" placeholder="80"/></div>
      <div class="dv-field"><label>Server</label>${switchHtml("srv.simulation", srv.simulation, 'id="cwSrvSim"')}</div>
    </div>`;
}

function devRow(d, i, pickable) {
  const name = d.name || d.camera_type || "Camera";
  const bits = [
    d.serial_number && `<span><b>serial</b> <span class="mono">${esc(d.serial_number)}</span></span>`,
    d.ip && `<span><b>IP</b> <span class="mono">${esc(d.ip)}</span></span>`,
    d.usb_type && `<span><b>USB</b> ${esc(d.usb_type)}</span>`,
    d.camera_type && `<span><b>type</b> ${esc(d.camera_type)}</span>`,
  ].filter(Boolean).join("");
  const used = d.used_by ? `<span class="dv-tag">used by ${esc(d.used_by)}</span>` : "";
  const tag = pickable ? "button" : "div";
  return `<${tag} class="dv-dev" ${pickable ? `type="button" data-dev="${i}"` : ""}>
    <span class="dv-dev-name">${esc(name)}${used}</span><span class="dv-dev-meta">${bits}</span></${tag}>`;
}

// ── the vision_server part ───────────────────────────────────────────
function serverWindow(w, body, name, s, ctx, reopen) {
  const ip = field(s, "ip"), port = field(s, "port"), sim = field(s, "simulation");
  const rest = s.fields.filter(f => !["ip", "port", "simulation"].includes(f.key));
  const v = s.vision;
  body.innerHTML = `
    <div class="muted dv-small">vision_server · the dorna_vision server the cameras are plugged into</div>
    <div class="dv-row">
      <div class="dv-field dv-grow"><label for="cwIp">IP address</label>
        <input class="input mono" id="cwIp" data-key="ip" value="${esc(ip?.value ?? "")}" placeholder="10.0.3.40" spellcheck="false"/></div>
      <div class="dv-field dv-port"><label for="cwPort">Port</label>
        <input class="input mono" id="cwPort" data-key="port" value="${esc(port?.value ?? "")}" inputmode="numeric" placeholder="80"/></div>
    </div>
    ${sim ? `<div class="dv-field"><label>Simulation</label>${switchHtml("simulation", sim.value)}</div>` : ""}
    ${rest.map(f => ctx.fieldHtml(f)).join("")}
    <div class="dv-row"><button class="btn btn-primary" data-save>Save</button><span class="dv-msg" data-msg></span></div>
    <div class="dv-sec">
      <div class="dv-grp">Cameras on this server</div>
      ${(s.cameras || []).length ? `<div class="muted dv-small">In this scene: ${s.cameras.map(c => `<b>${esc(c.name)}</b>${c.serial_number || c.ip ? ` (<span class="mono">${esc(c.serial_number || c.ip)}</span>)` : ""}`).join(", ")}</div>` : `<div class="muted dv-small">No camera in this scene names ${esc(name)} yet.</div>`}
      <div class="dv-row"><button class="btn" data-find>Find cameras</button>
        ${v.available ? `<a class="btn" href="${esc(v.url)}" target="_blank" rel="noopener" data-pi title="${esc(v.host)}:${esc(v.port)} — the vision server's own GUI">Open vision Pi ↗</a>` : ""}
        <span class="dv-msg" data-fmsg></span></div>
      <div class="dv-devs" data-devs></div>
    </div>`;
  const msg = body.querySelector("[data-msg]"), fmsg = body.querySelector("[data-fmsg]");
  body.querySelector("[data-save]").onclick = async () => {
    const values = {};
    for (const f of s.fields) {
      const el = body.querySelector(`[data-key="${f.key}"]`);
      if (el) values[f.key] = f.kind === "bool" ? el.checked : el.value;
    }
    setMsg(msg, "Saving…");
    try {
      const r = await ctx.post("/params", { component: name, values });
      ctx.saved(r);
      setMsg(msg, !r.changed.length ? "No changes." : `Saved to ${files(r)}.` + (r.relaunch ? " Relaunch to apply." : ""), "ok");
      if (r.changed.length) setTimeout(() => reopen(), 900);
    } catch (e) { setMsg(msg, e.message, "bad"); }
  };
  body.querySelector("[data-find]").onclick = () => findCameras(body, name, ctx, fmsg, {
    ip: body.querySelector("#cwIp").value, port: body.querySelector("#cwPort").value }, null);
}

// ── a camera part ────────────────────────────────────────────────────
function cameraWindow(w, body, name, s, ctx, reopen, keepFrame) {
  const fServer = field(s, "camera_cfg.server"), fType = field(s, "camera_cfg.type");
  const fSerial = field(s, "camera_cfg.serial_number"), fIp = field(s, "camera_cfg.ip"), fSim = field(s, "simulation");
  const rest = s.fields.filter(f => !CONN_KEYS.includes(f.key));
  const netTypes = fIp?.types || [];
  const curType = fType?.value || fType?.default || "";
  const servers = s.servers || [];
  const curSrv = fServer?.value || "";
  const dets = s.detections || [];
  const mine = dets.filter(d => d.mine), other = dets.filter(d => !d.mine);
  const detOpt = (d) => `<option value="${esc(d.name)}">${esc(d.name)}${d.exists ? "" : " (no file yet)"}</option>`;
  const typeOpts = (fType?.options || []).slice();
  if (curType && !typeOpts.includes(curType)) typeOpts.unshift(curType);

  body.innerHTML = `
    <div class="muted dv-small">${esc(s.type)}</div>
    <div class="dv-cam-wrap">
      <div class="dv-cam-view empty" data-view>
        <img data-img alt="${esc(name)}" hidden/>
        <svg class="dv-roi" data-roi hidden xmlns="http://www.w3.org/2000/svg"></svg>
        <div class="dv-cam-why" data-why></div>
      </div>
      <div class="dv-row dv-cam-bar" data-bar ${s.vision.available || s.feed.live ? "" : "hidden"}>
        <span class="dv-cam-st" data-st></span><span class="spacer"></span>
        <button class="btn btn-sm" data-freeze hidden title="Hold the picture still while you draw">Pause</button>
        ${s.vision.available ? `<a class="btn btn-sm" href="${esc(s.vision.url)}" target="_blank" rel="noopener" data-pi
            title="${esc(s.vision.host)}:${esc(s.vision.port)} — the vision server's own GUI${s.vision.server_simulated ? " (simulated in this scene)" : ""}">Open vision Pi ↗</a>` : ""}
      </div>
    </div>

    <div class="dv-sec" data-conn>
      <div class="dv-grp">Connection</div>
      <div class="dv-field"><label for="cwSrv">Vision server</label>
        ${servers.length ? `<select class="input" id="cwSrv" data-key="camera_cfg.server">
            <option value="" ${curSrv ? "" : "selected"}>— none —</option>
            ${servers.map(v => `<option value="${esc(v.name)}" ${v.name === curSrv ? "selected" : ""}>${esc(v.name)}${v.ip ? ` · ${esc(v.ip)}:${esc(v.port || 80)}` : ""}</option>`).join("")}
            ${curSrv && !servers.some(v => v.name === curSrv) ? `<option value="${esc(curSrv)}" selected>${esc(curSrv)} (not a vision_server)</option>` : ""}
          </select>`
        : `<input type="hidden" id="cwSrv" data-key="camera_cfg.server" value="${esc(curSrv)}"/>
           <div class="dv-note">This scene has no vision server. Add one with <b>New component ▸ vision_server</b>, save the scene, then pick it here.</div>`}
      </div>
      <div data-srvrow>${serverRow(servers.find(v => v.name === curSrv))}</div>
      <div class="dv-row">
        <div class="dv-field dv-grow"><label for="cwType">Camera type</label>
          <select class="input" id="cwType" data-key="camera_cfg.type">${typeOpts.map(o => `<option value="${esc(o)}" ${o === curType ? "selected" : ""}>${esc(o)}${!fType?.value && o === fType?.default ? " (the part's default)" : ""}</option>`).join("")}</select></div>
        <div class="dv-field"><label>Simulation</label>${switchHtml("simulation", fSim?.value !== false)}</div>
      </div>
      <div class="dv-row">
        <div class="dv-field dv-grow"><label for="cwSerial">Serial number</label>
          <input class="input mono" id="cwSerial" data-key="camera_cfg.serial_number" value="${esc(fSerial?.value ?? "")}" spellcheck="false" placeholder="from Find cameras"/></div>
        ${fIp ? `<div class="dv-field dv-grow" data-ipfield><label for="cwIp">Camera IP address</label>
          <input class="input mono" id="cwIp" data-key="camera_cfg.ip" value="${esc(fIp.value ?? "")}" spellcheck="false" placeholder="192.168.1.64"/></div>` : ""}
      </div>
      <div class="hint dv-small" data-addrhint></div>
      ${rest.map(f => ctx.fieldHtml(f)).join("")}
      <div class="dv-row">
        <button class="btn btn-primary" data-save>Save connection</button>
        <button class="btn" data-find title="Ask the vision server which cameras it sees">Find cameras</button>
        <span class="dv-msg" data-msg></span>
      </div>
      <div class="dv-devs" data-devs></div>
    </div>

    <div class="dv-sec" data-roisec>
      <div class="dv-grp">Region of interest</div>
      ${dets.length ? `<div class="dv-field"><label for="cwDet">Detection</label>
        <select class="input" id="cwDet">
          ${mine.length ? `<optgroup label="This camera's (recipes.j2)">${mine.map(detOpt).join("")}</optgroup>` : ""}
          ${other.length ? `<optgroup label="${mine.length ? "Other configs in vision/" : "Configs in vision/ (recipes.j2 ties none to this camera)"}">${other.map(detOpt).join("")}</optgroup>` : ""}
        </select></div>
        <div class="hint dv-small" data-roihint></div>
        <div class="dv-corners" data-corners></div>
        <div class="dv-row">
          <label class="dv-check"><input type="checkbox" data-inv/> Inverted — everything but the region</label>
          <label class="dv-check"><input type="checkbox" data-crop/> Crop — the model sees only the region</label>
        </div>
        <div class="dv-row">
          <button class="btn btn-primary" data-roisave>Save ROI</button>
          <button class="btn btn-ghost" data-roiclear title="No region: the whole frame">Whole frame</button>
          <span class="dv-msg" data-roimsg></span>
        </div>`
      : `<div class="dv-note">No detection configs in vision/ yet — the ROI is set per detection (vision/&lt;detection&gt;.yaml).</div>`}
    </div>

    <div class="dv-sec" data-adj>
      <div class="dv-grp">Camera adjustments</div>
      <div class="hint dv-small">Each button changes the live camera at once — nothing is saved to the project.</div>
      <div class="dv-row dv-end">
        <div class="dv-field dv-port"><label for="cwExp">Exposure (ms)</label>
          <input class="input mono" id="cwExp" inputmode="decimal" placeholder="8"/></div>
        <button class="btn" data-adj-exp>Set exposure</button>
        <button class="btn" data-adj-auto>Auto exposure</button>
      </div>
      <div class="dv-row dv-end">
        <span class="dv-lbl">White balance</span>
        <button class="btn" data-adj-wb="auto">Auto</button>
        <button class="btn" data-adj-wb="hold" title="Freeze the balance the camera has now">Hold</button>
        <span class="spacer"></span>
        <button class="btn btn-ghost" data-adj-read>Read settings</button>
      </div>
      <pre class="dv-result mono" data-adjout hidden></pre>
    </div>`;

  const $ = (sel) => body.querySelector(sel);
  // a camera that shows its picture: its ROI right under it; else how to connect it first
  if (s.feed.live) $("[data-conn]").before($("[data-roisec]"));
  const view = $("[data-view]"), img = $("[data-img]"), svg = $("[data-roi]"), why = $("[data-why]");
  const stEl = $("[data-st]"), freeze = $("[data-freeze]");
  const state = { alive: true, frozen: false, live: false, W: 0, H: 0, url: null, at: null,
                  det: null, roi: { corners: [], inv: 0, crop: 0 }, hadBox: false, timer: 0 };
  w._stop = () => { state.alive = false; clearTimeout(state.timer); };
  w.addEventListener("closed", () => w._stop(), { once: true });

  // ── connection ──
  const typeSel = $("#cwType"), srvSel = $("#cwSrv");
  const addrHint = () => {
    const net = netTypes.includes(typeSel.value);
    const ipf = $("[data-ipfield]");
    if (ipf) ipf.hidden = !net;
    $("[data-addrhint]").textContent = net
      ? "A PoE / GigE camera: its IP address or its serial number — either reaches it."
      : "A USB camera: its serial number. Find cameras lists the ones plugged into the vision server.";
  };
  typeSel.onchange = addrHint;
  addrHint();
  if (srvSel.tagName === "SELECT") srvSel.onchange = () => {
    $("[data-srvrow]").innerHTML = serverRow(servers.find(v => v.name === srvSel.value));
  };
  const msg = $("[data-msg]");
  $("[data-save]").onclick = async () => {
    const values = {};
    for (const f of s.fields) {
      const el = body.querySelector(`.dv-sec [data-key="${f.key}"]`);
      if (!el) continue;
      if (f.key === "camera_cfg.ip" && !netTypes.includes(typeSel.value) && !f.value) continue;   // not shown, never set
      values[f.key] = f.kind === "bool" ? el.checked : el.value;
    }
    setMsg(msg, "Saving…");
    try {
      const r = await ctx.post("/params", { component: name, values });
      ctx.saved(r);
      const out = [];
      if (r.changed.length) out.push(`${name}: ${files(r)}`);
      const srv = servers.find(v => v.name === srvSel.value);
      if (srv && $("#cwSrvIp")) {
        const sv = { ip: $("#cwSrvIp").value.trim(), port: $("#cwSrvPort").value.trim(), simulation: $("#cwSrvSim").checked };
        if (sv.ip !== String(srv.ip ?? "") || sv.port !== String(srv.port ?? "") || sv.simulation !== srv.simulation) {
          const r2 = await ctx.post("/params", { component: srv.name, values: sv });
          ctx.saved(r2);
          if (r2.changed.length) out.push(`${srv.name}: ${files(r2)}`);
        }
      }
      setMsg(msg, out.length ? `Saved — ${out.join("; ")}.` + (r.relaunch ? " Relaunch to apply." : "") : "No changes.", "ok");
      if (out.length) setTimeout(() => state.alive && reopen(state.url ? { url: state.url, W: state.W, H: state.H, at: state.at } : null), 900);
    } catch (e) { setMsg(msg, e.message, "bad"); }
  };
  let found = [];
  $("[data-find]").onclick = async () => {
    found = await findCameras(body, name, ctx, msg, {
      server: srvSel.value, ip: $("#cwSrvIp")?.value || "", port: $("#cwSrvPort")?.value || "" }, pick) || [];
  };
  function pick(d) {
    if (d.camera_type && [...typeSel.options].some(o => o.value === d.camera_type)) typeSel.value = d.camera_type;
    addrHint();
    $("#cwSerial").value = d.serial_number || "";
    if ($("#cwIp")) $("#cwIp").value = netTypes.includes(typeSel.value) ? (d.ip || "") : $("#cwIp").value;
    setMsg(msg, `Filled in ${d.name || d.camera_type || "the camera"} — Save connection to keep it.`, "ok");
  }

  // ── the picture ──
  const showWhy = (title, text) => {
    why.innerHTML = `<b>${esc(title)}</b><span>${esc(text)}</span>`;
    why.hidden = false;
  };
  const setFrame = (url, W, H) => {
    if (state.url && state.url !== url) URL.revokeObjectURL(state.url);
    state.url = url;
    img.src = url;
    img.hidden = false;
    view.classList.remove("empty");
    if (W && H && (W !== state.W || H !== state.H)) {
      state.W = W; state.H = H;
      view.style.aspectRatio = `${W} / ${H}`;
      view.style.width = `min(100%, calc(46vh * ${(W / H).toFixed(4)}))`;      // a tall frame stays on screen
      svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
      drawRoi();
    }
  };
  if (keepFrame?.url) { state.at = keepFrame.at; setFrame(keepFrame.url, keepFrame.W, keepFrame.H); }
  const still = (reason) => {
    const r = String(reason || "").trim();
    if (!state.url) return r;
    return r + (/[.!?]$/.test(r) ? " " : ". ") + `Showing the last picture${state.at ? `, from ${state.at.toLocaleTimeString()}` : ""}.`;
  };
  const tick = async () => {
    if (!state.alive) return;
    let wait = FEED_MS;
    if (!state.frozen && !document.hidden) {
      try {
        const r = await fetch(`${ctx.API}/camera/${encodeURIComponent(name)}.jpg`, { cache: "no-store" });
        if (!state.alive) return;
        if (r.ok && (r.headers.get("Content-Type") || "").startsWith("image/")) {
          const blob = await r.blob();
          const url = URL.createObjectURL(blob);
          const bmp = await createImageBitmap(blob).catch(() => null);
          state.at = new Date();
          setFrame(url, bmp?.width, bmp?.height);
          bmp?.close?.();
          why.hidden = true;
          view.classList.remove("still");
          state.live = true;
          stEl.innerHTML = `<span class="dot ok"></span>Live · ${state.W}×${state.H} px`;
          freeze.hidden = false;
          setAdj(true);
        } else {
          const j = await r.json().catch(() => ({}));
          state.live = false;
          view.classList.toggle("still", !!state.url);
          showWhy(REASON_TITLE[j.code] || "No picture", still(j.reason || j.error || `No image (${r.status})`));
          stEl.innerHTML = j.code === "unreachable" ? `<span class="dot bad"></span>Retrying…` : "";
          setAdj(false, j.reason);
          wait = j.code === "unreachable" ? RETRY_MS : 0;
        }
      } catch (e) {
        showWhy("Not reachable", still(e.message));
        wait = RETRY_MS;
      }
    }
    if (wait) state.timer = setTimeout(tick, wait);
  };
  freeze.onclick = () => {
    state.frozen = !state.frozen;
    freeze.textContent = state.frozen ? "Live" : "Pause";
    stEl.innerHTML = state.frozen ? `<span class="dot"></span>Paused · ${state.W}×${state.H} px` : stEl.innerHTML;
    if (!state.frozen) tick();
  };
  if (s.feed.live) { stEl.textContent = "Connecting…"; tick(); }
  else {
    view.classList.toggle("still", !!state.url);
    showWhy(REASON_TITLE[s.feed.code] || "No picture", still(s.feed.reason));
    setAdj(false, s.feed.reason);
  }

  // ── camera adjustments ──
  function setAdj(on, reason = "") {
    body.querySelectorAll("[data-adj] button").forEach(b => { b.disabled = !on; b.title = on ? "" : reason; });
  }
  const adjOut = $("[data-adjout]");
  const adjust = async (payload, label) => {
    adjOut.hidden = false;
    adjOut.className = "dv-result mono";
    adjOut.textContent = `${label}…`;
    try {
      const r = await ctx.post(`/camera/${encodeURIComponent(name)}/adjust`, payload);
      const lines = [...r.done.map(d => `✓ ${d.what}: ${brief(d.result)}`), ...r.errors.map(d => `✗ ${d.what}: ${d.error}`)];
      const st = r.settings || {};
      lines.push(`exposure: ${brief(st.exposure)}`, `white balance: ${brief(st.white_balance)}`);
      adjOut.textContent = lines.join("\n");
      if (r.errors.length) adjOut.classList.add("bad");
      const ms = expMs(st.exposure);
      if (ms != null && !$("#cwExp").value) $("#cwExp").value = ms;
    } catch (e) {
      adjOut.textContent = e.message;
      adjOut.classList.add("bad");
    }
  };
  $("[data-adj-exp]").onclick = () => {
    const v = $("#cwExp").value.trim();
    if (!v) { adjOut.hidden = false; adjOut.className = "dv-result mono bad"; adjOut.textContent = "Type the exposure in ms first."; return; }
    adjust({ exposure_ms: v }, `Exposure → ${v} ms`);
  };
  $("[data-adj-auto]").onclick = () => adjust({ auto_exposure: true }, "Exposure → auto");
  body.querySelectorAll("[data-adj-wb]").forEach(b => b.onclick = () => adjust({ white_balance: b.dataset.adjWb }, `White balance → ${b.dataset.adjWb}`));
  $("[data-adj-read]").onclick = () => adjust({}, "Reading");

  // ── ROI ──
  const detSel = $("#cwDet");
  if (!detSel) return;
  const corners = $("[data-corners]"), roiMsg = $("[data-roimsg]");
  const inv = $("[data-inv]"), crop = $("[data-crop]");
  const loadDet = () => {
    const d = dets.find(x => x.name === detSel.value);
    state.det = d;
    const roi = d?.roi || {};
    state.roi = { corners: (Array.isArray(roi.corners) ? roi.corners : []).map(p => [Math.round(+p[0]), Math.round(+p[1])]),
                  inv: roi.inv ? 1 : 0, crop: roi.crop ? 1 : 0 };
    state.hadBox = roi.box != null && !Array.isArray(roi.corners);
    inv.checked = !!state.roi.inv; crop.checked = !!state.roi.crop;
    $("[data-roihint]").innerHTML = (state.hadBox ? `<span class="dv-warn">This ROI is a 3D box (roi.box). Saving corners here replaces the box.</span> ` : "")
      + (d && !d.exists ? `vision/${esc(d.name)}.yaml does not exist yet — Save ROI creates it with the ROI alone. ` : "")
      + (d?.cameras.length && !d.mine ? `recipes.j2 runs it on ${esc(d.cameras.join(", "))}. ` : "")
      + "Drag on the picture for a rectangle, drag a corner to move it, or drag inside to move the region. Pixels of the full frame.";
    setMsg(roiMsg, "");
    renderCorners(); drawRoi();
  };
  detSel.onchange = loadDet;
  inv.onchange = () => { state.roi.inv = inv.checked ? 1 : 0; drawRoi(); };
  crop.onchange = () => { state.roi.crop = crop.checked ? 1 : 0; };

  function renderCorners() {
    const pts = state.roi.corners.length ? state.roi.corners : [[null, null], [null, null], [null, null], [null, null]];
    corners.innerHTML = `<div class="dv-corner-head muted dv-small">Corners — x, y in frame pixels</div>` +
      pts.map((p, i) => `<div class="dv-corner"><span class="muted dv-small">${i + 1}</span>
        <input class="input mono" inputmode="numeric" data-c="${i}" data-xy="0" value="${p[0] ?? ""}" aria-label="Corner ${i + 1} x"/>
        <input class="input mono" inputmode="numeric" data-c="${i}" data-xy="1" value="${p[1] ?? ""}" aria-label="Corner ${i + 1} y"/></div>`).join("");
  }
  corners.addEventListener("input", () => {
    const rows = [...corners.querySelectorAll(".dv-corner")].map(r => [...r.querySelectorAll("input")].map(i => i.value.trim()));
    if (rows.every(r => r.every(v => v === ""))) { state.roi.corners = []; drawRoi(); return; }
    if (rows.some(r => r.some(v => v === "" || !isFinite(+v)))) return;     // half typed: keep the drawing
    state.roi.corners = rows.map(r => r.map(v => Math.round(+v)));
    drawRoi();
  });
  const syncFields = () => {
    if (corners.querySelectorAll(".dv-corner").length !== Math.max(4, state.roi.corners.length)) return renderCorners();
    corners.querySelectorAll("input").forEach(i => { i.value = state.roi.corners[+i.dataset.c]?.[+i.dataset.xy] ?? ""; });
  };

  function drawRoi() {
    if (!state.W) { svg.toggleAttribute("hidden", true); return; }
    svg.toggleAttribute("hidden", false);
    const pts = state.roi.corners;
    const k = state.W / Math.max(1, svg.getBoundingClientRect().width || 1);     // frame px per screen px
    if (pts.length < 3) {
      svg.innerHTML = `<text x="${state.W / 2}" y="${24 * k}" text-anchor="middle" class="dv-roi-label" font-size="${13 * k}">No ROI — the whole frame</text>`;
      return;
    }
    const poly = pts.map(p => p.join(",")).join(" ");
    const outside = `M0,0H${state.W}V${state.H}H0Z M${pts.map(p => p.join(",")).join(" L")}Z`;
    const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
    const bw = Math.max(...xs) - Math.min(...xs), bh = Math.max(...ys) - Math.min(...ys);
    svg.innerHTML = `
      ${state.roi.inv ? `<polygon points="${poly}" class="dv-roi-dim"/>` : `<path d="${outside}" fill-rule="evenodd" class="dv-roi-dim"/>`}
      <polygon points="${poly}" class="dv-roi-halo" vector-effect="non-scaling-stroke"/>
      <polygon points="${poly}" class="dv-roi-line" vector-effect="non-scaling-stroke" data-move/>
      <text x="${Math.min(...xs)}" y="${Math.max(0, Math.min(...ys) - 8 * k)}" class="dv-roi-label" font-size="${13 * k}">${bw}×${bh} px</text>
      ${pts.map((p, i) => `<circle cx="${p[0]}" cy="${p[1]}" r="${7 * k}" class="dv-roi-pt"/>
        <circle cx="${p[0]}" cy="${p[1]}" r="${22 * k}" class="dv-roi-hit" data-pt="${i}"/>`).join("")}`;
  }
  new ResizeObserver(() => drawRoi()).observe(view);

  const toFrame = (e) => {
    const r = svg.getBoundingClientRect();         // the SVG is the frame's box, scaled: map back to frame pixels
    return [Math.round(clamp((e.clientX - r.left) / r.width * state.W, 0, state.W)),
            Math.round(clamp((e.clientY - r.top) / r.height * state.H, 0, state.H))];
  };
  svg.addEventListener("pointerdown", (e) => {
    if (!state.W || e.button !== 0) return;
    e.preventDefault();
    try { svg.setPointerCapture(e.pointerId); } catch (_) {}
    const p0 = toFrame(e), before = state.roi.corners.map(p => p.slice());
    const hit = e.target.closest("[data-pt]");
    const mode = hit ? "pt" : (e.target.closest("[data-move]") || (before.length >= 3 && inside(p0, before))) ? "move" : "rect";
    const i = hit ? +hit.dataset.pt : -1;
    const mv = (ev) => {
      const p = toFrame(ev);
      if (mode === "pt") state.roi.corners[i] = p;
      else if (mode === "move") {
        const dx = clamp(p[0] - p0[0], -Math.min(...before.map(q => q[0])), state.W - Math.max(...before.map(q => q[0])));
        const dy = clamp(p[1] - p0[1], -Math.min(...before.map(q => q[1])), state.H - Math.max(...before.map(q => q[1])));
        state.roi.corners = before.map(q => [q[0] + dx, q[1] + dy]);
      } else {
        const [x0, x1] = [Math.min(p0[0], p[0]), Math.max(p0[0], p[0])], [y0, y1] = [Math.min(p0[1], p[1]), Math.max(p0[1], p[1])];
        state.roi.corners = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
      }
      drawRoi(); syncFields();
    };
    const up = (ev) => {
      svg.removeEventListener("pointermove", mv); svg.removeEventListener("pointerup", up); svg.removeEventListener("pointercancel", up);
      if (mode === "rect") {
        const p = toFrame(ev);
        if (Math.abs(p[0] - p0[0]) < 4 || Math.abs(p[1] - p0[1]) < 4) state.roi.corners = before;   // a click, not a drag
      }
      drawRoi(); syncFields();
      setMsg(roiMsg, "Not saved yet.");
    };
    svg.addEventListener("pointermove", mv); svg.addEventListener("pointerup", up); svg.addEventListener("pointercancel", up);
  });

  $("[data-roiclear]").onclick = () => { state.roi.corners = []; renderCorners(); drawRoi(); setMsg(roiMsg, "Not saved yet."); };
  $("[data-roisave]").onclick = async () => {
    if (!state.det) return;
    setMsg(roiMsg, "Saving…");
    try {
      const r = await ctx.post(`/camera/${encodeURIComponent(name)}/roi`, {
        detection: state.det.name, corners: state.roi.corners, inv: !!state.roi.inv, crop: !!state.roi.crop,
        size: state.W ? [state.W, state.H] : null });
      state.det.roi = r.roi; state.det.exists = true; state.hadBox = false;
      state.roi.corners = r.roi.corners;
      syncFields(); drawRoi();
      setMsg(roiMsg, !r.changed.length ? `No change — ${r.file} already has this ROI.` :
        `${r.created ? "Created" : "Saved to"} ${r.file}.` + (r.relaunch ? " Relaunch to use it." : " Used from the next launch."), "ok");
    } catch (e) { setMsg(roiMsg, e.message, "bad"); }
  };
  loadDet();
}

async function findCameras(body, name, ctx, msg, where, pick) {
  const list = body.querySelector("[data-devs]");
  setMsg(msg, "Asking the vision server…");
  list.innerHTML = "";
  try {
    const r = await ctx.post(`/camera/${encodeURIComponent(name)}/find`, where);
    const devs = r.devices || [];
    setMsg(msg, devs.length ? `${devs.length} camera${devs.length > 1 ? "s" : ""} on ${r.host}:${r.port}` + (pick ? " — pick one." : ".")
      : `The vision server at ${r.host}:${r.port} sees no camera. Check its cable / power, and for a PoE camera its subnet.`, devs.length ? "ok" : "bad");
    list.innerHTML = devs.map((d, i) => devRow(d, i, !!pick)).join("");
    if (pick) list.querySelectorAll("[data-dev]").forEach(b => b.onclick = () => {
      list.querySelectorAll(".dv-dev").forEach(x => x.classList.toggle("on", x === b));
      pick(devs[+b.dataset.dev]);
    });
    return devs;
  } catch (e) {
    setMsg(msg, e.message, "bad");
    return null;
  }
}

function setMsg(el, text, kind = "") { el.className = "dv-msg" + (kind ? " " + kind : ""); el.textContent = text; }
const files = (r) => Object.keys(r.files || {}).map(f => f.split("/").pop()).join(", ") || "the scene";
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
function inside([x, y], pts) {
  let c = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [xi, yi] = pts[i], [xj, yj] = pts[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) c = !c;
  }
  return c;
}
function brief(v) {
  if (v == null) return "—";
  if (typeof v !== "object") return String(v);
  return Object.entries(v).map(([k, x]) => `${k} ${typeof x === "object" ? JSON.stringify(x) : x}`).join(", ");
}
function expMs(e) {
  // camera_exposure → {exposure, auto}: the camera's native ms (dorna_vision server/handlers.py)
  const ms = e && typeof e === "object" ? +e.exposure : NaN;
  return isFinite(ms) && ms > 0 ? +ms.toFixed(3) : null;
}
