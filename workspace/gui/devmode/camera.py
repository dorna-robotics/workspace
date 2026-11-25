"""The cameras for Developer mode's camera window and the assistant —
live frames, the vision server's device list, exposure / white balance.

Talks to the vision server directly with its own ``VisionClient``
session, resolved from the project's scene (``camera_cfg.server`` →
the vision_server component's ip/port; the camera by
``camera_cfg.serial_number``, or ``camera_cfg.ip`` for a network
camera). It never goes through a running project's ``VisionStation``:
that path faults the vision server on any hiccup (vision_station.py
``_call``), and a preview poll must never take a run's camera down.

Rate-capped per camera (``MIN_INTERVAL_S``): faster callers get the
last frame again. In simulation, or not wired, the answer is a reason
(NoImage, with a code the window shows it by), not an image.
"""
import threading
import time

MIN_INTERVAL_S = 0.4          # ≤ 2.5 grabs/s per camera, whatever the pollers do
QUALITY = 50                  # full-res JPEGs cost 350–600 ms (scene-check-guide §10)
TIMEOUT_S = 4.0
NETWORK_TYPES = {"hikrobot"}  # GigE / PoE: reachable by ip as well as by serial

_clients = {}                 # (host, port) → VisionClient
_opened = set()               # (host, port, serial) camera_add'ed by this session
_ip_serial = {}               # (host, port, camera ip) → its serial, as camera_add resolved it
_last = {}                    # (host, port, serial, quality) → (t, jpeg)
_lock = threading.Lock()


class NoImage(Exception):
    """A clean "no picture" answer. ``code``: simulated | no_server |
    server_simulated | no_address | no_camera."""

    def __init__(self, msg, code="no_camera"):
        super().__init__(msg)
        self.code = code


def _server_of(scene, cfg):
    cam = cfg.get("camera_cfg") if isinstance(cfg.get("camera_cfg"), dict) else {}
    server = cam.get("server")
    srv = (scene or {}).get(server) if server else None
    return server, (srv if isinstance(srv, dict) and srv.get("type") == "vision_server" else None)


def resolve(scene, name):
    """(host, port, serial, camera_cfg) for inspection component ``name``,
    or NoImage with the reason. ``serial`` is "" for a network camera
    addressed by its ip alone (camera_cfg keeps ``ip``)."""
    cfg = (scene or {}).get(name)
    if not isinstance(cfg, dict):
        raise NoImage(f"no component {name}")
    cam = dict(cfg.get("camera_cfg") or {})
    if cfg.get("simulation", True) is not False:
        raise NoImage("The camera is simulated — turn Simulation off to see its picture.", "simulated")
    server, srv = _server_of(scene, cfg)
    if srv is None:
        raise NoImage("No vision server is set for this camera — pick the one it is plugged into.", "no_server")
    if srv.get("simulation", True) is not False:
        raise NoImage(f"The vision server {server} is simulated — turn its simulation off to reach it.",
                      "server_simulated")
    host = str(srv.get("ip") or "")
    if not host:
        raise NoImage(f"The vision server {server} has no IP address.", "no_address")
    serial = str(cam.get("serial_number") or "")
    ip = str(cam.get("ip") or "") if str(cam.get("type") or "") in NETWORK_TYPES else ""
    if not serial and not ip:
        raise NoImage("The camera has no serial number" + (" or IP address" if cam.get("type") in NETWORK_TYPES else "")
                      + " — use Find cameras to pick it.", "no_address")
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


def _add(c, host, port, serial, cam_cfg):
    """camera_add on this session; the serial it pooled the camera by
    (for an ip-only network camera, the server resolves it)."""
    r = c.camera_add(serial, timeout=15, **cam_cfg)
    sn = str((r or {}).get("serial_number") or serial) if isinstance(r, dict) else serial
    if not serial and cam_cfg.get("ip"):
        _ip_serial[(host, port, cam_cfg["ip"])] = sn
    _opened.add((host, port, sn))
    return sn


def _opened_client(host, port, serial, cam_cfg, fn):
    """``fn(client, serial)`` on this module's own session, the camera
    opened on it first when it is not yet (no run has opened it: the
    server's pool is per serial, so a run that opens it later shares it).
    The session is dropped on any failure. Call it holding ``_lock``."""
    try:
        c = _client(host, port)
        sn = serial or _ip_serial.get((host, port, cam_cfg.get("ip")), "")
        if not sn:
            sn = _add(c, host, port, serial, cam_cfg)
        try:
            return fn(c, sn)
        except Exception:
            if (host, port, sn) in _opened:
                raise
            sn = _add(c, host, port, serial, cam_cfg)
            return fn(c, sn)
    except Exception:
        _drop(host, port)
        raise


def grab(host, port, serial, cam_cfg, quality=QUALITY):
    """One JPEG (bytes). Blocking — call it off the IOLoop."""
    key = (host, port, serial or cam_cfg.get("ip"), quality)
    with _lock:
        t, jpg = _last.get(key, (0.0, None))
        if jpg is not None and time.monotonic() - t < MIN_INTERVAL_S:
            return jpg
        jpg, _ = _opened_client(host, port, serial, cam_cfg, lambda c, sn: c.camera_get_img(
            sn, type="color_img", quality=quality, timeout=TIMEOUT_S))
        if not jpg:
            raise RuntimeError("the vision server sent no image")
        _last[key] = (time.monotonic(), jpg)
        return jpg


def call(host, port, serial, cam_cfg, fn):
    """``fn(client, serial)`` on the same private session as ``grab``
    (camera settings, a test detection) — never a running project's
    VisionStation. Blocking, serialised with the grabs."""
    with _lock:
        return _opened_client(host, port, serial, cam_cfg, fn)


def forget(host, port, serial, cam_cfg=None):
    """Drop the cached frames of a camera (after its settings changed)."""
    key = (host, port, serial or (cam_cfg or {}).get("ip"))
    with _lock:
        for k in [k for k in _last if k[:3] == key]:
            _last.pop(k, None)


def devices(host, port):
    """The cameras the vision server at host:port sees (camera_list):
    [{serial_number, ip, usb_type, camera_type, name, attached, added}].
    Blocking. Nothing is opened."""
    with _lock:
        try:
            out = _client(host, port).camera_list(timeout=10)
        except Exception:
            _drop(host, port)
            raise
    keep = ("serial_number", "ip", "usb_type", "camera_type", "name", "attached", "added")
    return [{k: d.get(k) for k in keep if d.get(k) not in (None, "")} for d in out or [] if isinstance(d, dict)]


def _clean(r):
    return {k: v for k, v in r.items() if k not in ("id", "stat")} if isinstance(r, dict) else r


def read_settings(c, serial):
    """{info, exposure, white_balance} of an opened camera; a part the
    camera has not becomes "not available: …"."""
    out = {}
    for k, fn in (("info", lambda: c.camera_info(serial, timeout=8)),
                  ("exposure", lambda: c.camera_exposure(serial, timeout=8)),
                  ("white_balance", lambda: c.camera_wb(serial, timeout=8))):
        try:
            out[k] = _clean(fn())
        except Exception as e:
            out[k] = f"not available: {type(e).__name__}: {e}"
    return out


def adjust(c, serial, exposure_ms=None, auto_exposure=None, white_balance=None, focus=None):
    """Apply exposure (ms pins it; auto_exposure=True hands it back to the
    camera), white balance ('auto' | 'hold' — hold freezes the current
    auto convergence) and focus to an opened camera. Returns (done,
    errors): [(what, the camera's answer)], [(what, why it refused)]."""
    steps = []
    if exposure_ms is not None:
        steps.append(("exposure", lambda: c.camera_exposure(serial, ms=float(exposure_ms), timeout=10)))
    elif auto_exposure:
        steps.append(("auto exposure", lambda: c.camera_exposure(serial, auto=True, timeout=10)))
    if white_balance == "auto":
        steps.append(("white balance auto", lambda: c.camera_wb(serial, auto=True, timeout=10)))
    elif white_balance == "hold":
        steps.append(("white balance hold", lambda: c.camera_wb(serial, hold=True, timeout=10)))
    if isinstance(focus, dict):
        if focus.get("region") is not None:
            steps.append(("focus region", lambda: c.camera_focus(serial, region=focus["region"], method=focus.get("method"))))
        else:
            steps.append(("focus", lambda: c.camera_focus(serial, mode=focus.get("mode"), position=focus.get("position"),
                                                          timeout=20)))
    done, errors = [], []
    for what, fn in steps:
        try:
            done.append((what, _clean(fn())))
        except Exception as e:
            errors.append((what, f"{type(e).__name__}: {e}"))
    return done, errors


def configurator(scene, name):
    """Where the vision configurator for camera ``name`` is: the vision
    server's own web GUI (the dorna_vision server serves it at its port;
    vision-guide §1), opened on its Playground page — where detections are
    built for a camera. The GUI routes by path only (/cameras, /playground)
    and takes no camera in the link, so the serial comes back to show
    beside it. A simulated server still has its address (the Pi may be on
    the bench): ``server_simulated`` says so. NoImage with the reason when
    there is nothing to open: no vision server named, or no address."""
    cfg = (scene or {}).get(name)
    if not isinstance(cfg, dict):
        raise NoImage(f"No component {name}")
    cam = dict(cfg.get("camera_cfg") or {})
    if cfg.get("type") == "vision_server":          # the server itself: its GUI
        server, serial, srv = name, "", cfg
    else:
        server, srv = _server_of(scene, cfg)
        serial = str(cam.get("serial_number") or "")
    if srv is None:
        raise NoImage("No vision server is set for this camera.", "no_server")
    host = str(srv.get("ip") or "")
    if not host:
        raise NoImage(f"The vision server {server} has no IP address.", "no_address")
    port = int(srv.get("port") or 80)
    return {"url": f"http://{host}:{port}/playground", "server": server, "host": host, "port": port,
            "serial": serial, "server_simulated": srv.get("simulation", True) is not False,
            "camera_simulated": bool(serial) and cfg.get("simulation", True) is not False}
