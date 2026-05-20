# workspace/runtime_server.py
import os
import time
import json
from threading import Thread
from typing import Callable, Any, List, Optional

import tornado.ioloop
import tornado.web
import tornado.websocket
from tornado import autoreload
import socketio

from workspace.runtime import Runtime
from workspace.devices import component_device_ids
from workspace.devices.component_contract import component_device_claim

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
            extra_kwargs = data.get("kwargs") or {}
            # Always refresh pending kwargs BEFORE bumping the start
            # token, so the gate-loop reads the new values when it
            # wakes. Without this, the second + later Start clicks
            # within one workspace process would reuse the kwargs
            # frozen at first-Start time.
            self.rt.set_workflow_kwargs(extra_kwargs)
            self.rt.start()
            th = self._workflow_thread_holder.get("thread")
            if th is None or not th.is_alive():
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
    def initialize(self, rt: Runtime, workspace=None):
        self.rt = rt
        self.workspace = workspace

    async def get(self):
        if self.workspace is not None:
            self.write(_status_payload(self.rt, self.workspace))
        else:
            # Back-compat for callers that didn't wire workspace yet.
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
        try:
            self.write_message(json.dumps(_status_payload(self._rt, self._workspace)))
        except Exception:
            pass

    def on_close(self):
        _status_ws_clients.discard(self)

    def initialize(self, rt: Runtime, workspace=None):
        self._rt = rt
        self._workspace = workspace


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
# Schedule WebSocket — live BT plan + execution timeline
# --------------------------------------------------
# Project-scoped channel that streams scheduling events to the GUI's
# Gantt-chart modal: ``schedule`` on each replan (with the full plan),
# ``action_start`` / ``action_end`` and ``swap_start`` / ``swap_end``
# as the BT executes. The Gantt overlays actual wall-time durations on
# top of the predicted schedule so operators can see drift live.
#
# The framework's `bt.launcher.run_protocol` wires `event_publisher`
# to `_broadcast_schedule_event` below; without a runtime server (e.g.
# headless tests) the publisher is None and these hooks are no-ops.
_schedule_ws_clients: set = set()
# Full history of schedule + runtime events for the current run, so a
# client that connects mid-run (or reloads its page) sees the same
# Gantt the operator was looking at — including every slice that has
# already finished. Reset when a fresh workflow starts (signalled by
# a ``schedule`` event with ``replan_id == 1``).
_schedule_history: List[dict] = []         # every schedule event so far
_schedule_runtime_events: List[dict] = []  # every action/swap start/end so far


class ScheduleWebSocket(tornado.websocket.WebSocketHandler):
    """WS /ws/schedule — push schedule + execution events to the GUI."""

    def check_origin(self, origin):
        return True

    def open(self):
        _schedule_ws_clients.add(self)
        # Replay the full history so the new client sees the same
        # chart the operator was looking at, including any slices
        # that already finished.
        try:
            for ev in _schedule_history:
                self.write_message(json.dumps(ev))
            for ev in _schedule_runtime_events:
                self.write_message(json.dumps(ev))
        except Exception:
            pass

    def on_close(self):
        _schedule_ws_clients.discard(self)


def _broadcast_schedule_event(event: dict) -> None:
    """Publisher passed into ``run_protocol`` via ``event_publisher``.

    Called from arbitrary threads (BT worker thread, replanner). Cache
    every event for late-joining clients, then hop onto the IO loop to
    push to every connected websocket.

    Cache lifecycle: a ``schedule`` event with ``replan_id == 1`` is
    the launcher's "fresh workflow" signal — clear the history so the
    operator's view starts from the new run's first slice. Subsequent
    schedule events append (slice appended to slice).
    """
    global _schedule_history, _schedule_runtime_events
    etype = event.get("type")
    if etype == "schedule":
        if event.get("replan_id") == 1:
            _schedule_history = []
            _schedule_runtime_events = []
        _schedule_history.append(event)
    elif etype in ("action_start", "action_end", "swap_start", "swap_end"):
        _schedule_runtime_events.append(event)

    if _main_ioloop is None or not _schedule_ws_clients:
        return
    msg = json.dumps(event)

    def _send():
        for c in list(_schedule_ws_clients):
            try:
                c.write_message(msg)
            except Exception:
                _schedule_ws_clients.discard(c)

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


def _project_device_claims(workspace) -> dict[str, str]:
    """Aggregated per-id sim/real claim across every component.

    Aggregation rule: ``"real"`` wins over ``"sim"``. If any component
    using a device claims it real, the project's net claim is real —
    auto-pause must respect the strictest claim, never get fooled into
    skipping a critical-down by a single sim claim from an unrelated
    component. Devices that no component claims (or that all claim
    real) end up with ``"real"`` in the dict.
    """
    out: dict[str, str] = {}
    components = getattr(workspace, "components", {}) or {}
    for comp in components.values():
        for did in component_device_ids(comp):
            claim = component_device_claim(comp, did) or "real"
            existing = out.get(did)
            # ``real`` is strictest — if any component claims real,
            # the net claim is real. Otherwise ``sim`` only sticks
            # when nothing has overridden it with ``real``.
            if existing == "real":
                continue
            out[did] = claim
    # Anything declared but not claimed → default to real.
    for did in _project_device_ids(workspace):
        out.setdefault(did, "real")
    return out


def project_device_claim_resolver(workspace):
    """Build a ``Callable[[str], str]`` that returns the live claim mode.

    Wires the orchestrator's auto-pause logic into the workspace's
    aggregated claim map. Always reads fresh from components, so a
    runtime ``core.simulation(True)`` toggle takes effect on the next
    bus event without explicit invalidation.
    """
    def _resolve(device_id: str) -> str:
        return _project_device_claims(workspace).get(device_id, "real")
    return _resolve


def _compute_devices_summary(workspace) -> Optional[dict]:
    """Aggregated device-health view for this project. Used by the
    dashboard pill and the Start/Resume confirmation gate.

    Each declared device falls into exactly ONE bucket so totals add
    up cleanly:

        sim         publisher self-flag OR project claim is "sim"
                    (never blocks; never enters down/recovering/etc.)
        recovering  state="recovering" (sim takes precedence)
        offline     online=False (publisher gone — no live truth)
        down        state="down", online=True, not sim
        ok          state="ok", online=True, not sim

    A device is **blocking** iff it is critical, not sim, and either
    down or offline. ``blocking_ids`` lists those ids — the
    Start/Resume gate uses them verbatim in the confirm dialog so the
    operator sees exactly what would crash.

    Returns ``None`` (no pill, no gate) when the project declares
    zero devices — there's nothing to summarize.
    """
    snapshot = _project_devices_snapshot(workspace)
    if not snapshot:
        return None
    out = {
        "total": len(snapshot),
        "ok": 0,
        "down": 0,
        "recovering": 0,
        "offline": 0,
        "sim": 0,
        "blocking": 0,
        "blocking_ids": [],
    }
    for d in snapshot:
        is_sim = bool(d.get("sim")) or d.get("claim") == "sim"
        critical = bool(d.get("critical", True))
        online = d.get("online", True)
        state = d.get("state", "down")
        # Bucketing — order matters; first match wins.
        if is_sim:
            out["sim"] += 1
        elif state == "recovering":
            out["recovering"] += 1
        elif not online:
            out["offline"] += 1
            if critical:
                out["blocking"] += 1
                out["blocking_ids"].append(d["id"])
        elif state == "down":
            out["down"] += 1
            if critical:
                out["blocking"] += 1
                out["blocking_ids"].append(d["id"])
        else:
            out["ok"] += 1
    return out


def _state_value(rt) -> str:
    """Return the runtime's state as the bare string value.

    ``RTState`` is a ``(str, Enum)`` so JSON encoders serialize it
    correctly on their own (``json.dumps(RTState.IDLE)`` → ``"IDLE"``)
    — but ``str(RTState.IDLE)`` returns ``"RTState.IDLE"`` (Enum's
    qualified name), which silently breaks every consumer that
    string-compares against ``"IDLE"`` / ``"PAUSED"`` / etc. The
    orchestrator's auto-kill (in ``broadcast_status``) is the most
    visible victim: workflow finishes RUNNING→IDLE, but ``cur ==
    "IDLE"`` becomes ``"RTSTATE.IDLE" == "IDLE"`` → False → no
    auto-kill → card shows Start instead of Launch.

    Always extract via ``.value`` (or fall back to ``str()`` for
    non-Enum inputs in tests). One helper, no surprises.
    """
    s = getattr(rt._status, "state", None)
    if s is None:
        s = getattr(rt, "state", "")
    return s.value if hasattr(s, "value") else str(s)


def _status_payload(rt, workspace) -> dict:
    """Single source of truth for the workspace's status snapshot.

    Used by HTTP ``/status``, the WS ``/ws/status`` initial push, and
    every status broadcast. Carries runtime state + the device-summary
    so the orchestrator dashboard's pill and the Start/Resume gate
    both have everything they need from one payload.

    ``run_started_at`` / ``run_finished_at`` are sourced from the
    runtime itself (set on RTState transitions) — not the orchestrator's
    polling loop — so the dashboard's "Up" timer reflects the actual
    moment the workflow started running, with no race against
    ``broadcast_status``.
    """
    run_started = getattr(rt, "run_started_at", None)
    run_finished = getattr(rt, "run_finished_at", None)
    # Compute uptime_s here so every push (HTTP /status, /ws/status,
    # broadcast_status fan-out) carries a fresh value. Without it, the
    # client falls back to the previous HTTP poll's uptime_s during the
    # WS-push window between state transitions and the next poll —
    # producing a flicker (Up shows 14, drops to 13 on RUNNING→IDLE
    # WS push, snaps back to 14 on next HTTP poll). One source of
    # truth, computed alongside the timestamps that power it.
    if run_started:
        if run_finished and run_finished >= run_started:
            uptime_s = run_finished - run_started
        else:
            uptime_s = time.time() - run_started
    else:
        uptime_s = None
    out: dict = {
        "state": _state_value(rt),
        "last_error": rt._status.last_error,
        "job_runs": rt._status.job_runs,
        "job_pauses": rt._status.job_pauses,
        "job_resumes": rt._status.job_resumes,
        "kills": rt._status.kills,
        "run_started_at": run_started,
        "run_finished_at": run_finished,
        "uptime_s": uptime_s,
    }
    si = getattr(rt, "step_info", None)
    if si:
        out["step"] = si
    summary = _compute_devices_summary(workspace)
    if summary is not None:
        out["devices_summary"] = summary
    return out


def _project_devices_snapshot(workspace) -> list[dict]:
    """List of device snapshots this project claims, in stable id order.
    Each entry is a dict from MQTTOrchestrator.list_devices() (id, state,
    msg, kind, critical, sim, meta, ts), augmented with ``claimed=True``
    and ``claim`` (the project's sim/real annotation). When the bus has
    no entry for a claimed id (device service not yet up), a placeholder
    with state="down", msg="not on bus" is returned so the UI can still
    show the dependency."""
    devices = getattr(workspace, "devices", None)
    claimed = _project_device_ids(workspace)
    if not claimed:
        return []
    bus = {d["id"]: d for d in (devices.list_devices() if devices else [])}
    claims = _project_device_claims(workspace)
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
                "sim": False,
                "meta": {},
                "ts": 0.0,
                "online": False,
                "claimed": True,
            }
        # Project-level claim mode (real/sim). Independent of bus.sim:
        # bus.sim is what the *publisher* says about itself; claim is
        # what *this project* says about how it uses the device. The
        # panel shows a SIM pill if either is true.
        entry["claim"] = claims.get(did, "real")
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
    claims so panels stay focused. Augments each event with the
    project's claim mode so a single push carries everything the
    panel needs to render the SIM pill correctly."""
    if not _device_ws_clients or _main_ioloop is None:
        return
    did = event.get("id")
    if did not in _project_device_ids(workspace):
        return
    augmented = dict(event)
    augmented["claim"] = _project_device_claims(workspace).get(did, "real")
    payload = json.dumps({"type": "device_state", **augmented})

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
            (r"/status", StatusHandler, dict(rt=self.rt, workspace=self.workspace)),
            (r"/ws/steps", StepWebSocket, dict(rt=self.rt)),
            (r"/ws/status", StatusWebSocket, dict(rt=self.rt, workspace=self.workspace)),
            (r"/ws/schedule", ScheduleWebSocket),

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

        # Wire status push: rt.on_status → broadcast a FULL status
        # payload (runtime state + devices_summary) to /ws/status
        # clients. Fires on every state transition + every last_error
        # update. Device-state changes also push status (see device
        # subscription below) so the dashboard's pill and gate stay
        # in lockstep with reality without extra fetches.
        def _push_full_status(_unused_runtime_status=None):
            try:
                payload = _status_payload(self.rt, self.workspace)
            except Exception:
                # Failure to assemble must NOT break runtime callbacks.
                # Fall back to runtime-only payload if devices_summary
                # couldn't be computed. _state_value preserves the
                # bare-string form so dashboard string-compares work.
                payload = {
                    "state": _state_value(self.rt),
                    "last_error": self.rt._status.last_error,
                }
            _broadcast_status(payload)
        self.rt.on_status = _push_full_status

        # Wire device push: workspace.devices (MQTTOrchestrator) → broadcast
        # to all device WS clients. The orchestrator's subscribe runs the
        # callback on paho's network thread; _broadcast_device_event hops
        # back onto the IO loop before writing. We ALSO trigger a status
        # push so the dashboard's devices_summary stays in lockstep.
        devices = getattr(self.workspace, "devices", None)
        if devices is not None and hasattr(devices, "subscribe"):
            try:
                def _on_device_event(evt):
                    _broadcast_device_event(self.workspace, evt)
                    # Refresh the status payload so dashboard pill +
                    # Start/Resume gate see updated counts within the
                    # same WS-push latency as the device row itself.
                    _push_full_status()
                devices.subscribe(_on_device_event)
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