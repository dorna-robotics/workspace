# server.py — Tornado + python-socketio (WS-only) with world-state replay + self-healing snapshots
import os, time
import tornado.web, tornado.ioloop
from tornado import autoreload
import socketio
import json

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")   # serves /static/*
WEB_DIR    = os.path.join(BASE_DIR, "web")      # serves index.html
CONFIG_DIR = os.path.join(BASE_DIR, "config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")

# ---- Env / Flags ----
DEV_NOCACHE = os.environ.get("DEV_NOCACHE", "1") == "1"  # set to 0 in prod
PORT = int(os.environ.get("PORT", "5000"))

def config_version() -> str:
    try:
        return str(int(os.path.getmtime(CONFIG_PATH)))
    except Exception:
        return str(int(time.time()))

sio = socketio.AsyncServer(
    async_mode="tornado",
    cors_allowed_origins="*",
    allow_upgrades=False,          # WS only (no polling/upgrade churn)
    ping_interval=20,
    ping_timeout=20,
    max_http_buffer_size=50 * 1024 * 1024,
)

# ---------- No-cache static handler (dev) ----------
class NoCacheStaticFileHandler(tornado.web.StaticFileHandler):
    def set_extra_headers(self, path):
        if DEV_NOCACHE:
            self.set_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.set_header("Pragma", "no-cache")
            self.set_header("Expires", "0")
    def compute_etag(self):
        return None if DEV_NOCACHE else super().compute_etag()

# ---------- healthz ----------
class HealthHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_status(200)
        self.finish("ok")

# Optional: expose config version for cache-busting on client
class ConfigVersionHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"version": config_version()}))

# ---------- world state ----------
# Stores the last-known spec per object: { name: {meshUrl/mesh/pose/visible/...}, ... }
world_state = {}

def merge_into_state(state, payload):
    """Shallow-merge each object's spec into world_state."""
    for name, spec in payload.items():
        prev = state.get(name, {})
        if not isinstance(prev, dict):
            prev = {}
        if isinstance(spec, dict):
            prev.update(spec)
        state[name] = prev

def _has_mesh_info(spec):
    return isinstance(spec, dict) and ("meshUrl" in spec or "mesh" in spec)

def world_has_any_mesh():
    return any(_has_mesh_info(v) for v in world_state.values())

# ---------- socket.io events ----------
@sio.event
async def upstream_update(sid, payload):
    """
    Producers push pose frames and (occasionally) full snapshots.
    If we see a brand-new object *without* mesh info, immediately request a snapshot to heal state.
    """
    need_snapshot = False
    for name, spec in payload.items():
        prev = world_state.get(name)
        if prev is None and not _has_mesh_info(spec):
            need_snapshot = True

    merge_into_state(world_state, payload)
    await sio.emit("scene_update", payload)

    if need_snapshot:
        await sio.emit("request_snapshot")

    return "ok"  # ACK for producer timing

@sio.event
async def connect(sid, environ, auth):
    print("connect", sid)
    if world_state and world_has_any_mesh():
        await sio.emit("scene_update", world_state, room=sid)
    else:
        await sio.emit("request_snapshot")

@sio.event
async def request_snapshot(sid):
    await sio.emit("request_snapshot")

@sio.event
async def disconnect(sid):
    print("disconnect", sid)

# ---------- app ----------
app = tornado.web.Application([
    (r"/socket.io/", socketio.get_tornado_handler(sio)),
    (r"/static/(.*)", NoCacheStaticFileHandler, {"path": STATIC_DIR}),
    (r"/config_version", ConfigVersionHandler),
    # Keep SPA-style fallback to index.html for anything else under WEB_DIR
    (r"/(.*)", tornado.web.StaticFileHandler, {"path": WEB_DIR, "default_filename": "index.html"}),
], debug=DEV_NOCACHE)

# add /healthz last so it won't be shadowed
app.add_handlers(r".*$", [(r"/healthz", HealthHandler)])

# ---------- entry ----------
if __name__ == "__main__":
    app.listen(PORT)
    print(f"[server] listening on http://127.0.0.1:{PORT}  (web: {WEB_DIR}, static: {STATIC_DIR}, DEV_NOCACHE={DEV_NOCACHE})")

    # autoreload on changes to key dirs/files
    for p in (STATIC_DIR, WEB_DIR, CONFIG_PATH):
        if os.path.exists(p):
            autoreload.watch(p)

    autoreload.start()
    tornado.ioloop.IOLoop.current().start()
