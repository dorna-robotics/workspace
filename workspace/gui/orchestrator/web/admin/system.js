// system.js — the System page: this unit, and its `upgrade`.
//
// NOTHING HERE NAMES A PART. The parts, their labels, folders and
// branches come from the upgrade's own record (system.py RECORD, written
// by each part of the upgrade repo); the progress steps come from the
// `==> <part>` lines the upgrade prints. A part added to the upgrade shows
// here on its own.
//
// Four views in one card: the UNIT (what is installed, how each part
// compares with what the upgrade installs), the CONFIRM (every risk a
// warning, the exact command), the PROGRESS (the steps, the live log —
// followed through the orchestrator's restart and the reboot), and the
// RESULT (the record of the run: finished or stopped, each part's commit
// before and now). Nothing is greyed out for the bench's state; the
// server refuses only "not installed" and "already running".

import { apiFetch, esc, toast } from "./api.js";

const $ = id => document.getElementById(id);
const host = $("sys");
// This browser started an upgrade: {started_at, before: {part: commit}} —
// kept across the restart and the reboot, cleared at the result.
const KEY = "system_upgrade";

let S = null;            // the last /system state
let run = null;          // the upgrade being followed
let pollTimer = null;

const warn = (html, bad = false) =>
  `<div class="ver-warn${bad ? " bad" : ""}"><span class="bang">!</span><div>${html}</div></div>`;
const short = h => (h || "").slice(0, 7);          // one length everywhere
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const fmtWhen = iso => {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
};
const fmtDur = s => {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
};
const fmtUp = s => {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? `${plural(d, "day")} ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
};
const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch { return null; } };
const save = v => { try { v ? localStorage.setItem(KEY, JSON.stringify(v)) : localStorage.removeItem(KEY); } catch {} };

function card(title, meta, body, foot = "") {
  host.innerHTML = `<div class="sys-card">
    <div class="sys-head"><span>${esc(title)}</span><div class="spacer"></div>${meta ? `<span class="mono">${esc(meta)}</span>` : ""}</div>
    <div class="sys-body">${body}</div>
    <div class="sys-foot" id="sysFoot">${foot}</div></div>`;
}
function onFoot(handlers) {
  for (const [id, fn] of Object.entries(handlers)) $(id)?.addEventListener("click", fn);
}

// ── a part, as its row and its warnings ─────────────────────────────────
function partNow(p) {
  const n = p.now;
  if (!p.dir) return { runs: "—", installs: "re-applied every upgrade", pill: "" };
  if (!n || !n.exists) return { runs: "not installed", installs: p.branch, pill: `<span class="pill off">new install</span>` };
  if (!n.git) return { runs: "not a git folder", installs: p.branch, pill: `<span class="pill bad">replaced</span>` };
  const runs = `${n.branch} ${short(n.head && n.head.hash)}`;
  let pill;
  if (n.reachable === false) pill = `<span class="pill off">GitHub unreachable</span>`;
  else if (n.switches) pill = `<span class="pill warn">switches branch</span>`;
  else if (n.behind) pill = `<span class="pill warn">${n.behind} newer</span>`;
  else pill = `<span class="pill ok">same</span>`;
  if ((n.changes || []).length || (n.untracked || []).length) pill += ` <span class="pill warn">local changes</span>`;
  return { runs, installs: n.target_branch || p.branch, pill };
}

function partWarnings(p) {
  const n = p.now, name = `<b>${esc(p.label || p.name)}</b> (<code>${esc(p.dir)}</code>)`;
  const out = [];
  if (!p.dir || !n || !n.exists) return out;
  if (!n.git) { out.push(warn(`${name} is not a git folder — the upgrade deletes it and clones it fresh.`, true)); return out; }
  if (n.reachable === false) out.push(warn(`${name}: GitHub cannot be reached — this part will fail: ${esc(n.fetch_error)}`, true));
  if (n.switches) out.push(warn(`${name} is on branch <code>${esc(n.branch)}</code> — the upgrade runs <code>${esc(n.target_branch)}</code> from now on (<code>${esc(n.branch)}</code> stays as a branch, not run).`));
  const c = n.changes || [], u = n.untracked || [];
  if (c.length) out.push(warn(`${name}: ${plural(c.length, "uncommitted change")} DISCARDED (<code>git reset --hard</code>).`
    + `<div class="ver-sub">${esc(c.slice(0, 6).join(", "))}${c.length > 6 ? ` … +${c.length - 6}` : ""}</div>`, true));
  if (u.length) out.push(warn(`${name}: ${plural(u.length, "untracked file")} DELETED (<code>git clean -fd</code>) — ignored files are kept.`
    + `<div class="ver-sub">${esc(u.slice(0, 6).join(", "))}${u.length > 6 ? ` … +${u.length - 6}` : ""}</div>`, true));
  if ((n.lost_commits || []).length) out.push(warn(`${name}: ${plural(n.lost_commits.length, "local commit")} on <code>${esc(n.branch)}</code> not on GitHub — <code>checkout -B</code> leaves them behind (only in <code>git reflog</code>).`
    + `<div class="ver-sub">${n.lost_commits.slice(0, 4).map(x => esc(`${x.hash} ${x.subject}`)).join("<br>")}</div>`, true));
  return out;
}

function statusPill(s) {
  if (!s.helper) return `<span class="pill bad">upgrade not installed</span>`;
  if (s.running) return `<span class="pill warn">upgrading</span>`;
  if (!s.record) return `<span class="pill off">no upgrade record yet</span>`;
  const parts = s.parts.filter(p => p.dir);
  if (parts.some(p => p.now && p.now.reachable === false)) return `<span class="pill off">GitHub unreachable</span>`;
  const behind = parts.filter(p => p.now && (!p.now.exists || p.now.behind || p.now.switches)).length;
  if (behind) return `<span class="pill warn">${plural(behind, "part")} to update</span>`;
  return `<span class="pill ok">up to date</span>`;
}

// ── the UNIT ────────────────────────────────────────────────────────────
function viewUnit() {
  const s = S, r = s.record;
  let rows = `<dt>Status</dt><dd>${statusPill(s)}</dd>
    <dt>Upgrade</dt><dd>${s.helper
      ? `<span class="ver-hash">sudo upgrade</span><div class="ver-sub">${esc(s.helper_path)}${r && r.upgrade && r.upgrade.branch ? ` · upgrade repo, branch ${esc(r.upgrade.branch)}` : ""}</div>`
      : `not installed<div class="ver-sub">first time: run the upgrade repo's one-line install from a terminal — it installs <code>${esc(s.helper_path)}</code></div>`}</dd>`;
  if (r && r.error) rows += `<dt>Last upgrade</dt><dd>${warn(esc(r.error), true)}</dd>`;
  else if (r) rows += `<dt>Last upgrade</dt><dd>${esc(fmtWhen(r.finished))} — ${r.result === "finished" ? "finished, rebooted" : `stopped: ${esc(r.reason || "")}`}`
    + `<div class="ver-sub">${esc(fmtDur((new Date(r.finished) - new Date(r.started)) / 1000))}</div></dd>`;
  rows += `<dt>Up</dt><dd>${esc(fmtUp(s.uptime_s))}</dd>`;

  let table;
  if (s.parts.length) {
    table = `<table class="sys-t"><tr><th>Part</th><th>Folder</th><th>This unit runs</th><th>The upgrade installs</th></tr>`
      + s.parts.map(p => {
        const x = partNow(p);
        const failed = p.exit != null && p.exit !== 0;
        return `<tr><td class="what">${esc(p.label || p.name)}${p.label && p.label !== p.name ? `<div class="sub">${esc(p.name)}</div>` : ""}`
          + `${failed ? `<div class="sub" style="color:var(--red)">failed last time (exit ${esc(p.exit)})</div>` : ""}</td>`
          + `<td class="h">${p.dir ? esc(p.dir.split("/").pop()) : `<span class="sub">—</span>`}</td>`
          + `<td class="h">${esc(x.runs)}</td>`
          + `<td>${p.dir ? `<span class="h">${esc(x.installs)}</span> · ` : ""}${x.pill || esc(x.installs)}</td></tr>`;
      }).join("") + `</table>`;
  } else {
    table = `<div class="ver-sub">The parts appear after the next upgrade — each part of the upgrade records itself (the upgrade repo's <code>lib/record.sh</code>).</div>`;
  }
  card("This unit", s.host, `<dl class="ver-rows">${rows}</dl>${table}`,
    `<div class="spacer"></div><button class="btn" id="btnSysCheck">Check again</button>`
    + (s.running ? `<button class="btn btn-primary" id="btnSysFollow">Show progress</button>`
       : s.helper ? `<button class="btn btn-primary" id="btnSysUpgrade">Upgrade…</button>` : ""));
  onFoot({ btnSysCheck: () => refresh(true), btnSysUpgrade: viewConfirm,
           btnSysFollow: () => follow(load() || { started_at: S.now - 3 * 3600, before: {} }) });
}

// ── the CONFIRM ─────────────────────────────────────────────────────────
function viewConfirm() {
  const s = S;
  let html = `<div class="ver-sub">Every part of the upgrade, then a reboot. It takes several minutes; this page follows it and reconnects by itself.</div>`;
  const runs = s.launched || [];
  if (runs.length) {
    const active = runs.filter(r => r.run_active);
    html += warn(`<b>${plural(runs.length, "project")} launched</b> — ${runs.map(r => esc(r.name) + (r.run_active ? " (running)" : "")).join(", ")}. `
      + `The upgrade stops the orchestrator: ${active.length ? "every run ends, " : ""}every project stops and its devices are released.`, active.length > 0);
  }
  html += warn(`<b>The unit reboots</b> at the end — the pendant, the kiosk and every open page lose the bench for about a minute.`);
  if (!s.record) html += warn(`No upgrade record on this unit yet — this page cannot list what each part will change. After this upgrade it can.`);
  for (const p of s.parts) html += partWarnings(p).join("");
  html += warn(`If a service does not come back, the upgrade stops <b>without rebooting</b> — and if that service is the orchestrator, this page cannot reconnect: SSH in and read <code>journalctl -u ${esc(s.unit)}</code>.`);
  html += `<div class="ver-label">Will run</div><ul class="ver-cmds sh"><li>systemd-run --unit=${esc(s.unit)} --collect ${esc(s.helper_path)}</li></ul>`
    + `<div class="ver-sub">Its own system job — it keeps going while the orchestrator restarts; one at a time.</div>`;
  card("Upgrade this unit", s.host, html,
    `<div class="spacer"></div><button class="btn" id="btnSysCancel">Cancel</button><button class="btn btn-warn" id="btnSysGo">Upgrade and reboot</button>`);
  onFoot({ btnSysCancel: viewUnit, btnSysGo: start });
}

async function start() {
  const go = $("btnSysGo");
  if (go) { go.disabled = true; go.textContent = "Starting…"; }
  const before = {};
  for (const p of S.parts) if (p.now && p.now.head) before[p.name] = { branch: p.now.branch, hash: p.now.head.hash };
  try {
    const r = await apiFetch("/system", { method: "POST", body: JSON.stringify({ confirmed: true }) });
    const v = { started_at: r.started_at, before };
    save(v);
    follow(v);
  } catch (e) {
    toast(String(e.message || e), "bad");
    if (go) { go.disabled = false; go.textContent = "Upgrade and reboot"; }
  }
}

// ── the PROGRESS — the journal, through the restart and the reboot ──────
function follow(v) {
  clearTimeout(pollTimer);
  run = { ...v, lines: [], cursor: "", marks: [], down: null, lastOk: Date.now() };
  poll();
}

function steps() {
  // The order the record knows (last upgrade's parts), then any part the
  // log announced that the record did not (a new one), then the finale.
  const names = (S && S.parts || []).map(p => p.name);
  for (const m of run.marks) if (!names.includes(m.name) && !["services", "reboot"].includes(m.name)) names.push(m.name);
  names.push("services", "reboot");
  const label = n => n === "services" ? "Services" : n === "reboot" ? "Reboot"
    : ((S && S.parts || []).find(p => p.name === n) || {}).label || n;
  const at = Object.fromEntries(run.marks.map((m, i) => [m.name, i]));
  const cur = run.marks.length ? run.marks[run.marks.length - 1].name : null;
  const now = Date.now() / 1000;
  return `<ul class="sys-steps">${names.map(n => {
    const i = at[n];
    if (i === undefined) return `<li><span class="ic"></span>${esc(label(n))}<span class="t"></span></li>`;
    const t0 = run.marks[i].t, t1 = i + 1 < run.marks.length ? run.marks[i + 1].t : now;
    const done = n !== cur;
    return `<li class="${done ? "done" : "now"}"><span class="ic">${done ? "✓" : ""}</span>${esc(label(n))}<span class="t">${fmtDur(t1 - t0)}</span></li>`;
  }).join("")}</ul>`;
}

function viewProgress() {
  const el = Date.now() / 1000 - run.started_at;
  const meta = `started ${new Date(run.started_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · ${fmtDur(el)}`;
  const last = run.marks.length ? run.marks[run.marks.length - 1].name : "";
  if (run.down) {
    const rebooting = last === "reboot";
    const off = fmtDur((Date.now() - run.down) / 1000);
    card("Upgrading", meta, `<div class="sys-big"><div class="ring"></div>
        <div class="sys-k">${rebooting ? "Rebooting" : "Reconnecting"} · ${off}</div>
        <h2>${rebooting ? "The unit is restarting" : "The orchestrator is restarting"}</h2>
        <div class="ver-sub">${rebooting ? "Every part installed and every service answered before the reboot."
          : "The upgrade restarts the orchestrator to run the new code."} This page reconnects by itself and shows the result.</div></div>${steps()}`);
    return;
  }
  const keep = host.querySelector(".sys-log");
  const stick = !keep || keep.scrollTop + keep.clientHeight >= keep.scrollHeight - 8;
  card("Upgrading", meta, `${steps()}<div class="ver-label">Live log — journalctl -u ${esc(S ? S.unit : "dorna-upgrade")}</div>
    <pre class="sys-log" id="sysLog">${esc(run.lines.slice(-400).map(l => l.text).join("\n")) || "Waiting for the first line…"}</pre>`,
    `<span class="ver-sub">Nothing to do here — the page follows the upgrade through the restart and the reboot.</span>`);
  const log = $("sysLog");
  if (log && stick) log.scrollTop = log.scrollHeight;
}

async function poll() {
  try {
    const r = await apiFetch(`/system/log?since=${encodeURIComponent(run.started_at)}&cursor=${encodeURIComponent(run.cursor)}`);
    run.down = null; run.lastOk = Date.now();
    run.cursor = r.cursor || run.cursor;
    for (const l of r.lines) {
      run.lines.push(l);
      const m = /^==> (\S+)/.exec(l.text);
      if (m) run.marks.push({ name: m[1], t: l.t });
    }
    if (run.lines.length > 4000) run.lines.splice(0, run.lines.length - 4000);
    // Over: the job has stopped and said something — or never said
    // anything in 20 s (it failed before its first line).
    if (!r.running && (run.lines.length || Date.now() / 1000 - run.started_at > 20)) {
      // The job is over: the record says how (or it ended before writing one).
      S = await apiFetch("/system?fetch=0");
      if (!S.running) return viewResult();
    }
  } catch (e) {
    run.down = run.down || Date.now();
  }
  viewProgress();
  pollTimer = setTimeout(poll, run.down ? 2500 : 1500);
}

// ── the RESULT — the record of this run ─────────────────────────────────
function viewResult() {
  const r = S.record;
  const ours = r && !r.error && new Date(r.finished).getTime() / 1000 >= run.started_at - 5;
  const tail = run.lines.slice(-30).map(l => l.text).join("\n");
  const actions = `<button class="btn" id="btnSysLog">Full log</button><div class="spacer"></div>`;
  if (!ours) {
    card("Upgrade stopped", "", `<div class="sys-big"><div class="bang">!</div><h2>Ended without a record</h2>
      <div class="ver-sub">The upgrade job is over but did not write its record — it stopped early (no network, the download failed) or was stopped by hand. Nothing after the failing step ran.</div></div>
      <div class="ver-label">Its last lines</div><pre class="sys-log">${esc(tail) || "(no output)"}</pre>`,
      actions + `<button class="btn" id="btnSysCheck">Check again</button><button class="btn btn-primary" id="btnSysAgain">Upgrade again…</button>`);
  } else {
    const ok = r.result === "finished";
    // S.parts: the record's parts (the server sends them beside the record).
    const failed = S.parts.filter(p => p.exit != null && p.exit !== 0);
    const rows = S.parts.filter(p => p.dir).map(p => {
      const b = (run.before || {})[p.name];        // {branch, hash}: what ran before
      const now = p.commit ? `${p.branch} ${short(p.commit)}` : "—";
      // git's %h may be shorter than 7 or longer: same = one is the other's prefix
      const same = b && p.commit && b.branch === p.branch && p.commit.startsWith(b.hash);
      return `<tr><td class="what">${esc(p.label || p.name)}${p.exit ? `<div class="sub" style="color:var(--red)">failed (exit ${esc(p.exit)})</div>` : ""}</td>`
        + `<td class="h">${b ? esc(`${b.branch} ${short(b.hash)}`) : `<span class="sub">not installed</span>`}</td>`
        + `<td class="h">${esc(now)}${same ? ` <span class="sub">same</span>` : ""}</td></tr>`;
    }).join("");
    const head = ok
      ? `<div class="sys-big"><div class="tick">✓</div><h2>${failed.length ? "Upgraded, with failures" : "Upgraded"}</h2>
         <div class="ver-sub">The unit rebooted and the orchestrator is back.${failed.length ? ` ${plural(failed.length, "part")} failed — see below and the full log.` : ""} Launch your projects again.</div></div>`
      : `<div class="sys-big"><div class="bang">!</div><h2>Stopped before the reboot</h2>
         <div class="ver-sub">${esc(r.reason || "")}. The parts before it are installed; the unit was NOT rebooted.</div></div>`;
    card(ok ? "Upgrade finished" : "Upgrade stopped",
      `${new Date(r.started).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} → ${new Date(r.finished).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · ${fmtDur((new Date(r.finished) - new Date(r.started)) / 1000)}`,
      head + (rows ? `<table class="sys-t"><tr><th>Part</th><th>Before</th><th>Now</th></tr>${rows}</table>` : "")
        + (ok ? "" : `<div class="ver-label">Its last lines</div><pre class="sys-log">${esc(tail)}</pre>`),
      actions + (ok ? `<button class="btn btn-primary" id="btnSysDone">Done</button>`
                    : `<button class="btn" id="btnSysCheck">Check again</button><button class="btn btn-primary" id="btnSysAgain">Upgrade again…</button>`));
  }
  save(null);
  onFoot({
    btnSysDone: () => refresh(true), btnSysCheck: () => refresh(true),
    btnSysAgain: async () => { await refresh(true); viewConfirm(); },
    btnSysLog: () => {
      const pre = host.querySelector(".sys-log") || (() => {
        host.querySelector(".sys-body").insertAdjacentHTML("beforeend", `<pre class="sys-log"></pre>`);
        return host.querySelector(".sys-log");
      })();
      pre.textContent = run.lines.map(l => l.text).join("\n");
      pre.style.maxHeight = "60vh";
    },
  });
}

// ── load ────────────────────────────────────────────────────────────────
async function refresh(fetchRemote) {
  card("This unit", "", `<div class="ver-busy">${fetchRemote ? "Checking each part with GitHub…" : "Reading this unit…"}</div>`);
  try {
    S = await apiFetch(`/system?fetch=${fetchRemote ? 1 : 0}`);
  } catch (e) {
    card("This unit", "", warn(esc(String(e.message || e)), true),
      `<div class="spacer"></div><button class="btn btn-primary" id="btnSysCheck">Check again</button>`);
    onFoot({ btnSysCheck: () => refresh(true) });
    return;
  }
  viewUnit();
}

(async () => {
  // An upgrade this browser started — still running, or over while the
  // page was away (the restart, the reboot): follow it to its result.
  const mine = load();
  if (mine) {
    try { S = await apiFetch("/system?fetch=0"); } catch {}
    follow(mine);
    return;
  }
  await refresh(true);
})();
