// App Navigation — collapse/expand sidebar, mobile burger
// Include via: <script src="/vendor/nav.js"></script>

(function() {
  var nav = document.querySelector(".app-nav");
  var overlay = document.querySelector(".app-nav-overlay");
  var collapse = document.querySelector(".app-nav-collapse");
  var burger = document.getElementById("btnBurger");
  var KEY = "nav_expanded";

  if (!nav) return;

  // External bench tools (Dorna Lab, vision server) live on fixed
  // last-octet IPs of the bench subnet. Links marked with
  // data-ext-octet="N" get their href derived from the page's own
  // host — same subnet, last octet swapped — so the nav works on any
  // bench with zero configuration. When the page isn't served from an
  // IPv4 address (localhost, mDNS name) the subnet can't be derived
  // and the links hide instead of pointing somewhere broken.
  var ipm = /^(\d{1,3}\.\d{1,3}\.\d{1,3})\.\d{1,3}$/.exec(location.hostname);
  nav.querySelectorAll("[data-ext-octet]").forEach(function (a) {
    if (ipm) a.href = "http://" + ipm[1] + "." + a.getAttribute("data-ext-octet") + "/";
    else a.style.display = "none";
  });

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
