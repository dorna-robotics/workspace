// App Navigation — collapse/expand sidebar, mobile burger
// Include via: <script src="/vendor/nav.js"></script>

(function() {
  var nav = document.querySelector(".app-nav");
  var overlay = document.querySelector(".app-nav-overlay");
  var collapse = document.querySelector(".app-nav-collapse");
  var burger = document.getElementById("btnBurger");
  var KEY = "nav_expanded";

  if (!nav) return;

  // Restore saved state (desktop only) — collapsed by default, expanded if saved
  if (window.innerWidth > 768 && localStorage.getItem(KEY) === "1") {
    nav.classList.add("expanded");
  }
  // Remove instant-load class so transitions work for user interactions
  requestAnimationFrame(function() {
    document.documentElement.classList.remove("nav-expanded");
  });

  // Desktop: collapse/expand toggle
  if (collapse) {
    collapse.addEventListener("click", function() {
      nav.classList.toggle("expanded");
      localStorage.setItem(KEY, nav.classList.contains("expanded") ? "1" : "0");
    });
  }

  // Mobile: burger opens sidebar as overlay
  function mobileOpen()  { nav.classList.add("mobile-open"); overlay && overlay.classList.add("show"); }
  function mobileClose() { nav.classList.remove("mobile-open"); overlay && overlay.classList.remove("show"); }

  if (burger) {
    burger.addEventListener("click", function() {
      nav.classList.contains("mobile-open") ? mobileClose() : mobileOpen();
    });
  }
  if (overlay) {
    overlay.addEventListener("click", mobileClose);
  }

  document.addEventListener("keydown", function(e) {
    if (e.key === "Escape") mobileClose();
  });

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

  // ── System: parts to update ─────────────────────────────────────────
  // The System item's badge — how many parts of the bench's upgrade are
  // not up to date (gui/orchestrator/system.py: needs_update, cached, a
  // fresh check at most hourly). Hidden when none, or when it is not
  // known yet (the first check runs in the background: asked again in
  // 20 s), or when the orchestrator does not answer. The System page
  // calls window.__navSystemRefresh after its own check.
  var sysBadge = document.getElementById("navSystemBadge");
  if (sysBadge) {
    var sysLink = sysBadge.closest ? sysBadge.closest("a") : null;
    var sysCheck = function(again) {
      var tok = "";
      try { tok = (localStorage.getItem("orch_token") || "").trim(); } catch (_) {}
      fetch("/orchestrator/api/system/summary", { headers: tok ? { "X-Orch-Token": tok } : {} })
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(j) {
          if (!j) return;
          if (j.to_update == null) { if (again) setTimeout(function() { sysCheck(false); }, 20000); return; }
          var n = j.to_update.length;
          sysBadge.hidden = !n;
          sysBadge.textContent = n ? String(n) : "";
          if (sysLink) sysLink.title = n ? "System — " + n + " part" + (n === 1 ? "" : "s") + " to update" : "System";
        })
        .catch(function() {});
    };
    window.__navSystemRefresh = function() { sysCheck(false); };
    sysCheck(true);
    setInterval(function() { sysCheck(false); }, 10 * 60 * 1000);
  }

  // ── Orchestrator running-workspace count badge ─────────────────────
  // Shows the number of workspaces in a strictly-running state
  // (RUNNING / ACTIVE) — matches the dashboard's "running" stat-item
  // count exactly (see admin/api.js ``isRunning``). PAUSED, ENDING,
  // IDLE, etc. are NOT counted: the badge is meant as an
  // "is-anything-actively-moving-right-now" glance, not a workspace
  // census.
  //
  // Live data comes from /orchestrator/ws/status — every push carries
  // the full ``statuses`` map so we can recompute the running count
  // from its values without hitting HTTP. If the WS is unreachable
  // the badge stays hidden (a hidden badge is more honest than a
  // stale total).
  var badge = document.getElementById("navOrchCount");
  if (!badge) return;

  function setCount(n) {
    var v = (typeof n === "number" && n >= 0) ? n : null;
    if (v === null || v === 0) {
      // Hide on zero too — an empty pill is just visual noise when
      // nothing is running.
      badge.hidden = true;
      badge.textContent = "";
      return;
    }
    badge.hidden = false;
    badge.textContent = String(v);
  }

  // Strict-running test — kept inline because this vendor script
  // can't import admin/api.js. Mirror the canonical definition.
  function isStrictlyRunning(state) {
    var s = String(state || "").toUpperCase();
    return s === "RUNNING" || s === "ACTIVE";
  }

  function countRunning(statuses) {
    if (!statuses || typeof statuses !== "object") return 0;
    var n = 0;
    for (var k in statuses) {
      if (Object.prototype.hasOwnProperty.call(statuses, k)) {
        var st = statuses[k];
        if (st && isStrictlyRunning(st.state)) n += 1;
      }
    }
    return n;
  }

  try {
    var proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    var url = proto + "//" + window.location.host + "/orchestrator/ws/status";
    var ws = null;
    var retryMs = 1000;

    function connect() {
      try { ws = new WebSocket(url); } catch (_) { return; }
      ws.onopen = function() { retryMs = 1000; };
      ws.onmessage = function(e) {
        try {
          var msg = JSON.parse(e.data);
          if (msg && msg.type === "status" && msg.statuses) {
            setCount(countRunning(msg.statuses));
          }
        } catch (_) {}
      };
      ws.onclose = function() {
        setTimeout(connect, retryMs);
        retryMs = Math.min(retryMs * 1.5, 8000);
      };
      ws.onerror = function() { try { ws.close(); } catch (_) {} };
    }
    connect();
  } catch (_) {
    // WS unavailable; leave the badge hidden.
  }
})();
