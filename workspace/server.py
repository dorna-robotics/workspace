# server.py — FIXED VERSION (preserves meshUrl correctly)

import os
import time
import json
import tornado.web
import tornado.ioloop
import socketio
from tornado import autoreload

# --------------------------------------------------
# Paths
# --------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
WEB_DIR = os.path.join(BASE_DIR, "gui", "orchestrator", "web")
VENDOR_DIR = os.path.join(BASE_DIR, "gui", "vendor")
CONFIG_DIR = os.path.join(BASE_DIR, "config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")

DEV_NOCACHE = os.environ.get("DEV_NOCACHE", "1") == "1"
PORT = int(os.environ.get("PORT", "5000"))


def config_version():
    """Returns version for cache-busting."""
    try:
        return str(int(os.path.getmtime(CONFIG_PATH)))
    except Exception:
        return str(int(time.time()))


# --------------------------------------------------
# Socket.IO server
# --------------------------------------------------
sio = socketio.AsyncServer(
    async_mode="tornado",
    cors_allowed_origins="*",
    allow_upgrades=False,  # websocket only
    ping_interval=20,
    ping_timeout=20,
    max_http_buffer_size=50 * 1024 * 1024,
)

# GLOBAL world state
world_state = {}

# --------------------------------------------------
# Replay recorder — captures the exact wire stream
# --------------------------------------------------
# One JSONL file per recording, saved in the ACTIVE PROJECT's core/
# folder (the Display announces it in its connect auth):
#   line 1: {"meta": {"started": iso8601, "project_core": ...}}
#   line 2: {"t": 0.0, "snap": <full world_state>}      — the opening scene
#   line N: {"t": secs, "u": <upstream payload>}        — every delta after
# Replaying = apply the snapshot, then the deltas in order — the same
# thing the live viewer does, which is what makes playback exact.
recorder = {"fp": None, "path": None, "t0": None, "frames": 0}
project_core = None  # announced by the Display on connect


def record_line(obj):
    fp = recorder["fp"]
    if fp is None:
        return
    try:
        fp.write(json.dumps(obj, separators=(",", ":")) + "\n")
        recorder["frames"] += 1
    except Exception as e:
        print("[record] write failed, stopping:", e)
        record_stop()


def record_start():
    if recorder["fp"] is not None:
        return {"ok": False, "error": "already recording"}
    if not project_core:
        return {"ok": False, "error": "no project connected yet — run a "
                "workspace with a project scene first"}
    os.makedirs(project_core, exist_ok=True)
    name = time.strftime("replay_%Y%m%d_%H%M%S.jsonl")
    path = os.path.join(project_core, name)
    try:
        fp = open(path, "w")
    except Exception as e:
        return {"ok": False, "error": f"cannot open {path}: {e}"}
    recorder.update(fp=fp, path=path, t0=time.time(), frames=0)
    record_line({"meta": {"started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          "project_core": project_core}})
    record_line({"t": 0.0, "snap": world_state})
    print(f"[record] started -> {path}")
    return {"ok": True, "path": path, "name": name}


def record_stop():
    fp, path, t0, n = (recorder["fp"], recorder["path"],
                       recorder["t0"], recorder["frames"])
    recorder.update(fp=None, path=None, t0=None, frames=0)
    if fp is None:
        return {"ok": False, "error": "not recording"}
    try:
        fp.close()
    except Exception:
        pass
    secs = round(time.time() - t0, 1) if t0 else 0
    print(f"[record] stopped: {path} ({n} lines, {secs}s)")
    return {"ok": True, "path": path, "frames": n, "seconds": secs}


def record_status():
    on = recorder["fp"] is not None
    return {"ok": True, "recording": on,
            "path": recorder["path"],
            "seconds": round(time.time() - recorder["t0"], 1) if on else 0,
            "frames": recorder["frames"],
            "project_core": project_core}


# --------------------------------------------------
# SAFE MERGE FIX
# --------------------------------------------------
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

        # ---- PRESERVE MESH URL (THE FIX) ----
        if "meshUrl" in prev and "meshUrl" not in spec:
            spec["meshUrl"] = prev["meshUrl"]

        # Also preserve componentName + solidName if missing
        if "componentName" in prev and "componentName" not in spec:
            spec["componentName"] = prev["componentName"]
        if "solidName" in prev and "solidName" not in spec:
            spec["solidName"] = prev["solidName"]

        prev.update(spec)
        state[name] = prev


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
        self.write(json.dumps({"version": config_version()}))


class HealthHandler(tornado.web.RequestHandler):
    def get(self):
        self.write("ok")


class RecordHandler(tornado.web.RequestHandler):
    """POST /record/start | /record/stop, GET /record/status — the
    viewer's record button. Only this server (the one fed by a live
    Display) has these; the button hides itself where they 404."""

    def get(self, action):
        if action != "status":
            self.set_status(405)
            self.write({"ok": False, "error": "GET is status only"})
            return
        self.write(record_status())

    def post(self, action):
        if action == "start":
            out = record_start()
        elif action == "stop":
            out = record_stop()
        else:
            self.set_status(404)
            out = {"ok": False, "error": f"unknown action {action}"}
        if not out.get("ok") and self.get_status() < 400:
            self.set_status(409)
        self.write(out)


# --------------------------------------------------
# Socket.IO Events
# --------------------------------------------------
@sio.event
async def connect(sid, environ, auth):
    global project_core
    core_dir = (auth or {}).get("project_core") or ""
    if core_dir:
        project_core = core_dir
        print(f"[record] project core announced: {project_core}")
    if world_state:
        await sio.emit("scene_update", world_state, room=sid)
    else:
        await sio.emit("request_snapshot", room=sid)


@sio.event
async def upstream_update(sid, payload):
    merge_into_state(world_state, payload)
    if recorder["fp"] is not None:
        record_line({"t": round(time.time() - recorder["t0"], 4),
                     "u": payload})
    # broadcast update to all clients
    await sio.emit("scene_update", payload)
    return "ok"


@sio.event
async def request_snapshot(sid):
    await sio.emit("request_snapshot", room=sid)


@sio.event
async def disconnect(sid):
    print("disconnect", sid)


# --------------------------------------------------
# Tornado App
# --------------------------------------------------
app = tornado.web.Application([
    (r"/socket.io/", socketio.get_tornado_handler(sio)),
    (r"/static/(.*)", NoCacheStaticFileHandler, {"path": STATIC_DIR}),
    (r"/vendor/(.*)", NoCacheStaticFileHandler, {"path": VENDOR_DIR}),
    (r"/config_version", ConfigVersionHandler),
    (r"/(.*)", tornado.web.StaticFileHandler,
        {"path": WEB_DIR, "default_filename": "index.html"}),
], debug=DEV_NOCACHE)

app.add_handlers(r".*$", [(r"/healthz", HealthHandler)])
app.add_handlers(r".*$", [(r"/record/(start|stop|status)", RecordHandler)])


# --------------------------------------------------
# Main
# --------------------------------------------------
if __name__ == "__main__":
    app.listen(PORT)
    print(f"[server] listening at http://127.0.0.1:{PORT}")
    print(" - static:", STATIC_DIR)
    print(" - web:", WEB_DIR)
    print(" - DEV_NOCACHE =", DEV_NOCACHE)

    for p in (STATIC_DIR, WEB_DIR, CONFIG_PATH):
        if os.path.exists(p):
            autoreload.watch(p)

    autoreload.start()
    tornado.ioloop.IOLoop.current().start()
