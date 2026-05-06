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
        simulation: Authored simulation intent. When True (or when host/
            serial are empty so there's nothing real to talk to), no
            client is opened, ``detect()`` returns canned values, and
            the camera is stubbed onto the device bus with a SIM badge.
            Failures in real mode never flip this — they raise.
        label: Free-form name for log lines (e.g. component name).

    Raises:
        VisionClientImportError: ``simulation=False`` but the
            ``dorna_vision_client`` package is not importable.
        VisionServerUnreachableError: ``simulation=False`` but the
            vision server's host/port refuses connections or the camera
            can't be registered.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        serial_number: str,
        camera_cfg: Optional[dict] = None,
        simulation: bool = True,
        label: str = "vision",
    ):
        self.host = host
        self.port = int(port)
        self.serial_number = serial_number
        self.camera_cfg = dict(camera_cfg or {})
        self.label = label

        # Simulation gate. True when explicitly authored OR when there's
        # nothing to connect to (no host/serial). Real-mode failures must
        # NOT flip this to True — that's the silent-demote bug we removed.
        self.simulation = bool(simulation) or not host or not serial_number
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
            self._client.connect(host=self.host, port=self.port)
            self._client.camera_add(
                serial_number=self.serial_number,
                **self.camera_cfg,
            )
            print(
                f"✅ {self.label} connected @ {self.host}:{self.port} "
                f"(camera {self.serial_number})"
            )
        except Exception as ex:
            self._safe_close()
            self._client = None
            raise VisionServerUnreachableError(
                f"{self.label}: vision server unreachable @ "
                f"{self.host}:{self.port}: {type(ex).__name__}: {ex}. "
                f"The camera will appear red on the device bus when the "
                f"server is running."
            ) from ex

    # ── Detection lifecycle ────────────────────────────────────────────

    def add_detection(self, name: str, **detection_preset: Any) -> bool:
        """Register a detection on the server. Returns False in simulation."""
        if self.simulation or self._client is None:
            return False
        try:
            self._client.detection_add(
                name=name,
                camera_serial_number=self.serial_number,
                **detection_preset,
            )
            return True
        except Exception as ex:
            print(f"[{self.label}] detection_add({name}) failed: {ex}")
            return False

    def capture(self, name: str, data: Any = None) -> dict:
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

        In simulation, returns ``{"ok": True, "ts": None, "has_joint": False,
        "sim": True}`` so callers can skip the capture/run split branch.
        Server errors are surfaced as ``{"ok": False, "msg": ...}``; we
        never raise here, so callers can write linear control flow.
        """
        if self.simulation or self._client is None:
            return {"name": name, "ok": True, "ts": None, "has_joint": False, "sim": True}
        try:
            return self._client.detection_capture(name, data=data)
        except Exception as ex:
            return {"name": name, "ok": False, "msg": f"{type(ex).__name__}: {ex}"}

    def detect(
        self,
        name: str,
        retval: Any = [],
        use_last: bool = False,
        data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Run the named detection. Returns ``retval`` in simulation.

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
            return retval

        if not use_last:
            # capture → run pattern. Capture errors raise; detection
            # errors fall through the legacy log-and-return-retval
            # path so non-camera failures (bad model, missing key) keep
            # the existing recipe contract.
            snap = self.capture(name, data=data)
            if not snap.get("ok"):
                raise CameraUnavailableError(name, snap.get("msg", "capture failed"))
            use_last = True   # run on the just-captured frame

        try:
            return self._client.detection_run(name, use_last=use_last, **kwargs)
        except Exception as ex:
            print(f"[{self.label}] detect({name}) failed: {ex}")
            return retval

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
