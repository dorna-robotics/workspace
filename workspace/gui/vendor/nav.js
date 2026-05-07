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

  // ── Orchestrator workspace count badge ─────────────────────────────
  // Shown next to the Orchestrator nav link. Initial value from one
  // HTTP fetch on load; live updates from /orchestrator/ws/status
  // (the same WS the dashboard listens to). When that WS is down the
  // page also falls back to a low-frequency HTTP refresh so the count
  // stays approximately right.
  var badge = document.getElementById("navOrchCount");
  if (!badge) return;

  function setCount(n) {
    var v = (typeof n === "number" && n >= 0) ? n : null;
    if (v === null) {
      badge.hidden = true;
      badge.textContent = "";
      return;
    }
    badge.hidden = false;
    badge.textContent = String(v);
  }

  function fetchCount() {
    fetch("/orchestrator/api/workspaces", { cache: "no-store" })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(j) {
        if (!j || !Array.isArray(j.workspaces)) return;
        setCount(j.workspaces.length);
      })
      .catch(function() {});
  }

  fetchCount();

  // Live updates via the orchestrator-level status WS. Same endpoint
  // the dashboard subscribes to — every push carries the full
  // ``statuses`` map so we can read the count from its keys without
  // hitting HTTP again.
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
            setCount(Object.keys(msg.statuses).length);
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
    // WS unavailable — fall back to periodic HTTP refresh.
    setInterval(fetchCount, 10000);
  }
})();
