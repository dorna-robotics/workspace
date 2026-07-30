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
        the supplied ``sim_return`` (default ``[]``). Use during dev / on a
        machine that can't reach the vision server. Robot motions in the
        recipes are NOT gated on simulation — only the detection call is.
    """

    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "lens": [0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0],
                "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0],}},
        # All camera-related config under one dict (mirrors core.py).
        # ``serial_number`` / ``ip`` / ``port`` identify the vision
        # server and the camera it should manage; the rest is forwarded
        # to ``camera_add`` on the server.
        camera_cfg={
            "serial_number": "",
            "ip": "127.0.0.1",
            "port": 80,
            # Camera driver on the vision server: "d405" (RealSense,
            # depth+color) or "ueye_xs" (IDS uEye XS, color + autofocus).
            "type": "d405",
            "stream": {"width":1280, "height":720, "fps":30},
            "mode": "bgrd",
            "filter": {},
            "exposure": None,
            "native_res": None,
        },
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
        cam_cfg = prm["camera_cfg"]
        self.vision = VisionStation(
            ip=cam_cfg.get("ip", "127.0.0.1"),
            port=int(cam_cfg.get("port", 80)),
            serial_number=cam_cfg.get("serial_number", ""),
            camera_cfg=cam_cfg,
            simulation=prm["simulation"],
            label=self.name,
        )

        # Detection the operator "Detect" button runs (the last one
        # registered via ``add_detection``; defaults to "default").
        self._default_detection = "default"

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
        self._default_detection = name
        return self.vision.add_detection(name, **detection_preset)

    def lens_pose(self) -> list:
        """The lens's CURRENT world pose (xyzabc) — the per-capture
        frame the Inspector passes so detections report in scene
        coordinates. Fixed vs robot-mounted is purely where this
        component sits in the kinematic tree."""
        return [float(v) for v in self.assembly["body"].pose("lens")]

    def capture(self, name: str, data=None, camera_in_world=None) -> dict:
        """Capture a fresh atomic snapshot (camera frames + robot joints)
        and cache it server-side. Pair with ``detect(name, use_last=True)``
        so detection runs only on a confirmed-fresh frame. See
        VisionStation.capture for the reply shape and ``data`` modes.
        """
        return self.vision.capture(name, data=data, camera_in_world=camera_in_world)

    def detect(self, name: str, sim_return=[], use_last: bool = False, data=None, camera_in_world=None, **kwargs):
        """Run the named detection. By default, captures a fresh frame
        first and runs on it (raises ``CameraUnavailableError`` on
        capture failure). Pass ``use_last=True`` to skip capture and
        run on the previously cached frame. See VisionStation.detect.

        ``sim_return`` (device-guide §17) — the detection result returned
        in sim (default ``[]``); pass detections to inject them.
        """
        return self.vision.detect(name, sim_return=sim_return, use_last=use_last, data=data,
                                  camera_in_world=camera_in_world, **kwargs)

    # ── Operator action (workspace.components.operator_actions) ────────

    def operator_detect(self):
        """No-arg ``detect`` for the Operator Actions UI — runs the
        default detection (the last one registered, or "default"). In
        sim this returns the canned ``sim_return``; against a real vision
        server it returns the detection result. Inherited by every
        inspection component (module, horizontal, …)."""
        return self.detect(self._default_detection)

    def operator_actions(self) -> list[dict]:
        return [{"label": "Detect", "method": "operator_detect", "icon": "eye"}]

    def close(self):
        self.vision.close()
