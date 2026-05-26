import { apiFetch, stateVariant, stateLabel, isRunning, isLaunched, isStarted, isWaiting, fmtUptime, esc, wsViewerUrl, connectStatusWS, confirmDialog, deviceFaultGate } from "./api.js";
import { renderKwargsForm, readKwargsForm, validateKwargsForm, loadKwargsFromFile } from "./kwargs.js";

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

// ---- Render cache ----
// Per-card cached innerHTML + className. ``render()`` is hit on
// every WebSocket status tick (so several times per second during
// active runs); without this guard, every workspace card would be
// rebuilt + re-listened-up every tick, regardless of whether
// anything actually changed. Cache lets us short-circuit at the
// "no diff" case, which is the overwhelmingly common one.
const _lastCardHtml = new Map();
const _lastCardCls  = new Map();

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
document.getElementById("btnParamsLoad").addEventListener("click", () => loadKwargsFromFile(paramsForm, toast));
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
  document.getElementById("btnParamsLoad").style.display = frozen ? "none" : "";

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

// ── Device-health indicator on project cards ─────────────────────────
// Renders inline with the URL · Up meta row, label-value style ("Devices
// ●2 ok") so the eye reads it the same way it reads URL / Up. The dot
// color and the short value label both signal the same worst-case
// status — redundant on purpose, since color alone is invisible to
// color-blind operators. Worst-case ordering (used for both color and
// text):
//   blocking      → red, "N blocking"
//   recovering    → orange (pulsing), "N recovering"
//   down          → orange, "N down"
//   offline       → orange, "N offline"
//   sim only      → cyan, "N sim" (or "K ok · N sim" if mixed)
//   all ok        → green, "N ok"
//   no devices    → not rendered at all
//
// Tooltip carries the full breakdown + blocking ids verbatim so the
// operator can identify the device without leaving the dashboard.
function devicesPillHtml(summary) {
  if (!summary || !summary.total) return "";
  const blocking = summary.blocking || 0;
  const recovering = summary.recovering || 0;
  const down = summary.down || 0;
  const offline = summary.offline || 0;
  const sim = summary.sim || 0;
  const ok = summary.ok || 0;
  const total = summary.total || 0;

  // Pill content is just the total device count. Color is the only
  // status signal: gray = nothing requires attention (everything ok,
  // including authored sim — operator chose that, no need to nag),
  // orange = degraded (down / offline / recovering), red = blocking.
  // Tooltip carries the full breakdown for the operator who wants
  // to dig in. Total count stays stable so the pill identifies the
  // project the same way every time.
  let variant;
  if (blocking > 0)            variant = "bad";
  else if (recovering > 0)     variant = "warn";
  else if (down > 0 || offline > 0) variant = "warn";
  else                          variant = "off";  // ok / sim / mixed = neutral

  // Plain-prose tooltip — one line per non-zero bucket, full words.
  // This is what shows on hover; designed to read top-to-bottom.
  const lines = [];
  lines.push(`${total} device${total === 1 ? "" : "s"}`);
  lines.push("");
  if (ok)         lines.push(`${ok} ok`);
  if (sim)        lines.push(`${sim} in simulation`);
  if (recovering) lines.push(`${recovering} recovering`);
  if (down)       lines.push(`${down} down`);
  if (offline)    lines.push(`${offline} service offline`);
  if (blocking) {
    lines.push("");
    lines.push(`${blocking} blocking Start/Resume:`);
    (summary.blocking_ids || []).forEach(id => lines.push("  " + id));
  }
  const title = lines.join("\n");

  return (
    `<span class="wc-meta-item devices-meta devices-meta--${variant}" title="${esc(title)}">` +
      `<span>Devices</span>` +
      `<strong class="devices-count">${total}</strong>` +
    `</span>`
  );
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
        <svg class="empty-icon" width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <rect x="3" y="4" width="18" height="16" rx="3"/>
          <line x1="3" y1="9" x2="21" y2="9"/>
          <circle cx="7" cy="6.5" r="0.6" fill="currentColor"/>
          <circle cx="9" cy="6.5" r="0.6" fill="currentColor"/>
        </svg>
        <h3>No workspaces yet</h3>
        <p>Workspaces let you launch and monitor a Dorna project from one place.</p>
        <button class="btn btn-primary empty-cta" id="emptyAddBtn">+ Add Workspace</button>
      </div>`;
    document.getElementById("emptyAddBtn")?.addEventListener("click", () => {
      document.getElementById("btnAdd")?.click();
    });
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
    const waiting = isWaiting(state);
    const uptime  = fmtUptime(st.uptime_s);
    const devicesPill = devicesPillHtml(st.devices_summary);

    let el = wsGrid.querySelector(`.ws-card[data-name="${CSS.escape(ws.name)}"]`);
    const isNew = !el;
    if (isNew) {
      el = document.createElement("div");
      el.setAttribute("data-name", ws.name);
    }

    const newCls = `ws-card${running ? " is-running" : variant === "bad" ? " is-error" : variant === "warn" ? " is-ready" : ""}`;
    if (_lastCardCls.get(ws.name) !== newCls) {
      el.className = newCls;
      _lastCardCls.set(ws.name, newCls);
    }

    const newHtml = `
      <div class="wc-head">
        <a class="wc-head-link" href="workspace.html?name=${encodeURIComponent(ws.name)}" title="Open ${esc(ws.name)}">
          <div class="wc-avatar" style="background:${wsColor(ws.name)}">${esc((ws.name[0]||'?').toUpperCase())}</div>
          <div class="wc-info">
            <div class="wc-name">
              ${esc(ws.name)}
              ${ws.label ? `<span class="wc-label">${esc(ws.label)}</span>` : ""}
            </div>
            <div class="wc-path mono">${esc(ws.path_to_file)}</div>
          </div>
        </a>
        <span class="pill ${variant}">
          <span class="dot ${variant}${waiting ? " pulse" : ""}"></span>
          ${esc(stateLabel(state))}
        </span>
        <button class="btn btn-sm btn-ghost btn-icon remove-btn" title="Remove"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
      </div>
      <div class="wc-meta">
        <span class="wc-meta-item" title="${esc(wsViewerUrl(ws))}">
          <span>URL</span>
          <strong>${esc(wsViewerUrl(ws).replace(/^https?:\/\//, ""))}</strong>
        </span>
        ${uptime ? `<span class="wc-meta-item"><span>Up</span> <strong>${esc(uptime)}</strong></span>` : ""}
        ${devicesPill}
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
        <div class="wc-actions">
          ${!launched
            ? `<div class="spacer"></div><button class="btn btn-sm btn-primary action-btn" data-cmd="launch">Launch</button>
               <button class="btn btn-sm kwargs-toggle-btn">Parameters</button>`
            : state.toUpperCase() === "LAUNCHED_NOT_READY"
            ? `<span class="wc-starting">Starting…</span>
               <div class="spacer"></div>
               <button class="btn btn-sm action-btn" data-cmd="kill">Kill</button>`
            : (() => {
                // PARKING is a *flag* — the workflow is still draining
                // through park-cleanup, so Pause / Resume stay reachable.
                // Park itself disables once a park is in flight; Start
                // is disabled while any work (running or parking) is in
                // flight.
                const parking = state.toUpperCase() === "PARKING";
                const active  = running || parking;
                // Start slot reads "Resume" once a run has begun
                // (RUNNING / PAUSED / PARKING) — even while disabled.
                // See workspace.js / api.js for the rationale; this
                // keeps the vocabulary consistent across sidebar,
                // pendant, and card.
                const startLbl = isStarted(state) ? "Resume" : "Start";
                return `<button class="btn btn-sm btn-primary action-btn" data-cmd="start" ${active ? "disabled" : ""}>${startLbl}</button>
               <button class="btn btn-sm action-btn"             data-cmd="pause" ${!active ? "disabled" : ""}>Pause</button>
               <button class="btn btn-sm btn-warn action-btn"    data-cmd="park"  ${!active || parking ? "disabled" : ""}>Park</button>
               <div class="spacer"></div>
               <button class="btn btn-sm btn-danger action-btn"  data-cmd="kill">Kill</button>`;
              })()
          }
        </div>
      </div>
    `;

    // Short-circuit: if neither the className nor the innerHTML
    // differs from the previous render, nothing about this card has
    // changed → skip the write and the listener re-wiring. This is
    // the common case during steady-state runs.
    if (!isNew && _lastCardHtml.get(ws.name) === newHtml) {
      return; // continue forEach
    }
    el.innerHTML = newHtml;
    _lastCardHtml.set(ws.name, newHtml);

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
        if (cmd === "park" && !await confirmDialog({
          title: `Park "${ws.name}"?`,
          message: "The current action will finish, then the project's Park steps run and the workflow ends. Click Start to begin a new run.",
          confirm: "Park Workflow",
          icon: "park",
          variant: "danger",
        })) return;
        if (cmd === "kill" && !await confirmDialog({
          title: `Kill "${ws.name}"?`,
          message: "This will immediately terminate the process. Any running workflow will be aborted.",
          confirm: "Kill Process",
          icon: "kill",
        })) return;
        // Device-fault gate for Start / Resume. Fetches fresh status
        // from the workspace and prompts if any critical device is
        // still down. See deviceFaultGate in api.js for the full
        // contract. Cancel here aborts before sending the command.
        if (cmd === "start") {
          const stateUpper = (ws.lastStatus?.state || "").toUpperCase();
          const action = stateUpper === "PAUSED" ? "Resume" : "Start";
          const ok = await deviceFaultGate(ws.name, action);
          if (!ok) return;
        }
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
      if (!await confirmDialog({
        title: `Remove "${ws.name}"?`,
        message: "This will unregister the workspace from the dashboard.",
        confirm: "Remove",
        icon: "remove",
        remember: "remove_workspace",
      })) return;
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

  // Remove stale cards and their cache entries
  wsGrid.querySelectorAll(".ws-card[data-name]").forEach(card => {
    const name = card.getAttribute("data-name");
    if (!workspaces.find(w => w.name === name)) {
      card.remove();
      _lastCardHtml.delete(name);
      _lastCardCls.delete(name);
    }
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

// Load from file (YAML or JSON)
const fileInput = document.getElementById("fileInput");
document.getElementById("btnLoadFile").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", async () => {
  const file = fileInput.files[0];
  if (!file) return;
  fileInput.value = "";
  try {
    const text = await file.text();
    let data;
    if (file.name.endsWith(".json")) {
      data = JSON.parse(text);
    } else {
      // Simple YAML key: value parser (no dependency needed for flat config)
      data = {};
      for (const line of text.split("\n")) {
        const m = line.match(/^\s*([a-zA-Z_]\w*)\s*:\s*(.+?)\s*$/);
        if (m) data[m[1]] = m[2].replace(/^["']|["']$/g, "");
      }
    }
    const map = { name: "f_name", label: "f_label", port: "f_port", path: "f_path", node_url: "f_nodeUrl" };
    let filled = 0;
    for (const [key, id] of Object.entries(map)) {
      if (data[key] != null && String(data[key]).trim()) {
        document.getElementById(id).value = String(data[key]).trim();
        filled++;
      }
    }
    toast(filled ? `Loaded ${filled} field${filled > 1 ? "s" : ""} from ${file.name}` : "No matching fields found", filled ? "ok" : "warn");
  } catch (err) {
    toast(`Failed to parse ${file.name}: ${err.message}`, "bad");
  }
});

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
