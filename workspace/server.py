# server.py — Tornado + python-socketio (WS-only) with world-state replay + self-healing snapshots
import os, asyncio
import tornado.web, tornado.ioloop
import socketio

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")   # serves /static/CAD/*
WEB_DIR    = os.path.join(BASE_DIR, "web")      # serves index.html

sio = socketio.AsyncServer(
    async_mode="tornado",
    cors_allowed_origins="*",
    allow_upgrades=False,          # WS only (no polling/upgrade churn)
    ping_interval=20,
    ping_timeout=20,
    max_http_buffer_size=50 * 1024 * 1024,
)

app = tornado.web.Application([
    (r"/socket.io/", socketio.get_tornado_handler(sio)),
    (r"/static/(.*)", tornado.web.StaticFileHandler, {"path": STATIC_DIR}),
    (r"/(.*)", tornado.web.StaticFileHandler, {"path": WEB_DIR, "default_filename": "index.html"}),
], debug=False)

# Simple /healthz -> 200 OK
class HealthHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_status(200)
        self.finish("ok")
app.add_handlers(r".*$", [(r"/healthz", HealthHandler)])

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

    # Check if this payload introduces any new objects without meshes
    for name, spec in payload.items():
        prev = world_state.get(name)
        if prev is None and not _has_mesh_info(spec):
            # First time we hear about this object and there's no mesh info -> we need a snapshot
            need_snapshot = True

    # Merge then fan out
    merge_into_state(world_state, payload)
    await sio.emit("scene_update", payload)

    # Ask producers for a full snapshot if needed
    if need_snapshot:
        await sio.emit("request_snapshot")

    return "ok"  # ACK for producer timing

@sio.event
async def connect(sid, environ, auth):
    print("connect", sid)
    # If we already have mesh-bearing state, replay it to this viewer only
    if world_state and world_has_any_mesh():
        await sio.emit("scene_update", world_state, room=sid)
    else:
        # Either empty state or pose-only state -> ask producers for a fresh snapshot
        await sio.emit("request_snapshot")

@sio.event
async def request_snapshot(sid):
    # Forward viewer's request to all producers
    await sio.emit("request_snapshot")

@sio.event
async def disconnect(sid):
    print("disconnect", sid)

# ---------- entry ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.listen(port)
    print(f"[server] listening on http://127.0.0.1:{port}  (web dir: {WEB_DIR}, static dir: {STATIC_DIR})")
    tornado.ioloop.IOLoop.current().start()
