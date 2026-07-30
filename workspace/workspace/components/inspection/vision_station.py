"""VisionStation — shared helper that wraps a dorna_vision client.

Used by any component that owns a remote camera + detections on a vision
server: today the ``Inspection`` component (fixed camera) and ``Core`` (robot-
mounted camera). Tomorrow any new station that follows the same pattern.

Responsibilities:
  * hold a single ``VisionClient`` connection (or none, in simulation),
  * register the camera on the server during construction,
  * expose ``add_detection``, ``capture``, and ``detect``,
  * make the **capture → run-on-captured-frame** pattern the default for
    ``detect()`` so recipes never accidentally run on stale or junk data.

Failure model — authored intent is honoured, faults are surfaced loud:
  * ``simulation=True`` → no server contact. ``detect()`` returns canned
    values. **Does not publish anything to the device bus.** The camera
    is owned by the vision server daemon (which holds the USB handle);
    publishing a competing entry would race with the daemon's truth
    on retained MQTT topics. Workspace's sim intent for the camera is
    surfaced as a project-level claim mode (see
    ``DeviceComponent.device_claim``) so the panel can render a SIM
    pill on top of the daemon's bus state without overwriting it.
  * ``simulation=False`` + missing client lib → raise on construct.
    A missing import is a config error, not a runtime fault.
  * ``simulation=False`` + server unreachable → raise on construct.
    The vision server's own bus adapter will surface the camera as
    red; the workspace must not pretend it's running in sim.

Health monitoring: the vision server is the **sole publisher** for
``camera:<sn>``. This helper never writes to that topic. See
``docs/device-guide.md`` §8 (one-publisher-per-id rule).
"""

from __future__ import annotations

from typing import Any, Optional


class CameraUnavailableError(RuntimeError):
    """Raised when the server-side capture step fails (camera USB gone,
    librealsense error, etc.). Carries the device id and the server's
    error message so orchestrator-side handlers can surface it cleanly
    or wait for the device bus to report ``state=ok`` before retrying.
    """

    def __init__(self, name: str, msg: str):
        super().__init__(f"capture failed for {name!r}: {msg}")
        self.detection_name = name
        self.msg = msg


class VisionClientImportError(RuntimeError):
    """Raised when ``dorna_vision_client`` isn't importable in real mode.

    Distinguished from a runtime connect failure because the fix is a
    deployment action (install the package), not a recovery loop. Raised
    eagerly during VisionStation construction so misconfigured projects
    fail at launch, not on the first ``detect()`` call.
    """


class VisionServerUnreachableError(RuntimeError):
    """Raised when the vision server can't be reached in real mode.

    The vision server's own MQTT adapter will publish the camera as
    ``state=down`` on the bus — the panel surfaces the fault there.
    This exception just stops the workspace from continuing as if
    everything were fine.
    """


class VisionStation:
    """Tiny VisionClient wrapper. Authored sim stays sim; faults raise.

    Args:
        host: Vision server hostname or IP.
        port: Vision server port.
        serial_number: USB serial of the camera the server should manage.
        camera_cfg: Dict forwarded to ``camera_add`` (stream, K, D, mode, ...).
        simulation: Authored simulation intent. When True (or when ip/
            serial are empty so there's nothing real to talk to), no
            client is opened, ``detect()`` returns canned values, and
            the camera is stubbed onto the device bus with a SIM badge.
            Failures in real mode never flip this — they raise.
        label: Free-form name for log lines (e.g. component name).

    Raises:
        VisionClientImportError: ``simulation=False`` but the
            ``dorna_vision_client`` package is not importable.
        VisionServerUnreachableError: ``simulation=False`` but the
            vision server's ip/port refuses connections or the camera
            can't be registered.
    """

    def __init__(
        self,
        *,
        ip: str,
        port: int,
        serial_number: str,
        camera_cfg: Optional[dict] = None,
        simulation: bool = True,
        label: str = "vision",
    ):
        self.ip = ip
        self.port = int(port)
        # str() at the boundary: an unquoted serial in scene yaml
        # arrives as an int, and the server pool keys by STRING — the
        # mismatch reads as a 10 s connect timeout, not a type error.
        self.serial_number = str(serial_number) if serial_number else ""
        self.camera_cfg = dict(camera_cfg or {})
        # ip / port / serial_number identify the server and camera;
        # mount is WORKSPACE-side kinematics (the lens-on-flange
        # transform Core.lens_pose composes for camera_in_world) —
        # none of them are Camera.connect parameters. Everything else
        # forwards to camera_add.
        for k in ("ip", "port", "serial_number", "mount"):
            self.camera_cfg.pop(k, None)
        self.label = label
        # Detections this station authored — replayed on reconnect: a
        # vision-server restart kills the SESSION, and detections are
        # per-session state on the server.
        self._detections: dict = {}
        # Set on any failed client call; the next call re-establishes
        # the session before running. NOT the sim gate — a dead session
        # in real mode keeps failing loudly, never demotes to sim.
        self._dead = False

        # Simulation gate. True when explicitly authored OR when there's
        # nothing to connect to (no ip/serial). Real-mode failures must
        # NOT flip this to True — that's the silent-demote bug we removed.
        self.simulation = bool(simulation) or not ip or not serial_number
        self._client = None

        if self.simulation:
            # Workspace does NOT publish for the camera — the vision
            # server owns that bus entry. Workspace sim intent surfaces
            # via DeviceComponent.device_claim, which the panel reads
            # alongside the bus snapshot.
            return

        # Real mode. Import errors are deployment problems; raise rather
        # than pretend to run in sim. The orchestrator surfaces the
        # exception at workspace launch so the operator sees a clear,
        # actionable failure.
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
            self._client.camera_add(
                serial_number=self.serial_number,
                **self.camera_cfg,
            )
            print(
                f"✅ {self.label} connected @ {self.ip}:{self.port} "
                f"(camera {self.serial_number})"
            )
            self._bus_connect()
        except Exception as ex:
            self._safe_close()
            self._client = None
            raise VisionServerUnreachableError(
                f"{self.label}: vision server unreachable @ "
                f"{self.ip}:{self.port}: {type(ex).__name__}: {ex}. "
                f"The camera will appear red on the device bus when the "
                f"server is running."
            ) from ex

    # ── Detection lifecycle ────────────────────────────────────────────

    def _reconnect(self) -> None:
        """One reconnect attempt after a dead socket. The restart that
        killed the socket also killed the server-side session, so this
        re-adds the camera (idempotent on the pool) and re-registers
        every detection this station authored."""
        from dorna_vision_client import VisionClient
        self._safe_close()
        self._client = VisionClient()
        self._client.connect(host=self.ip, port=self.port)
        self._client.camera_add(serial_number=self.serial_number, **self.camera_cfg)
        for name, preset in self._detections.items():
            self._client.detection_add(
                name=name, camera_serial_number=self.serial_number, **preset)
        self._bus_connect()
        print(f"🔁 {self.label}: vision server reconnected @ {self.ip}:{self.port}")

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

    def _call(self, thunk):
        """Client calls fail HONESTLY — the operation is never retried
        behind the action's back. A failure marks the session dead and
        surfaces (capture -> ok False -> CameraUnavailableError -> the
        workflow pauses per the device's critical flag, same as any
        other device). The NEXT call — the action re-running after the
        operator fixed the server and hit Resume — re-establishes the
        session first and runs once. Session re-establishment is
        transport-level plumbing (MQTT's own reconnect is the same
        class); RECOVERY stays explicit: pause, operator, Resume."""
        if self._dead:
            self._reconnect()
            self._dead = False
        try:
            return thunk()
        except Exception:
            self._dead = True
            raise

    def add_detection(self, name: str, **detection_preset: Any) -> bool:
        """Register a detection on the server. Returns False in simulation."""
        if self.simulation or self._client is None:
            return False
        self._detections[name] = dict(detection_preset)
        try:
            self._call(lambda: self._client.detection_add(
                name=name,
                camera_serial_number=self.serial_number,
                **detection_preset,
            ))
            return True
        except Exception as ex:
            print(f"[{self.label}] detection_add({name}) failed: {ex}")
            return False

    def capture(self, name: str, data: Any = None, camera_in_world: Any = None,
                focus: Any = None) -> dict:
        """Capture a fresh atomic snapshot (camera frames + robot joints)
        for ``name`` and cache it on the server.

        Returns the server's reply dict. Use ``ok`` to branch:

            reply = vision.capture("cap_check")
            if reply["ok"]:
                result = vision.detect("cap_check", use_last=True)
            else:
                # snap["msg"] explains why; orchestrator-side retry/pause
                ...

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
        Server errors are surfaced as ``{"ok": False, "msg": ...}``; we
        never raise here, so callers can write linear control flow.
        """
        if self.simulation or self._client is None:
            return {"name": name, "ok": True, "ts": None, "has_joint": False, "sim": True}
        try:
            return self._call(lambda: self._client.detection_capture(
                name, data=data, camera_in_world=camera_in_world, focus=focus))
        except Exception as ex:
            return {"name": name, "ok": False, "msg": f"{type(ex).__name__}: {ex}"}

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
        """Run the named detection. Returns ``sim_return`` in simulation.

        ``sim_return`` (device-guide §17) — explicit sim injection, shaped
        exactly like a real detection result (a list). Its default ``[]``
        is the canned sim value; pass detections to inject them. Real mode
        ignores it for the sim path, but still falls back to it if the
        detection call itself errors (so non-camera failures keep the
        recipe contract).

        **Default behavior (capture → run-on-captured-frame).** When
        ``use_last`` is False, the call first issues a capture for
        ``name`` and only proceeds to detection if the capture
        succeeded. On capture failure, raises
        :class:`CameraUnavailableError` carrying the server's reason —
        the recipe never runs detection on stale or junk data.

        Pass ``use_last=True`` to skip the capture step (e.g. when you
        already called ``capture()`` yourself, or you want to run a
        second detection on the same cached frame). The server uses
        whatever ``det.camera_data`` already holds.

        ``data`` is forwarded to the capture step (see ``capture`` for
        accepted shapes). Ignored when ``use_last=True``.
        """
        if self.simulation or self._client is None:
            return sim_return

        if not use_last:
            # capture → run pattern. Capture errors raise; detection
            # errors fall through the legacy log-and-return-sim_return
            # path so non-camera failures (bad model, missing key) keep
            # the existing recipe contract. ``focus`` rides the capture
            # (it must land BEFORE the grab; on use_last the frame
            # already exists, so it is ignored there).
            snap = self.capture(name, data=data, camera_in_world=camera_in_world,
                                focus=focus)
            if not snap.get("ok"):
                raise CameraUnavailableError(name, snap.get("msg", "capture failed"))
            use_last = True   # run on the just-captured frame

        try:
            return self._call(lambda: self._client.detection_run(name, use_last=use_last, **kwargs))
        except Exception as ex:
            print(f"[{self.label}] detect({name}) failed: {ex}")
            return sim_return

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
