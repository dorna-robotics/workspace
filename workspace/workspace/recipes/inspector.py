import numpy as np
from dorna_vision import Detection

import workspace.recipes.util as util
from workspace.recipes.plate import Plate

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
class FixedInspector(Plate):
    def __init__(self, workspace, core, 
        container, # component
        solid_name = None,
        anchor = "place",
        padding = 50,
        gap = 2, # mm
        ref_joints = [0, 0, 0, 0, 0, 0, 0, 0],
        speed_factor=0.5,
        left_approach=True,
        base_distance=350,
        rail_step=5.0,
        rail_span=10,
        jmove_vaj=[200, 5000, 50000],
        lmove_vaj=[200, 5000, 50000],
        motion="lmove",
        detection_preset = {},
        **kwargs
        ):

        # super
        super().__init__(
            workspace=workspace,
            core=core,
            container=container,
            solid_name=solid_name,
            anchor=anchor,
            padding=padding,
            gap=gap,
            ref_joints=ref_joints,
            speed_factor=speed_factor,
            left_approach=left_approach,
            base_distance=base_distance,
            rail_step=rail_step,
            rail_span=rail_span,
            jmove_vaj=jmove_vaj,
            lmove_vaj=lmove_vaj,
            motion=motion,
            **kwargs
        )

        # detection_preset
        self.detection_preset = detection_preset

        # init detections
        self.detection = Detection(camera=self.container.camera, robot=None, **self.detection_preset)
        

    """
    present the robot to the insepction component
    """
    def present(self, load_anchor="center", **kwargs):
        return self.place_in(index="place", container=None, offset=None, approach=True, exit=False, output=False, load_anchor=load_anchor, **kwargs)
    
    """
    run detection
    """
    def detect(self, retval=[], **kwargs):
        if not self.container.simulation:
            retval = {d:self.detection[d].run() for d in self.detection}
        return retval

    """
    rotate j5
    """
    def rotate(self, d_j5=90, **kwargs):
        # current joint
        current_joint = self.core.robot_api.joint()

        # new_joint
        new_joint = current_joint[:]
        new_joint["j5"] = (new_joint["j5"] + d_j5 + 175) % 350 - 175

        # motion
        self.core.robot_api.jmove(new_joint, vel=300*self.speed_factor, accel=4000*self.speed_factor, jerk=10000*self.speed_factor)

        # sleep
        self.core.robot_api.sleep(0.1)

        return True



class MobileInspector():
    def __init__(self, workspace, core,
        detection_preset = {},
        **kwargs
        ):

        self.workspace = workspace
        self.core = core

        # detection_preset
        self.detection_preset = detection_preset

        # init detections
        self.detection = Detection(camera=self.core.camera, robot=self.core.robot_api, **self.detection_preset)
        
    
    """
    run detection
    """
    def detect(self, retval=[], **kwargs):
        if not self.container.simulation:
            retval = {d:self.detection[d].run() for d in self.detection}
        return retval
