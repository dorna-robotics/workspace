from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe


"""
detection_preset shape: a dict configuring a single Detection on the vision server.
Example:
    detection_preset = {
        "roi":       {"corners": [], "inv": False, "crop": False},
        "detection": {"cmd": "kp", "path": "model/microplate_keypoint.pkl", "conf": 0.5, "cls": {}},
        "sort":      {"cmd": "shuffle", "max_det": 100},
        "display":   {"label": 0, "save_img": False, "save_img_roi": False},
    }

The recipe registers this preset under a single name (default "default") on the
vision server during construction; ``detect()`` runs it via RPC. Robot motions
(``present()``, ``rotate()``) always run — only the detection call short-circuits
in simulation, so workflow timing stays the same with or without hardware.
"""


class FixedInspector(Recipe):
    DEFAULTS = dict(
        base_distance=200,
        # ref joints
        target_anchor="place",
    )

    def __init__(self, workspace, core, component, detection_preset=None,
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

        # Cache the name we registered the detection under so detect() can
        # find it. Each FixedInspector instance owns one named detection.
        self.detection_name = detection_name
        if detection_preset:
            self.component.add_detection(self.detection_name, **detection_preset)

    def present(self, approach=True, padding=50, load_anchor="center", **kwargs):
        """Position the held item in front of the inspector's camera.

        Robot motion runs whether or not we're in simulation — only
        ``detect()`` returns canned values when the component is offline.
        """
        return self.place(
            anchor="place",
            solid_name="body",
            approach=approach,
            exit=False,
            attachment=False,
            trigger_io=False,
            padding=padding,
            gap=2,
            load_anchor=load_anchor,
            gravity_offset=0,
            **kwargs,
        )

    def capture(self, data=None) -> dict:
        """Capture a fresh atomic snapshot for this inspector's detection
        (camera frames + robot joints) and cache it server-side.

        Pair with ``detect(use_last=True)`` when you want one frame to
        feed multiple detections, or when you need to branch on capture
        success before running the (potentially expensive) detection.

        ``detect()`` already calls capture internally by default — only
        use this when you specifically want the two-step.
        """
        return self.component.capture(self.detection_name, data=data)

    def detect(self, sim_return=True, **kwargs):
        """Run the inspector's detection. By default, captures a fresh
        frame first and runs on it; raises ``CameraUnavailableError``
        on capture failure (so the recipe never operates on stale data).

        Returns ``sim_return`` (default True) in simulation — device-guide
        §17. Pass ``use_last=True`` to skip capture and run on the
        previously-cached frame; pass ``data=...`` (None / dict /
        server-local path) to bypass the live camera for replay/testing.
        """
        return self.component.detect(self.detection_name, sim_return=sim_return, **kwargs)

    def rotate(self, rotation=90, **kwargs):
        """Rotate j5 — used to flip the camera angle."""
        return super().rotate(rotation=rotation, joint="j5", **kwargs)


class MobileInspector:
    """Robot-mounted camera. Detection runs on the vision server using the
    Core's camera; this recipe is a thin wrapper around ``core.detect()``.

    Same simulation semantics as FixedInspector: the recipe stays usable when
    the vision server is unreachable, ``detect()`` just returns canned values.
    """

    def __init__(self, workspace, core, detection_preset=None,
                 detection_name="default", **kwargs):
        self.workspace = workspace
        self.core = core
        self.detection_name = detection_name
        if detection_preset:
            self.core.add_detection(self.detection_name, **detection_preset)

    def capture(self, data=None) -> dict:
        """Capture a fresh atomic snapshot. See FixedInspector.capture."""
        return self.core.capture(self.detection_name, data=data)

    def detect(self, sim_return=True, **kwargs):
        """Run the inspector's detection. Default: captures-then-runs on
        a fresh frame; raises ``CameraUnavailableError`` on capture
        failure. See FixedInspector.detect for the full contract.
        """
        return self.core.detect(self.detection_name, sim_return=sim_return, **kwargs)
