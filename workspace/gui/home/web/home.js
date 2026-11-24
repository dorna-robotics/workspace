// The projects page — image cards over the orchestrator registry.
//
// A card opens the project's HMI (the pendant). Its foot holds the run
// controls the dashboard has (api.js wireCommand: Park and Kill are
// hold-to-activate). The name renames on double-click; the ⋯ button and
// a right-click open the card menu: Make a copy, Rename, GitHub, Delete.
// Logged in (/account/api/status — gui/account) adds New project, the
// Developer button and the developer links; logged out is the offline
// mode: run the projects that are here.
import { apiFetch, stateVariant, stateLabel, isRunning, isLaunched, isBooting, canStart,
         isStarted, isWaiting, fmtUptime, esc, connectStatusWS, deviceFaultGate,
         wireCommand, toast, getToken } from "/orchestrator/api.js";
import { renderKwargsForm, readKwargsForm, validateKwargsForm, frozenValues, recordChip } from "/orchestrator/kwargs.js";

const $ = (id) => document.getElementById(id);
const grid = $("grid");
const pendantUrl = (n) => `/orchestrator/workspace.html?name=${encodeURIComponent(n)}&pendant=1`;
const devUrl = (n) => `/dev/?project=${encodeURIComponent(n)}`;
const enc = encodeURIComponent;
window.__toast = (m, k) => toast(m, k);
window.__aiContext = { page: "home" };

let projects = [];          // [{name, label, path_to_file, port, node_url, dir, thumb, chassis, git, st}]
let loggedIn = false;
let editing = null;         // the name being renamed inline — its card is not re-rendered
const cardHtml = new Map();
const CHASSIS = { core_500: "Core 500", core_1000: "Core 1000" };

// ---- the page's own API (gui/home/handlers.py) ----
async function home(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  const tok = getToken();
  if (tok) headers["X-Orch-Token"] = tok;
  if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const res = await fetch("/home/api" + path, { ...opts, headers });
  const text = await res.text();
  let j = {};
  try { j = text ? JSON.parse(text) : {}; } catch { j = { error: text }; }
  if (!res.ok) throw new Error(j.error || `${res.status} ${res.statusText}`);
  return j;
}

// ---- account: logged in or the offline mode ----
async function refreshAccount() {
  let next = false;
  try {
    const r = await fetch("/account/api/status", { cache: "no-store" });
    if (r.ok) next = !!(await r.json()).logged_in;
  } catch { /* no account module, or offline: logged out */ }
  if (next === loggedIn && document.body.dataset.account) return;
  loggedIn = next;
  document.body.dataset.account = loggedIn ? "in" : "out";
  $("btnNew").hidden = !loggedIn;
  document.querySelectorAll("[data-dev]").forEach((el) => { el.hidden = !loggedIn; });
  cardHtml.clear();
  render();
}
window.addEventListener("account:change", refreshAccount);
setInterval(refreshAccount, 30000);
$("cardJupyter").href = `http://${location.hostname}:8888/doc/workspaces/auto`;

// ---- data ----
async function loadProjects() {
  const j = await home("/projects");
  const prev = new Map(projects.map((p) => [p.name, p]));
  projects = (j.projects || []).map((p) => ({ ...p, st: prev.get(p.name)?.st || { state: "unknown" },
                                             kwargs_values: prev.get(p.name)?.kwargs_values || {} }));
}

async function refreshStatuses() {
  try {
    const j = await apiFetch("/workspaces/status");
    applyStatuses(j.statuses || {});
  } catch (e) {
    projects.forEach((p) => { p.st = { state: "OFFLINE", last_error: String(e) }; });
    render();
  }
}

let reloading = false;
async function applyStatuses(statuses) {
  const have = projects.map((p) => p.name).sort().join("\n");
  const want = Object.keys(statuses).sort().join("\n");
  if (have !== want && !reloading) {
    reloading = true;
    try { await loadProjects(); } catch {} finally { reloading = false; }
  }
  projects.forEach((p) => { p.st = statuses[p.name] || { state: "OFFLINE" }; });
  render();
}

// ---- icons ----
const I = {
  git: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="8" r="2.5"/><line x1="6" y1="8.5" x2="6" y2="15.5"/><path d="M18 10.5c0 4-6 3-11 5.5"/></svg>`,
  dev: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>`,
  more: `<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>`,
  copy: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>`,
  rename: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/></svg>`,
  trash: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2"/></svg>`,
  // The bench, for a project with no picture yet: rail, arm, plates.
  bench: `<svg viewBox="0 0 160 100" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="8" y="78" width="144" height="10" rx="3"/><rect x="20" y="70" width="120" height="8" rx="2"/><rect x="62" y="60" width="22" height="10" rx="2"/><path d="M73 60V42l22-14 18 10"/><circle cx="73" cy="42" r="4"/><circle cx="95" cy="28" r="4"/><path d="M113 38v8"/><path d="M108 46h10"/><rect x="18" y="52" width="26" height="18" rx="2"/><rect x="118" y="56" width="22" height="14" rx="2"/></svg>`,
};

function thumbHtml(p) {
  const st = p.st || {};
  const state = st.state || "unknown";
  const v = stateVariant(state);
  const pill = `<span class="pill ${v}"><span class="dot ${v}${isWaiting(state) ? " pulse" : ""}"></span>${esc(stateLabel(state).replace(/_/g, " "))}</span>`;
  const ph = `<div class="pc-ph">${I.bench}${p.chassis ? `<span class="pc-ph-chip">${esc(CHASSIS[p.chassis] || p.chassis)}</span>` : ""}</div>`;
  const img = p.thumb
    ? `<img src="/home/api/projects/${enc(p.name)}/thumb?v=${p.thumb}" alt="" loading="lazy" data-ph="1">` : ph;
  return `<div class="pc-thumb">${img}${pill}</div>`;
}

function footHtml(p) {
  const st = p.st || {};
  const state = st.state || "unknown";
  if (p.node_url) return "";
  if (!isLaunched(state)) {
    return `<button class="btn btn-sm btn-primary action-btn" data-cmd="launch">Launch</button>
            <button class="btn btn-sm params-btn">Parameters</button>`;
  }
  if (isBooting(state)) {
    return `<span class="pc-starting">Starting…</span><div class="spacer"></div>
            <button class="btn btn-sm action-btn" data-cmd="kill">Kill</button>`;
  }
  const parking = state.toUpperCase() === "PARKING";
  const active = isRunning(state) || parking;
  return `<button class="btn btn-sm btn-primary action-btn" data-cmd="start" ${canStart(state) ? "" : "disabled"}>${isStarted(state) ? "Resume" : "Start"}</button>
          <button class="btn btn-sm action-btn" data-cmd="pause" ${active ? "" : "disabled"}>Pause</button>
          <button class="btn btn-sm btn-warn action-btn" data-cmd="park" ${!active || parking ? "disabled" : ""}>Park</button>
          <div class="spacer"></div>
          <button class="btn btn-sm btn-danger action-btn" data-cmd="kill">Kill</button>`;
}

function cardInner(p) {
  const st = p.st || {};
  const uptime = fmtUptime(st.uptime_s);
  const steps = st.step?.steps || [];
  const last = steps.length ? steps[steps.length - 1] : null;
  const stepLbl = last ? (typeof last === "string" ? last : last.label || "") : "";
  const prog = st.step?.progress;
  const sub = [p.label, p.chassis && !p.label ? CHASSIS[p.chassis] : ""].filter(Boolean).join(" · ");
  return `${thumbHtml(p)}
    <div class="pc-body">
      <div class="pc-text">
        <div class="pc-name" title="${esc(p.name)} — double-click to rename">${esc(p.name)}</div>
        <div class="pc-sub" title="${esc(p.path_to_file)}">${esc(sub || p.dir || p.node_url || "")}</div>
      </div>
      <div class="pc-tools">
        <button class="ic-btn" data-act="github" title="GitHub" aria-label="GitHub">${I.git}</button>
        <a class="ic-btn" data-act="dev" href="${devUrl(p.name)}" title="Developer" aria-label="Developer"${loggedIn ? "" : " hidden"}>${I.dev}</a>
        <button class="ic-btn" data-act="more" title="More" aria-label="More" aria-haspopup="menu">${I.more}</button>
      </div>
    </div>
    <div class="pc-meta">${uptime ? `<span>Up ${esc(uptime)}</span>` : ""}${st.last_error
      ? `<span class="pc-err" title="${esc(st.last_error)}"><span class="msg-mark sm" data-level="bad" aria-hidden="true">!</span>${esc(st.last_error.slice(0, 60))}</span>` : ""}</div>
    ${stepLbl ? `<div class="pc-step" title="${esc(stepLbl)}">${esc(stepLbl)}</div>` : ""}
    ${prog != null && prog >= 0 ? `<div class="pc-progress"><div class="progress-bar"><div class="progress-bar-fill${prog >= 100 ? " done" : ""}" style="width:${Math.min(100, prog)}%"></div></div><span>${Math.min(100, Math.round(prog))}%</span></div>` : ""}
    <div class="pc-foot">${footHtml(p)}</div>`;
}

// ---- render ----
function render() {
  $("projCount").textContent = projects.length ? String(projects.length) : "";
  if (!projects.length) {
    grid.innerHTML = `<div class="home-empty"><h3>No projects yet</h3>
      <p>Import a project from GitHub, a zip or a folder on this robot${loggedIn ? ", or start a new one" : ""}.</p>
      <button class="btn btn-primary" id="emptyImport">Import a project</button></div>`;
    $("emptyImport").addEventListener("click", openImport);
    cardHtml.clear();
    return;
  }
  grid.querySelector(".home-empty")?.remove();
  projects.forEach((p, i) => {
    let el = grid.querySelector(`.proj-card[data-name="${CSS.escape(p.name)}"]`);
    if (!el) {
      el = document.createElement("div");
      el.dataset.name = p.name;
      el.tabIndex = 0;
      wireCard(el);
      cardHtml.delete(p.name);
    }
    if (grid.children[i] !== el) grid.insertBefore(el, grid.children[i] || null);
    if (editing === p.name) return;
    const v = stateVariant(p.st?.state || "");
    el.className = `proj-card${isRunning(p.st?.state) ? " is-running" : v === "bad" ? " is-error" : ""}`;
    const html = cardInner(p);
    if (cardHtml.get(p.name) === html) return;
    el.innerHTML = html;
    cardHtml.set(p.name, html);
    wireCardContent(el, p);
  });
  grid.querySelectorAll(".proj-card").forEach((el) => {
    if (!projects.find((p) => p.name === el.dataset.name)) { cardHtml.delete(el.dataset.name); el.remove(); }
  });
}

const proj = (el) => projects.find((p) => p.name === el.dataset.name);

function wireCard(el) {
  let nameClick = null;
  el.addEventListener("click", (e) => {
    if (e.defaultPrevented || e.button !== 0) return;
    if (e.target.closest("a, button, input, select, textarea, label, .pc-foot")) return;
    if (String(window.getSelection ? window.getSelection() : "")) return;
    const go = () => { const p = proj(el); if (!p) return;
      if (e.ctrlKey || e.metaKey) window.open(pendantUrl(p.name), "_blank"); else location.href = pendantUrl(p.name); };
    // A click on the name waits a beat: a double-click there renames.
    if (e.target.closest(".pc-name")) { clearTimeout(nameClick); nameClick = setTimeout(go, 280); return; }
    go();
  });
  el.addEventListener("dblclick", (e) => {
    if (!e.target.closest(".pc-name")) return;
    clearTimeout(nameClick);
    e.preventDefault();
    startRename(el);
  });
  el.addEventListener("contextmenu", (e) => {
    if (e.target.closest("input, textarea")) return;
    e.preventDefault();
    openMenu(el, { x: e.clientX, y: e.clientY });
  });
  el.addEventListener("keydown", (e) => {
    if (e.target !== el) return;
    if (e.key === "Enter") { const p = proj(el); if (p) location.href = pendantUrl(p.name); }
    if (e.key === "F2") startRename(el);
  });
}

function wireCardContent(el, p) {
  const img = el.querySelector("img[data-ph]");
  if (img) img.addEventListener("error", () => { p.thumb = 0; cardHtml.delete(p.name); render(); }, { once: true });
  el.querySelector('[data-act="github"]').addEventListener("click", () => openVersion(p.name));
  el.querySelector('[data-act="more"]').addEventListener("click", (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    openMenu(el, { x: r.right, y: r.bottom + 4, alignRight: true });
  });
  el.querySelector(".params-btn")?.addEventListener("click", () => openParams(p.name, false));
  el.querySelectorAll(".action-btn").forEach((btn) => {
    const cmd = btn.dataset.cmd;
    wireCommand(btn, cmd, async () => {
      if (cmd === "start") {
        const action = (p.st?.state || "").toUpperCase() === "PAUSED" ? "Resume" : "Start";
        if (!await deviceFaultGate(p.name, action)) return false;
      }
      const body = { cmd };
      if (cmd === "start" && p.kwargs_values && Object.keys(p.kwargs_values).length) body.kwargs = p.kwargs_values;
      await apiFetch(`/workspace/${enc(p.name)}/cmd`, { method: "POST", body: JSON.stringify(body) });
      await refreshStatuses();
    });
  });
}

// ---- card menu ----
const menu = $("cardMenu");
function closeMenu() { menu.hidden = true; menu.innerHTML = ""; }
function openMenu(el, at) {
  const p = proj(el);
  if (!p) return;
  const launched = isLaunched(p.st?.state);
  const items = [
    ["copy", I.copy, "Make a copy", () => copyProject(p.name), !!p.node_url],
    ["rename", I.rename, "Rename", () => startRename(el), !!p.node_url],
    ["github", I.git, "GitHub", () => openVersion(p.name), !!p.node_url],
    "hr",
    ["delete", I.trash, "Delete…", () => openDelete(p.name), false],
  ];
  menu.innerHTML = "";
  for (const it of items) {
    if (it === "hr") { menu.appendChild(document.createElement("hr")); continue; }
    const [key, icon, label, fn, off] = it;
    const b = document.createElement("button");
    b.setAttribute("role", "menuitem");
    if (key === "delete") b.className = "danger";
    b.innerHTML = `${icon}<span>${esc(label)}</span>`;
    b.disabled = off;
    if ((key === "rename" || key === "delete") && launched) b.title = "Kill the project first";
    b.addEventListener("click", () => { closeMenu(); fn(); });
    menu.appendChild(b);
  }
  menu.hidden = false;
  const w = menu.offsetWidth, h = menu.offsetHeight;
  let x = at.alignRight ? at.x - w : at.x, y = at.y;
  x = Math.max(8, Math.min(x, innerWidth - w - 8));
  y = Math.max(8, Math.min(y, innerHeight - h - 8));
  menu.style.left = `${x}px`; menu.style.top = `${y}px`;
  menu.querySelector("button:not(:disabled)")?.focus();
}
document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target) && !e.target.closest('[data-act="more"]')) closeMenu(); }, true);
window.addEventListener("resize", closeMenu);
window.addEventListener("scroll", closeMenu, true);

// ---- rename (inline) ----
function startRename(el) {
  const p = proj(el);
  if (!p || p.node_url) return;
  if (isLaunched(p.st?.state)) { toast(`Kill ${p.name} before you rename it.`, "warn"); return; }
  const nameEl = el.querySelector(".pc-name");
  if (!nameEl || editing) return;
  editing = p.name;
  const input = document.createElement("input");
  input.className = "input pc-rename";
  input.value = p.name;
  input.setAttribute("aria-label", "Project name");
  nameEl.replaceWith(input);
  input.focus(); input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const next = input.value.trim();
    if (save && next && next !== p.name) {
      try {
        const r = await home(`/projects/${enc(p.name)}/rename`, { method: "POST", body: JSON.stringify({ name: next }) });
        el.dataset.name = r.name;
        cardHtml.delete(p.name);
        if (r.folder_kept_why) toast(`Renamed — the folder keeps its name: ${r.folder_kept_why}.`, "info");
        editing = null;
        await loadProjects();
        await refreshStatuses();
        return;
      } catch (e) { toast(String(e.message || e), "bad"); }
    }
    editing = null;
    cardHtml.delete(p.name);
    render();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
  input.addEventListener("click", (e) => e.stopPropagation());
}

// ---- copy ----
async function copyProject(name) {
  try {
    const r = await home(`/projects/${enc(name)}/copy`, { method: "POST", body: "{}" });
    toast(`Copied to ${r.added[0].name}`, "ok");
    await loadProjects(); await refreshStatuses();
  } catch (e) { toast(String(e.message || e), "bad"); }
}

// ---- modals ----
function showModal(id) { $(id).classList.add("show"); }
function hideModal(id) { $(id).classList.remove("show"); }
document.querySelectorAll(".modal-overlay").forEach((m) => {
  m.addEventListener("click", (e) => { if (e.target === m || e.target.closest("[data-close]")) m.classList.remove("show"); });
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!menu.hidden) { closeMenu(); return; }
  const open = [...document.querySelectorAll(".modal-overlay.show")].pop();
  if (open) open.classList.remove("show");
});
function showErr(id, msg) { const el = $(id); el.textContent = msg || ""; el.hidden = !msg; }
async function busy(btn, label, fn) {
  const was = btn.textContent;
  btn.disabled = true; btn.textContent = label;
  try { return await fn(); } finally { btn.disabled = false; btn.textContent = was; }
}

// Import: GitHub link / zip / a folder on the Pi
let importKind = "git";
function setImportKind(k) {
  importKind = k;
  document.querySelectorAll("#importKinds .seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.kind === k));
  document.querySelectorAll("#importModal [data-pane]").forEach((el) => { el.hidden = el.dataset.pane !== k; });
  showErr("imErr", "");
}
$("importKinds").addEventListener("click", (e) => { const b = e.target.closest(".seg-btn"); if (b) setImportKind(b.dataset.kind); });
function openImport() { setImportKind(importKind); showModal("importModal"); setTimeout(() => $(importKind === "git" ? "imUrl" : importKind === "path" ? "imPath" : "imZip").focus(), 50); }
$("btnImport").addEventListener("click", openImport);
$("imGo").addEventListener("click", () => busy($("imGo"), "Importing…", async () => {
  showErr("imErr", "");
  const name = $("imName").value.trim();
  try {
    let r;
    if (importKind === "zip") {
      const f = $("imZip").files[0];
      if (!f) throw new Error("Choose a zip file.");
      const fd = new FormData();
      fd.append("file", f);
      if (name) fd.append("name", name);
      r = await home("/import/zip", { method: "POST", body: fd });
    } else if (importKind === "git") {
      r = await home("/import/git", { method: "POST", body: JSON.stringify({ url: $("imUrl").value.trim(), name }) });
    } else {
      r = await home("/import/path", { method: "POST", body: JSON.stringify({ path: $("imPath").value.trim(), name }) });
    }
    hideModal("importModal");
    ["imUrl", "imPath", "imName", "imZip"].forEach((id) => { $(id).value = ""; });
    toast(`Imported ${r.added.map((a) => a.name).join(", ")}`, "ok");
    await loadProjects(); await refreshStatuses();
  } catch (e) { showErr("imErr", String(e.message || e)); }
}));

// New project: Core 500 / Core 1000
let chassis = "core_500";
$("chassisPick").addEventListener("click", (e) => {
  const b = e.target.closest(".chassis-opt");
  if (!b) return;
  chassis = b.dataset.chassis;
  document.querySelectorAll(".chassis-opt").forEach((o) => o.classList.toggle("active", o === b));
  $("newName").placeholder = chassis.replace("_", "");
});
$("btnNew").addEventListener("click", () => { showErr("newErr", ""); showModal("newModal"); });
$("newGo").addEventListener("click", () => busy($("newGo"), "Creating…", async () => {
  showErr("newErr", "");
  try {
    const r = await home("/new", { method: "POST", body: JSON.stringify({ chassis, name: $("newName").value.trim() }) });
    hideModal("newModal");
    $("newName").value = "";
    toast(`Created ${r.added[0].name}`, "ok");
    await loadProjects(); await refreshStatuses();
  } catch (e) { showErr("newErr", String(e.message || e)); }
}));

// Delete: type the name, like GitHub
let delName = "";
function openDelete(name) {
  const p = projects.find((x) => x.name === name);
  if (!p) return;
  if (isLaunched(p.st?.state)) { toast(`Kill ${name} before you delete it.`, "warn"); return; }
  delName = name;
  $("delTitle").textContent = `Delete ${name}?`;
  $("delText").innerHTML = p.node_url
    ? `This removes the project from this page. Nothing is deleted on its node.`
    : `The project leaves this page and its folder moves to the trash on this robot (<span class="mono">~/.workspace/trash</span>).`;
  $("delLabel").innerHTML = `Type <span class="del-name">${esc(name)}</span> to confirm`;
  $("delName").value = "";
  $("delGo").disabled = true;
  showErr("delErr", "");
  showModal("delModal");
  setTimeout(() => $("delName").focus(), 50);
}
$("delName").addEventListener("input", () => { $("delGo").disabled = $("delName").value.trim() !== delName; });
$("delName").addEventListener("keydown", (e) => { if (e.key === "Enter" && !$("delGo").disabled) $("delGo").click(); });
$("delGo").addEventListener("click", () => busy($("delGo"), "Deleting…", async () => {
  try {
    const r = await home(`/projects/${enc(delName)}/delete`, { method: "POST", body: JSON.stringify({ confirm: $("delName").value.trim() }) });
    hideModal("delModal");
    toast(r.trashed_to ? `Deleted ${delName} — the folder is in the trash.` : `Removed ${delName} — the folder stays: ${r.folder_kept_why}.`, "ok");
    await loadProjects(); render();
  } catch (e) { showErr("delErr", String(e.message || e)); }
}));

// ---- GitHub: the Version panel (project_git.py via /workspace/<n>/version) ----
let verName = "";
const verApi = () => `/workspace/${enc(verName)}/version`;
const vWarn = (html, bad = false) => `<div class="ver-warn${bad ? " bad" : ""}"><span class="bang">!</span><div>${html}</div></div>`;
const vCommit = (c) => c ? `<span class="ver-hash">${esc(c.hash)}</span> ${esc(c.subject)}<div class="ver-sub">${esc(c.date)}</div>` : "—";
function vStatus(v) {
  let cls = "ok", txt = "Up to date";
  if (!v || !v.git) { cls = "bad"; txt = "No git"; }
  else if (v.reachable === false) { cls = "off"; txt = "Unknown — no access"; }
  else if (v.behind) { cls = "warn"; txt = `${v.behind} behind GitHub`; }
  else if (v.ahead || (v.changes || []).length) { cls = "warn"; txt = "Local changes"; }
  return `<span class="pill ${cls}">${esc(txt)}</span>`;
}
function vFoot(btns) {
  const f = $("verFoot");
  f.innerHTML = "";
  for (const [label, cls, fn] of btns) {
    const b = document.createElement("button");
    b.className = `btn ${cls}`.trim(); b.textContent = label;
    b.addEventListener("click", fn);
    f.appendChild(b);
  }
}
function vBusy(text) { $("verBody").innerHTML = `<div class="ver-busy">${esc(text)}</div>`; $("verFoot").querySelectorAll("button").forEach((b) => { b.disabled = true; }); }
const vClose = () => hideModal("verModal");
function vState(v) {
  $("verTitle").textContent = `GitHub — ${verName}`;
  if (!v.git) {
    $("verBody").innerHTML = vWarn(`This project is not a git repository${v.error ? `: ${esc(v.error)}` : ""}.`, true);
    vFoot([["Close", "", vClose]]);
    return;
  }
  const n = (v.changes || []).length, a = v.ahead || 0;
  const local = !n && !a ? "no local changes" : [n && `${n} uncommitted change${n === 1 ? "" : "s"}`, a && `${a} local commit${a === 1 ? "" : "s"} not on GitHub`].filter(Boolean).join(" · ");
  let html = `<dl class="ver-rows">
    <dt>Status</dt><dd>${vStatus(v)}</dd>
    <dt>Repository</dt><dd>${esc(v.url || "— no origin remote")}</dd>
    <dt>Branch</dt><dd>${esc(v.detached ? "— detached HEAD" : v.branch)}</dd>
    <dt>Running</dt><dd>${vCommit(v.head)}</dd>
    <dt>GitHub</dt><dd>${v.remote ? vCommit(v.remote) : "—"}</dd>
    <dt>This robot</dt><dd>${esc(local)}</dd>
    ${(v.shared_with || []).length ? `<dt>Same repo</dt><dd>${esc(v.shared_with.join(", "))}<div class="ver-sub">updates with this project</div></dd>` : ""}
  </dl>`;
  if (v.reachable === false) html += vWarn("This robot cannot reach the repository — no read key, or no network.", true) + `<div class="ver-err">${esc(v.fetch_error)}</div>`;
  $("verBody").innerHTML = html;
  vFoot([["Check again", "", () => loadVersion()], ["Get latest…", "btn-primary", () => vConfirm(v)]]);
}
function vConfirm(v) {
  $("verTitle").textContent = "Get latest from GitHub";
  const n = (v.changes || []).length;
  let html = `<div class="ver-sub">${esc(verName)}, branch <b>${esc(v.branch)}</b>: <span class="ver-hash">${esc(v.head && v.head.hash)}</span> → <span class="ver-hash">${esc(v.remote ? v.remote.hash : "?")}</span></div>`;
  if (v.platform) html += vWarn("This project lives inside the Workspace installation's own repository — Get latest resets the whole installation.", true);
  if (v.run_active) html += vWarn("A run is active — updating the code under it can crash the run.", true);
  else if (v.launched) html += vWarn("The project is launched — it keeps the old code until you relaunch it.");
  if (n) html += vWarn(`${n} uncommitted change${n === 1 ? "" : "s"} — set aside with <code>git stash</code>.`);
  if (v.ahead) html += vWarn(`${v.ahead} local commit${v.ahead === 1 ? "" : "s"} — kept on branch <code>backup/${esc(v.stamp)}</code>.`);
  if (v.plan_error) html += vWarn(`Cannot update: ${esc(v.plan_error)}`, true);
  else html += `<div class="ver-label">Will run, in this order</div><ul class="ver-cmds sh">${v.plan.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>`;
  $("verBody").innerHTML = html;
  const risky = v.platform || v.run_active || v.launched || n || v.ahead || v.reachable === false;
  vFoot([["Cancel", "", () => vState(v)], ...(v.plan_error ? [] : [["Get latest", risky ? "btn-warn" : "btn-primary", () => vRun(v)]])]);
}
async function vRun(v) {
  vBusy("Getting the latest from GitHub…");
  try {
    const r = await apiFetch(verApi(), { method: "POST", body: JSON.stringify({ confirmed: v.plan, stamp: v.stamp }) });
    let html = `<div class="ver-ok"><span class="tick">✓</span><div>Updated <span class="ver-hash">${esc(r.from && r.from.hash)}</span> → <span class="ver-hash">${esc(r.to && r.to.hash)}</span> <span class="ver-sub">${esc(r.to ? r.to.subject : "")}</span></div></div>`;
    if (r.launched) html += vWarn("Relaunch the project to use the new code.");
    $("verBody").innerHTML = html;
    vFoot([["Close", "btn-primary", vClose]]);
  } catch (e) {
    $("verBody").innerHTML = vWarn("Get latest failed — nothing after the failing step ran.", true) + `<div class="ver-err">${esc(String(e))}</div>`;
    vFoot([["Close", "", vClose], ["Check again", "btn-primary", () => loadVersion()]]);
  }
}
async function loadVersion() {
  vBusy("Checking with GitHub…");
  try { vState(await apiFetch(`${verApi()}?fetch=1`)); }
  catch (e) { $("verBody").innerHTML = vWarn(esc(String(e)), true); vFoot([["Close", "", vClose], ["Check again", "btn-primary", () => loadVersion()]]); }
}
function openVersion(name) { verName = name; $("verTitle").textContent = `GitHub — ${name}`; showModal("verModal"); loadVersion(); }

// ---- Parameters (as on the dashboard) ----
async function openParams(name, frozen) {
  const p = projects.find((x) => x.name === name);
  if (!p) return;
  const form = $("paramsForm"), foot = $("paramsFoot");
  let schema = {}, values = {}, record = "";
  try {
    const j = await apiFetch(`/workspace/${enc(name)}/launch_config`);
    schema = j.kwargs_schema || {};
    values = frozen ? frozenValues(j) : (j.kwargs_values || {});
    record = frozen ? recordChip(j) : "";
  } catch (e) { toast(`Parameters: ${e.message || e}`, "bad"); return; }
  $("paramsTitle").textContent = `Parameters — ${name}`;
  $("paramsRecord").innerHTML = record;
  renderKwargsForm(form, schema, values, frozen, name);
  foot.innerHTML = "";
  const btn = (label, cls, fn) => { const b = document.createElement("button"); b.className = `btn ${cls}`.trim(); b.textContent = label; b.addEventListener("click", fn); foot.appendChild(b); return b; };
  if (frozen || !Object.keys(schema).length) {
    if (!Object.keys(schema).length) form.innerHTML = `<div class="kwargs-empty">This project has no parameters.</div>`;
    btn("Close", "", () => hideModal("paramsModal"));
  } else {
    btn("Cancel", "", () => hideModal("paramsModal"));
    btn("Reset all", "", () => renderKwargsForm(form, schema, {}, false, name));
    btn("Set", "btn-primary", async (e) => {
      const errs = validateKwargsForm(form, schema);
      if (errs.length) { toast(errs[0].key ? `${errs[0].message} (${errs[0].key})` : errs[0].message, "bad"); return; }
      const vals = readKwargsForm(form);
      e.currentTarget.disabled = true;
      try {
        await apiFetch(`/workspace/${enc(name)}/kwargs`, { method: "POST", body: JSON.stringify({ kwargs_values: vals }) });
        p.kwargs_values = vals;
        hideModal("paramsModal");
      } catch (err) { toast(String(err), "bad"); }
      finally { e.currentTarget && (e.currentTarget.disabled = false); }
    });
  }
  showModal("paramsModal");
}

// ---- live status ----
let wsLive = false, pollTimer = null;
function schedulePoll() {
  clearTimeout(pollTimer);
  if (wsLive) return;
  const active = projects.some((p) => isLaunched(p.st?.state));
  pollTimer = setTimeout(async () => { await refreshStatuses(); schedulePoll(); }, active ? 2000 : 6000);
}
try {
  connectStatusWS((statuses) => { wsLive = true; clearTimeout(pollTimer); applyStatuses(statuses); });
} catch { /* polling covers it */ }

(async () => {
  await refreshAccount();
  try { await loadProjects(); } catch (e) { toast(String(e.message || e), "bad"); }
  render();
  await refreshStatuses();
  schedulePoll();
})();
