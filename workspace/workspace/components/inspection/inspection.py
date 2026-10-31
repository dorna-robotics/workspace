from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid

from workspace.components.inspection.vision_server import VisionServer
from workspace.components.inspection.vision_station import VisionStation


class Inspection:
    """Camera component: one camera on a vision server, its session and
    detections through :class:`VisionStation`.

    The ``Camera`` object and ``Detection`` runtime live on the vision
    server (typically a different Pi where the camera is plugged in). The
    server is its own component and device (``vision_server``); this
    camera names it in ``camera_cfg.server`` — declared above it in the
    scene. Two cards: ``camera:<sn>`` is the camera hardware (the vision
    server process publishes it), the server's card is every capture and
    detection (a failure puts it down; Recover there).

    Simulation:
        ``simulation=True`` (or no server / no serial) → no session,
        ``detect()`` returns the caller's ``sim_return``. Robot motions in
        the recipes are NOT gated on simulation — only the vision calls.
    """

    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "lens": [0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0],
                "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0],}},
        # All camera-related config under one dict. ``server`` names the
        # vision_server component, ``serial_number`` the camera it should
        # manage; the rest is forwarded to ``camera_add`` on the server.
        camera_cfg={
            "server": "",
            "serial_number": "",
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

        # The camera's session on its server (VisionStation): the
        # simulation gate, camera_add on connect, the add_detection /
        # detect / close surface. Components build in scene order, so
        # the server is declared above the camera.
        cam_cfg = prm["camera_cfg"]
        server = None
        if cam_cfg["server"]:
            comp = workspace.components.get(cam_cfg["server"])
            if not isinstance(comp, VisionServer):
                raise ValueError(f"{name}: camera_cfg.server {cam_cfg['server']!r} is not a "
                                 f"vision_server declared above it in the scene")
            server = comp.server
        self.vision = VisionStation(
            server=server,
            serial_number=cam_cfg["serial_number"],
            camera_cfg=cam_cfg,
            simulation=prm["simulation"],
            label=self.name,
        )

        # Detection the operator "Detect" button runs (the last one
        # registered via ``add_detection``; defaults to "default").
        self._default_detection = "default"

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

    def frame(self, quality: int = 100):
        """One frame from this camera as JPEG bytes, no detection
        (VisionStation.frame); ``None`` in simulation."""
        return self.vision.frame(quality=quality)

    def get_img(self, name: str, kind: str = "img", quality: int = 85, max_side=None, meta=False):
        """The named detection's last image as JPEG bytes (``"img"`` the
        drawn frame, ``"img_roi"`` the crop); ``None`` in simulation or
        on failure; ``meta=True`` adds the server's meta (the full frame's
        ``source_shape``). See VisionStation.get_img."""
        return self.vision.get_img(name, kind=kind, quality=quality, max_side=max_side, meta=meta)

    def detect(self, name: str, sim_return=[], use_last: bool = False, data=None, camera_in_world=None, **kwargs):
        """Run the named detection: the result list, ``[]`` when nothing
        is found, None when the read failed (the server is then down). By
        default captures a fresh frame first and runs on it; pass
        ``use_last=True`` to run on the previously cached frame. See
        VisionStation.detect.

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
        server the detection result, or None when it failed. Inherited by
        every inspection component (module, horizontal, …)."""
        return self.detect(self._default_detection)

    def operator_actions(self) -> list[dict]:
        return [{"label": "Detect", "method": "operator_detect", "icon": "eye"}]

    def close(self):
        self.vision.close()
