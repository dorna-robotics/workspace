"""VisionStation — shared helper that wraps a dorna_vision client.

Used by any component that owns a remote camera + detections on a vision
server: today the ``Inspection`` component (fixed camera) and ``Core`` (robot-
mounted camera). Tomorrow any new station that follows the same pattern.

Responsibilities:
  * hold a single ``VisionClient`` connection (or none, in simulation),
  * register the camera on the server during construction,
  * expose ``add_detection(name, ...)`` and ``detect(name, ...)``,
  * collapse failure modes into ``simulation=True`` so the workflow keeps
    running even when the vision server is unreachable.

Health monitoring is orthogonal: the vision server publishes the camera's
state to the device bus via its adapter; the orchestrator consumes those
events independently of this helper. See ``docs/device-guide.md``.
"""

from __future__ import annotations

from typing import Any, Optional


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
            try:
                from dorna_vision_client import VisionClient
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
                    f"❌ {self.label} vision server unreachable: {ex} — "
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

    def detect(self, name: str, retval: Any = [], **kwargs: Any) -> Any:
        """Run the named detection on the server. Returns ``retval`` in simulation."""
        if self.simulation or self._client is None:
            return retval
        try:
            return self._client.detection(name).run(**kwargs)
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
