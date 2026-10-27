"""VisionStation — one camera's session on its vision server.

Used by the ``Inspection`` component (every camera, fixed or
robot-mounted). The SERVER is its own component and device
(``vision_server.py``); the camera names it in ``camera_cfg.server``.

Responsibilities:
  * hold this camera's ``VisionClient`` session (none in simulation),
  * register the camera on the server during construction,
  * expose ``add_detection``, ``capture``, ``detect``, ``frame``,
    ``get_img``,
  * make **capture → run-on-captured-frame** the default for ``detect()``
    so a detection never runs on a stale or junk frame.

Failure model — one rule for every call the workflow depends on:
  * ``simulation=True`` → no server contact; ``detect()`` returns the
    caller's ``sim_return``. Never in real mode.
  * Real mode, a capture / detection run / frame fails (a crashed model,
    a bad preset, a lost socket, the camera not grabbing) → the SERVER
    device goes down with what failed (VisionServerStation.fault) — the
    run pauses when it is critical — and the call returns None: a failed
    read (project-guide §8). Nothing is retried here; the operator's
    Recover on the server card re-establishes the session (reconnect).
  * ``get_img`` is observability: a failure logs and returns None, and
    never puts the server down.
  * At launch: missing client lib, unreachable server, a camera that
    does not open in time → raise (a launch that cannot work fails).

The camera's own bus entry ``camera:<sn>`` is the vision server
process's (one publisher per id, device-guide §8); this helper never
writes it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


class VisionClientImportError(RuntimeError):
    """Raised when ``dorna_vision_client`` isn't importable in real mode.

    Distinguished from a runtime connect failure because the fix is a
    deployment action (install the package), not a recovery loop. Raised
    eagerly during VisionStation construction so misconfigured projects
    fail at launch, not on the first ``detect()`` call.
    """


# How long the vision server may take to OPEN a camera or LOAD a model.
# The first open of a Hikrobot PoE camera after power-up takes ~20-22 s
# (field, 2026-10-26) and a cold model load (a ~7 MB .pkl plus
# torch/openvino) can pass 10 s too — the client's DEFAULT_TIMEOUT of
# 10 s gave up while the server kept opening, and the launch failed.
# The client's own camera_recover waits the same 60 s. A scene's
# camera_cfg ``timeout`` (or a detection preset's) overrides it.
COLD_OPEN_TIMEOUT = 60


class VisionCameraOpenTimeoutError(RuntimeError):
    """Raised when the vision server was reached but did not open the
    camera in time — a slow (cold) camera, not an unreachable server."""


class VisionServerUnreachableError(RuntimeError):
    """Raised at launch when the vision server can't be reached, or
    refused to register the camera, in real mode — the launch cannot
    work."""


class VisionStation:
    """One camera's session on its vision server. Authored sim stays
    sim; failures put the server down and return None.

    Args:
        server: The camera's ``VisionServerStation`` (the vision_server
            component's ``server``), or None — no server, simulation.
        serial_number: Serial of the camera the server should manage.
        camera_cfg: Dict forwarded to ``camera_add`` (type, stream, K,
            D, mode, ...).
        simulation: Authored simulation intent. True (or no server, or
            no serial: nothing real to talk to) → no client is opened and
            ``detect()`` returns ``sim_return``. Never flipped by a
            failure.
        label: The camera component's name, for log lines and faults.

    Raises:
        ValueError: a real camera on a server authored as simulation.
        VisionClientImportError: real mode, ``dorna_vision_client`` not
            importable.
        VisionServerUnreachableError: real mode, the server refuses the
            connection or the camera.
        VisionCameraOpenTimeoutError: the server answered but did not
            open the camera within ``COLD_OPEN_TIMEOUT`` (or the
            camera_cfg ``timeout``) — a cold camera, not a dead server.
    """

    def __init__(
        self,
        *,
        server,
        serial_number: str,
        camera_cfg: Optional[dict] = None,
        simulation: bool = True,
        label: str = "vision",
    ):
        self.server = server
        # str() at the boundary: an unquoted serial in scene yaml
        # arrives as an int, and the server pool keys by STRING — the
        # mismatch reads as a 10 s connect timeout, not a type error.
        self.serial_number = str(serial_number) if serial_number else ""
        self.camera_cfg = dict(camera_cfg or {})
        # server / serial_number identify the server and camera; mount
        # is WORKSPACE-side kinematics (the lens-on-flange transform
        # Core.lens_pose composes for camera_in_world) — none of them are
        # Camera.connect parameters. Everything else forwards to
        # camera_add.
        for k in ("server", "serial_number", "mount"):
            self.camera_cfg.pop(k, None)
        # The open's timeout is camera_add's own keyword, not a connect
        # parameter — popped so ** never passes it twice.
        self.open_timeout = float(self.camera_cfg.pop("timeout", COLD_OPEN_TIMEOUT))
        self.label = label
        # Detections this station has REGISTERED on the server — written
        # only once the server accepted one, re-registered by reconnect():
        # detections are per-session state on the server.
        self._detections: dict = {}

        # Simulation gate. True when authored OR when there is nothing to
        # connect to (no server / no serial). A failure never flips it.
        self.simulation = bool(simulation) or server is None or not self.serial_number
        self._client = None

        if self.simulation:
            # The camera's bus entry is the vision server's; sim intent
            # surfaces via the component's device_claim.
            return
        if server.simulation:
            raise ValueError(f"{self.label}: a real camera on vision server "
                             f"{server.label!r}, which is in simulation")

        self._open()
        server.add_camera(self)

    @property
    def ip(self) -> str:
        return self.server.ip

    @property
    def port(self) -> int:
        return self.server.port

    def _open(self) -> None:
        """Connect and add the camera — at launch. A missing client lib is
        a deployment problem; then two failures, two messages: the SERVER
        not answering, and the server answering but the CAMERA not
        opening in time."""
        try:
            from dorna_vision_client import VisionClient
        except ImportError as ex:
            raise VisionClientImportError(
                f"{self.label}: dorna_vision_client not installed ({ex}). "
                f"Install with: sudo pip3 install -e "
                f"/path/to/vision/dorna_vision-client"
            ) from ex
        try:
            self._client = VisionClient()
            self._client.connect(host=self.ip, port=self.port)
        except Exception as ex:
            self._safe_close()
            self._client = None
            raise VisionServerUnreachableError(
                f"{self.label}: vision server unreachable @ "
                f"{self.ip}:{self.port}: {type(ex).__name__}: {ex}"
            ) from ex
        try:
            self._client.camera_add(
                serial_number=self.serial_number,
                timeout=self.open_timeout,
                **self.camera_cfg,
            )
            print(
                f"✅ {self.label} connected @ {self.ip}:{self.port} "
                f"(camera {self.serial_number})"
            )
            self._bus_connect()
        except TimeoutError as ex:
            self._safe_close()
            self._client = None
            raise VisionCameraOpenTimeoutError(
                f"{self.label}: camera {self.serial_number} did not open within "
                f"{self.open_timeout:g} s on the vision server @ {self.ip}:{self.port} "
                f"(cold start?) — retry. {ex}"
            ) from ex
        except Exception as ex:
            self._safe_close()
            self._client = None
            raise VisionServerUnreachableError(
                f"{self.label}: vision server @ {self.ip}:{self.port} failed to "
                f"add camera {self.serial_number}: {type(ex).__name__}: {ex}"
            ) from ex

    # ── Detection lifecycle ────────────────────────────────────────────

    def reconnect(self) -> None:
        """A fresh session — the server's Recover (VisionServerStation.
        recover). A server restart killed the session and its detections;
        a crashed detection may hold a broken model: connect again, re-add
        the camera (idempotent on the pool), re-register every detection
        this station authored. Raises on failure — the server stays
        down."""
        from dorna_vision_client import VisionClient
        self._safe_close()
        self._client = VisionClient()
        self._client.connect(host=self.ip, port=self.port)
        # A server restart is a cold open too — the same timeouts.
        self._client.camera_add(serial_number=self.serial_number, timeout=self.open_timeout,
                                **self.camera_cfg)
        for name, preset in self._detections.items():
            self._client.detection_add(
                name=name, camera_serial_number=self.serial_number,
                **self._detection_kwargs(preset))
        self._bus_connect()
        print(f"🔁 {self.label}: session re-established @ {self.ip}:{self.port}")

    @staticmethod
    def _detection_kwargs(preset: dict) -> dict:
        """detection_add's keywords: the preset, with the cold-load
        timeout unless the preset names its own."""
        kw = dict(preset)
        kw.setdefault("timeout", COLD_OPEN_TIMEOUT)
        return kw

    def _bus_connect(self) -> None:
        """Site-bus handshake (device-guide §8): tell the unit to
        publish device state to THIS host's broker. Host is implicit —
        the server uses the caller's address — so a fleet of benches
        needs zero per-machine bus config. Best-effort: an older
        server without the command keeps its own DEVICE_MQTT_HOST and
        we say so once instead of failing the launch."""
        import os
        try:
            port = int(os.environ.get("DEVICE_MQTT_PORT", "1883"))
            self._client.bus_connect(port=port)
            print(f"[bus] {self.label}: unit publishes device state to this host")
        except Exception as ex:
            print(f"[{self.label}] bus handshake unavailable ({type(ex).__name__}) — "
                  f"unit keeps its own DEVICE_MQTT_HOST; set it manually for bus health")

    def _call(self, what: str, thunk):
        """Every call the workflow depends on goes through here: a
        failure puts the SERVER down with what failed, then raises for
        the caller to turn into its own result. Never retried — Recover
        on the server card is the one way back."""
        try:
            return thunk()
        except Exception as ex:
            self.server.fault(f"{self.label} ({self.serial_number}): {what}: "
                              f"{type(ex).__name__}: {ex}")
            raise

    def add_detection(self, name: str, **detection_preset: Any) -> bool:
        """Register a detection on the server. Returns False in
        simulation, True once the server accepted it.

        ``config:`` (a detection config file, vision-guide §5) comes
        absolute from a recipes file (the loader resolved it against that
        file); written in Python it is relative to the working folder, as
        any path in that code. A detection that cannot be registered
        RAISES — a missing config, a preset the server refuses, a server
        that does not answer: every later read on it would fail — and
        the server is down with why. A vlm detection that RUNS is like
        any other: no usable answer is an empty result (vision-guide §9)."""
        if self.simulation:
            return False
        if isinstance(detection_preset.get("config"), str):
            p = Path(detection_preset["config"]).expanduser()
            if not p.is_file():
                raise RuntimeError(f"[{self.label}] detection {name!r}: config "
                                   f"{detection_preset['config']!r} not found ({p.resolve()})")
        self._call(f"detection_add {name}", lambda: self._client.detection_add(
            name=name,
            camera_serial_number=self.serial_number,
            **self._detection_kwargs(detection_preset),
        ))
        # Recorded only AFTER the server accepted it: one the server
        # refuses is never re-registered by reconnect() as if it were.
        self._detections[name] = dict(detection_preset)
        return True

    def frame(self, quality: int = 100):
        """One frame from this station's camera as JPEG bytes — no
        detection runs. Collect one per pose, then
        ``detect(name, data=[frame_1, frame_2, ...])`` judges them as the
        views of one part (vlm). ``None`` in simulation, and when the
        grab failed (the server is then down)."""
        if self.simulation:
            return None
        try:
            return self._call("frame", lambda: self._client.camera_get_img(
                self.serial_number, type="color_img", quality=quality))[0]
        except Exception:
            return None

    def capture(self, name: str, data: Any = None, camera_in_world: Any = None,
                focus: Any = None) -> dict:
        """Capture a fresh atomic snapshot (camera frames + robot joints)
        for ``name`` and cache it on the server.

        Returns the server's reply dict. Use ``ok`` to branch:

            reply = vision.capture("cap_check")
            if reply["ok"]:
                result = vision.detect("cap_check", use_last=True)
            # not ok: the server is down with reply["msg"]

        ``data`` passes through to ``Detection.get_camera_data``:
          * ``None``  — live camera (default).
          * ``dict``  — pre-fetched payload (replay / cross-detection).
          * ``str``   — server-local image path (file replay).

        ``focus`` (cameras with a focus surface — uEye XS): applied
        BEFORE the grab and stored as the detection's pin from then on,
        e.g. ``{"mode": "manual", "position": 164}``. None = leave the
        lens wherever the detection's existing pin (or the camera) has it.

        In simulation, returns ``{"ok": True, "ts": None, "has_joint": False,
        "sim": True}`` so callers can skip the capture/run split branch.
        A failed capture — the call raised, or the server answered
        ``ok: False`` (the camera did not grab) — puts the server down
        and returns ``{"ok": False, "msg": ...}``; never raises.
        """
        if self.simulation:
            return {"name": name, "ok": True, "ts": None, "has_joint": False, "sim": True}
        try:
            snap = self._call(f"capture {name}", lambda: self._client.detection_capture(
                name, data=data, camera_in_world=camera_in_world, focus=focus))
        except Exception as ex:
            return {"name": name, "ok": False, "msg": f"{type(ex).__name__}: {ex}"}
        if not snap.get("ok"):
            self.server.fault(f"{self.label} ({self.serial_number}): capture {name}: "
                              f"{snap.get('msg', 'capture failed')}")
        return snap

    def get_img(self, name: str, kind: str = "img", quality: int = 85,
                max_side: Optional[int] = None, meta: bool = False):
        """The named detection's LAST image as JPEG bytes — ``"img"`` is the
        full frame as the server drew it (boxes and labels when
        ``display.label`` is on), ``"img_roi"`` the crop the model saw.
        Fetched over the wire: this is how a project keeps its captures
        with the project instead of on the vision unit's disk. ``None``
        in simulation, and on any failure (logged once per call) — an
        image is observability and never fails a run. ``max_side`` has
        the server downscale before encoding (fewer bytes on the wire).
        ``meta=True`` returns ``(bytes, meta)`` — the server's ``meta`` says
        the image's ``shape`` and the full frame's ``source_shape`` [h, w],
        so a downscaled picture still maps back to frame pixels; ``(None,
        None)`` where it would return None."""
        if self.simulation:
            return (None, None) if meta else None
        try:
            data, info = self._client.detection_get_img(
                name, type=kind, quality=int(quality), max_side=max_side)
            return (data, info) if meta else data
        except Exception as ex:
            print(f"[{self.label}] get_img({name}, {kind}) failed: {ex}")
            return (None, None) if meta else None

    def detect(
        self,
        name: str,
        sim_return: Any = [],
        use_last: bool = False,
        data: Any = None,
        camera_in_world: Any = None,
        focus: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Run the named detection: its result list, ``[]`` when it found
        nothing, or **None when the read failed** — the server is then
        down with what failed (project-guide §8: the read action returns
        False; after Recover and Resume the planner selects it again).

        ``sim_return`` (device-guide §17) — what simulation returns,
        shaped exactly like a real result (a list). Real mode never
        returns it.

        **Default behavior (capture → run-on-captured-frame).** When
        ``use_last`` is False, the call first captures a fresh frame for
        ``name`` and runs only if the capture succeeded — never on a
        stale or junk frame. ``use_last=True`` skips the capture (you
        called ``capture()`` yourself, or a second detection runs on the
        same cached frame). ``data`` is forwarded to the capture (see
        ``capture``); a list of images (vlm) is judged as given, no
        capture.
        """
        if self.simulation:
            return sim_return

        if isinstance(data, (list, tuple)):
            # several images of ONE part (vlm) — judged as given, no capture,
            # no camera pose: the caller collected them (frame())
            try:
                return self._call(f"detect {name}", lambda: self._client.detection_run(
                    name, data=list(data), **kwargs))
            except Exception:
                return None

        if not use_last:
            # ``focus`` rides the capture (it must land BEFORE the grab; on
            # use_last the frame already exists, so it is ignored there).
            snap = self.capture(name, data=data, camera_in_world=camera_in_world, focus=focus)
            if not snap.get("ok"):
                return None
            use_last = True   # run on the just-captured frame

        try:
            return self._call(f"detect {name}", lambda: self._client.detection_run(
                name, use_last=use_last, **kwargs))
        except Exception:
            return None

    # ── Lifecycle ──────────────────────────────────────────────────────

    def close(self) -> None:
        """Disconnect from the vision server. Idempotent."""
        self._safe_close()
        self._client = None

    def _safe_close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
