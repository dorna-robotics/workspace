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

    def detect(self, retval=True, **kwargs):
        """Forward to the component, which round-trips to the vision server.
        Returns ``retval`` (default True) in simulation."""
        return self.component.detect(self.detection_name, retval=retval, **kwargs)

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

    def detect(self, retval=True, **kwargs):
        return self.core.detect(self.detection_name, retval=retval, **kwargs)
