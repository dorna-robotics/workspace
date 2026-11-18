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

  // ── The unit's declaration: the Vision item, the home page's cards ──
  // /unit.json is /etc/dorna/unit.json as the upgrade wrote it for this
  // unit type (gui/server.py). Its `links` say where Dorna Lab and the
  // vision server are — {host} is this page's own host, {subnet} its first
  // three octets. Nothing here guesses an address: one the unit does not
  // declare, or one that needs {subnet} on a page opened by name, is shown
  // with why, never hidden.
  function unitLink(tpl) {
    var h = window.location.hostname;
    var m = /^(\d{1,3}\.\d{1,3}\.\d{1,3})\.\d{1,3}$/.exec(h);
    if (!tpl) return { why: "Not declared on this unit — run sudo upgrade" };
    if (tpl.indexOf("{subnet}") >= 0 && !m) return { why: "Open this page by its IP address to reach it" };
    return { href: tpl.replace("{host}", h).replace("{subnet}", m ? m[1] : "") };
  }
  var navLinks = nav.querySelector(".app-nav-links");
  var visionItem = document.createElement("a");
  visionItem.className = "app-nav-link";
  visionItem.target = "_blank";
  visionItem.rel = "noopener";
  visionItem.title = "Vision";
  visionItem.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    + 'stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>'
    + '<circle cx="12" cy="12" r="3"/></svg><span>Vision</span>';
  var systemItem = navLinks && navLinks.querySelector('a[href="/orchestrator/system.html"]');
  if (navLinks) navLinks.insertBefore(visionItem, systemItem);   // null: at the end
  fetch("/unit.json")
    .then(function(r) { return r.ok ? r.json() : {}; })
    .catch(function() { return {}; })
    .then(function(unit) {
      var links = unit.links || {};
      var v = unitLink(links.vision);
      if (v.href) visionItem.href = v.href;
      else { visionItem.classList.add("off"); visionItem.title = "Vision — " + v.why; }
      // the home page's Dorna Lab and Vision cards (gui/server.py LANDING_HTML)
      [["cardDornaLab", "dorna_lab"], ["cardVision", "vision"]].forEach(function(c) {
        var card = document.getElementById(c[0]);
        if (!card) return;
        var l = unitLink(links[c[1]]);
        if (l.href) { card.href = l.href; return; }
        card.classList.add("card-off");
        card.querySelector(".card-hint").textContent = l.why;
      });
    });

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
