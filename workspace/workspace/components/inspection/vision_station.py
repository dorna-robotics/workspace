"""VisionStation — shared helper that wraps a dorna_vision client.

Used by any component that owns a remote camera + detections on a vision
server: today the ``Inspection`` component (fixed camera) and ``Core`` (robot-
mounted camera). Tomorrow any new station that follows the same pattern.

Responsibilities:
  * hold a single ``VisionClient`` connection (or none, in simulation),
  * register the camera on the server during construction,
  * expose ``add_detection``, ``capture``, and ``detect``,
  * make the **capture → run-on-captured-frame** pattern the default for
    ``detect()`` so recipes never accidentally run on stale or junk data,
  * collapse failure modes into ``simulation=True`` so the workflow keeps
    running even when the vision server is unreachable.

Health monitoring is orthogonal: the vision server publishes the camera's
state to the device bus via its adapter; the orchestrator consumes those
events independently of this helper. See ``docs/device-guide.md``.
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


class VisionStation:
    """Tiny VisionClient wrapper with a simulation fallback.

    Args:
        host: Vision server hostname or IP.
        port: Vision server port.
        serial_number: USB serial of the camera the server should manage.
        camera_cfg: Dict forwarded to ``camera_add`` (stream, K, D, mode, ...).
        simulation: When True (or when host/serial are empty), no client is
            opened and ``detect()`` returns canned values.
        label: Free-form name for log lines (e.g. component name).
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

        # Simulation gate. True when explicitly requested OR when there's
        # nothing to connect to. Auto-falls back to True if the connect
        # fails so the component stays usable.
        self.simulation = bool(simulation) or not host or not serial_number
        self._client = None

        if not self.simulation:
            # Try to import the client first — it's a separate Python
            # package (``dorna_vision_client``) that must be installed
            # next to ``workspace``. Surface this as a config error
            # rather than a "server unreachable", since restarting the
            # vision server won't fix a missing import.
            try:
                from dorna_vision_client import VisionClient
            except ImportError as ex:
                print(
                    f"❌ {self.label} dorna_vision_client not installed "
                    f"({ex}) — install it with:\n"
                    f"    sudo pip3 install -e /path/to/vision/dorna_vision-client\n"
                    f"  Falling back to simulation mode for now."
                )
                self._client = None
                self.simulation = True
                return

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
                print(
                    f"❌ {self.label} vision server unreachable @ "
                    f"{self.host}:{self.port}: {type(ex).__name__}: {ex} — "
                    "falling back to simulation mode"
                )
                self._safe_close()
                self._client = None
                self.simulation = True

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
