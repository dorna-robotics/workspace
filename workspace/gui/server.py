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
    LaunchConfigHandler,
    UpdateKwargsHandler,
    FileUploadHandler,
    StatusWebSocket,
    _ws_poll_loop,
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
  <link rel="icon" type="image/png" href="/vendor/favicon.png"/>
  <style>
    :root {
      --bg: #000000; --surface: #1c1c1e; --surface2: #2c2c2e; --border: #3a3a3c;
      --border2: #2c2c2e; --text: #f5f5f7; --muted: #8e8e93;
      --accent: #0a84ff; --accent-h: #409cff;
      --font: -apple-system, BlinkMacSystemFont, "SF Pro Display", system-ui, sans-serif;
      --radius: 14px;
    }
    [data-theme="light"] {
      --bg: #f2f2f7; --surface: #ffffff; --surface2: #f2f2f7; --border: #d1d1d6;
      --border2: #e5e5ea; --text: #1d1d1f; --muted: #86868b;
      --accent: #007aff; --accent-h: #0063cc;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--font); background: var(--bg); color: var(--text);
      -webkit-font-smoothing: antialiased; min-height: 100vh; display: flex; flex-direction: column;
    }
    a { color: var(--accent); text-decoration: none; }

    /* Topbar */
    .topbar {
      display: flex; align-items: center; gap: 12px;
      padding: 0 20px; height: 54px; min-height: 54px;
      border-bottom: 1px solid var(--border2);
      background: rgba(28,28,30,0.85); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
      flex-shrink: 0;
    }
    [data-theme="light"] .topbar { background: rgba(255,255,255,0.85); }
    .brand-link { display: flex; align-items: center; flex-shrink: 0; opacity: 0.85; transition: opacity 0.15s; }
    .brand-link:hover { opacity: 1; }
    .brand-link img { border-radius: 4px; }
    .topbar-nav { display: flex; gap: 2px; margin-left: 4px; }
    .topbar-nav-link {
      padding: 5px 12px; font-size: 12px; font-weight: 500; color: var(--muted);
      border-radius: 8px; text-decoration: none; transition: color 0.12s, background 0.12s;
    }
    .topbar-nav-link:hover { color: var(--text); background: var(--surface2); }
    .spacer { flex: 1; }
    .btn {
      display: inline-flex; align-items: center; gap: 5px; padding: 5px 8px;
      border-radius: 8px; border: none; background: transparent; color: var(--muted);
      font-family: var(--font); font-size: 12px; cursor: pointer;
      transition: background 0.12s, color 0.12s;
    }
    .btn:hover { background: var(--surface2); color: var(--text); }

    /* Main content */
    .main {
      flex: 1; display: flex; align-items: center; justify-content: center; padding: 40px 20px;
    }
    .container { text-align: center; }
    h1 { font-size: 28px; font-weight: 700; margin-bottom: 8px; letter-spacing: -0.5px; }
    .subtitle { color: var(--muted); font-size: 14px; margin-bottom: 32px; }
    .cards { display: flex; gap: 16px; justify-content: center; flex-wrap: wrap; }
    a.card {
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      width: 200px; height: 140px;
      background: var(--surface); border: 1px solid var(--border2); border-radius: var(--radius);
      text-decoration: none; color: var(--text);
      transition: background 0.15s, border-color 0.15s, transform 0.1s;
    }
    a.card:hover { background: var(--surface2); border-color: var(--accent); transform: translateY(-2px); }
    .card-icon { margin-bottom: 10px; opacity: 0.7; }
    .card-label { font-size: 14px; font-weight: 600; }
    .card-hint { font-size: 11px; color: var(--muted); margin-top: 4px; }
  </style>
</head>
<body>
  <header class="topbar">
    <a href="/" class="brand-link"><img src="/vendor/favicon.png" alt="Dorna" height="22"/></a>
    <nav class="topbar-nav">
      <a href="/orchestrator/" class="topbar-nav-link">Orchestrator</a>
      <a href="/scene-builder/" class="topbar-nav-link">Scene Builder</a>
    </nav>
    <div class="spacer"></div>
    <button class="btn" id="btnFullscreen" title="Fullscreen"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg></button>
    <button class="btn" id="btnTheme" title="Toggle theme"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg></button>
  </header>
  <main class="main">
    <div class="container">
      <h1>Dorna Workspace</h1>
      <p class="subtitle">Select an application</p>
      <div class="cards">
        <a class="card" href="/orchestrator/">
          <div class="card-icon"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg></div>
          <div class="card-label">Orchestrator</div>
          <div class="card-hint">Manage workspaces</div>
        </a>
        <a class="card" href="/scene-builder/">
          <div class="card-icon"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg></div>
          <div class="card-label">Scene Builder</div>
          <div class="card-hint">Design hardware layouts</div>
        </a>
      </div>
    </div>
  </main>
  <script>
    // Theme toggle
    const theme = localStorage.getItem("theme") || "dark";
    if (theme === "light") document.documentElement.setAttribute("data-theme", "light");
    document.getElementById("btnTheme").addEventListener("click", () => {
      const isLight = document.documentElement.getAttribute("data-theme") === "light";
      document.documentElement.setAttribute("data-theme", isLight ? "" : "light");
      localStorage.setItem("theme", isLight ? "dark" : "light");
    });
    // Fullscreen toggle
    document.getElementById("btnFullscreen").addEventListener("click", () => {
      if (!document.fullscreenElement) document.documentElement.requestFullscreen();
      else document.exitFullscreen();
    });
  </script>
</body>
</html>"""


class LandingHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "text/html")
        self.write(LANDING_HTML)


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
        (r"/orchestrator/api/workspace/([^/]+)/launch_config", LaunchConfigHandler, dict(orch=orch)),
        (r"/orchestrator/api/workspace/([^/]+)/kwargs", UpdateKwargsHandler, dict(orch=orch)),
        (r"/orchestrator/api/workspace/([^/]+)/upload/([^/]+)", FileUploadHandler, dict(orch=orch)),

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
        (r"/scene-builder/api/config_version", ConfigVersionHandler),

        # ---- Scene Builder Socket.IO + GUI (catch-all last) ----
        (r"/scene-builder/?", tornado.web.RedirectHandler, {"url": "/scene-builder/index.html"}),
        (r"/scene-builder/socket\.io/(.*)", _socketio.get_tornado_handler(sb_sio)),
        (r"/scene-builder/(.*)", NoCacheStaticFileHandler, {"path": SB_WEB_DIR}),

        # ---- Shared static ----
        (r"/static/(.*)", NoCacheStaticFileHandler, {"path": STATIC_DIR}),
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
