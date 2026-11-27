// The top bar's shared parts — the Updates button and panel.
// Include via: <script src="/vendor/nav.js"></script>
// (There is no sidebar: every page is reached from the projects page.)

(function() {
  // ── The brand: Dorna's mark + DORNA + Workspace, at the start of the
  // page's top bar (after its back button); it goes to the projects page.
  // Not inside a frame (Developer mode's tabs) — the outer page has it.
  var bar = window.self === window.top && document.querySelector(".topbar, .ws-header, .sb-header");
  if (bar && !bar.querySelector(".ws-brand")) {
    var brand = document.createElement("a");
    brand.className = "ws-brand"; brand.href = "/"; brand.title = "Dorna Workspace — projects";
    brand.setAttribute("aria-label", "Dorna Workspace — projects");
    brand.innerHTML = '<span class="ws-brand-mark"></span><span class="ws-brand-word"></span><span class="ws-brand-app">Workspace</span>';
    var first = bar.firstElementChild;
    bar.insertBefore(brand, first && first.matches('a[href="/"]') ? first.nextSibling : first);
  }

  // ── Updates: the top bar's button and panel (like Fusion 360's) ──────
  // Mounted where a page has #updatesSlot (the main page). The panel
  // shows this Workspace's version (/home/api/version — workspace.version),
  // the unit (/unit.json, as the upgrade declared it) and the System
  // page's data (gui/orchestrator/system.py: the last upgrade's record,
  // the parts to update by its needs_update rule, whether one runs). The
  // dot on the button: parts to update, from the cheap summary (a fresh
  // check at most hourly, in the background). "Upgrade…" is the System
  // page's action — its confirm, then POST /orchestrator/api/system
  // {confirmed: true}; it is offered only when the upgrade tool is
  // installed, otherwise the panel shows the command to run.
  var updSlot = document.getElementById("updatesSlot");
  if (updSlot) (function() {
    var SYS = "/orchestrator/api/system";
    var KEY = "system_upgrade";            // the System page follows the same run (system.js)
    var ICON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 11-2.64-6.36"/><polyline points="21 3 21 9 15 9"/><polyline points="12 7 12 12 15 14"/></svg>';
    updSlot.innerHTML = '<button class="btn btn-ghost btn-sm btn-icon upd-btn" id="btnUpdates" title="Updates" aria-label="Updates" aria-haspopup="dialog" aria-expanded="false">'
      + ICON + '<span class="upd-dot" hidden></span></button>';
    var btn = updSlot.querySelector("button"), dot = updSlot.querySelector(".upd-dot");
    var panel = document.createElement("div");
    panel.className = "upd-menu"; panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "Updates");
    panel.hidden = true;
    document.body.appendChild(panel);

    var S = null, version = "", unit = {}, busy = "", logLine = "", logTimer = null, logCursor = "";

    function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
    function plural(n, w) { return n + " " + w + (n === 1 ? "" : "s"); }
    function headers(json) {
      var h = json ? { "Content-Type": "application/json" } : {}, tok = "";
      try { tok = (localStorage.getItem("orch_token") || "").trim(); } catch (_) {}
      if (tok) h["X-Orch-Token"] = tok;
      return h;
    }
    function getJSON(url) {
      return fetch(url, { headers: headers(false), cache: "no-store" }).then(function(r) {
        return r.json().catch(function() { return {}; }).then(function(j) {
          if (!r.ok) throw new Error(j.error || r.statusText);
          return j;
        });
      });
    }
    function fmtWhen(iso) {
      var d = new Date(iso);
      return !iso || isNaN(d) ? "—" : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
    }
    function fmtUp(s) {
      if (s == null) return "—";
      var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
      return d ? plural(d, "day") + " " + h + " h" : h ? h + " h " + m + " min" : m + " min";
    }
    function setDot(n, running) {
      dot.hidden = !(n || running);
      btn.title = running ? "Updates — upgrading" : n ? "Updates — " + plural(n, "part") + " to update" : "Updates";
    }

    // The status — the System page's rule (system.js statusPill).
    function status(s) {
      if (!s) return ['off', "Checking…"];
      if (s.running) return ["warn", "Upgrading"];
      if (!s.helper) return ["bad", "No upgrade tool"];
      if (!s.record) return ["off", "Not upgraded yet"];
      if (s.to_update.length) return ["warn", "Update available"];
      if (s.parts.some(function(p) { return p.dir && p.now && p.now.reachable === false; })) return ["off", "Can't check for updates"];
      return ["ok", "Up to date"];
    }

    function render() {
      var s = S, st = status(s), r = s && s.record, rows = "";
      var unitName = unit.name || unit.model || unit.type || "";
      rows += '<dt>Version</dt><dd class="upd-ver">' + (version ? "v" + esc(version) : "—") + "</dd>";
      rows += "<dt>Unit</dt><dd>" + esc(unitName || location.hostname)
        + (unitName ? '<div class="ver-sub">' + esc(location.hostname) + "</div>" : "") + "</dd>";
      if (s) {
        if (r && r.error) rows += "<dt>Last upgrade</dt><dd>" + esc(r.error) + "</dd>";
        else if (r) rows += "<dt>Last upgrade</dt><dd>" + esc(fmtWhen(r.finished))
          + '<div class="ver-sub">' + (r.result === "finished" ? "finished" : "stopped — " + esc(r.reason || ""))
          + (r.upgrade && r.upgrade.branch ? " · channel " + esc(r.upgrade.branch) : "") + "</div></dd>";
        rows += "<dt>Uptime</dt><dd>" + esc(fmtUp(s.uptime_s)) + "</dd>";
      }
      var parts = "";
      if (s && s.to_update.length && !s.running) {
        var label = function(n) { var p = s.parts.filter(function(x) { return x.name === n; })[0]; return (p && p.label) || n; };
        parts = '<div class="upd-parts"><div class="ver-label">To update</div><ul>'
          + s.to_update.map(function(n) { return "<li>" + esc(label(n)) + "</li>"; }).join("") + "</ul></div>";
      }
      var note = "";
      if (s && s.running) note = '<div class="upd-note"><div class="ver-sub">Installing, then the unit reboots — about a minute offline at the end.</div>'
        + (logLine ? '<div class="upd-log mono">' + esc(logLine) + "</div>" : "") + "</div>";
      else if (s && !s.helper) note = '<div class="upd-note"><div class="ver-sub">Install it once from a terminal on the robot, then upgrade from here:</div>'
        + '<ul class="ver-cmds sh"><li>sudo upgrade</li></ul></div>';
      var foot = "";
      if (s && !s.running) {
        foot += '<button class="btn" data-upd="check"' + (busy ? " disabled" : "") + ">" + (busy === "check" ? "Checking…" : "Check for updates") + "</button>";
        if (s.helper) foot += '<button class="btn ' + (s.to_update.length || !s.record ? "btn-primary" : "") + '" data-upd="upgrade"' + (busy ? " disabled" : "") + ">"
          + (busy === "upgrade" ? "Starting…" : "Upgrade…") + "</button>";
      }
      panel.innerHTML = '<div class="upd-head"><div class="upd-name">Dorna Workspace</div>'
        + '<span class="pill ' + st[0] + '">' + esc(st[1]) + "</span></div>"
        + '<dl class="ver-rows upd-rows">' + rows + "</dl>" + parts + note
        + (foot ? '<div class="upd-foot">' + foot + "</div>" : "");
    }

    function place() {
      var b = btn.getBoundingClientRect();
      panel.style.top = Math.round(b.bottom + 6) + "px";
      panel.style.right = Math.max(8, Math.round(window.innerWidth - b.right)) + "px";
    }
    function load(fetchRemote) {
      return getJSON(SYS + "?fetch=" + (fetchRemote ? 1 : 0)).then(function(s) {
        S = s; setDot(s.to_update.length, s.running);
        if (s.running) follow(); else stopFollow();
      }).catch(function(e) {
        S = null;
        if (window.__toast) window.__toast(String(e.message || e), "bad");
      });
    }
    function open() {
      panel.hidden = false; btn.setAttribute("aria-expanded", "true");
      render(); place();
      load(false).then(function() { if (!panel.hidden) { render(); place(); } });
    }
    function close() { panel.hidden = true; btn.setAttribute("aria-expanded", "false"); }
    btn.addEventListener("click", function(e) { e.stopPropagation(); panel.hidden ? open() : close(); });
    document.addEventListener("click", function(e) { if (!panel.hidden && !panel.contains(e.target) && !btn.contains(e.target)) close(); });
    document.addEventListener("keydown", function(e) { if (e.key === "Escape") close(); });
    window.addEventListener("resize", function() { if (!panel.hidden) place(); });

    panel.addEventListener("click", function(e) {
      var b = e.target.closest("[data-upd]");
      if (!b || busy) return;
      if (b.dataset.upd === "check") {
        busy = "check"; render();
        load(true).then(function() { busy = ""; render(); });
      } else if (b.dataset.upd === "upgrade") upgrade();
    });

    // The System page's confirm (system.js viewConfirm), as a dialog.
    function upgrade() {
      busy = "upgrade"; render();
      load(false).then(function() {
        var s = S;
        if (!s || s.running || !s.helper) { busy = ""; render(); return; }
        var lines = [], runs = s.launched || [];
        if (runs.length) {
          var active = runs.filter(function(r) { return r.run_active; });
          lines.push(plural(runs.length, "project") + " launched (" + runs.map(function(r) { return r.name + (r.run_active ? ", running" : ""); }).join("; ")
            + "): the upgrade stops " + (active.length ? "every run and " : "") + "every project, and releases its devices.");
        }
        lines.push("The unit reboots at the end — the pendant and every open page lose the robot for about a minute.");
        if (!s.record) lines.push("This unit has not been upgraded from here before, so the changes can't be listed yet.");
        if (s.parts.some(function(p) { var n = p.now || {}; return (n.changes || []).length || (n.untracked || []).length; }))
          lines.push("Some parts have local changes — the upgrade discards them.");
        var ask = window.confirmDialog ? window.confirmDialog({
          title: "Upgrade and reboot?", confirm: "Upgrade and reboot", variant: "danger", dangerous: true,
          message: ["Installs the latest version of every part, then restarts the unit. Takes several minutes."].concat(lines).join(" "),
        }) : Promise.resolve(false);
        return ask.then(function(ok) {
          if (!ok) { busy = ""; render(); return; }
          var before = {};
          s.parts.forEach(function(p) { if (p.now && p.now.head) before[p.name] = { branch: p.now.branch, hash: p.now.head.hash }; });
          return fetch(SYS, { method: "POST", headers: headers(true), body: JSON.stringify({ confirmed: true }) })
            .then(function(r) { return r.json().then(function(j) { if (!r.ok) throw new Error(j.error || r.statusText); return j; }); })
            .then(function(j) {
              try { localStorage.setItem(KEY, JSON.stringify({ started_at: j.started_at, before: before })); } catch (_) {}
              S.running = true; busy = ""; setDot(0, true); follow(); render();
            })
            .catch(function(e) { busy = ""; render(); if (window.__toast) window.__toast(String(e.message || e), "bad"); });
        });
      });
    }

    // While an upgrade runs: its latest journal line in the panel; the
    // orchestrator restarts and the unit reboots — missed polls just retry.
    function follow() {
      if (logTimer) return;
      var since = 0;
      try { since = (JSON.parse(localStorage.getItem(KEY) || "null") || {}).started_at || 0; } catch (_) {}
      if (!since) since = Date.now() / 1000 - 3 * 3600;
      var tick = function() {
        getJSON(SYS + "/log?since=" + encodeURIComponent(since) + "&cursor=" + encodeURIComponent(logCursor)).then(function(r) {
          logCursor = r.cursor || logCursor;
          if (r.lines.length) logLine = r.lines[r.lines.length - 1].text;
          if (!r.running) { stopFollow(); return load(false).then(function() { if (!panel.hidden) render(); }); }
          if (!panel.hidden) render();
        }).catch(function() { logLine = "Reconnecting…"; if (!panel.hidden) render(); })
          .then(function() { if (logTimer) logTimer = setTimeout(tick, 2500); });
      };
      logTimer = setTimeout(tick, 0);
    }
    function stopFollow() { if (logTimer) clearTimeout(logTimer); logTimer = null; }

    getJSON("/home/api/version").then(function(j) { version = j.version || ""; }).catch(function() {});
    getJSON("/unit.json").then(function(j) { unit = j || {}; }).catch(function() {});
    // The dot — the nav's cheap glance; the first check runs in the background.
    var glance = function(again) {
      getJSON(SYS + "/summary").then(function(j) {
        if (j.to_update == null) { if (again) setTimeout(function() { glance(false); }, 20000); setDot(0, j.running); return; }
        setDot(j.to_update.length, j.running);
      }).catch(function() {});
    };
    glance(true);
    setInterval(function() { glance(false); }, 10 * 60 * 1000);
  })();
})();
