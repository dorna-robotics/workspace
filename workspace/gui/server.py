"""
Unified GUI server — serves orchestrator and scene builder on one port.

    http://<ip>:5000/orchestrator/     → orchestrator dashboard
    http://<ip>:5000/scene-builder/    → scene builder
    http://<ip>:5000/                  → the projects page (gui/home)

Usage:
    cd workspace
    sudo python3 gui/server.py
"""

import json
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

from workspace.version import version as _ws_version

# Once per process — the top bar's Updates panel reads it (/home/api/version).
_WS_VERSION = _ws_version()

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
    SceneHandler,
    ProjectScreenFileHandler,
    UpdateKwargsHandler,
    FileUploadHandler,
    ProjectFilesHandler,
    ProjectFilesSocket,
    ProjectUploadHandler,
    StatusWebSocket,
    _ws_poll_loop,
    WorkspaceDevicesHandler,
    WorkspaceDeviceCmdHandler,
    ProjectVersionHandler,
    SystemHandler,
    SystemLogHandler,
    SystemSummaryHandler,
)

# ---------------------------------------------------------------------------
# Import scene builder (triggers dorna2 patches + component scanning)
# ---------------------------------------------------------------------------
from gui.scene_builder.server import (
    SchedulePreviewHandler,
    sio as sb_sio,
    PerfHandler,
    CatalogHandler,
    CategoriesHandler,
    TypeMetaHandler,
    InstantiateHandler,
    InstantiateBatchHandler,
    RailsHandler,
    SaveConfigHandler,
    SetProjectHandler,
    ProjectBundleHandler,
    SolveRefHandler,
    ResetHandler,
    RecipeIKHandler,
    ReplayListHandler,
    ReplayFileHandler,
)
import socketio as _socketio

# Account (device-code login) + the on-device AI assistant (builder C)
from gui.account import routes as account_routes
from gui.ai.handlers import routes as ai_routes

# ---------------------------------------------------------------------------
# Main page — the projects page (gui/home)
# ---------------------------------------------------------------------------
from gui.home.handlers import home_routes


# What this unit is — /etc/dorna/unit.json, written per unit type by the
# upgrade (os/setup.sh), never guessed here: `links` (where Dorna Lab and
# the vision server are) and `system` (the app that owns the System page);
# the top bar's Updates panel (vendor/nav.js) shows the unit from it. Read per request, so an upgrade's new
# declaration needs no restart; {} before the upgrade has written it.
UNIT_DECLARATION = "/etc/dorna/unit.json"


class UnitHandler(tornado.web.RequestHandler):
    def get(self):
        try:
            with open(UNIT_DECLARATION) as f:
                unit = json.load(f)
        except FileNotFoundError:
            unit = {}
        self.set_header("Cache-Control", "no-store")
        self.write(unit)


class ProjectAwareStaticHandler(NoCacheStaticFileHandler):
    """Serves static files — the open project first, then library static/.
    A ``CAD/<type>.glb`` request resolves to the project component's own
    folder, ``components/<type>/<type>.glb`` (runtime_server
    .component_mesh_path — one rule for both servers)."""
    def get_absolute_path(self, root, path):
        from gui.scene_builder.server import _project_path
        if _project_path:
            project_file = os.path.join(_project_path, path)
            if os.path.isfile(project_file):
                return project_file
            from workspace.runtime_server import component_mesh_path
            mesh = component_mesh_path(_project_path, path)
            if mesh:
                return mesh
        return super().get_absolute_path(root, path)

    def validate_absolute_path(self, root, absolute_path):
        if os.path.isfile(absolute_path):
            return absolute_path
        return super().validate_absolute_path(root, absolute_path)


class ConfigVersionHandler(tornado.web.RequestHandler):
    def get(self):
        import time as _time
        self.write({"version": str(int(_time.time()))})


# ---- Developer mode (builder D): /dev/ page + API ----
from gui.devmode.handlers import routes as _devmode_routes


# ---------------------------------------------------------------------------
# Build unified Tornado app
# ---------------------------------------------------------------------------
def make_app(port=5000):
    orch = Orchestrator(port=port)
    orch.load_registry()

    routes = [
        # ---- Main page: projects (gui/home — builder B) ----
        *home_routes(orch, _WS_VERSION),
        (r"/unit.json", UnitHandler),

        # ---- Account + on-device AI (gui/account, gui/ai) ----
        *account_routes(),
        *ai_routes(orch),

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
        (r"/orchestrator/api/workspace/([^/]+)/scene", SceneHandler, dict(orch=orch)),
        (r"/orchestrator/api/workspace/([^/]+)/setup/(.*)", ProjectScreenFileHandler, dict(orch=orch, key="setup")),
        (r"/orchestrator/api/workspace/([^/]+)/replan/(.*)", ProjectScreenFileHandler, dict(orch=orch, key="replan")),
        (r"/orchestrator/api/workspace/([^/]+)/kwargs", UpdateKwargsHandler, dict(orch=orch)),
        (r"/orchestrator/api/workspace/([^/]+)/upload/([^/]+)", FileUploadHandler, dict(orch=orch)),
        # A project folder's files: bytes over HTTP (download / zip /
        # preview, streamed upload); listing + actions + live changes
        # over the folder socket below (fslive.py).
        (r"/orchestrator/api/workspace/([^/]+)/files/([^/]+)/upload", ProjectUploadHandler, dict(orch=orch)),
        (r"/orchestrator/api/workspace/([^/]+)/files/([^/]+)", ProjectFilesHandler, dict(orch=orch)),
        (r"/orchestrator/ws/files/([^/]+)", ProjectFilesSocket, dict(orch=orch)),
        (r"/orchestrator/api/workspace/([^/]+)/devices", WorkspaceDevicesHandler, dict(orch=orch)),
        # The Version panel: git state vs the remote, and Get latest (project_git.py).
        (r"/orchestrator/api/workspace/([^/]+)/version", ProjectVersionHandler, dict(orch=orch)),
        (r"/orchestrator/api/system", SystemHandler, dict(orch=orch)),
        (r"/orchestrator/api/system/log", SystemLogHandler, dict(orch=orch)),
        (r"/orchestrator/api/system/summary", SystemSummaryHandler, dict(orch=orch)),
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
        (r"/scene-builder/api/instantiate_batch", InstantiateBatchHandler),
        (r"/scene-builder/api/rails", RailsHandler),
        (r"/scene-builder/api/save_config", SaveConfigHandler),
        (r"/scene-builder/api/set_project", SetProjectHandler),
        (r"/scene-builder/api/project_bundle", ProjectBundleHandler),
        (r"/scene-builder/api/solve_ref", SolveRefHandler),
        (r"/scene-builder/api/schedule_preview", SchedulePreviewHandler),
        (r"/scene-builder/api/perf", PerfHandler),
        (r"/scene-builder/api/reset", ResetHandler),
        (r"/scene-builder/api/recipe_ik", RecipeIKHandler),
        (r"/scene-builder/api/replays", ReplayListHandler),
        (r"/scene-builder/api/replay", ReplayFileHandler),
        (r"/scene-builder/api/config_version", ConfigVersionHandler),

        # ---- Scene Builder Socket.IO + GUI (catch-all last) ----
        (r"/scene-builder/?", tornado.web.RedirectHandler, {"url": "/scene-builder/index.html"}),
        (r"/scene-builder/socket\.io/(.*)", _socketio.get_tornado_handler(sb_sio)),
        (r"/scene-builder/(.*)", NoCacheStaticFileHandler, {"path": SB_WEB_DIR}),

        # ---- Developer mode (/dev/; builder D — gui/devmode/) ----
        *_devmode_routes(orch),          # page, API and its static files (all login-gated)

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

    # The orchestrator's handle on this IOLoop (Orchestrator.loop)
    orch.loop = tornado.ioloop.IOLoop.current()
    # Start orchestrator WS broadcast loop
    orch.loop.add_callback(_ws_poll_loop, orch)

    print(f"Dorna Workspace server running on {host}:{port}")
    print(f"  Orchestrator:  http://{host}:{port}/orchestrator/")
    print(f"  Scene Builder: http://{host}:{port}/scene-builder/")
    tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()
