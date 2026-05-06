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
from workspace.devices import component_device_ids

# --------------------------------------------------
# Viewer/Static paths
# --------------------------------------------------
# runtime_server.py is inside:  .../workspace/workspace/workspace/runtime_server.py
# web/ and static/ are here:    .../workspace/workspace/gui/orchestrator/web  and  .../workspace/workspace/static
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WEB_DIR = os.path.join(BASE_DIR, "gui", "orchestrator", "web")
STATIC_DIR = os.path.join(BASE_DIR, "static")
VENDOR_DIR = os.path.join(BASE_DIR, "gui", "vendor")

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


class FallbackStaticHandler(NoCacheStaticFileHandler):
    """Serves from multiple directories — first match wins."""

    def initialize(self, paths: list[str]):
        self._paths = [p for p in paths if os.path.isdir(p)]
        super().initialize(self._paths[0] if self._paths else "")

    def get_absolute_path(self, root, path):
        for d in self._paths:
            full = os.path.join(d, path)
            if os.path.isfile(full):
                return full
        return super().get_absolute_path(root, path)

    def validate_absolute_path(self, root, absolute_path):
        if os.path.isfile(absolute_path):
            return absolute_path
        return super().validate_absolute_path(root, absolute_path)


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
            self.rt.end()
            # If no workflow thread is running, end has nobody to catch it — kill instead
            th = self._workflow_thread_holder.get("thread")
            if th is None or not th.is_alive():
                self.rt.kill()
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
_status_ws_clients: set = set()

# Captured at server startup so broadcast helpers called from non-IOLoop
# threads (paho's network thread, the workflow thread) can hop back onto
# the correct loop. IOLoop.current() from a foreign thread is unreliable.
_main_ioloop: Optional[tornado.ioloop.IOLoop] = None


class StepWebSocket(tornado.websocket.WebSocketHandler):
    def check_origin(self, origin):
        return True

    def open(self):
        _step_ws_clients.add(self)
        # Send current steps + progress immediately on connect, so a
        # freshly-loaded UI doesn't have to wait for the next emission.
        rt = self._rt
        si = rt.step_info
        try:
            payload = {"steps": (si["steps"] if si else [])}
            if si and si.get("progress", -1) >= 0:
                payload["progress"] = si["progress"]
            self.write_message(json.dumps(payload))
        except Exception:
            pass

    def on_close(self):
        _step_ws_clients.discard(self)

    def initialize(self, rt: Runtime):
        self._rt = rt


def _broadcast_steps(steps: list, progress: int = -1):
    """Called from rt.on_step (workflow thread) — schedule send on IO loop.

    Includes the current progress (0-100, or -1 for unset) alongside the
    steps list so the UI's progress bar updates in real time. Without
    this, progress only reached the UI via the slower HTTP polling and
    a fast final 100% emission could be missed entirely.
    """
    if _main_ioloop is None:
        return
    payload = {"steps": steps}
    if progress is not None and progress >= 0:
        payload["progress"] = progress
    msg = json.dumps(payload)

    def _send():
        for c in list(_step_ws_clients):
            try:
                c.write_message(msg)
            except Exception:
                _step_ws_clients.discard(c)

    _main_ioloop.add_callback(_send)


# --------------------------------------------------
# Status WebSocket — push runtime state changes in real time
# --------------------------------------------------
class StatusWebSocket(tornado.websocket.WebSocketHandler):
    """WS /ws/status — push RTStatus snapshots on every state transition.

    Lets the UI react to state changes (IDLE / RUNNING / PAUSED / etc.)
    and last_error updates within milliseconds, instead of waiting on
    the slower HTTP /status polling. The HTTP endpoint is kept around
    as a fallback for initial fetch and reconnect.
    """

    def check_origin(self, origin):
        return True

    def open(self):
        _status_ws_clients.add(self)
        # Initial snapshot so a freshly-loaded UI doesn't have to wait
        # for the next state transition to populate.
        rt = self._rt
        try:
            self.write_message(json.dumps({
                "state": str(rt._status.state),
                "last_error": rt._status.last_error,
                "job_runs": rt._status.job_runs,
                "job_pauses": rt._status.job_pauses,
                "job_resumes": rt._status.job_resumes,
                "kills": rt._status.kills,
            }))
        except Exception:
            pass

    def on_close(self):
        _status_ws_clients.discard(self)

    def initialize(self, rt: Runtime):
        self._rt = rt


def _broadcast_status(status: dict):
    """Called from rt.on_status (any thread) — schedule send on IO loop."""
    if _main_ioloop is None:
        return
    msg = json.dumps(status)

    def _send():
        for c in list(_status_ws_clients):
            try:
                c.write_message(msg)
            except Exception:
                _status_ws_clients.discard(c)

    _main_ioloop.add_callback(_send)


# --------------------------------------------------
# Device panel — list / recover / release / live state push
# --------------------------------------------------
# Project-scoped device view. Walks workspace.components for their
# `device_ids` declarations (per the DeviceComponent contract in
# docs/device-guide.md §8), intersects with the bus cache held by
# workspace.devices (MQTTOrchestrator), and exposes:
#   GET  /devices               → list of devices this project depends on
#   POST /devices/<id>/recover  → trigger remote recover
#   POST /devices/<id>/release  → trigger remote release
#   WS   /ws/devices            → push device_state events as they happen
_device_ws_clients: set = set()


def _project_device_ids(workspace) -> set[str]:
    """Union of every component's `device_ids` declaration. Empty if none."""
    out: set[str] = set()
    components = getattr(workspace, "components", {}) or {}
    for comp in components.values():
        for did in component_device_ids(comp):
            out.add(did)
    return out


def _project_devices_snapshot(workspace) -> list[dict]:
    """List of device snapshots this project claims, in stable id order.
    Each entry is a dict from MQTTOrchestrator.list_devices() (id, state,
    msg, kind, critical, meta, ts), augmented with `claimed=True`. When
    the bus has no entry for a claimed id (device service not yet up),
    a placeholder with state="down", msg="not on bus" is returned so the
    UI can still show the dependency."""
    devices = getattr(workspace, "devices", None)
    claimed = _project_device_ids(workspace)
    if not claimed:
        return []
    bus = {d["id"]: d for d in (devices.list_devices() if devices else [])}
    out = []
    for did in sorted(claimed):
        if did in bus:
            entry = dict(bus[did])
            entry["claimed"] = True
        else:
            entry = {
                "id": did,
                "state": "down",
                "msg": "not on bus",
                "kind": did.split(":", 1)[0] if ":" in did else "device",
                "critical": True,
                "meta": {},
                "ts": 0.0,
                "online": False,
                "claimed": True,
            }
        out.append(entry)
    return out


class DevicesHandler(tornado.web.RequestHandler):
    """GET /devices → JSON list of project-claimed devices."""

    def initialize(self, workspace):
        self.workspace = workspace

    def get(self):
        self.set_header("Content-Type", "application/json")
        self.set_header("Cache-Control", "no-store")
        self.write(json.dumps({"devices": _project_devices_snapshot(self.workspace)}))


class DeviceCmdHandler(tornado.web.RequestHandler):
    """POST /devices/<id>/<action>  with action ∈ {recover, release}.

    Forwards through workspace.devices (the MQTTOrchestrator) and returns
    the device's reply payload. Refuses to act on devices the project
    doesn't claim — keeps a project from accidentally poking unrelated
    hardware on the bus.
    """

    def initialize(self, workspace):
        self.workspace = workspace

    def post(self, device_id: str, action: str):
        self.set_header("Content-Type", "application/json")
        if action not in ("recover", "release"):
            self.set_status(400)
            self.write(json.dumps({"ok": False, "msg": f"unknown action: {action}"}))
            return

        if device_id not in _project_device_ids(self.workspace):
            self.set_status(403)
            self.write(json.dumps({
                "ok": False,
                "msg": f"device {device_id} is not claimed by this project",
            }))
            return

        devices = getattr(self.workspace, "devices", None)
        if devices is None:
            self.set_status(503)
            self.write(json.dumps({"ok": False, "msg": "device bus unavailable"}))
            return

        # Fire-and-forget: publish the cmd and return immediately. The
        # device's response flows through state events on /ws/devices,
        # which is what the panel actually renders. Blocking the HTTP
        # request on the 30 s reply window is wasted latency and gives
        # the operator no feedback during the wait.
        async_fn = getattr(devices, f"{action}_async", None)
        try:
            if callable(async_fn):
                reply = async_fn(device_id)
            else:
                reply = getattr(devices, action)(device_id)
        except Exception as ex:
            self.set_status(500)
            self.write(json.dumps({"ok": False, "msg": f"{type(ex).__name__}: {ex}"}))
            return

        if isinstance(reply, dict) and reply.get("offline"):
            self.set_status(409)  # Conflict — device service not on bus
        self.write(json.dumps(reply or {"ok": False, "msg": "no reply"}))


class DeviceWebSocket(tornado.websocket.WebSocketHandler):
    """WS /ws/devices — push device_state events to the project page.

    On connect, pushes the full project snapshot so a freshly-loaded page
    sees current state without waiting for the next transition. After
    that, pushes one event per device update from MQTTOrchestrator's
    subscription channel (filtered to claimed ids).
    """

    def check_origin(self, origin):
        return True

    def initialize(self, workspace):
        self.workspace = workspace

    def open(self):
        _device_ws_clients.add(self)
        try:
            for entry in _project_devices_snapshot(self.workspace):
                self.write_message(json.dumps({"type": "device_state", **entry}))
        except Exception:
            pass

    def on_close(self):
        _device_ws_clients.discard(self)


def _broadcast_device_event(workspace, event: dict):
    """Called by MQTTOrchestrator.subscribe (paho's network thread) —
    fan out to project clients. Filters to ids the project actually
    claims so panels stay focused."""
    if not _device_ws_clients or _main_ioloop is None:
        return
    if event.get("id") not in _project_device_ids(workspace):
        return
    payload = json.dumps({"type": "device_state", **event})

    def _send():
        for c in list(_device_ws_clients):
            try:
                c.write_message(payload)
            except Exception:
                _device_ws_clients.discard(c)

    try:
        _main_ioloop.add_callback(_send)
    except Exception:
        pass


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

            # static assets (meshes/textures/etc) — project-local first, library fallback
            (r"/static/(.*)", FallbackStaticHandler, {"paths": [
                os.getcwd(),        # project-local: my_project/CAD/
                self.static_dir,    # library: workspace/static/CAD/
            ]}),

            # shared vendor assets (Three.js, Socket.IO, etc.)
            (r"/vendor/(.*)", NoCacheStaticFileHandler, {"path": VENDOR_DIR}),

            # runtime API
            (r"/cmd", CmdHandler, dict(
                rt=self.rt,
                workflow_fn=self.workflow_fn,
                workflow_thread_holder=self._workflow_thread_holder,
                workspace=self.workspace,
            )),
            (r"/status", StatusHandler, dict(rt=self.rt)),
            (r"/ws/steps", StepWebSocket, dict(rt=self.rt)),
            (r"/ws/status", StatusWebSocket, dict(rt=self.rt)),

            # device panel — see DevicesHandler / DeviceCmdHandler / DeviceWebSocket
            (r"/devices", DevicesHandler, dict(workspace=self.workspace)),
            (r"/devices/([^/]+)/(recover|release)", DeviceCmdHandler, dict(workspace=self.workspace)),
            (r"/ws/devices", DeviceWebSocket, dict(workspace=self.workspace)),

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
        # Wire status push: rt.on_status → broadcast RTStatus snapshots
        # to /ws/status clients. Fires on every state transition + every
        # last_error update, so the UI button labels / state pill /
        # error display refresh in real time instead of waiting on the
        # ~1 Hz HTTP /status polling.
        self.rt.on_status = _broadcast_status

        # Wire device push: workspace.devices (MQTTOrchestrator) → broadcast
        # to all device WS clients. The orchestrator's subscribe runs the
        # callback on paho's network thread; _broadcast_device_event hops
        # back onto the IO loop before writing.
        devices = getattr(self.workspace, "devices", None)
        if devices is not None and hasattr(devices, "subscribe"):
            try:
                devices.subscribe(lambda evt: _broadcast_device_event(self.workspace, evt))
            except Exception:
                pass

        self.app = tornado.web.Application(routes, debug=DEV_NOCACHE)

    def run(self):
        global _main_ioloop
        _main_ioloop = tornado.ioloop.IOLoop.current()
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