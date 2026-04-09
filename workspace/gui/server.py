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
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", system-ui, sans-serif;
      background: #000; color: #f5f5f7;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh;
    }
    .container { text-align: center; }
    h1 { font-size: 28px; font-weight: 700; margin-bottom: 8px; letter-spacing: -0.5px; }
    p { color: #8e8e93; font-size: 14px; margin-bottom: 32px; }
    .cards { display: flex; gap: 16px; justify-content: center; flex-wrap: wrap; }
    a.card {
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      width: 200px; height: 140px;
      background: #1c1c1e; border: 1px solid #3a3a3c; border-radius: 16px;
      text-decoration: none; color: #f5f5f7;
      transition: background 0.15s, border-color 0.15s, transform 0.1s;
    }
    a.card:hover { background: #2c2c2e; border-color: #0a84ff; transform: translateY(-2px); }
    .card-icon { font-size: 32px; margin-bottom: 10px; opacity: 0.7; }
    .card-label { font-size: 14px; font-weight: 600; }
    .card-hint { font-size: 11px; color: #8e8e93; margin-top: 4px; }
  </style>
</head>
<body>
  <div class="container">
    <h1>Dorna Workspace</h1>
    <p>Select an application</p>
    <div class="cards">
      <a class="card" href="/orchestrator/">
        <div class="card-icon">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
        </div>
        <div class="card-label">Orchestrator</div>
        <div class="card-hint">Manage workspaces</div>
      </a>
      <a class="card" href="/scene-builder/">
        <div class="card-icon">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
        </div>
        <div class="card-label">Scene Builder</div>
        <div class="card-hint">Design hardware layouts</div>
      </a>
    </div>
  </div>
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
