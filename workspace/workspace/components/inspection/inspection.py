from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid

from workspace.components.inspection.vision_station import VisionStation


class Inspection:
    """Vision-station component: holds a connection to a remote dorna_vision
    server and proxies camera / detection lifecycle through it via the
    shared :class:`VisionStation` helper.

    The actual ``Camera`` object and ``Detection`` runtime live on the vision
    server (typically a different Pi where the USB camera is plugged in).
    This component is a thin client — it registers the camera + detections
    on the server, and exposes ``detect(name)`` which round-trips an RPC.

    Health monitoring is orthogonal: the vision server publishes camera
    health to the device bus via its adapter; the orchestrator consumes
    those events independently of this component. See ``docs/device-guide.md``.

    Simulation:
        ``simulation=True`` → no VisionClient is opened, ``detect()`` returns
        the supplied ``retval`` (default ``[]``). Use during dev / on a
        machine that can't reach the vision server. Robot motions in the
        recipes are NOT gated on simulation — only the detection call is.
    """

    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "camera": [0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0],
                "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0],}},
        camera_cfg={
            "stream": {"width":848, "height":480, "fps":15},
            "K": None,
            "D": None,
            "mode": "bgrd",
            "filter": {},
            "exposure": None,
            "native_res": None,
        },
        # cfg
        camera_serial_number="",
        vision_server_host="127.0.0.1",
        vision_server_port=80,
        simulation=True,
    )

    def __init__(self, name: str, workspace, type=None, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS)
        merge(prm, kwargs)

        # init
        self.name = name
        self.workspace = workspace
        self.type = type

        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        }

        # slot
        self.slot = {
           "body": ["place"]
        }

        # Vision server connection (shared helper). VisionStation handles
        # the simulation gate, connect-or-fall-back-to-sim, camera_add on
        # connect, and the add_detection / detect / close surface.
        self.vision = VisionStation(
            host=prm["vision_server_host"],
            port=prm["vision_server_port"],
            serial_number=prm["camera_serial_number"],
            camera_cfg=prm["camera_cfg"],
            simulation=prm["simulation"],
            label=self.name,
        )

    # ── DeviceComponent contract (workspace.devices.DeviceComponent) ───

    @property
    def device_ids(self) -> list[str]:
        """Device ids this component depends on. See docs/device-guide.md §8."""
        sn = self.vision.serial_number
        return [f"camera:{sn}"] if sn else []

    def device_claim(self, device_id: str) -> str:
        """Project-level sim/real claim for ``device_id``.

        The vision server owns the camera's bus entry (it holds the USB
        handle), so the workspace cannot truthfully publish the camera
        as sim. Instead, this method tells the panel + orchestrator
        that *this project* uses the camera in sim mode — a workspace-
        side annotation that overlays the daemon's bus state without
        overwriting it.
        """
        sn = self.vision.serial_number
        if sn and device_id == f"camera:{sn}":
            return "sim" if self.vision.simulation else "real"
        return "real"

    # ── Convenience wrappers (delegate to the helper) ──────────────────

    def add_detection(self, name: str, **detection_preset) -> bool:
        return self.vision.add_detection(name, **detection_preset)

    def capture(self, name: str, data=None) -> dict:
        """Capture a fresh atomic snapshot (camera frames + robot joints)
        and cache it server-side. Pair with ``detect(name, use_last=True)``
        so detection runs only on a confirmed-fresh frame. See
        VisionStation.capture for the reply shape and ``data`` modes.
        """
        return self.vision.capture(name, data=data)

    def detect(self, name: str, retval=[], use_last: bool = False, data=None, **kwargs):
        """Run the named detection. By default, captures a fresh frame
        first and runs on it (raises ``CameraUnavailableError`` on
        capture failure). Pass ``use_last=True`` to skip capture and
        run on the previously cached frame. See VisionStation.detect.
        """
        return self.vision.detect(name, retval=retval, use_last=use_last, data=data, **kwargs)

    def close(self):
        self.vision.close()
