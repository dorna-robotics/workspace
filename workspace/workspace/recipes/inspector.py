from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe


"""
ONE inspector for fixed AND robot-mounted cameras. Fixed vs mobile is a
SCENE property — where the camera solid sits in the kinematic tree —
not a class split. Every capture/detect states the lens's world pose at
imaging time (``camera_in_world``): the workspace is the single
kinematic authority, and the vision server never models the robot.

Contract (vision-guide §5):
  * capture at REST — ``present()`` ends checkpointed; mobile detection
    while moving is not supported.
  * the component's ``lens`` anchor is calibrated — every world result
    inherits its accuracy. A component without a ``lens`` anchor (the
    core camera, until its mount is calibrated) passes no pose and the
    server falls back to its configured ``base_in_world``.

detection_preset shape: a dict configuring a single Detection on the vision server.
Example:
    detection_preset = {
        "roi":       {"corners": [], "inv": False, "crop": False},
        "detection": {"cmd": "kp", "path": "model/microplate_keypoint.pkl", "conf": 0.5, "cls": {}},
        "sort":      {"cmd": "shuffle", "max_det": 100},
        "display":   {"label": 0, "save_img": False, "save_img_roi": False},
    }

Full reference — camera wiring, intrinsics, save_img paths, USB
fallback, ROI boxes: docs/vision-guide.md.

The recipe registers this preset under a single name (default "default") on the
vision server during construction; ``detect()`` runs it via RPC. Robot motions
(``present()``, ``rotate()``) always run — only the detection call short-circuits
in simulation, so workflow timing stays the same with or without hardware.
"""


class Inspector(Recipe):
    DEFAULTS = dict(
        base_distance=200,
        # ref joints
        target_anchor="place",
    )

    def __init__(self, workspace, core, component=None, detection_preset=None,
                 detection_name="default", **kwargs):
        # prm
        prm = deepcopy(Recipe.DEFAULTS)
        merge(prm, self.DEFAULTS)
        merge(prm, kwargs)

        super().__init__(
            workspace=workspace,
            core=core,
            component=component,
            **prm,
        )

        # component=None -> the robot-mounted core camera (no station,
        # no motion surface); detections run through core's vision.
        self._vision_owner = component if component is not None else core

        # Cache the name we registered the detection under so detect() can
        # find it. Each Inspector instance owns one named detection.
        self.detection_name = detection_name
        if detection_preset:
            self._vision_owner.add_detection(self.detection_name, **detection_preset)

    def _camera_in_world(self):
        """The lens's world pose at THIS moment — the per-capture frame.
        None when the owning component has no calibrated ``lens`` anchor
        yet (the server then uses its configured base_in_world)."""
        try:
            return self._vision_owner.lens_pose()
        except Exception:
            return None

    def present(self, approach=True, padding=50, soft_approach=False, load_anchor="center", **kwargs):
        """Position the held item in front of the inspector's camera.

        Robot motion runs whether or not we're in simulation — only
        ``detect()`` returns canned values when the component is offline.
        Requires a station component (raises on the core-camera form —
        the robot-mounted camera moves with the arm instead).
        """
        if self.component is None:
            raise ValueError("present() needs a station component — the "
                             "robot-mounted camera moves with the arm instead")
        return self.place(
            anchor="place",
            solid_name="body",
            approach=approach,
            exit=False,
            attachment=False,
            trigger_io=False,
            padding=padding,
            gap=2,
            soft_approach=soft_approach,
            load_anchor=load_anchor,
            gravity_offset=0,
            **kwargs,
        )

    def capture(self, data=None) -> dict:
        """Capture a fresh atomic snapshot for this inspector's detection
        and cache it server-side, stamped with the lens's current world
        pose. Pair with ``detect(use_last=True)`` for one-frame/many-
        detections flows; ``detect()`` already captures by default.
        """
        return self._vision_owner.capture(
            self.detection_name, data=data,
            camera_in_world=self._camera_in_world())

    def detect(self, sim_return=True, **kwargs):
        """Run the inspector's detection. By default, captures a fresh
        frame first (stamped with the lens's current world pose) and
        runs on it; raises ``CameraUnavailableError`` on capture
        failure. Returns ``sim_return`` (default True) in simulation —
        device-guide §17. ``use_last=True`` skips capture; ``data=...``
        bypasses the live camera for replay/testing.
        """
        kwargs.setdefault("camera_in_world", self._camera_in_world())
        return self._vision_owner.detect(self.detection_name, sim_return=sim_return, **kwargs)

    def rotate(self, rotation=90, **kwargs):
        """Rotate j5 — used to flip the camera angle."""
        return super().rotate(rotation=rotation, joint="j5", **kwargs)
