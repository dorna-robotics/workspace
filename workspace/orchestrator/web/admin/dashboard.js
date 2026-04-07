import { apiFetch, stateVariant, stateLabel, isRunning, isLaunched, fmtUptime, esc, wsViewerUrl, connectStatusWS } from "./api.js";
import { renderKwargsForm, readKwargsForm, validateKwargsForm } from "./kwargs.js";

let workspaces = [];
let prevStates = {};   // name → last known state string (for transition toasts)
let firstPoll  = true;
let pollTimer  = null;

// ---- Avatar color hash (deterministic per workspace name) ----
const AVATAR_PALETTE = ["#3b82f6","#8b5cf6","#ec4899","#14b8a6","#f59e0b","#ef4444","#22c55e","#6366f1"];
function wsColor(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) & 0xffff;
  return AVATAR_PALETTE[h % AVATAR_PALETTE.length];
}

// ---- DOM refs ----
const wsGrid    = document.getElementById("wsGrid");
const wsCount   = document.getElementById("wsCount");
const toastArea = document.getElementById("toastArea");
const wsSearch  = document.getElementById("wsSearch");

document.getElementById("orchUrl").textContent = window.location.origin;

// ---- Toast ----
function toast(msg, type = "ok") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  el.addEventListener("click", () => el.remove());
  toastArea.appendChild(el);
  setTimeout(() => el.remove(), type === "bad" ? 7000 : 5000);
}

// ---- API ----
async function loadWorkspaces() {
  const j   = await apiFetch("/workspaces");
  const arr = Array.isArray(j?.workspaces) ? j.workspaces : [];
  workspaces = arr.map(s => {
    const prev = workspaces.find(w => w.name === s.name);
    return {
      name:         s.name,
      label:        s.label || "",
      port:         Number(s.port || 0),
      path_to_file: s.path_to_file || "",
      node_url:     (s.node_url || "").trim(),
      kwargs_values: prev?.kwargs_values || {},
      lastStatus:   prev?.lastStatus || { state: "unknown" },
    };
  });
}

async function refreshStatuses() {
  try {
    const j = await apiFetch("/workspaces/status");
    workspaces.forEach(ws => {
      ws.lastStatus = j.statuses?.[ws.name] || { state: "OFFLINE" };
    });
  } catch (e) {
    workspaces.forEach(ws => { ws.lastStatus = { state: "OFFLINE", last_error: String(e) }; });
  }
}

function checkStateTransitions() {
  workspaces.forEach(ws => {
    const cur  = (ws.lastStatus?.state || "").toUpperCase();
    const prev = prevStates[ws.name];
    if (prev !== undefined && prev !== cur) {
      if (cur === "RUNNING" || cur === "ACTIVE")          toast(`${ws.name} is running`, "ok");
      else if (["ERROR","FAILED","OFFLINE"].includes(cur)) toast(`${ws.name}: ${cur.toLowerCase()}`, "bad");
    }
    prevStates[ws.name] = cur;
  });
}

async function sendCmd(name, cmd, kwargs) {
  const payload = { cmd };
  if (kwargs) payload.kwargs = kwargs;
  return apiFetch(`/workspace/${encodeURIComponent(name)}/cmd`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ---- Parameters Modal ----
const paramsModal    = document.getElementById("paramsModalOverlay");
const paramsForm     = document.getElementById("paramsForm");
const paramsTitle    = document.getElementById("paramsModalTitle");
const paramsFoot     = document.getElementById("paramsModalFoot");

document.getElementById("btnParamsClose").addEventListener("click", () => paramsModal.classList.remove("show"));
paramsModal.addEventListener("click", (e) => { if (e.target === paramsModal) paramsModal.classList.remove("show"); });

async function openParamsModal(name, frozen) {
  const ws = workspaces.find(w => w.name === name);
  if (!ws) return;

  // Fetch data first, then show the modal fully built
  let schema = {}, values = {}, fetchError = false;
  try {
    const j = await apiFetch(`/workspace/${encodeURIComponent(name)}/launch_config`);
    schema = j.kwargs_schema || {};
    values = j.kwargs_values || {};
  } catch {
    fetchError = true;
  }

  paramsTitle.textContent = `Parameters — ${name}`;

  if (fetchError) {
    paramsForm.innerHTML = `<div class="kwargs-empty">Could not load parameters</div>`;
    paramsFoot.innerHTML = `<button class="btn" id="btnParamsDone">Cancel</button>`;
    paramsFoot.querySelector("#btnParamsDone").addEventListener("click", () => paramsModal.classList.remove("show"));
  } else {
    renderKwargsForm(paramsForm, schema, values, frozen, name);

    if (frozen) {
      paramsFoot.innerHTML = `<button class="btn" id="btnParamsDone">Cancel</button>`;
      paramsFoot.querySelector("#btnParamsDone").addEventListener("click", () => paramsModal.classList.remove("show"));
    } else if (Object.keys(schema).length) {
      paramsFoot.innerHTML = `
        <button class="btn" id="btnParamsCancel">Cancel</button>
        <div class="spacer"></div>
        <button class="btn" id="btnParamsReset">Reset All</button>
        <button class="btn btn-primary" id="btnParamsSet">Set</button>`;
      paramsFoot.querySelector("#btnParamsCancel").addEventListener("click", () => paramsModal.classList.remove("show"));
      paramsFoot.querySelector("#btnParamsReset").addEventListener("click", () => {
        renderKwargsForm(paramsForm, schema, {}, false, name);
        toast("Reset to defaults", "ok");
      });
      paramsFoot.querySelector("#btnParamsSet").addEventListener("click", async () => {
        const errs = validateKwargsForm(paramsForm, schema);
        if (errs.length) { toast(`Invalid: ${errs[0].message} (${errs[0].key})`, "bad"); return; }
        const vals = readKwargsForm(paramsForm);
        try {
          await apiFetch(`/workspace/${encodeURIComponent(name)}/kwargs`, {
            method: "POST", body: JSON.stringify({ kwargs_values: vals })
          });
          ws.kwargs_values = vals;
          toast("Parameters set", "ok");
          paramsModal.classList.remove("show");
        } catch (err) { toast(String(err), "bad"); }
      });
    } else {
      paramsFoot.innerHTML = `<button class="btn" id="btnParamsDone">Cancel</button>`;
      paramsFoot.querySelector("#btnParamsDone").addEventListener("click", () => paramsModal.classList.remove("show"));
    }
  }

  paramsModal.classList.add("show");
}

// ---- Stats bar ----
function updateStats() {
  const total   = workspaces.length;
  const running = workspaces.filter(w => isRunning(w.lastStatus?.state)).length;
  const errors  = workspaces.filter(w => ["ERROR","FAILED","OFFLINE","REMOTE_OFFLINE"].includes((w.lastStatus?.state||"").toUpperCase())).length;
  const offline = workspaces.filter(w => ["NOT_LAUNCHED",""].includes((w.lastStatus?.state||"").toUpperCase())).length;
  const idle    = total - running - errors - offline;

  document.getElementById("statTotal").textContent   = total;
  document.getElementById("statRunning").textContent = running;
  document.getElementById("statIdle").textContent    = Math.max(0, idle);
  document.getElementById("statErrors").textContent  = errors;
  document.getElementById("statOffline").textContent = offline;
}

// ---- Render ----
function render() {
  updateStats();

  if (!workspaces.length) {
    wsGrid.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">◫</div>
        <h3>No workspaces yet</h3>
        <p>Click <strong>Add Workspace</strong> to get started.</p>
      </div>`;
    return;
  }

  // Clear empty state if it's still in the grid
  wsGrid.querySelector(".empty-state")?.remove();

  workspaces.forEach((ws) => {
    const st      = ws.lastStatus || {};
    const state   = st.state || "unknown";
    const variant = stateVariant(state);
    const running = isRunning(state);
    const launched = isLaunched(state);
    const uptime  = fmtUptime(st.uptime_s);

    let el = wsGrid.querySelector(`.ws-card[data-name="${CSS.escape(ws.name)}"]`);
    const isNew = !el;
    if (isNew) {
      el = document.createElement("div");
      el.setAttribute("data-name", ws.name);
    }

    el.className = `ws-card${running ? " is-running" : variant === "bad" ? " is-error" : variant === "warn" ? " is-ready" : ""}`;

    el.innerHTML = `
      <div class="wc-head">
        <div class="wc-avatar" style="background:${wsColor(ws.name)}">${esc((ws.name[0]||'?').toUpperCase())}</div>
        <div class="wc-info">
          <div class="wc-name">
            ${esc(ws.name)}
            ${ws.label ? `<span class="wc-label">${esc(ws.label)}</span>` : ""}
          </div>
          <div class="wc-path mono">${esc(ws.path_to_file)}</div>
        </div>
        <span class="pill ${variant}">
          <span class="dot ${variant}${running ? " pulse" : ""}"></span>
          ${esc(stateLabel(state))}
        </span>
        <button class="btn btn-sm btn-ghost btn-icon remove-btn" title="Remove from registry" style="margin-left:4px">✕</button>
      </div>
      <div class="wc-meta">
        <span class="wc-meta-item" title="${esc(wsViewerUrl(ws))}">
          <span>URL</span>
          <strong>${esc(wsViewerUrl(ws).replace(/^https?:\/\//, ""))}</strong>
        </span>
        ${uptime ? `<span class="wc-meta-item"><span>Up</span> <strong>${esc(uptime)}</strong></span>` : ""}
        ${st.last_error ? `<span class="wc-err" title="${esc(st.last_error)}">⚠ ${esc(st.last_error.length > 60 ? st.last_error.slice(0, 60) + "…" : st.last_error)}</span>` : ""}
      </div>
      ${(() => {
        const steps = st.step?.steps;
        if (!steps || !steps.length) return "";
        const last = steps[steps.length - 1];
        const label = typeof last === "string" ? last : (last.label || "");
        const level = (typeof last === "object" && last.level) || "info";
        if (!label) return "";
        return `<div class="wc-step-msg${level === "error" ? " err" : level === "warning" ? " warn" : ""}" title="${esc(label)}">${esc(label)}</div>`;
      })()}
      ${(() => {
        const p = st.step?.progress;
        if (p == null || p < 0) return "";
        return `<div class="wc-progress"><div class="progress-bar"><div class="progress-bar-fill${p >= 100 ? " done" : ""}" style="width:${Math.min(100, Math.max(0, p))}%"></div></div><span class="wc-progress-label">${Math.min(100, Math.max(0, p))}%</span></div>`;
      })()}
      <div class="wc-footer">
        <a class="btn btn-primary btn-sm" href="workspace.html?name=${encodeURIComponent(ws.name)}">Open</a>
        <div class="spacer"></div>
        <div class="wc-actions">
          ${!launched
            ? `<button class="btn btn-sm btn-primary action-btn" data-cmd="launch">Launch</button>`
            : state.toUpperCase() === "LAUNCHED_NOT_READY"
            ? `<span class="wc-starting">Starting…</span>
               <button class="btn btn-sm btn-danger action-btn" data-cmd="kill" title="Kill process">Kill</button>`
            : `<button class="btn btn-sm btn-primary action-btn" data-cmd="start"   title="Start"   ${running  ? "disabled" : ""}><svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21"/></svg></button>
               <button class="btn btn-sm action-btn"             data-cmd="pause"   title="Pause"   ${!running ? "disabled" : ""}><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="4" x2="6" y2="20"/><line x1="18" y1="4" x2="18" y2="20"/></svg></button>
               <button class="btn btn-sm btn-danger action-btn"  data-cmd="end"    title="End">End</button>
               <button class="btn btn-sm btn-danger action-btn" data-cmd="kill" title="Kill (emergency)">Kill</button>`
          }
          <button class="btn btn-sm btn-icon kwargs-toggle-btn" title="Parameters"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></button>
        </div>
      </div>
    `;

    // Kwargs (parameters) modal button
    const kwargsToggle = el.querySelector(".kwargs-toggle-btn");
    if (kwargsToggle) {
      kwargsToggle.addEventListener("click", async (e) => {
        e.preventDefault();
        await openParamsModal(ws.name, launched);
      });
    }

    // Action buttons (launch / kill / restart)
    el.querySelectorAll(".action-btn").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.preventDefault();
        const cmd = btn.dataset.cmd;
        if (cmd === "kill" && !confirm(`Stop "${ws.name}"? This will kill the process.`)) return;
        btn.disabled = true;
        try {
          let kwargs = undefined;
          if (cmd === "start" && ws.kwargs_values && Object.keys(ws.kwargs_values).length) {
            kwargs = ws.kwargs_values;
          }
          await sendCmd(ws.name, cmd, kwargs);
          await refreshStatuses();
          render();
          toast(`${cmd} → ${ws.name}`, "ok");
        } catch (err) {
          toast(String(err), "bad");
          btn.disabled = false;
        }
      });
    });

    // Remove button
    el.querySelector(".remove-btn").addEventListener("click", async (e) => {
      e.preventDefault();
      if (isLaunched(ws.lastStatus?.state)) {
        toast("Kill the workspace before removing.", "warn");
        return;
      }
      if (!confirm(`Remove "${ws.name}" from registry?`)) return;
      try {
        await apiFetch("/remove_workspace", { method: "POST", body: JSON.stringify({ name: ws.name }) });
        workspaces = workspaces.filter(w => w.name !== ws.name);
        el.remove();
        render();
        toast(`Removed ${ws.name}`, "ok");
      } catch (err) { toast(String(err), "bad"); }
    });

    if (isNew) wsGrid.appendChild(el);
  });

  // Remove stale cards
  wsGrid.querySelectorAll(".ws-card[data-name]").forEach(card => {
    if (!workspaces.find(w => w.name === card.getAttribute("data-name"))) card.remove();
  });
}

// ---- Filter tags ----
let _activeFilter = "all";

function wsCategory(ws) {
  const state = (ws.lastStatus?.state || "").toUpperCase();
  if (isRunning(state)) return "running";
  if (["ERROR","FAILED","OFFLINE","REMOTE_OFFLINE"].includes(state)) return "error";
  if (["NOT_LAUNCHED",""].includes(state)) return "offline";
  return "idle";
}

document.getElementById("statsBar").addEventListener("click", (e) => {
  const item = e.target.closest(".stat-item[data-filter]");
  if (!item) return;
  _activeFilter = item.dataset.filter;
  document.querySelectorAll(".stat-item[data-filter]").forEach(s => s.classList.toggle("active", s.dataset.filter === _activeFilter));
  applyFilters();
});

// ---- Search + state filter ----
function applyFilters() {
  const q = (wsSearch?.value || "").trim().toLowerCase();
  let visible = 0;
  wsGrid.querySelectorAll(".ws-card[data-name]").forEach(card => {
    const ws = workspaces.find(w => w.name === card.getAttribute("data-name"));
    if (!ws) return;
    const matchesSearch = !q || [ws.name, ws.label, ws.path_to_file].join(" ").toLowerCase().includes(q);
    const matchesFilter = _activeFilter === "all" || wsCategory(ws) === _activeFilter;
    const show = matchesSearch && matchesFilter;
    card.style.display = show ? "" : "none";
    if (show) visible++;
  });
  const total = workspaces.length;
  wsCount.textContent = `${visible} of ${total}`;
}

wsSearch?.addEventListener("input", applyFilters);

// ---- Poll (fallback when WS is not connected) ----
async function poll() {
  await refreshStatuses();
  if (!firstPoll) checkStateTransitions();
  firstPoll = false;
  render();
  applyFilters();
}

let _wsConnected = false;

function schedulePoll() {
  if (_wsConnected) return;  // WS handles updates — no polling needed
  const hasActive = workspaces.some(w =>
    ["RUNNING","ACTIVE","LAUNCHED_NOT_READY"].includes((w.lastStatus?.state || "").toUpperCase())
  );
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => { await poll(); schedulePoll(); }, hasActive ? 2000 : 6000);
}

// ---- WebSocket live updates ----
function applyWsStatuses(statuses) {
  workspaces.forEach(ws => {
    ws.lastStatus = statuses[ws.name] || { state: "OFFLINE" };
  });
  if (!firstPoll) checkStateTransitions();
  firstPoll = false;
  render();
  applyFilters();
}

try {
  connectStatusWS((statuses) => {
    _wsConnected = true;
    clearTimeout(pollTimer);  // stop polling — WS is live
    applyWsStatuses(statuses);
  });
} catch (_) { /* WS unavailable — polling handles it */ }

// ---- Add Workspace Modal ----
const modal = document.getElementById("modalOverlay");

document.getElementById("btnAdd").addEventListener("click", () => {
  modal.classList.add("show");
  document.getElementById("f_name").focus();
});
document.getElementById("btnModalClose").addEventListener("click",  () => modal.classList.remove("show"));
document.getElementById("btnModalCancel").addEventListener("click", () => modal.classList.remove("show"));
modal.addEventListener("click", (e) => { if (e.target === modal) modal.classList.remove("show"); });

document.getElementById("btnModalConfirm").addEventListener("click", async () => {
  const name    = document.getElementById("f_name").value.trim();
  const port    = Number(document.getElementById("f_port").value);
  const label   = document.getElementById("f_label").value.trim();
  const path    = document.getElementById("f_path").value.trim();
  const nodeUrl = document.getElementById("f_nodeUrl").value.trim();

  if (!name || !path) { toast("Name and path are required.", "bad"); return; }

  const confirmBtn = document.getElementById("btnModalConfirm");
  confirmBtn.disabled = true;
  try {
    const payload = { name, port, label, path_to_file: path };
    if (nodeUrl) payload.node_url = nodeUrl;
    await apiFetch("/add_workspace", { method: "POST", body: JSON.stringify(payload) });

    modal.classList.remove("show");
    ["f_name", "f_label", "f_path", "f_nodeUrl"].forEach(id => {
      document.getElementById(id).value = "";
    });

    await loadWorkspaces();
    await poll();
    toast(`Workspace "${name}" added`, "ok");
  } catch (err) {
    toast(String(err), "bad");
  } finally { confirmBtn.disabled = false; }
});


// ---- Init ----
(async () => {
  try {
    await loadWorkspaces();
    await poll();
    schedulePoll();
  } catch (err) { toast(String(err), "bad"); }
})();
