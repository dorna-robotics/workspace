# workspace/runtime_server.py
import os
import time
import json
from threading import Thread
from typing import Callable, Any, Optional

import tornado.ioloop
import tornado.web
from tornado import autoreload
import socketio

from workspace.runtime import Runtime

# --------------------------------------------------
# Viewer/Static paths
# --------------------------------------------------
# runtime_server.py is inside:  .../workspace/workspace/workspace/runtime_server.py
# web/ and static/ are here:    .../workspace/workspace/web  and  .../workspace/workspace/static
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WEB_DIR = os.path.join(BASE_DIR, "web")
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
def has_meshurl(state: dict) -> bool:
    return any(isinstance(v, dict) and v.get("meshUrl") for v in state.values())


def merge_into_state(state, payload):
    """
    Safely merges incoming pose frames or snapshots.

    CRITICAL FIX:
    - If previous world_state contains meshUrl, and
      the incoming update does NOT contain meshUrl,
      we KEEP the existing meshUrl.
    """
    for name, spec in payload.items():
        prev = state.get(name, {})

        # preserve meshUrl
        if "meshUrl" in prev and "meshUrl" not in spec:
            spec["meshUrl"] = prev["meshUrl"]

        # preserve optional identity fields if you use them
        if "componentName" in prev and "componentName" not in spec:
            spec["componentName"] = prev["componentName"]
        if "solidName" in prev and "solidName" not in spec:
            spec["solidName"] = prev["solidName"]

        prev.update(spec)
        state[name] = prev


@sio.event
async def connect(sid, environ, auth):
    # If we already have a complete state, send it to this client
    if world_state:
        await sio.emit("scene_update", world_state, room=sid)

    # If we don't yet have meshUrl in state, force Display to resend snapshot
    if not has_meshurl(world_state):
        await sio.emit("request_snapshot")


def _has_meshurl(d: dict) -> bool:
    try:
        return any(isinstance(v, dict) and ("meshUrl" in v) for v in d.values())
    except Exception:
        return False


@sio.event
async def upstream_update(sid, payload):
    merge_into_state(world_state, payload)
    await sio.emit("scene_update", payload)   # back to payload
    return "ok"


@sio.event
async def request_snapshot(sid):
    await sio.emit("request_snapshot", room=sid)


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
                self._workflow_thread_holder["thread"] = self.rt.run_workflow_thread(
                    self.workflow_fn, workspace=self.workspace
                )

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
        self.write({"state": self.rt.state, "last_error": self.rt.status.last_error})


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

            # health
            (r"/healthz", HealthHandler),

            # ✅ ADD THIS
            (r"/config_version", ConfigVersionHandler),

            # viewer app (SPA)  <-- keep this LAST
            (r"/(.*)", tornado.web.StaticFileHandler, {
                "path": self.web_dir,
                "default_filename": "index.html",
            }),
        ]

        self.app = tornado.web.Application(routes, debug=DEV_NOCACHE)

    def run(self):
        self.app.listen(self.port, address=self.host)
        print(f"[runtime] listening at http://127.0.0.1:{self.port}")
        print(" - viewer:", self.web_dir)
        print(" - static:", self.static_dir)
        print(" - DEV_NOCACHE =", DEV_NOCACHE)

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