// The projects page — image cards over the orchestrator registry.
//
// A card opens the project's HMI (the pendant) — the card or its Open
// button; launching and running happen there. The name renames on double-click; the ⋯ button and
// a right-click open the card menu: Make a copy, Rename, GitHub, Delete.
// Logged in (/account/api/status — gui/account) adds New project, Import
// and the Developer button; logged out is the offline mode: run the
// projects that are here.
import { apiFetch, stateVariant, stateLabel, isRunning, isLaunched, isWaiting, fmtUptime, esc,
         connectStatusWS, toast, getToken } from "/orchestrator/api.js";

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
  // Offline: run existing projects only — no new, import, copy, rename, delete.
  $("btnNew").hidden = !loggedIn;
  $("btnImport").hidden = !loggedIn;
  cardHtml.clear();
  render();
}
window.addEventListener("account:change", refreshAccount);
setInterval(refreshAccount, 30000);

// ---- data ----
// One listing in flight at a time: the status push that lands before the
// first listing (no cards yet → "the project set changed") joins it.
let projectsLoading = null, listed = false;
function loadProjects() {
  return projectsLoading || (projectsLoading = (async () => {
    try {
      const j = await home("/projects");
      const prev = new Map(projects.map((p) => [p.name, p]));
      projects = (j.projects || []).map((p) => ({ ...p, st: prev.get(p.name)?.st || { state: "unknown" } }));
    } finally { projectsLoading = null; }
  })());
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
  const pill = `<span class="pill ${v}"><span class="dot ${v}${isWaiting(state) ? " pulse" : ""}"></span>${esc(stateLabel(state))}</span>`;
  const ph = `<div class="pc-ph">${I.bench}${p.chassis ? `<span class="pc-ph-chip">${esc(CHASSIS[p.chassis] || p.chassis)}</span>` : ""}</div>`;
  const img = p.thumb
    ? `<img src="/home/api/projects/${enc(p.name)}/thumb?v=${p.thumb}" alt="" loading="lazy" data-ph="1">` : ph;
  return `<div class="pc-thumb">${img}${pill}</div>`;
}

// One button: Open — the project's HMI, where it is launched, run and parked.
function footHtml(p) {
  if (p.node_url) return "";
  return `<button class="btn btn-primary open-btn">Open</button>`;
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
    if (!listed) return;                 // not "No projects yet" before the first listing is in
    grid.innerHTML = loggedIn
      ? `<div class="home-empty"><h3>No projects yet</h3>
      <p>Start one for a Core 500 or Core 1000, or bring one in from GitHub, a zip or a folder with Import (top right).</p>
      <button class="btn btn-primary" id="emptyNew">New project</button></div>`
      : `<div class="home-empty"><h3>No projects yet</h3>
      <p>Log in (top right) to create or import a project. Without logging in, this robot runs the projects it already has.</p></div>`;
    $("emptyNew")?.addEventListener("click", openNew);
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
  el.querySelector(".open-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (e.ctrlKey || e.metaKey) window.open(pendantUrl(p.name), "_blank"); else location.href = pendantUrl(p.name);
  });
}

// ---- card menu ----
const menu = $("cardMenu");
function closeMenu() { menu.hidden = true; menu.innerHTML = ""; }
function openMenu(el, at) {
  const p = proj(el);
  if (!p) return;
  const launched = isLaunched(p.st?.state);
  const items = loggedIn ? [
    ["copy", I.copy, "Make a copy", () => copyProject(p.name), !!p.node_url],
    ["rename", I.rename, "Rename", () => startRename(el), !!p.node_url],
    ["github", I.git, "GitHub", () => openVersion(p.name), !!p.node_url],
    "hr",
    ["delete", I.trash, "Delete…", () => openDelete(p.name), false],
  ] : [
    ["github", I.git, "GitHub", () => openVersion(p.name), !!p.node_url],
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
  if (!p || p.node_url || !loggedIn) return;
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
function openNew() { showErr("newErr", ""); showModal("newModal"); }
$("btnNew").addEventListener("click", openNew);
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

// ---- GitHub: the project's repository, as on the website (gui/home/github.py) ----
// One private repository per project in Dorna's organization (<account>_<project>),
// reached with a token the account server hands out for it. Save version commits
// everything; Push sends the versions; Check looks for changes made elsewhere (the
// website, another robot); Pull merges them in — or, when both changed the same
// lines, changes nothing and names the files. A project's own origin (where it was
// imported from) keeps its Get latest.
let verName = "", gh = null, ghBusy = "", ghConflicts = null;
const ghApi = () => `/projects/${enc(verName)}/github`;
const vWarn = (html, bad = false) => `<div class="ver-warn${bad ? " bad" : ""}"><span class="bang">!</span><div>${html}</div></div>`;
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const ago = (t) => {
  const sec = Math.max(0, Date.now() / 1000 - t);
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
};
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

function ghRender() {
  const g = gh, n = (g.changes || []).length;
  $("verTitle").textContent = `GitHub — ${verName}`;
  const link = g.url ? `<a href="${esc(g.url)}" target="_blank" rel="noopener">${esc(g.url.replace("https://github.com/", ""))} ↗</a>` : "—";
  let line, btn = "";
  if (g.github_error) line = `GitHub can't be reached: ${g.github_error}`;
  else if (!g.git) line = "No versions yet — Save version starts this project's history.";
  else if (g.behind) {
    line = `GitHub has ${plural(g.behind, "newer change")}, made outside this robot.`;
    btn = `<button class="btn btn-primary" data-gh="pull">${ghBusy === "pull" ? "Pulling…" : "Pull"}</button>`;
  } else if (g.ahead || (!g.on_github && g.head)) {
    line = g.ahead ? `${plural(g.ahead, "saved version")} not on GitHub yet.` : "The last version isn't on GitHub yet.";
    btn = `<button class="btn btn-primary" data-gh="push">${ghBusy === "push" ? "Pushing…" : "Push"}</button>`;
  } else line = g.head ? "Up to date with GitHub." : "Nothing saved yet.";
  const vers = (g.versions || []).map((v) => `<li><b>${esc(v.title)}</b><span class="ver-sub">${esc(v.author)} · ${ago(v.at)} · <span class="ver-hash">${esc(v.short)}</span></span></li>`).join("");
  $("verBody").innerHTML = `
    <div class="gh-row"><span>${link}</span><button class="btn btn-ghost" data-gh="check" title="Look on GitHub for changes made outside this robot">${ghBusy === "check" ? "Checking…" : "Check"}</button></div>
    <div class="gh-row"><span>${n ? `${plural(n, "change")} since the last version — not saved as a version yet.` : g.git ? "Everything is saved as a version." : "Nothing is saved as a version yet."}</span>
      <button class="btn${btn ? "" : " btn-primary"}" data-gh="save"${n || !g.git ? "" : " disabled"}>Save version</button></div>
    <div class="gh-row"><span>${esc(line)}</span>${btn}</div>
    ${g.behind && g.ahead ? `<div class="ver-sub">Pull merges them with this project's own versions; then push the result.</div>` : ""}
    ${g.behind && g.launched ? vWarn("The project is launched — after a pull it keeps the old files until you relaunch it.") : ""}
    ${ghConflicts ? vWarn(ghConflicts.unsaved
      ? `GitHub changed ${ghConflicts.files.map((f) => `<span class="mono">${esc(f)}</span>`).join(", ")}, which also ${ghConflicts.files.length === 1 ? "has" : "have"} unsaved changes here — nothing was changed. Save a version, then pull again.`
      : `GitHub and this project changed the same lines in ${ghConflicts.files.map((f) => `<span class="mono">${esc(f)}</span>`).join(", ")} — nothing was changed. Edit one side so they agree, then pull again.`, true) : ""}
    ${vers ? `<div class="ver-label">Saved versions</div><ul class="gh-vers">${vers}</ul>` : ""}
    ${g.origin ? `<div class="gh-row gh-origin"><span class="ver-sub">Imported from ${esc(g.origin)}</span><button class="btn btn-ghost" data-gh="latest">Get latest…</button></div>` : ""}`;
  const acts = { check: () => ghAct("check", () => ghLoad(true)), save: ghSave, push: () => ghAct("push", ghPush), pull: () => ghAct("pull", ghPull), latest: originLatest };
  $("verBody").querySelectorAll("[data-gh]").forEach((b) => b.addEventListener("click", acts[b.dataset.gh]));
  vFoot([["Close", "", vClose]]);
}
async function ghAct(what, fn) {
  if (ghBusy) return;
  ghBusy = what; ghRender();
  try { await fn(); } catch (e) { toast(String(e.message || e), "bad"); } finally { ghBusy = ""; await ghLoad(false); }
}
async function ghPush() {
  await home(ghApi(), { method: "POST", body: JSON.stringify({ cmd: "push" }) });
  toast("Pushed to GitHub", "ok");
}
async function ghPull() {
  const r = await home(ghApi(), { method: "POST", body: JSON.stringify({ cmd: "pull" }) });
  ghConflicts = (r.conflicts || []).length ? { files: r.conflicts, unsaved: !!r.unsaved } : null;
  if (ghConflicts) { toast(r.unsaved ? "Not pulled: save your changes first" : "Not pulled: GitHub and this project changed the same lines", "bad"); return; }
  toast(r.pulled ? (r.merged ? "Pulled and merged with this project's versions — push the result" : "Pulled the changes from GitHub") : "Already up to date", "ok");
}
async function ghLoad(fetchIt) {
  if (fetchIt) vBusy("Checking with GitHub…");
  try { gh = await home(`${ghApi()}?fetch=${fetchIt ? 1 : 0}`); ghRender(); }
  catch (e) { $("verBody").innerHTML = vWarn(esc(String(e.message || e)), true); vFoot([["Close", "", vClose], ["Check again", "btn-primary", () => ghLoad(true)]]); }
}
async function ghSave() {
  let msg = "";
  try { msg = (await home(ghApi(), { method: "POST", body: JSON.stringify({ cmd: "draft" }) })).message; }
  catch (e) { toast(String(e.message || e), "bad"); return; }
  if (!msg) { toast("Nothing changed since the last version", ""); return; }
  $("verTitle").textContent = "Save version";
  $("verBody").innerHTML = `<label class="ver-sub" for="ghMsg">What changed — written from the project's files; edit it as you like. The first line is its title.</label>
    <textarea class="input gh-msg" id="ghMsg" rows="8" spellcheck="true">${esc(msg)}</textarea>`;
  const submit = async (push) => {
    $("verFoot").querySelectorAll("button").forEach((b) => { b.disabled = true; });
    try {
      const r = await home(ghApi(), { method: "POST", body: JSON.stringify({ cmd: "save", message: $("ghMsg").value, push }) });
      if (r.push_error) toast(`Saved version ${r.short}, but it isn't on GitHub yet: ${r.push_error}`, "bad");
      else toast(r.github ? `Saved version ${r.short} and pushed it to GitHub` : `Saved version ${r.short}`, "ok");
      ghConflicts = null;
      await ghLoad(false);
    } catch (e) { toast(String(e.message || e), "bad"); $("verFoot").querySelectorAll("button").forEach((b) => { b.disabled = false; }); }
  };
  vFoot([["Cancel", "", ghRender], ["Save only", "", () => submit(false)], ["Save & push", "btn-primary", () => submit(true)]]);
  setTimeout(() => $("ghMsg").focus(), 50);
}
function ghLoggedOut() {
  $("verTitle").textContent = `GitHub — ${verName}`;
  $("verBody").innerHTML = `<div class="ver-sub">Log in to use GitHub. Each project gets a private repository in your Dorna account — save versions, push them, and pull the changes made on the website or another robot.</div>`;
  vFoot([["Close", "", vClose], ...(window.dornaAccount ? [["Log in", "btn-primary", () => { vClose(); window.dornaAccount.login(); }]] : [])]);
}

// Get latest from the project's own origin (project_git.py via /workspace/<n>/version)
const verApi = () => `/workspace/${enc(verName)}/version`;
const vCommit = (c) => c ? `<span class="ver-hash">${esc(c.hash)}</span> ${esc(c.subject)}` : "?";
async function originLatest() {
  vBusy(`Checking ${gh.origin}…`);
  try {
    const v = await apiFetch(`${verApi()}?fetch=1`);
    if (v.reachable === false) {
      $("verBody").innerHTML = vWarn("This robot cannot reach that repository — no read key, or no network.", true) + `<div class="ver-err">${esc(v.fetch_error)}</div>`;
      vFoot([["Back", "", ghRender]]);
      return;
    }
    vConfirm(v);
  } catch (e) { $("verBody").innerHTML = vWarn(esc(String(e)), true); vFoot([["Back", "", ghRender]]); }
}
function vConfirm(v) {
  $("verTitle").textContent = "Get latest";
  const n = (v.changes || []).length;
  let html = `<div class="ver-sub">${esc(verName)}, branch <b>${esc(v.branch)}</b>: ${vCommit(v.head)} → ${vCommit(v.remote)}</div>`;
  if (!v.behind && !v.plan_error) html += `<div class="ver-sub">Already the latest from ${esc(v.url)}.</div>`;
  if (v.platform) html += vWarn("This project lives inside the Workspace installation's own repository — Get latest resets the whole installation.", true);
  if (v.run_active) html += vWarn("A run is active — updating the code under it can crash the run.", true);
  else if (v.launched) html += vWarn("The project is launched — it keeps the old code until you relaunch it.");
  if (n) html += vWarn(`${n} uncommitted change${n === 1 ? "" : "s"} — set aside with <code>git stash</code>.`);
  if (v.ahead) html += vWarn(`${v.ahead} local commit${v.ahead === 1 ? "" : "s"} — kept on branch <code>backup/${esc(v.stamp)}</code>.`);
  if (v.plan_error) html += vWarn(`Cannot update: ${esc(v.plan_error)}`, true);
  else html += `<div class="ver-label">Will run, in this order</div><ul class="ver-cmds sh">${v.plan.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>`;
  $("verBody").innerHTML = html;
  const risky = v.platform || v.run_active || v.launched || n || v.ahead;
  vFoot([["Cancel", "", ghRender], ...(v.plan_error ? [] : [["Get latest", risky ? "btn-warn" : "btn-primary", () => vRun(v)]])]);
}
async function vRun(v) {
  vBusy("Getting the latest…");
  try {
    const r = await apiFetch(verApi(), { method: "POST", body: JSON.stringify({ confirmed: v.plan, stamp: v.stamp }) });
    let html = `<div class="ver-ok"><span class="tick">✓</span><div>Updated ${vCommit(r.from)} → ${vCommit(r.to)}</div></div>`;
    if (r.launched) html += vWarn("Relaunch the project to use the new code.");
    $("verBody").innerHTML = html;
  } catch (e) {
    $("verBody").innerHTML = vWarn("Get latest failed — nothing after the failing step ran.", true) + `<div class="ver-err">${esc(String(e))}</div>`;
  }
  vFoot([["Back", "btn-primary", () => ghLoad(true)]]);
}

function openVersion(name) {
  verName = name; gh = null; ghConflicts = null;
  $("verTitle").textContent = `GitHub — ${name}`;
  showModal("verModal");
  if (loggedIn) ghLoad(true); else ghLoggedOut();
}

// ---- live status ----
let wsLive = false, pollTimer = null;
function schedulePoll() {
  clearTimeout(pollTimer);
  if (wsLive) return;
  const active = projects.some((p) => isLaunched(p.st?.state));
  pollTimer = setTimeout(async () => { await refreshStatuses(); schedulePoll(); }, active ? 2000 : 6000);
}
// The websocket pushes statuses; polling runs whenever no push came lately
// (the socket dropped, or is reconnecting).
let wsSeen = 0;
try {
  connectStatusWS((statuses) => { wsLive = true; wsSeen = Date.now(); clearTimeout(pollTimer); applyStatuses(statuses); });
} catch { /* polling covers it */ }
setInterval(() => {
  if (wsLive && Date.now() - wsSeen > 8000) { wsLive = false; schedulePoll(); }
}, 4000);

(async () => {
  // The account answers from the account server (up to seconds when it is
  // slow or unreachable): the cards don't wait for it — they show in the
  // offline mode and gain the logged-in buttons when it answers.
  refreshAccount();
  try { await loadProjects(); } catch (e) { toast(String(e.message || e), "bad"); }
  listed = true;
  render();
  await refreshStatuses();
  schedulePoll();
})();
