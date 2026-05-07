"""Orchestrator HTTP/WS server — entry point + barrel.

Top-level glue. The actual orchestrator logic lives in:

  * ``workspace_info`` — WorkspaceInfo + log/run-history helpers
  * ``orchestrator``   — Orchestrator class, thread pools, registry
  * ``handlers``       — HTTP RequestHandler classes + auth
  * ``websockets``     — WebSocket handlers + broadcast loop

This file:

  * Re-exports every public name external callers (notably
    ``gui.server``) used to import from the old monolithic
    ``gui.orchestrator.server``. Keeping the barrel means existing
    imports keep working without churn.
  * Defines ``NoCacheStaticFileHandler`` (specific to this server,
    not orchestrator-domain logic).
  * Defines ``OrchestratorHTTPServer`` — the Tornado app that wires
    routes to handlers and runs the broadcast loop.
"""
from __future__ import annotations

import asyncio
import os
from threading import Thread

import tornado.ioloop
import tornado.web

# Re-exports — public API surface for external callers (e.g. gui/server.py
# imports from here). Keep names in sync; if you rename one of these,
# update the import block in gui/server.py too.
from gui.orchestrator.workspace_info import (  # noqa: F401
    MAX_LOG_BYTES,
    WorkspaceInfo,
    _free_port,
    _log_pump,
    _now_str,
    _truncate_log_if_needed,
)
from gui.orchestrator.orchestrator import (  # noqa: F401
    ORCH_TOKEN,
    Orchestrator,
    REG_PATH,
    _cmd_pool,
    _status_pool,
)
from gui.orchestrator.handlers import (  # noqa: F401
    AddWorkspaceHandler,
    AuthedHandler,
    FileUploadHandler,
    LaunchConfigHandler,
    RemoveWorkspaceHandler,
    UpdateKwargsHandler,
    WorkspaceCmdHandler,
    WorkspaceDeviceCmdHandler,
    WorkspaceDevicesHandler,
    WorkspaceLogsHandler,
    WorkspaceStatusHandler,
    WorkspacesListHandler,
    WorkspacesStatusHandler,
)
from gui.orchestrator.websockets import (  # noqa: F401
    StatusWebSocket,
    WorkspaceLogsWebSocket,
    _start_status_subscriber,
    _stop_status_subscriber,
    _ws_clients,
    _ws_poll_loop,
    _ws_prev_states,
    _workspace_status_subscribers,
    broadcast_status,
)


class NoCacheStaticFileHandler(tornado.web.StaticFileHandler):
    """Static handler that disables every layer of HTTP caching. Used
    for the admin GUI assets so a fresh deploy is visible on the next
    page load without operators having to hard-refresh."""

    def set_extra_headers(self, path):
        self.set_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.set_header("Pragma", "no-cache")
        self.set_header("Expires", "0")

    def compute_etag(self):
        return None


class OrchestratorHTTPServer:
    """Tornado app: routes + the periodic status broadcast.

    Standalone entry point for running the orchestrator on its own
    (``python -m gui.orchestrator.server``). The unified GUI server
    in ``gui.server`` reuses ``Orchestrator`` and the handler classes
    directly — see the barrel imports above — so everything here is
    just the wiring for the standalone case.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 9000):
        self.orch = Orchestrator(port=port)
        self.orch.load_registry()

        self.host = host
        self.port = port

        base_dir = os.path.dirname(os.path.abspath(__file__))
        web_dir = os.path.join(base_dir, "web")

        self.app = tornado.web.Application([
            # ---- GUI (serve ./web) ----
            (r"/", tornado.web.RedirectHandler, {"url": "/web/admin/index.html"}),
            (r"/web/(.*)", NoCacheStaticFileHandler, {"path": web_dir}),

            # ---- API ----
            (r"/workspaces/status", WorkspacesStatusHandler, dict(orch=self.orch)),
            (r"/workspaces", WorkspacesListHandler, dict(orch=self.orch)),
            (r"/add_workspace", AddWorkspaceHandler, dict(orch=self.orch)),
            (r"/remove_workspace", RemoveWorkspaceHandler, dict(orch=self.orch)),
            (r"/workspace/([^/]+)/cmd", WorkspaceCmdHandler, dict(orch=self.orch)),
            (r"/workspace/([^/]+)/status", WorkspaceStatusHandler, dict(orch=self.orch)),
            (r"/workspace/([^/]+)/logs", WorkspaceLogsHandler, dict(orch=self.orch)),
            (r"/workspace/([^/]+)/launch_config", LaunchConfigHandler, dict(orch=self.orch)),
            (r"/workspace/([^/]+)/kwargs", UpdateKwargsHandler, dict(orch=self.orch)),
            (r"/workspace/([^/]+)/upload/([^/]+)", FileUploadHandler, dict(orch=self.orch)),

            # ---- WebSocket live status ----
            (r"/ws/status", StatusWebSocket, dict(orch=self.orch)),
        ])

    def run(self) -> None:
        self.app.listen(self.port, address=self.host)

        # Suppress noisy WebSocketClosedError from Tornado's internal tasks.
        # These fire when a client disconnects mid-write, which happens
        # constantly in normal browser navigation; the default handler
        # logs them at ERROR level and clutters the orchestrator log.
        _orig_handler = asyncio.get_event_loop().get_exception_handler()

        def _ws_exception_handler(loop, context):
            exc = context.get("exception")
            if exc and "WebSocketClosedError" in type(exc).__name__:
                return  # silently ignore
            if _orig_handler:
                _orig_handler(loop, context)
            else:
                loop.default_exception_handler(context)
        asyncio.get_event_loop().set_exception_handler(_ws_exception_handler)

        # Start WS broadcast loop
        tornado.ioloop.IOLoop.current().add_callback(_ws_poll_loop, self.orch)
        print(f"Orchestrator server running on {self.host}:{self.port}")
        print(f"GUI: http://{self.host}:{self.port}/web/orchestrator.html")
        if ORCH_TOKEN:
            print("Auth: ENABLED (X-Orch-Token required for add/cmd/remove)")
        else:
            print("Auth: disabled (set ORCH_TOKEN to enable)")
        print("Registry:", REG_PATH)
        tornado.ioloop.IOLoop.current().start()

    def run_in_thread(self) -> Thread:
        t = Thread(target=self.run, daemon=True)
        t.start()
        return t


if __name__ == "__main__":
    server = OrchestratorHTTPServer(port=5000)
    server.run()
