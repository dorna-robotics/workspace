import { apiFetch, stateVariant, isRunning, isLaunched, fmtUptime, fmtTimestamp, esc, wsViewerUrl } from "./api.js";

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
  toastArea.appendChild(el);
  setTimeout(() => el.remove(), 3500);
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

async function sendCmd(name, cmd) {
  return apiFetch(`/workspace/${encodeURIComponent(name)}/cmd`, {
    method: "POST",
    body: JSON.stringify({ cmd }),
  });
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
  wsCount.textContent = `${workspaces.length} workspace${workspaces.length !== 1 ? "s" : ""}`;
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

    el.className = `ws-card${running ? " is-running" : variant === "bad" ? " is-error" : ""}`;

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
          ${esc(state)}
        </span>
      </div>
      <div class="wc-meta">
        <span class="wc-meta-item" title="${esc(wsViewerUrl(ws))}">
          <span>URL</span>
          <strong>${esc(wsViewerUrl(ws).replace(/^https?:\/\//, ""))}</strong>
        </span>
        ${uptime ? `<span class="wc-meta-item"><span>Up</span> <strong>${esc(uptime)}</strong></span>` : ""}
        ${st.last_error ? `<span class="wc-err" title="${esc(st.last_error)}">⚠ error</span>` : ""}
      </div>
      <div class="wc-footer">
        <a class="btn btn-primary btn-sm" href="workspace.html?name=${encodeURIComponent(ws.name)}">Open</a>
        <div class="spacer"></div>
        <div class="wc-actions">
          ${!launched
            ? `<button class="btn btn-sm btn-primary action-btn" data-cmd="launch">Launch</button>`
            : `<button class="btn btn-sm btn-primary action-btn" data-cmd="start"   title="Start"   ${running  ? "disabled" : ""}>▶</button>
               <button class="btn btn-sm action-btn"             data-cmd="pause"   title="Pause"   ${!running ? "disabled" : ""}>⏸</button>
               <button class="btn btn-sm action-btn"             data-cmd="relaunch" title="Relaunch">↻</button>
               <button class="btn btn-sm btn-danger action-btn"  data-cmd="kill"    title="Kill process">Kill</button>`
          }
          <button class="btn btn-sm btn-ghost btn-icon remove-btn" title="Remove from registry">✕</button>
        </div>
      </div>
    `;

    // Action buttons (launch / kill / relaunch)
    el.querySelectorAll(".action-btn").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.preventDefault();
        const cmd = btn.dataset.cmd;
        btn.disabled = true;
        try {
          await sendCmd(ws.name, cmd);
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

// ---- Search filter ----
function applySearch() {
  const q = (wsSearch?.value || "").trim().toLowerCase();
  wsGrid.querySelectorAll(".ws-card[data-name]").forEach(card => {
    const ws = workspaces.find(w => w.name === card.getAttribute("data-name"));
    if (!ws) return;
    const haystack = [ws.name, ws.label, ws.path_to_file].join(" ").toLowerCase();
    card.style.display = (!q || haystack.includes(q)) ? "" : "none";
  });
}

wsSearch?.addEventListener("input", applySearch);

// ---- Poll ----
async function poll() {
  await refreshStatuses();
  if (!firstPoll) checkStateTransitions();
  firstPoll = false;
  render();
  applySearch();
}

function schedulePoll() {
  const hasActive = workspaces.some(w =>
    ["RUNNING","ACTIVE","LAUNCHED_NOT_READY"].includes((w.lastStatus?.state || "").toUpperCase())
  );
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => { await poll(); schedulePoll(); }, hasActive ? 2000 : 6000);
}

// ---- Add Workspace Modal ----
const modal = document.getElementById("modalOverlay");

document.getElementById("btnAdd").addEventListener("click", () => {
  modal.classList.add("show");
  document.getElementById("f_name").focus();
});
document.getElementById("btnModalClose").addEventListener("click",  () => modal.classList.remove("show"));
document.getElementById("btnModalCancel").addEventListener("click", () => modal.classList.remove("show"));
modal.addEventListener("click", (e) => { if (e.target === modal) modal.classList.remove("show"); });

document.getElementById("chkAdv").addEventListener("change", function () {
  document.getElementById("advSection").style.display = this.checked ? "flex" : "none";
});

document.getElementById("btnModalConfirm").addEventListener("click", async () => {
  const name    = document.getElementById("f_name").value.trim();
  const port    = Number(document.getElementById("f_port").value);
  const label   = document.getElementById("f_label").value.trim();
  const path    = document.getElementById("f_path").value.trim();
  const args    = document.getElementById("f_args").value.trim();
  const nodeUrl = document.getElementById("f_nodeUrl").value.trim();

  if (!name || !path) { toast("Name and path are required.", "bad"); return; }

  const confirmBtn = document.getElementById("btnModalConfirm");
  confirmBtn.disabled = true;
  try {
    const payload = { name, port, label, path_to_file: path, args };
    if (nodeUrl) payload.node_url = nodeUrl;
    await apiFetch("/add_workspace", { method: "POST", body: JSON.stringify(payload) });

    modal.classList.remove("show");
    ["f_name", "f_label", "f_path", "f_args", "f_nodeUrl"].forEach(id => {
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
