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
WEB_DIR = os.path.join(BASE_DIR, "orchestrator", "web")
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


# --------------------------------------------------
# Socket.IO Events
# --------------------------------------------------
@sio.event
async def connect(sid, environ, auth):
    if world_state:
        await sio.emit("scene_update", world_state, room=sid)
    else:
        await sio.emit("request_snapshot", room=sid)


@sio.event
async def upstream_update(sid, payload):
    merge_into_state(world_state, payload)
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
    (r"/config_version", ConfigVersionHandler),
    (r"/(.*)", tornado.web.StaticFileHandler,
        {"path": WEB_DIR, "default_filename": "index.html"}),
], debug=DEV_NOCACHE)

app.add_handlers(r".*$", [(r"/healthz", HealthHandler)])


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
