"""
Unified GUI server — serves orchestrator and scene builder on one port.

    http://<ip>:5000/orchestrator/     → orchestrator dashboard
    http://<ip>:5000/scene-builder/    → scene builder
    http://<ip>:5000/                  → redirects to orchestrator

Usage:
    cd workspace
    sudo python3 gui/server.py
"""

import os
import sys

# Bytecode caches in tmpfs, not on the SD card (see orchestrator.py's
# launch env for the full why). sys.pycache_prefix is the runtime
# knob (the env var only takes effect at interpreter startup, so it
# wouldn't cover THIS process); set both, before the heavy imports,
# so the orchestrator's own modules follow the same rule and any
# non-orchestrator children inherit it too.
sys.pycache_prefix = "/tmp/pycache"
os.environ.setdefault("PYTHONPYCACHEPREFIX", "/tmp/pycache")

import asyncio

import tornado.web
import tornado.ioloop
import tornado.websocket

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
GUI_DIR = os.path.dirname(os.path.abspath(__file__))
WS_ROOT = os.path.dirname(GUI_DIR)  # workspace/

ORCH_WEB_DIR = os.path.join(GUI_DIR, "orchestrator", "web", "admin")
SB_WEB_DIR = os.path.join(GUI_DIR, "scene_builder", "web")
STATIC_DIR = os.path.join(WS_ROOT, "static")

# Ensure workspace package is importable
if WS_ROOT not in sys.path:
    sys.path.insert(0, WS_ROOT)

# ---------------------------------------------------------------------------
# Import orchestrator
# ---------------------------------------------------------------------------
from gui.orchestrator.server import (
    Orchestrator,
    NoCacheStaticFileHandler,
    WorkspacesListHandler,
    WorkspacesStatusHandler,
    AddWorkspaceHandler,
    RemoveWorkspaceHandler,
    WorkspaceCmdHandler,
    WorkspaceStatusHandler,
    WorkspaceLogsHandler,
    WorkspaceLogsWebSocket,
    LaunchConfigHandler,
    UpdateKwargsHandler,
    FileUploadHandler,
    StatusWebSocket,
    _ws_poll_loop,
    WorkspaceDevicesHandler,
    WorkspaceDeviceCmdHandler,
)

# ---------------------------------------------------------------------------
# Import scene builder (triggers dorna2 patches + component scanning)
# ---------------------------------------------------------------------------
from gui.scene_builder.server import (
    sio as sb_sio,
    CatalogHandler,
    CategoriesHandler,
    TypeMetaHandler,
    InstantiateHandler,
    RailsHandler,
    SaveConfigHandler,
    SetProjectHandler,
)
import socketio as _socketio

# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------
LANDING_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dorna Workspace</title>
  <script>(function(){ document.documentElement.setAttribute("data-theme", localStorage.getItem("orch_theme")||"light"); if(localStorage.getItem("nav_expanded")==="1")document.documentElement.classList.add("nav-expanded"); })();</script>
  <link rel="icon" type="image/png" href="/vendor/favicon.png"/>
  <link rel="stylesheet" href="/vendor/base.css"/>
  <link rel="stylesheet" href="/vendor/nav.css"/>
  <style>
    body { min-height: 100vh; display: flex; flex-direction: column; }

    .main {
      /* Anchored at a fixed height (not vertically centered) so
         expanding the Developer section only grows DOWNWARD — the
         cards above never move. */
      flex: 1; display: flex; align-items: flex-start; justify-content: center;
      padding: 16vh 24px 40px;
    }
    .container { text-align: center; max-width: 640px; }
    h1 { font-size: 32px; font-weight: 800; margin-bottom: 6px; letter-spacing: -0.8px; }
    .subtitle { color: var(--muted); font-size: 14px; margin-bottom: 40px; }
    .cards {
      display: grid;
      grid-template-columns: 1fr;
      gap: 12px;
      text-align: left;
    }
    a.card {
      display: flex; align-items: center; gap: 14px;
      padding: 16px 18px;
      background: var(--surface); border: 1px solid var(--border2); border-radius: 14px;
      text-decoration: none; color: var(--text);
      transition: background 0.15s, border-color 0.15s, transform 0.1s, box-shadow 0.15s;
    }
    a.card:hover {
      background: var(--surface2); border-color: var(--accent);
      transform: translateY(-2px); box-shadow: var(--shadow);
    }
    .card-icon {
      width: 44px; height: 44px; border-radius: 12px;
      display: flex; align-items: center; justify-content: center;
      flex-shrink: 0;
    }
    .card-icon svg { opacity: 0.9; }
    .card-text { min-width: 0; }
    .card-label { font-size: 14px; font-weight: 600; letter-spacing: -0.2px; }
    .card-hint { font-size: 11px; color: var(--muted); margin-top: 2px; }
    .cards-label {
      display: flex; align-items: center; gap: 5px;
      text-align: left; font-size: 11px; font-weight: 600;
      color: var(--muted); text-transform: uppercase; letter-spacing: 0.8px;
      margin: 24px 0 10px 4px;
      cursor: pointer; user-select: none;
    }
    .cards-label:hover { color: var(--text); }
    .cards[hidden] { display: none; }
    .cards-label svg { transition: transform 0.15s; }
    .cards-label.open svg { transform: rotate(90deg); }

    /* Per-card accent colors */
    .card-orchestrator .card-icon { background: rgba(10,132,255,0.12); color: var(--accent); }
    .card-scene .card-icon { background: rgba(52,199,89,0.12); color: var(--green); }
    .card-jupyter .card-icon { background: rgba(255,69,58,0.12); color: var(--red); }
    .card-lab .card-icon { background: rgba(191,90,242,0.12); color: #bf5af2; }
    .card-vision .card-icon { background: rgba(255,159,10,0.12); color: #ff9f0a; }
    [data-theme="light"] .card-orchestrator .card-icon { background: rgba(0,122,255,0.08); }
    [data-theme="light"] .card-scene .card-icon { background: rgba(36,138,61,0.08); }
    [data-theme="light"] .card-jupyter .card-icon { background: rgba(215,0,21,0.08); }
    [data-theme="light"] .card-lab .card-icon { background: rgba(175,82,222,0.08); color: #af52de; }
    [data-theme="light"] .card-vision .card-icon { background: rgba(255,149,0,0.08); color: #c93400; }

  </style>
</head>
<body>
  <nav class="app-nav">
    <div class="app-nav-header">
      <button class="app-nav-collapse" title="Toggle sidebar"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="3" x2="9" y2="21"/></svg></button>
    </div>
    <div class="app-nav-links">
      <a href="/" class="app-nav-link active" title="Home">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
        <span>Home</span>
      </a>
      <a href="/orchestrator/" class="app-nav-link" title="Orchestrator" data-orch-link>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
        <span>Orchestrator</span>
        <span class="app-nav-badge" id="navOrchCount" hidden></span>
      </a>
    </div>
  </nav>
  <div class="app-nav-overlay"></div>
  <header class="topbar">
    <button class="burger-btn" id="btnBurger" title="Menu"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg></button>
    <div class="spacer"></div>
    <div class="topbar-actions">
      <button class="btn btn-ghost btn-sm btn-icon js-fullscreen-toggle" id="btnFullscreen" title="Fullscreen"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg></button>
      <button class="btn btn-ghost btn-sm btn-icon js-theme-toggle" id="btnTheme" title="Toggle theme"></button>
    </div>
  </header>
  <main class="main">
    <div class="container">
      <h1>Dorna Workspace</h1>
      <p class="subtitle">Select an application</p>
      <div class="cards">
        <a class="card card-orchestrator" href="/orchestrator/">
          <div class="card-icon"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg></div>
          <div class="card-text"><div class="card-label">Orchestrator</div><div class="card-hint">Manage workspaces</div></div>
        </a>
        <a class="card card-lab" id="cardDornaLab" target="_blank" rel="noopener">
          <div class="card-icon"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="9" width="14" height="11" rx="2"/><line x1="12" y1="9" x2="12" y2="5"/><circle cx="12" cy="3.5" r="1.5"/><circle cx="9.5" cy="13.5" r="0.75" fill="currentColor"/><circle cx="14.5" cy="13.5" r="0.75" fill="currentColor"/><line x1="9" y1="17" x2="15" y2="17"/><line x1="2" y1="13" x2="5" y2="13"/><line x1="19" y1="13" x2="22" y2="13"/></svg></div>
          <div class="card-text"><div class="card-label">Dorna Lab</div><div class="card-hint">Robot monitoring</div></div>
        </a>
      </div>
      <div class="cards-label" id="devToggle" title="Show developer tools">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
        Developer
      </div>
      <div class="cards" id="devCards" hidden>
        <a class="card card-jupyter" id="cardJupyter">
          <div class="card-icon"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 014 4v14a3 3 0 00-3-3H2z"/><path d="M22 3h-6a4 4 0 00-4 4v14a3 3 0 013-3h7z"/></svg></div>
          <div class="card-text"><div class="card-label">Jupyter</div><div class="card-hint">Notebooks &amp; development</div></div>
        </a>
        <a class="card card-scene" href="/scene-builder/">
          <div class="card-icon"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg></div>
          <div class="card-text"><div class="card-label">Scene Builder</div><div class="card-hint">Design workspace layout</div></div>
        </a>
        <a class="card card-vision" id="cardVision" target="_blank" rel="noopener">
          <div class="card-icon"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></div>
          <div class="card-text"><div class="card-label">Vision</div><div class="card-hint">Vision server</div></div>
        </a>
      </div>
      <script>
        var h = window.location.hostname;
        document.getElementById("cardJupyter").href = "http://" + h + ":8888/doc/workspaces/auto";
        // Dorna Lab / vision server live on fixed last-octet IPs of the
        // bench subnet — derive from the page's own host. Hidden when
        // the page isn't served from an IPv4 address (no subnet to derive).
        var m = /^(\\d{1,3}\\.\\d{1,3}\\.\\d{1,3})\\.\\d{1,3}$/.exec(h);
        var lab = document.getElementById("cardDornaLab");
        var vis = document.getElementById("cardVision");
        if (m) {
          lab.href = "http://" + m[1] + ".10/";
          vis.href = "http://" + m[1] + ".40/";
        } else {
          lab.style.display = "none";
          vis.style.display = "none";
        }

        // Developer section — collapsible, collapsed by default,
        // choice persisted per browser.
        var devToggle = document.getElementById("devToggle");
        var devCards = document.getElementById("devCards");
        function devApply(open) {
          devCards.hidden = !open;
          devToggle.classList.toggle("open", open);
          devToggle.title = open ? "Hide developer tools" : "Show developer tools";
        }
        devApply(false);  // always collapsed on load
        devToggle.addEventListener("click", function () {
          devApply(devCards.hidden);
        });
      </script>
    </div>
  </main>
  <script src="/vendor/theme.js"></script>
  <script src="/vendor/nav.js"></script>
</body>
</html>"""


class LandingHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "text/html")
        self.write(LANDING_HTML)


class ProjectAwareStaticHandler(NoCacheStaticFileHandler):
    """Serves static files — checks project CAD/ folder first, then library static/."""
    def get_absolute_path(self, root, path):
        from gui.scene_builder.server import _project_path
        if _project_path:
            project_file = os.path.join(_project_path, path)
            if os.path.isfile(project_file):
                return project_file
        return super().get_absolute_path(root, path)

    def validate_absolute_path(self, root, absolute_path):
        if os.path.isfile(absolute_path):
            return absolute_path
        return super().validate_absolute_path(root, absolute_path)


class ConfigVersionHandler(tornado.web.RequestHandler):
    def get(self):
        import time as _time
        self.write({"version": str(int(_time.time()))})


# ---------------------------------------------------------------------------
# Build unified Tornado app
# ---------------------------------------------------------------------------
def make_app(port=5000):
    orch = Orchestrator(port=port)
    orch.load_registry()

    routes = [
        # ---- Landing ----
        (r"/", LandingHandler),

        # ---- Orchestrator API (must be before catch-all) ----
        (r"/orchestrator/api/workspaces/status", WorkspacesStatusHandler, dict(orch=orch)),
        (r"/orchestrator/api/workspaces", WorkspacesListHandler, dict(orch=orch)),
        (r"/orchestrator/api/add_workspace", AddWorkspaceHandler, dict(orch=orch)),
        (r"/orchestrator/api/remove_workspace", RemoveWorkspaceHandler, dict(orch=orch)),
        (r"/orchestrator/api/workspace/([^/]+)/cmd", WorkspaceCmdHandler, dict(orch=orch)),
        (r"/orchestrator/api/workspace/([^/]+)/status", WorkspaceStatusHandler, dict(orch=orch)),
        (r"/orchestrator/api/workspace/([^/]+)/logs", WorkspaceLogsHandler, dict(orch=orch)),
        (r"/orchestrator/ws/logs/([^/]+)", WorkspaceLogsWebSocket, dict(orch=orch)),
        (r"/orchestrator/api/workspace/([^/]+)/launch_config", LaunchConfigHandler, dict(orch=orch)),
        (r"/orchestrator/api/workspace/([^/]+)/kwargs", UpdateKwargsHandler, dict(orch=orch)),
        (r"/orchestrator/api/workspace/([^/]+)/upload/([^/]+)", FileUploadHandler, dict(orch=orch)),
        (r"/orchestrator/api/workspace/([^/]+)/devices", WorkspaceDevicesHandler, dict(orch=orch)),
        (r"/orchestrator/api/workspace/([^/]+)/devices/([^/]+)/(recover|release)", WorkspaceDeviceCmdHandler, dict(orch=orch)),

        # ---- Orchestrator WebSocket + GUI (catch-all last) ----
        (r"/orchestrator/ws/status", StatusWebSocket, dict(orch=orch)),
        (r"/orchestrator/?", tornado.web.RedirectHandler, {"url": "/orchestrator/index.html"}),
        (r"/orchestrator/(.*)", NoCacheStaticFileHandler, {"path": ORCH_WEB_DIR}),

        # ---- Scene Builder API (must be before catch-all) ----
        (r"/scene-builder/api/catalog", CatalogHandler),
        (r"/scene-builder/api/categories", CategoriesHandler),
        (r"/scene-builder/api/type_meta", TypeMetaHandler),
        (r"/scene-builder/api/instantiate", InstantiateHandler),
        (r"/scene-builder/api/rails", RailsHandler),
        (r"/scene-builder/api/save_config", SaveConfigHandler),
        (r"/scene-builder/api/set_project", SetProjectHandler),
        (r"/scene-builder/api/config_version", ConfigVersionHandler),

        # ---- Scene Builder Socket.IO + GUI (catch-all last) ----
        (r"/scene-builder/?", tornado.web.RedirectHandler, {"url": "/scene-builder/index.html"}),
        (r"/scene-builder/socket\.io/(.*)", _socketio.get_tornado_handler(sb_sio)),
        (r"/scene-builder/(.*)", NoCacheStaticFileHandler, {"path": SB_WEB_DIR}),

        # ---- Shared static (project CAD/ first, then library static/) ----
        (r"/static/(.*)", ProjectAwareStaticHandler, {"path": STATIC_DIR}),
        (r"/vendor/(.*)", NoCacheStaticFileHandler, {"path": os.path.join(GUI_DIR, "vendor")}),
    ]

    app = tornado.web.Application(routes)
    return app, orch


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "0.0.0.0")

    app, orch = make_app(port=port)
    app.listen(port, address=host)

    # Suppress WebSocketClosedError noise
    _orig_handler = asyncio.get_event_loop().get_exception_handler()
    def _ws_exception_handler(loop, context):
        exc = context.get("exception")
        if exc and "WebSocketClosedError" in type(exc).__name__:
            return
        if _orig_handler:
            _orig_handler(loop, context)
        else:
            loop.default_exception_handler(context)
    asyncio.get_event_loop().set_exception_handler(_ws_exception_handler)

    # Start orchestrator WS broadcast loop
    tornado.ioloop.IOLoop.current().add_callback(_ws_poll_loop, orch)

    print(f"Dorna Workspace server running on {host}:{port}")
    print(f"  Orchestrator:  http://{host}:{port}/orchestrator/")
    print(f"  Scene Builder: http://{host}:{port}/scene-builder/")
    tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()
