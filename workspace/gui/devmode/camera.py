"""Live camera preview for Developer mode — one JPEG per request.

Talks to the vision server directly with its own ``VisionClient``
session, resolved from the project's scene (``camera_cfg.server`` →
the vision_server component's ip/port, ``camera_cfg.serial_number``).
It never goes through a running project's ``VisionStation``: that
path faults the vision server on any hiccup (vision_station.py
``_call``), and a preview poll must never take a run's camera down.

Rate-capped per camera (``MIN_INTERVAL_S``): faster callers get the
last frame again. In simulation, or with no server/serial, the answer
is a reason, not an image.
"""
import threading
import time

MIN_INTERVAL_S = 0.4          # ≤ 2.5 grabs/s per camera, whatever the pollers do
QUALITY = 50                  # full-res JPEGs cost 350–600 ms (scene-check-guide §10)
TIMEOUT_S = 4.0

_clients = {}                 # (host, port) → VisionClient
_opened = set()               # (host, port, serial) camera_add'ed by this session
_last = {}                    # (host, port, serial) → (t, jpeg)
_lock = threading.Lock()


class NoImage(Exception):
    """A clean "no picture" answer: simulation or not wired."""


def resolve(scene, name):
    """(host, port, serial, camera_cfg) for inspection component ``name``,
    or NoImage with the reason."""
    cfg = (scene or {}).get(name)
    if not isinstance(cfg, dict):
        raise NoImage(f"no component {name}")
    cam = dict(cfg.get("camera_cfg") or {})
    if cfg.get("simulation", True) is not False:
        raise NoImage("Simulation — no camera image")
    server = cam.get("server")
    serial = str(cam.get("serial_number") or "")
    srv = (scene or {}).get(server) if server else None
    if not isinstance(srv, dict) or srv.get("type") != "vision_server":
        raise NoImage("No vision server set for this camera — set it in Parameters")
    if srv.get("simulation", True) is not False:
        raise NoImage(f"Simulation — vision server {server} is simulated")
    host = str(srv.get("ip") or "")
    if not host:
        raise NoImage(f"Vision server {server} has no IP address")
    if not serial:
        raise NoImage("No camera serial number — set it in Parameters")
    port = int(srv.get("port") or 80)
    for k in ("server", "serial_number", "mount", "timeout"):
        cam.pop(k, None)
    return host, port, serial, cam


def _client(host, port):
    key = (host, port)
    c = _clients.get(key)
    if c is None:
        from dorna_vision_client import VisionClient
        c = VisionClient()
        c.connect(host=host, port=port, timeout=TIMEOUT_S, default_timeout=TIMEOUT_S)
        _clients[key] = c
    return c


def _drop(host, port):
    c = _clients.pop((host, port), None)
    for k in [k for k in _opened if k[:2] == (host, port)]:
        _opened.discard(k)
    if c is not None:
        try:
            c.close(timeout=1)
        except Exception:
            pass


def grab(host, port, serial, cam_cfg):
    """One JPEG (bytes). Blocking — call it off the IOLoop."""
    key = (host, port, serial)
    with _lock:
        t, jpg = _last.get(key, (0.0, None))
        if jpg is not None and time.monotonic() - t < MIN_INTERVAL_S:
            return jpg
        try:
            c = _client(host, port)
            try:
                jpg, _ = c.camera_get_img(serial, type="color_img", quality=QUALITY, timeout=TIMEOUT_S)
            except Exception:
                if key in _opened:
                    raise
                # Not open on the server yet (no run has opened it): open
                # it once for this session — the server's pool is per
                # serial, so a run that opens it later shares it.
                c.camera_add(serial, timeout=15, **cam_cfg)
                _opened.add(key)
                jpg, _ = c.camera_get_img(serial, type="color_img", quality=QUALITY, timeout=TIMEOUT_S)
        except Exception:
            _drop(host, port)
            raise
        if not jpg:
            raise RuntimeError("the vision server sent no image")
        _last[key] = (time.monotonic(), jpg)
        return jpg


def configurator(scene, name):
    """Where the vision configurator for camera ``name`` is: the vision
    server's own web GUI (the dorna_vision server serves it at its port;
    vision-guide §1), opened on its Playground page — where detections are
    built for a camera. The GUI routes by path only (/cameras, /playground)
    and takes no camera in the link, so the serial comes back to show
    beside it. NoImage with the reason when there is nothing to open:
    no vision server named, a simulated one, or no address."""
    cfg = (scene or {}).get(name)
    if not isinstance(cfg, dict):
        raise NoImage(f"No component {name}")
    cam = dict(cfg.get("camera_cfg") or {})
    if cfg.get("type") == "vision_server":          # the server itself: its GUI
        server, serial, srv = name, "", cfg
    else:
        server = cam.get("server")
        serial = str(cam.get("serial_number") or "")
        srv = (scene or {}).get(server) if server else None
    if not isinstance(srv, dict) or srv.get("type") != "vision_server":
        raise NoImage("No vision server is set for this camera. Set its Vision server in Parameters "
                      "(a vision_server component in the scene, with the server's IP and port).")
    if srv.get("simulation", True) is not False:
        raise NoImage(f"The vision server {server} is in simulation — there is no configurator to open. "
                      "Turn its simulation off (Workflow tab) and set its IP address to use the real one.")
    host = str(srv.get("ip") or "")
    if not host:
        raise NoImage(f"The vision server {server} has no IP address — set it in its Parameters.")
    port = int(srv.get("port") or 80)
    return {"url": f"http://{host}:{port}/playground", "server": server, "host": host, "port": port,
            "serial": serial, "camera_simulated": bool(serial) and cfg.get("simulation", True) is not False}
