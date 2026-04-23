from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe

"""
detection comes in a dictionary of names, and it can run multiple detections at once
and the result will also return in that format as well
det_preset = {
            'roi': {'corners': [], 'inv': False, 'crop': False}, 
            'detection': {'cmd': 'kp', 'path': 'model/microplate_keypoint.pkl', 'conf': 0.5, 'cls': {}},
            'sort': {'cmd': 'shuffle', 'max_det': 100},
            'display': {'label': 0, 'save_img': False, 'save_img_roi': False}
            }

result = []
"""
class FixedInspector(Recipe):
    DEFAULTS = dict(
        base_distance=200,
        # ref joints
        target_anchor="place",
    )

    def __init__(self, workspace, core, component, detection_preset = {}, **kwargs):
        # prm
        prm = deepcopy(Recipe.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, kwargs) # kwargs

        super().__init__(
            workspace=workspace,
            core=core,
            component=component,
            **prm
        )

        # detection_preset
        self.detection_preset = detection_preset

        try:
            from dorna_vision import Detection
            # init detections
            self.detection = Detection(camera=self.component.camera, robot=None, **self.detection_preset)
        except Exception as ex:
            self.detection
            print(f"[Detection disabled] {ex}")

    """
    present the robot to the insepction component
    """
    def present(self, approach=True, padding=50, load_anchor="center", **kwargs):
        """Position the held item in front of the fixed inspector's camera.

        A lightweight ``place`` (no release, no attach, no IO, gravity_offset=0)
        that holds the load at the inspector's ``place`` anchor so the camera
        can see it. Follow with ``detect(...)`` to run inspection.
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
            **kwargs)


    def detect(self, retval=True, **kwargs):
        """Run detection on the inspector's current view.

        Returns ``retval`` (default True) in simulation or when no detection
        object is configured; otherwise returns whatever ``Detection.run`` yields.
        """
        if not self.component.simulation and self.detection is not None:
            retval = self.detection.run(**kwargs)
        return retval


    def rotate(self, rotation=90, **kwargs):
        """Rotate j5 by ``rotation`` degrees — used to flip the camera angle."""
        return super().rotate(rotation=rotation, joint="j5", **kwargs)


class MobileInspector:
    def __init__(self, workspace, core, detection_preset = {}, **kwargs):

        self.workspace = workspace
        self.core = core

        # detection_preset
        self.detection_preset = detection_preset

        # init detections
        try:
            from dorna_vision import Detection
            # init detections
            self.detection = Detection(camera=self.core.camera, robot=self.core.robot_api, camera_mount=self.core.camera_mount, **self.detection_preset)
        except Exception as ex:
            self.detection = None
            print(f"[Detection disabled] {ex}")

    
    def detect(self, retval=True, **kwargs):
        """Run detection through the robot-mounted camera (MobileInspector).

        Re-binds ``detection.robot`` to the current ``core.robot_api`` before
        running so detection stays consistent across robot reconnections.
        Returns ``retval`` in simulation.
        """
        if not self.core._simulation_mode and self.detection is not None:
            # ensure Detection always points to the current robot API
            if self.detection.robot is not self.core.robot_api:
                self.detection.robot = self.core.robot_api

            retval = self.detection.run(**kwargs)
        return retval
