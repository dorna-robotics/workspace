# workspace/runtime_server.py
import os
import time
import json
from pathlib import Path
from threading import Thread
from typing import Callable, Any, List, Optional

import yaml
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

# Cache control. Default OFF — static assets (GLB models, JS, CSS)
# get normal browser caching, which makes page reloads ~10× faster
# on a typical scene because 49 MB of CAD models stop being
# re-downloaded every time. Combined with the stable
# ``_CONFIG_VERSION`` below, browsers serve from disk on reload.
# Set ``DEV_NOCACHE=1`` when actively editing GUI / static files in
# the running tree and you want every refresh to pull fresh content.
DEV_NOCACHE = os.environ.get("DEV_NOCACHE", "0") == "1"

# Cache-bust query string appended to every GLB / static URL by the
# viewer (see web/index.html ``versioned()``). Stable per server
# process — same value on every request — so the browser sees
# matching URLs across reloads and serves from its disk cache.
# Restart the server to invalidate (or override with
# ``CONFIG_VERSION`` env var, e.g. set to your git SHA in
# production).
_CONFIG_VERSION = os.environ.get("CONFIG_VERSION") or str(int(time.time()))


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
        # A delete tombstone removes the entry outright. Merging it kept
        # dead objects in the cache forever AND poisoned re-used names:
        # the stale ``delete`` flag survived the merge, so a viewer that
        # connected later would drop the freshly re-added object.
        if isinstance(spec, dict) and spec.get("delete"):
            state.pop(name, None)
            continue
        prev = state.get(name, {})

        if "meshUrl" in prev and "meshUrl" not in spec:
            spec["meshUrl"] = prev["meshUrl"]
        if "componentName" in prev and "componentName" not in spec:
            spec["componentName"] = prev["componentName"]
        if "solidName" in prev and "solidName" not in spec:
            spec["solidName"] = prev["solidName"]

        prev.update(spec)
        state[name] = prev


# Live socket.io clients. Both edges are logged with the resulting count
# so a disconnect can be read for what it is: routine churn (a viewer that
# reconnects a second later) or a client that actually stayed gone. Only
# the disconnect edge used to be printed, which made every ordinary
# reconnect look like a one-way failure in the run log.
_clients: set = set()


def _client_log(edge: str, sid: str) -> None:
    n = len(_clients)
    print(f"{edge} {sid} ({n} client{'' if n == 1 else 's'})")


@sio.event
async def connect(sid, environ, auth):
    _clients.add(sid)
    _client_log("connect", sid)
    if world_state:
        await sio.emit("scene_update", world_state, room=sid)
    if not _has_meshurl(world_state):
        await sio.emit("request_snapshot")


@sio.event
async def upstream_update(sid, payload):
    merge_into_state(world_state, payload)
    if _recorder["fp"] is not None:
        _record_line({"t": round(time.time() - _recorder["t0"], 4),
                      "u": payload})
    await sio.emit("scene_update", payload)   # back to payload
    return "ok"


@sio.event
async def request_snapshot(sid):
    # Broadcast to all clients so the Display picks it up and resends the snapshot
    await sio.emit("request_snapshot")


@sio.event
async def disconnect(sid):
    _clients.discard(sid)
    _client_log("disconnect", sid)


# --------------------------------------------------
# Replay recorder — same contract as workspace/server.py
# --------------------------------------------------
# One JSONL per recording in the PROJECT's core/ folder:
#   {"meta": ...} then {"t": 0, "snap": world_state} then {"t", "u"}
# per upstream delta. The viewer's record button drives it via
# /record/start|stop|status (it probes status and hides where absent).
_recorder = {"fp": None, "path": None, "t0": None, "frames": 0}
_record_core_dir = None  # set at RuntimeServer init from the project


def _record_line(obj):
    fp = _recorder["fp"]
    if fp is None:
        return
    try:
        fp.write(json.dumps(obj, separators=(",", ":")) + "\n")
        _recorder["frames"] += 1
    except Exception as e:
        print("[record] write failed, stopping:", e)
        _record_stop()


def _record_start():
    if _recorder["fp"] is not None:
        return {"ok": False, "error": "already recording"}
    if not _record_core_dir:
        return {"ok": False, "error": "no project core/ dir known"}
    os.makedirs(_record_core_dir, exist_ok=True)
    name = time.strftime("replay_%Y%m%d_%H%M%S.jsonl")
    path = os.path.join(_record_core_dir, name)
    try:
        fp = open(path, "w")
    except Exception as e:
        return {"ok": False, "error": f"cannot open {path}: {e}"}
    _recorder.update(fp=fp, path=path, t0=time.time(), frames=0)
    _record_line({"meta": {"started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "project_core": _record_core_dir}})
    _record_line({"t": 0.0, "snap": world_state})
    print(f"[record] started -> {path}")
    return {"ok": True, "path": path, "name": name}


def _record_stop():
    fp, path, t0, n = (_recorder["fp"], _recorder["path"],
                       _recorder["t0"], _recorder["frames"])
    _recorder.update(fp=None, path=None, t0=None, frames=0)
    if fp is None:
        return {"ok": False, "error": "not recording"}
    try:
        fp.close()
    except Exception:
        pass
    secs = round(time.time() - t0, 1) if t0 else 0
    print(f"[record] stopped: {path} ({n} lines, {secs}s)")
    return {"ok": True, "path": path, "frames": n, "seconds": secs}


def _record_status():
    on = _recorder["fp"] is not None
    return {"ok": True, "recording": on, "path": _recorder["path"],
            "seconds": round(time.time() - _recorder["t0"], 1) if on else 0,
            "frames": _recorder["frames"], "project_core": _record_core_dir}


class RecordHandler(tornado.web.RequestHandler):
    def get(self, action):
        if action != "status":
            self.set_status(405)
            self.write({"ok": False, "error": "GET is status only"})
            return
        self.write(_record_status())

    def post(self, action):
        out = (_record_start() if action == "start"
               else _record_stop() if action == "stop"
               else {"ok": False, "error": f"unknown action {action}"})
        if not out.get("ok") and self.get_status() < 400:
            self.set_status(409)
        self.write(out)


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


class HmiStaticFileHandler(NoCacheStaticFileHandler):
    """The project's own hmi/ folder.

    The pendant page is served by the orchestrator GUI, on a different
    port from this runtime server, so every read of a project screen is
    cross-origin. A <link> stylesheet does not care, but ``fetch()`` (the
    HTML shape) and ``import()`` (the JS shape) both refuse to hand the
    body to the page without these headers. Read-only project UI files
    on the same LAN as an unauthenticated runtime server — nothing here
    is more exposed than the rest of the API.
    """

    def set_extra_headers(self, path):
        super().set_extra_headers(path)
        self.set_header("Access-Control-Allow-Origin", "*")

    def options(self, *args):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.set_status(204)
        self.finish()


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
        # Stable per server process (see ``_CONFIG_VERSION``). Same
        # value on every request → the viewer's ``versioned()`` builds
        # identical GLB URLs across reloads → browser cache hits.
        self.write(json.dumps({"version": _CONFIG_VERSION}))


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

        elif cmd == "park":
            self.rt.park()
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
# rt.op push cadence (ms). The store is written at workflow speed; the
# socket is fed at this rate.
OP_FLUSH_MS = 100

# Parsed hmi.j2 for this workspace — set once in RuntimeServer.__init__.
_PENDANT_SPEC: dict = {"widgets": [], "warnings": []}

_step_ws_clients: set = set()
_status_ws_clients: set = set()
_op_ws_clients: set = set()

# Multiplexed all-channel client set. Used by ``AllWebSocket`` at
# ``/ws`` — replaces /ws/steps + /ws/status + /ws/devices +
# /ws/operator_actions for clients that want a single channel.
# Legacy endpoints stay live (the orchestrator's per-workspace
# subscriber + the 3D viewer still use ``/ws/status``); the mux is
# additive, not destructive. See docs/internal/ws-multiplexing-plan.md
# for the design + migration triggers.
_all_ws_clients: set = set()

# Captured at server startup so broadcast helpers called from non-IOLoop
# threads (paho's network thread, the workflow thread) can hop back onto
# the correct loop. IOLoop.current() from a foreign thread is unreliable.
_main_ioloop: Optional[tornado.ioloop.IOLoop] = None


def _broadcast_dual(
    legacy_clients: set,
    legacy_msg: str,
    mux_type: str,
    mux_payload: dict,
) -> None:
    """Fan an event to legacy WS clients (bare message) AND multiplexed
    /ws clients (with ``{type, payload}`` envelope) in a single IO loop
    callback. Skip the mux serialize when no mux clients are connected.
    Honours each mux client's subscription filter via ``c.wants(type)``.
    """
    if _main_ioloop is None:
        return
    mux_msg = (
        json.dumps({"type": mux_type, "payload": mux_payload})
        if _all_ws_clients else None
    )

    def _send():
        for c in list(legacy_clients):
            try:
                c.write_message(legacy_msg)
            except Exception:
                legacy_clients.discard(c)
        if mux_msg is not None:
            for c in list(_all_ws_clients):
                try:
                    if c.wants(mux_type):
                        c.write_message(mux_msg)
                except Exception:
                    _all_ws_clients.discard(c)

    _main_ioloop.add_callback(_send)


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
    _broadcast_dual(_step_ws_clients, json.dumps(payload), "step_state", payload)


def _presentable_result(result):
    """Coerce ANY operator-action return into something the UI can show —
    centrally, so components return their native object (a ``Scan``, a
    ``Reading``, a float, …) and never need a hand-written ``str()``
    wrapper or a ``*_once`` method.

    * JSON-native (str/int/float/bool/list/dict/None) → returned as-is.
    * "Empty" (None / "" / []) → ``None`` (caller treats as "no result").
    * Anything else (a dataclass, an object) → its ``str()``.

    Returns ``None`` when there's nothing meaningful to present."""
    if result is None or result == "" or result == []:
        return None
    if isinstance(result, (str, int, float, bool, list, dict)):
        return result
    try:
        return str(result)
    except Exception:
        return None


def _log_operator_result(rt, component_name: str, method_name: str, result) -> None:
    """Print an operator action's return value to stdout so it lands in the
    operator's LOGS panel (which tails the project's stdout), letting the
    operator see what the action produced — e.g. the barcode a Detect read,
    the grams a Weigh measured. One place for every operator action.

    Goes to Logs, NOT the Steps timeline: an operator action is out-of-band
    (not a workflow step), so it belongs in the raw log stream. The result
    is coerced centrally via ``_presentable_result`` — components return
    their native object, no per-method ``str()`` wrapper. Unpresentable /
    empty → a trivial "(no result)" line. Never raises. (``rt`` is unused;
    kept in the signature so callers don't change.)"""
    try:
        label = f"{component_name}.{method_name}"
        shown = _presentable_result(result)
        print(f"[OPERATOR] {label}: {'(no result)' if shown is None else shown}", flush=True)
    except Exception:
        pass


def _run_operator_action(rt, fn):
    """Run an operator action under the runtime's operator marking, so its
    rt.*-touching calls pass the pause gate instead of hanging until Resume
    (see ``Runtime.operator_call``). Runs on an executor thread."""
    if rt is None:
        return fn()
    with rt.operator_call():
        return fn()


# --------------------------------------------------
# Status WebSocket — push runtime state changes in real time
# --------------------------------------------------
# ── HMI declaration (hmi/hmi.j2) ────────────────────────────────────
# The project DECLARES widgets; the platform renders them. Widgets come
# from a catalog the pendant owns — a project never ships markup, so it
# cannot drift from the design system.
HMI_WIDGETS = ("state", "stat", "progress")


def _project_dir(workspace):
    """The project folder, or None.

    Prefers an explicitly declared ``workspace.project_dir`` (set by the
    project's main.py) and only then falls back to deriving it from the
    scene paths. The guess breaks for SUBPROJECTS sharing one scene:
    with ``scene: [../scene/core_1000.j2, ...]`` the first scene path
    resolves into the PARENT's scene/ folder, so every subproject
    guessed the parent — and got the parent's pendant, hmi/methods
    library and replay core/. An attribute (not a RuntimeServer kwarg)
    so project repos setting it keep working on older platforms that
    simply ignore it."""
    declared = getattr(workspace, "project_dir", None)
    if declared:
        try:
            return Path(declared).resolve()
        except Exception:
            pass
    try:
        paths = getattr(workspace, "config_paths", None) or []
        if not paths:
            return None
        proj = Path(paths[0]).resolve().parent
        return proj.parent if proj.name == "scene" else proj
    except Exception:
        return None


def _load_pendant_spec(workspace) -> dict:
    """Resolve the project's ``pendant:`` declaration.

    Two shapes, and the FILE is the primary one:

      pendant: hmi/pendant.html  → the project ships its own screen. The
                                platform hosts it (shadow-scoped, design
                                tokens inherited) and binds rt.op values
                                into it. Nothing about that screen lives
                                in the platform.
      pendant: hmi/hmi.j2        → the built-in widget list, for a project
                                that wants a default screen with no
                                front-end work at all.

    Returns ``{"kind": "file"|"widgets", ...}``. Never raises and never
    blocks a launch: a run must start even when its display is broken.
    For the widget shape, an unknown widget name is dropped WITH a
    warning (a typo must not silently render nothing); binding keys are
    not checked, since a key appears only once its action runs.
    """
    out = {"kind": "widgets", "widgets": [], "warnings": []}
    try:
        proj = _project_dir(workspace)
        if proj is None:
            return out
        launch_path = proj / "launch.yaml"
        if not launch_path.is_file():
            return out
        launch = yaml.safe_load(launch_path.read_text()) or {}
        rel = launch.get("pendant")
        if not rel:
            # ``hmi:`` and ``params:`` were renamed to ``pendant:`` and
            # ``setup:`` — same meaning, clearer names (they used to read
            # as synonyms of ``kwargs:``). Say so instead of silently
            # dropping the screen.
            if launch.get("hmi"):
                out["warnings"].append(
                    "launch.yaml: `hmi:` was renamed to `pendant:` — the "
                    "screen is NOT loaded until you rename it")
            return out                      # no declaration → default pendant
        f = proj / str(rel)
        if not f.is_file():
            out["warnings"].append(f"pendant file not found: {rel}")
            return out

        # A project-supplied screen: hand the pendant its URL and let it
        # host the thing. The platform never parses or ships this file's
        # contents — it is the project's UI, served from the project.
        if f.suffix.lower() in (".html", ".htm", ".js"):
            out["kind"] = "file"
            out["src"] = f"/hmi/{Path(rel).name}"
            out["kind_hint"] = "js" if f.suffix.lower() == ".js" else "html"
            css = f.with_suffix(".css")      # beside the screen, not in the root
            if css.is_file():
                out["css"] = f"/hmi/{css.name}"
            return out

        text = f.read_text()
        if str(rel).endswith(".j2") or "{%" in text or "{{" in text:
            from jinja2 import Template
            text = Template(text).render()
        data = yaml.safe_load(text) or {}
        widgets = data.get("hmi", data) if isinstance(data, dict) else data
        if isinstance(widgets, dict):
            widgets = [widgets]
        if not isinstance(widgets, list):
            out["warnings"].append("hmi file must be a list of widgets")
            return out
        for w in widgets:
            if not isinstance(w, dict) or not w.get("widget"):
                out["warnings"].append(f"skipped malformed entry: {w!r}")
                continue
            name = str(w["widget"])
            if name not in HMI_WIDGETS:
                out["warnings"].append(
                    f"unknown widget {name!r} — known: {', '.join(HMI_WIDGETS)}")
                continue
            out["widgets"].append(w)
    except Exception as ex:
        out["warnings"].append(f"{type(ex).__name__}: {ex}")
    return out


class OpWebSocket(tornado.websocket.WebSocketHandler):
    """WS /ws/op — current operator-facing values (``rt.op``).

    A VALUE channel, not a timeline: each message carries the keys that
    changed since the last one. New clients get a full snapshot first
    (``snapshot: true``) so a freshly-opened pendant is never blank,
    and every message carries a monotonic ``rev`` so a client can spot
    a gap and resync rather than sit on a stale reading.
    """

    def check_origin(self, origin):
        return True

    def initialize(self, rt: Runtime):
        self._rt = rt

    def open(self):
        _op_ws_clients.add(self)
        try:
            self.write_message(json.dumps(self._rt.op_snapshot()))
        except Exception:
            pass

    def on_close(self):
        _op_ws_clients.discard(self)


def _broadcast_op(payload: dict) -> None:
    _broadcast_dual(_op_ws_clients, json.dumps(payload), "op_state", payload)


def _op_flush(rt: Runtime) -> None:
    """Drain pending value changes and push them.

    Called on a fixed cadence rather than per ``rt.op()`` call: a hot
    loop writing values must cost the socket a bounded number of small
    messages per second, not one per write. Last-write-wins, so a slow
    client loses intermediate values (correct for a current-value
    channel) and never causes queue growth.

    The pending set is a SEND QUEUE, so it is drained every tick even
    with nobody connected — the values themselves live in the store and
    a connecting client gets them in its snapshot. Keeping them queued
    instead would grow across an unwatched run and hand the first
    client a delta duplicating the snapshot it just received.
    """
    try:
        delta = rt.op_drain()
    except Exception:
        return
    if delta and (_op_ws_clients or _all_ws_clients):
        _broadcast_op(delta)


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
    _broadcast_dual(_status_ws_clients, json.dumps(status), "runtime_status", status)


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

    if _main_ioloop is None:
        return
    if not _schedule_ws_clients and not _all_ws_clients:
        return
    msg = json.dumps(event)  # legacy /ws/schedule clients
    mux_msg = (
        json.dumps({"type": "schedule_event", "payload": event})
        if _all_ws_clients else None
    )

    def _send():
        for c in list(_schedule_ws_clients):
            try:
                c.write_message(msg)
            except Exception:
                _schedule_ws_clients.discard(c)
        if mux_msg is not None:
            for c in list(_all_ws_clients):
                try:
                    if c.wants("schedule_event"):
                        c.write_message(mux_msg)
                except Exception:
                    _all_ws_clients.discard(c)

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
_op_actions_ws_clients: set = set()
# Operator actions currently executing, keyed "component.method". An action
# runs on a worker thread (so the IOLoop stays free); this guards against a
# second invocation of the same action piling on while the first is still in
# flight (e.g. mashing Enable, which would overlap the cylinder animation).
_op_actions_inflight: set = set()


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


# Declared-but-never-published watchdog: a claimed-real device that
# stays off the bus is almost always broker topology — every machine
# defaulting DEVICE_MQTT_HOST=localhost gives each host its own
# isolated bus — not a dead device. Say so in the panel and once,
# loudly, in the log (device-guide §8).
_NOT_ON_BUS_WARN_SEC = 20.0
_not_on_bus_since: dict = {}
_not_on_bus_warned: set = set()


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
            _not_on_bus_since.pop(did, None)
            _not_on_bus_warned.discard(did)
        else:
            first = _not_on_bus_since.setdefault(did, time.time())
            msg = "not on bus"
            if (claims.get(did, "real") == "real"
                    and time.time() - first > _NOT_ON_BUS_WARN_SEC):
                msg = ("not on bus — no publisher seen; check "
                       "DEVICE_MQTT_HOST / broker reachability")
                if did not in _not_on_bus_warned:
                    _not_on_bus_warned.add(did)
                    print(f"[devices] {did}: declared by this project but no bus "
                          f"publisher after {int(_NOT_ON_BUS_WARN_SEC)}s — likely "
                          f"broker topology: DEVICE_MQTT_HOST defaults to localhost "
                          f"on every machine, so each host gets an ISOLATED bus. "
                          f"Point every workspace host and device unit at ONE "
                          f"shared broker (device-guide §8).")
            entry = {
                "id": did,
                "state": "down",
                "msg": msg,
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


def _operator_actions_snapshot(workspace) -> list[dict]:
    """List every operator action exposed by every component, in
    stable component-name order. Each entry: ``{component, label,
    method}``. Components that don't declare ``operator_actions``
    contribute nothing.
    """
    from workspace.components.operator_actions import component_operator_actions
    out: list[dict] = []
    components = getattr(workspace, "components", {}) or {}
    for name in sorted(components):
        comp = components[name]
        for action in component_operator_actions(comp):
            entry = {
                "component": name,
                "label":     action["label"],
                "method":    action["method"],
            }
            if action.get("icon"):
                entry["icon"] = action["icon"]
            if action.get("group"):
                entry["group"] = action["group"]
            out.append(entry)
    return out


class OperatorActionsWebSocket(tornado.websocket.WebSocketHandler):
    """WS /ws/operator_actions — list + invoke over a single pre-opened
    socket. Sub-millisecond per click since there's no HTTP handshake
    on the hot path.

    Client → server messages:
      ``{"type": "invoke", "component": "...", "method": "..."}``
          Two safety gates:
              1. method must appear in the component's declared
                 ``operator_actions`` list (stops attribute-guess attacks).
              2. runtime must not be RUNNING (out-of-band ops mid-run
                 would race the workflow).
          Reply: ``{"type": "invoke_result", "component", "method",
                    "ok": bool, "msg"?, "result"?}``

    Server → client messages (also sent on connect):
      ``{"type": "actions", "actions": [...]}``  — current snapshot
          of operator actions from all components.
    """

    def initialize(self, workspace):
        self.workspace = workspace

    def check_origin(self, origin):
        return True

    def open(self):
        _op_actions_ws_clients.add(self)
        # Push the snapshot immediately so the panel can render
        # without an extra fetch round-trip.
        self.write_message(json.dumps({
            "type":    "actions",
            "actions": _operator_actions_snapshot(self.workspace),
        }))

    def on_close(self):
        _op_actions_ws_clients.discard(self)

    async def on_message(self, raw):
        try:
            msg = json.loads(raw)
        except Exception:
            return
        if msg.get("type") != "invoke":
            return
        component_name = str(msg.get("component", "") or "")
        method_name    = str(msg.get("method", "") or "")
        await self._invoke(component_name, method_name)

    async def _invoke(self, component_name: str, method_name: str):
        from workspace.components.operator_actions import component_operator_actions

        def reply(ok: bool, msg: str = "", result=None):
            payload = {
                "type":      "invoke_result",
                "component": component_name,
                "method":    method_name,
                "ok":        ok,
            }
            if msg:    payload["msg"] = msg
            if ok and isinstance(result, (str, int, float, bool, list, dict, type(None))):
                payload["result"] = result
            try:
                self.write_message(json.dumps(payload))
            except Exception:
                pass

        components = getattr(self.workspace, "components", {}) or {}
        comp = components.get(component_name)
        if comp is None:
            reply(False, f"unknown component: {component_name}")
            return

        declared = {a["method"] for a in component_operator_actions(comp)}
        if method_name not in declared:
            reply(False, f"{component_name}.{method_name} is not an operator action")
            return

        rt = getattr(self.workspace, "rt", None)
        state = (getattr(rt, "state", "") or "").upper() if rt is not None else ""
        if state in ("RUNNING", "ACTIVE"):
            reply(False, "cannot run operator actions while workflow is running")
            return

        # An operator action may block (animation, sleep, a motion). Run it
        # on a worker thread so the IOLoop stays responsive — otherwise the
        # whole server (WS + HTTP + 3D viewer) freezes for the action's
        # duration. The check-and-add below runs on the IOLoop before the
        # await, so it's race-free.
        key = f"{component_name}.{method_name}"
        if key in _op_actions_inflight:
            reply(False, f"{key} is already running")
            return
        _op_actions_inflight.add(key)
        try:
            loop = tornado.ioloop.IOLoop.current()
            result = await loop.run_in_executor(None, _run_operator_action, rt, getattr(comp, method_name))
        except Exception as ex:
            reply(False, f"{type(ex).__name__}: {ex}")
            return
        finally:
            _op_actions_inflight.discard(key)

        reply(True, result=result)
        _log_operator_result(rt, component_name, method_name, result)


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


# --------------------------------------------------
# Multiplexed WebSocket — single channel for steps + status + devices
# + operator_actions. See docs/internal/ws-multiplexing-plan.md.
# Legacy endpoints (/ws/steps, /ws/status, /ws/devices,
# /ws/operator_actions) stay live for back-compat — the orchestrator's
# per-workspace subscriber + the 3D viewer still use /ws/status, and
# any external monitoring should keep working unchanged.
# --------------------------------------------------
class AllWebSocket(tornado.websocket.WebSocketHandler):
    """WS /ws — multiplexed channel pushing all event types as
    ``{type, payload}`` envelopes.

    Server → client envelope types:
      ``step_state``         — same payload shape /ws/steps sends.
      ``runtime_status``     — same payload shape /ws/status sends.
      ``device_state``       — single device update.
      ``devices_snapshot``   — full devices snapshot (on connect + scene change).
      ``operator_actions``   — full op-actions snapshot (on connect + scene change).
      ``invoke_result``      — reply to a client-side ``invoke``.

    Client → server envelope types:
      ``invoke``             — ``{type, payload: {component, method}}``.
                               Same safety gates as
                               :class:`OperatorActionsWebSocket._invoke`.

    A client wanting to ignore a category just doesn't dispatch on its
    type — there's no required subscribe step. (Server-side
    subscription filtering is a future addition; see the plan doc.)
    """

    def check_origin(self, origin):
        return True

    def initialize(self, rt: Runtime, workspace):
        self._rt = rt
        self.workspace = workspace
        # Per-client subscription filter. ``None`` means "all types"
        # (back-compat: a client that doesn't send a subscribe message
        # gets every envelope). A set means "only these types."
        # Mutated by ``subscribe`` / ``unsubscribe`` client→server
        # messages; read by ``wants(etype)`` on every broadcast.
        self._subs: Optional[set] = None

    def wants(self, etype: str) -> bool:
        """True iff this client is currently subscribed to ``etype``."""
        return self._subs is None or etype in self._subs

    def open(self):
        _all_ws_clients.add(self)
        # Initial snapshots — same payloads each legacy endpoint sends
        # on its open(), wrapped in the envelope.
        try:
            # Steps + progress.
            si = self._rt.step_info
            step_payload = {"steps": (si["steps"] if si else [])}
            if si and si.get("progress", -1) >= 0:
                step_payload["progress"] = si["progress"]
            self._send("step_state", step_payload)

            # Runtime status.
            self._send("runtime_status", _status_payload(self._rt, self.workspace))

            # Operator values — full snapshot, same shape /ws/op sends.
            self._send("op_state", self._rt.op_snapshot())

            # HMI declaration — what this project wants displayed. Sent
            # once on connect; it is static for the life of the run.
            self._send("pendant_spec", _PENDANT_SPEC)

            # Devices — bulk snapshot in one envelope (more efficient
            # than N device_state envelopes on connect).
            self._send("devices_snapshot", {
                "devices": _project_devices_snapshot(self.workspace),
            })

            # Operator actions.
            self._send("operator_actions", {
                "actions": _operator_actions_snapshot(self.workspace),
            })

            # Schedule history — replay every schedule + action/swap
            # event from the current run so a freshly-opened Gantt
            # modal shows the same chart the operator was looking at
            # (same shape ScheduleWebSocket replays on open).
            for ev in _schedule_history:
                self._send("schedule_event", ev)
            for ev in _schedule_runtime_events:
                self._send("schedule_event", ev)
        except Exception:
            pass

    def on_close(self):
        _all_ws_clients.discard(self)

    async def on_message(self, raw):
        try:
            env = json.loads(raw)
        except Exception:
            return
        etype = env.get("type")
        payload = env.get("payload") or {}
        if etype == "invoke":
            await self._invoke(
                str(payload.get("component", "") or ""),
                str(payload.get("method", "") or ""),
            )
        elif etype == "subscribe":
            # ``{"type":"subscribe", "payload":{"types":["step_state",
            # "runtime_status"]}}`` — opt into specific event types
            # only. First subscribe message replaces the implicit
            # "all" default with an explicit allowlist.
            types = payload.get("types") or []
            if not isinstance(types, list):
                return
            if self._subs is None:
                self._subs = set()
            for t in types:
                if isinstance(t, str):
                    self._subs.add(t)
        elif etype == "unsubscribe":
            types = payload.get("types") or []
            if not isinstance(types, list):
                return
            if self._subs is None:
                # Currently "all" — materialize the implicit set so we
                # can remove from it. Keep ``invoke_result`` always on
                # since it's the reply path for client→server invokes.
                self._subs = {
                    "step_state", "runtime_status", "device_state",
                    "devices_snapshot", "operator_actions",
                    "schedule_event", "invoke_result",
                }
            for t in types:
                if isinstance(t, str) and t != "invoke_result":
                    self._subs.discard(t)

    def _send(self, etype: str, payload: dict) -> None:
        # Respect the per-client subscription filter for initial-
        # snapshot sends too. ``invoke_result`` is always allowed —
        # filtering away the reply to a client's own invoke would be
        # confusing.
        if etype != "invoke_result" and not self.wants(etype):
            return
        try:
            self.write_message(json.dumps({"type": etype, "payload": payload}))
        except Exception:
            pass

    async def _invoke(self, component_name: str, method_name: str) -> None:
        """Same gates + dispatch as OperatorActionsWebSocket._invoke.
        Reply is a unicast ``invoke_result`` envelope to this client.
        The method runs on a worker thread so a blocking action (the
        cylinder animation, a sleep, a motion) never freezes the IOLoop
        — this is the channel the project page actually uses."""
        from workspace.components.operator_actions import component_operator_actions

        def reply(ok: bool, msg: str = "", result=None) -> None:
            p = {
                "component": component_name,
                "method":    method_name,
                "ok":        ok,
            }
            if msg:
                p["msg"] = msg
            if ok:
                shown = _presentable_result(result)
                if shown is not None:
                    p["result"] = shown
            self._send("invoke_result", p)

        components = getattr(self.workspace, "components", {}) or {}
        comp = components.get(component_name)
        if comp is None:
            reply(False, f"unknown component: {component_name}")
            return

        declared = {a["method"] for a in component_operator_actions(comp)}
        if method_name not in declared:
            reply(False, f"{component_name}.{method_name} is not an operator action")
            return

        rt = getattr(self.workspace, "rt", None)
        state = (getattr(rt, "state", "") or "").upper() if rt is not None else ""
        if state in ("RUNNING", "ACTIVE"):
            reply(False, "cannot run operator actions while workflow is running")
            return

        # Off the IOLoop, with an in-flight guard against pile-up — see
        # OperatorActionsWebSocket._invoke for the full rationale.
        key = f"{component_name}.{method_name}"
        if key in _op_actions_inflight:
            reply(False, f"{key} is already running")
            return
        _op_actions_inflight.add(key)
        try:
            loop = tornado.ioloop.IOLoop.current()
            result = await loop.run_in_executor(None, _run_operator_action, rt, getattr(comp, method_name))
        except Exception as ex:
            reply(False, f"{type(ex).__name__}: {ex}")
            return
        finally:
            _op_actions_inflight.discard(key)

        reply(True, result=result)
        _log_operator_result(rt, component_name, method_name, result)


def _broadcast_scene_changed(workspace):
    """Push fresh snapshots to all clients of the panels whose content
    derives from ``workspace.components`` — namely:

      * /ws/devices          (re-snapshot of the device list)
      * /ws/operator_actions (re-snapshot of the operator-actions list)
      * /ws (multiplexed)    (one envelope per panel type)

    Called by ``Workspace._notify_scene_changed`` after a successful
    ``add_component`` / ``remove_component``. Best-effort: a missing
    IOLoop or a dead client never blocks the caller.
    """
    if _main_ioloop is None:
        return

    devices_snap = _project_devices_snapshot(workspace) if (_device_ws_clients or _all_ws_clients) else None
    actions_snap = _operator_actions_snapshot(workspace) if (_op_actions_ws_clients or _all_ws_clients) else None

    # Legacy bare-shape messages (each panel has its own pre-existing
    # message format the legacy clients expect).
    device_legacy = (
        json.dumps({"type": "snapshot", "devices": devices_snap})
        if _device_ws_clients and devices_snap is not None else None
    )
    op_legacy = (
        json.dumps({"type": "actions", "actions": actions_snap})
        if _op_actions_ws_clients and actions_snap is not None else None
    )
    # Mux envelopes (same payloads, wrapped).
    device_mux = (
        json.dumps({"type": "devices_snapshot", "payload": {"devices": devices_snap}})
        if _all_ws_clients and devices_snap is not None else None
    )
    op_mux = (
        json.dumps({"type": "operator_actions", "payload": {"actions": actions_snap}})
        if _all_ws_clients and actions_snap is not None else None
    )

    def _send():
        if device_legacy is not None:
            for c in list(_device_ws_clients):
                try:
                    c.write_message(device_legacy)
                except Exception:
                    _device_ws_clients.discard(c)
        if op_legacy is not None:
            for c in list(_op_actions_ws_clients):
                try:
                    c.write_message(op_legacy)
                except Exception:
                    _op_actions_ws_clients.discard(c)
        if device_mux is not None or op_mux is not None:
            for c in list(_all_ws_clients):
                try:
                    if device_mux is not None and c.wants("devices_snapshot"):
                        c.write_message(device_mux)
                    if op_mux is not None and c.wants("operator_actions"):
                        c.write_message(op_mux)
                except Exception:
                    _all_ws_clients.discard(c)

    _main_ioloop.add_callback(_send)


def _broadcast_device_event(workspace, event: dict):
    """Called by MQTTOrchestrator.subscribe (paho's network thread) —
    fan out to project clients. Filters to ids the project actually
    claims so panels stay focused. Augments each event with the
    project's claim mode so a single push carries everything the
    panel needs to render the SIM pill correctly."""
    if (not _device_ws_clients and not _all_ws_clients) or _main_ioloop is None:
        return
    did = event.get("id")
    if did not in _project_device_ids(workspace):
        return
    augmented = dict(event)
    augmented["claim"] = _project_device_claims(workspace).get(did, "real")
    # Legacy clients get the flat ``{type:"device_state", ...}`` shape
    # they've always seen; mux clients get the envelope.
    legacy_msg = json.dumps({"type": "device_state", **augmented})
    mux_msg = (
        json.dumps({"type": "device_state", "payload": augmented})
        if _all_ws_clients else None
    )

    def _send():
        for c in list(_device_ws_clients):
            try:
                c.write_message(legacy_msg)
            except Exception:
                _device_ws_clients.discard(c)
        if mux_msg is not None:
            for c in list(_all_ws_clients):
                try:
                    if c.wants("device_state"):
                        c.write_message(mux_msg)
                except Exception:
                    _all_ws_clients.discard(c)

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
            (r"/record/(start|stop|status)", RecordHandler),

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
            (r"/ws/op", OpWebSocket, dict(rt=self.rt)),
            (r"/ws/schedule", ScheduleWebSocket),

            # device panel — see DevicesHandler / DeviceCmdHandler / DeviceWebSocket
            (r"/devices", DevicesHandler, dict(workspace=self.workspace)),
            (r"/devices/([^/]+)/(recover|release)", DeviceCmdHandler, dict(workspace=self.workspace)),
            (r"/ws/devices", DeviceWebSocket, dict(workspace=self.workspace)),

            # operator actions — WS-only (list + invoke on one socket)
            (r"/ws/operator_actions", OperatorActionsWebSocket, dict(workspace=self.workspace)),

            # Multiplexed channel — steps + status + devices + operator_actions
            # on one socket. The admin project page uses this; legacy endpoints
            # above remain for back-compat (orchestrator subscriber + 3D viewer
            # still use /ws/status). See docs/internal/ws-multiplexing-plan.md.
            (r"/ws", AllWebSocket, dict(rt=self.rt, workspace=self.workspace)),

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

        # Serve the project's own hmi/ folder (its screen, css, modules
        # and any asset they import). Project-owned files, served from
        # the project — the platform holds none of it.
        _proj = _project_dir(workspace)
        # Replay recordings land in the project's core/ folder.
        global _record_core_dir
        if _proj is not None:
            _record_core_dir = str(_proj / "core")
        if _proj is not None and (_proj / "hmi").is_dir():
            routes.insert(0, (r"/hmi/(.*)", HmiStaticFileHandler,
                              {"path": str(_proj / "hmi")}))

        # Parse the project's HMI declaration once. Warnings are printed
        # at startup (a typo'd widget must be visible then, not silently
        # missing on the pendant hours later).
        global _PENDANT_SPEC
        _PENDANT_SPEC = _load_pendant_spec(workspace)
        for w in _PENDANT_SPEC.get("warnings", []):
            print(f"[hmi] {w}")
        if _PENDANT_SPEC.get("widgets"):
            print(f"[pendant] {len(_PENDANT_SPEC['widgets'])} widget(s) declared")

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

        # Operator-value flusher: bounded push rate for rt.op (see
        # _op_flush). 100ms — human-perceptible immediacy, ~10 small
        # messages/second worst case however hard a project writes.
        tornado.ioloop.PeriodicCallback(
            lambda: _op_flush(self.rt), OP_FLUSH_MS
        ).start()

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