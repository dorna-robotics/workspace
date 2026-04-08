# workspace/runtime_server.py
import os
import time
import json
from threading import Thread
from typing import Callable, Any, Optional

import tornado.ioloop
import tornado.web
import tornado.websocket
from tornado import autoreload
import socketio

from workspace.runtime import Runtime

# --------------------------------------------------
# Viewer/Static paths
# --------------------------------------------------
# runtime_server.py is inside:  .../workspace/workspace/workspace/runtime_server.py
# web/ and static/ are here:    .../workspace/workspace/orchestrator/web  and  .../workspace/workspace/static
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WEB_DIR = os.path.join(BASE_DIR, "orchestrator", "web")
STATIC_DIR = os.path.join(BASE_DIR, "static")

DEV_NOCACHE = os.environ.get("DEV_NOCACHE", "1") == "1"


# --------------------------------------------------
# Socket.IO server (viewer realtime)
# --------------------------------------------------
sio = socketio.AsyncServer(
    async_mode="tornado",
    cors_allowed_origins="*",
    allow_upgrades=False,  # websocket only
    ping_interval=20,
    ping_timeout=20,
    max_http_buffer_size=50 * 1024 * 1024,
)

# GLOBAL world state (viewer)
world_state = {}
def _has_meshurl(d: dict) -> bool:
    try:
        return any(isinstance(v, dict) and ("meshUrl" in v) for v in d.values())
    except Exception:
        return False


def merge_into_state(state, payload):
    """
    Safely merges incoming pose frames or snapshots.
    Preserves meshUrl and identity fields from previous state if the
    incoming update doesn't include them.
    """
    for name, spec in payload.items():
        prev = state.get(name, {})

        if "meshUrl" in prev and "meshUrl" not in spec:
            spec["meshUrl"] = prev["meshUrl"]
        if "componentName" in prev and "componentName" not in spec:
            spec["componentName"] = prev["componentName"]
        if "solidName" in prev and "solidName" not in spec:
            spec["solidName"] = prev["solidName"]

        prev.update(spec)
        state[name] = prev


@sio.event
async def connect(sid, environ, auth):
    if world_state:
        await sio.emit("scene_update", world_state, room=sid)
    if not _has_meshurl(world_state):
        await sio.emit("request_snapshot")


@sio.event
async def upstream_update(sid, payload):
    merge_into_state(world_state, payload)
    await sio.emit("scene_update", payload)   # back to payload
    return "ok"


@sio.event
async def request_snapshot(sid):
    # Broadcast to all clients so the Display picks it up and resends the snapshot
    await sio.emit("request_snapshot")


@sio.event
async def disconnect(sid):
    print("disconnect", sid)


# --------------------------------------------------
# Tornado handlers
# --------------------------------------------------
class NoCacheStaticFileHandler(tornado.web.StaticFileHandler):
    def set_extra_headers(self, path):
        if DEV_NOCACHE:
            self.set_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.set_header("Pragma", "no-cache")
            self.set_header("Expires", "0")

    def compute_etag(self):
        return None if DEV_NOCACHE else super().compute_etag()


class ConfigVersionHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "application/json")
        # simple cache-buster; doesn’t need to be fancy
        self.write(json.dumps({"version": str(int(time.time()))}))


class HealthHandler(tornado.web.RequestHandler):
    def get(self):
        self.write("ok")


class CmdHandler(tornado.web.RequestHandler):
    def initialize(self, rt: Runtime, workflow_fn: Callable[..., Any],
                   workflow_thread_holder: dict, workspace: Any):
        self.rt = rt
        self.workflow_fn = workflow_fn
        self._workflow_thread_holder = workflow_thread_holder
        self.workspace = workspace

    async def post(self):
        try:
            data = json.loads(self.request.body.decode("utf-8"))
            cmd = data.get("cmd", "").lower()
        except Exception:
            self.set_status(400)
            self.write({"error": "Invalid JSON"})
            return

        if cmd == "start":
            self.rt.start()
            th = self._workflow_thread_holder.get("thread")
            if th is None or not th.is_alive():
                extra_kwargs = data.get("kwargs") or {}
                self._workflow_thread_holder["thread"] = self.rt.run_workflow_thread(
                    self.workflow_fn, workspace=self.workspace, **extra_kwargs
                )

        elif cmd == "end":
            th = self._workflow_thread_holder.get("thread")
            if th is None or not th.is_alive():
                # No workflow running — just kill directly
                self.rt.kill()
            else:
                self.rt.end()
        elif cmd == "pause":
            self.rt.pause()
        elif cmd == "resume":
            self.rt.resume()
        elif cmd == "kill":
            self.rt.kill()
        else:
            self.set_status(400)
            self.write({"error": "Unknown cmd"})
            return

        self.write({"status": "ok", "state": self.rt.state})


class StatusHandler(tornado.web.RequestHandler):
    def initialize(self, rt: Runtime):
        self.rt = rt

    async def get(self):
        out = {"state": self.rt.state, "last_error": self.rt.status.last_error}
        si = self.rt.step_info
        if si:
            out["step"] = si
        self.write(out)


# --------------------------------------------------
# Step WebSocket — push step updates to dashboard
# --------------------------------------------------
_step_ws_clients: set = set()


class StepWebSocket(tornado.websocket.WebSocketHandler):
    def check_origin(self, origin):
        return True

    def open(self):
        _step_ws_clients.add(self)
        # Send current steps immediately on connect
        rt = self._rt
        si = rt.step_info
        try:
            self.write_message(json.dumps({"steps": si["steps"] if si else []}))
        except Exception:
            pass

    def on_close(self):
        _step_ws_clients.discard(self)

    def initialize(self, rt: Runtime):
        self._rt = rt


def _broadcast_steps(steps: list):
    """Called from rt.on_step (workflow thread) — schedule send on IO loop."""
    ioloop = tornado.ioloop.IOLoop.current()
    msg = json.dumps({"steps": steps})

    def _send():
        for c in list(_step_ws_clients):
            try:
                c.write_message(msg)
            except Exception:
                _step_ws_clients.discard(c)

    ioloop.add_callback(_send)


# --------------------------------------------------
# Unified server
# --------------------------------------------------
class RuntimeServer:
    """
    Single server:
    - Runtime API: /cmd, /status
    - Viewer UI:   / (index.html)
    - Static:      /static/*
    - Socket.IO:   /socket.io/
    """

    def __init__(
        self,
        runtime: Runtime,
        workflow_fn: Callable[..., Any],
        workspace: Any,
        host: str = "0.0.0.0",
        port: Optional[int] = None,
        web_dir: Optional[str] = None,
        static_dir: Optional[str] = None,
    ):
        self.rt = runtime
        self.workflow_fn = workflow_fn
        self.workspace = workspace
        self.host = host
        self._workflow_thread_holder = {}

        self.web_dir = web_dir or WEB_DIR
        self.static_dir = static_dir or STATIC_DIR

        # port
        if port is None:
            port = int(getattr(workspace, "port", 8000))
        self.port = int(port)

        routes = [
            # realtime (viewer)
            (r"/socket.io/", socketio.get_tornado_handler(sio)),

            # static assets (meshes/textures/etc)
            (r"/static/(.*)", NoCacheStaticFileHandler, {"path": self.static_dir}),

            # runtime API
            (r"/cmd", CmdHandler, dict(
                rt=self.rt,
                workflow_fn=self.workflow_fn,
                workflow_thread_holder=self._workflow_thread_holder,
                workspace=self.workspace,
            )),
            (r"/status", StatusHandler, dict(rt=self.rt)),
            (r"/ws/steps", StepWebSocket, dict(rt=self.rt)),

            # health
            (r"/healthz", HealthHandler),

            # ✅ ADD THIS
            (r"/config_version", ConfigVersionHandler),

            # viewer app (SPA)  <-- keep this LAST
            (r"/(.*)", NoCacheStaticFileHandler, {
                "path": self.web_dir,
                "default_filename": "index.html",
            }),
        ]

        # Wire step push: rt.on_step → broadcast to all step WS clients
        self.rt.on_step = _broadcast_steps

        self.app = tornado.web.Application(routes, debug=DEV_NOCACHE)

    def run(self):
        self.app.listen(self.port, address=self.host)
        print(f"[runtime] listening at http://127.0.0.1:{self.port}")
        print(" - viewer:", self.web_dir)
        print(" - static:", self.static_dir)
        print(" - DEV_NOCACHE =", DEV_NOCACHE)

        # Suppress WebSocketClosedError noise from Tornado internals
        import asyncio
        _orig = asyncio.get_event_loop().get_exception_handler()
        def _suppress_ws_closed(loop, ctx):
            exc = ctx.get("exception")
            if exc and "WebSocketClosedError" in type(exc).__name__:
                return
            if _orig:
                _orig(loop, ctx)
            else:
                loop.default_exception_handler(ctx)
        asyncio.get_event_loop().set_exception_handler(_suppress_ws_closed)

        # autoreload for dev
        for p in (self.web_dir, self.static_dir):
            if os.path.exists(p):
                autoreload.watch(p)
        autoreload.start()

        tornado.ioloop.IOLoop.current().start()

    def run_in_thread(self):
        t = Thread(target=self.run, daemon=True)
        t.start()
        return t