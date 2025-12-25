from copy import deepcopy
from mergedeep import merge
from dorna_vision import Detection
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
        # ref joints
        target_solid_name="body",
        target_anchor="place",
        target_offset=[0, 0, 50, 0, 180, 0],
        initial_joints = [0, 0, 0, 0, 0, 0, 0, 0],
        # IK
        left_approach=True,
        base_distance=350,
        rail_step=5.0,
        rail_span=10,        
        # motion
        motion_type="lmove",
        speed_factor=0.5,
        jmove_vaj=[200, 5000, 50000],
        lmove_vaj=[200, 5000, 50000],
        # calibration
        calibration=True,
        calibration_targets={}, # {solid_name: {anchor_1:..., anchor_2:...},...}
        calibration_target_offset=[0, 0, -30, 0, 0, 0],
        calibration_tool_solid_name="body",
        calibration_tool_anchor="tcp",
        calibration_tool_offset=[0, 0, 0, 0, 0, 0],
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

        # init detections
        self.detection = Detection(camera=self.component.camera, robot=None, **self.detection_preset)
        

    """
    present the robot to the insepction component
    """
    def present(self, **kwargs):
        return self.place_in(
            anchor="place",
            solid_name="body",
            approach=True,
            exit=False,
            attachment=False, 
            trigger_io=False,
            padding=50,
            gap=2,
            load_anchor="center", 
            **kwargs)

    
    """
    run detection
    """
    def detect(self, retval=[], **kwargs):
        if not self.component.simulation:
            retval = {d:self.detection[d].run() for d in self.detection}
        return retval

    """
    rotate j5
    """
    def rotate(self, rotation=90, **kwargs):
        # current joint
        current_joint = self.core.robot_api.joint()

        # new_joint
        new_joint = current_joint[:]
        new_joint["j5"] = (new_joint["j5"] + rotation + 175) % 350 - 175

        # motion
        self.core.robot_api.jmove(joint=new_joint, vel=300*self.speed_factor, accel=4000*self.speed_factor, jerk=10000*self.speed_factor)

        # sleep
        self.core.robot_api.sleep(0.1)

        return True



class MobileInspector:
    def __init__(self, workspace, core, component, detection_preset = {}, **kwargs):

        self.workspace = workspace
        self.core = core
        self.component = component

        # detection_preset
        self.detection_preset = detection_preset

        # init detections
        self.detection = Detection(camera=self.core.camera, robot=self.core.robot_api, **self.detection_preset)
        
    
    """
    run detection
    """
    def detect(self, retval=[], **kwargs):
        if not self.component.simulation:
            retval = {d:self.detection[d].run() for d in self.detection}
        return retval
