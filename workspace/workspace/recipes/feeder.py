from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe

class Feeder(Recipe):
    DEFAULTS = dict(
        # ref joints
        target_solid_name="body",
        target_anchor="center",
        target_offset=[0, 0, 50, 0, 180, 0],
        initial_joints = [0, 0, 0, 0, 0, 0, 0, 0],
        # IK
        left_approach=True,
        base_distance=100,
        rail_step=5.0,
        rail_span=10,        
        # motion
        motion_type="lmove",
        speed_factor=0.5,
        jmove_vaj=[200, 1000, 5000],
        lmove_vaj=[200, 1000, 5000],
        # calibration
        calibration=True,
        calibration_targets={}, # {solid_name: {anchor_1:..., anchor_2:...},...}
        calibration_target_offset=[0, 0, -30, 0, 0, 0],
        calibration_tool_solid_name="body",
        calibration_tool_anchor="tcp",
        calibration_tool_offset=[0, 0, 0, 0, 0, 0],
    )

    def __init__(self, workspace, core, component, **kwargs):
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

    
    # mix: mix the feeder for certain turns and shift the slots
    def mix(self, turn=3, shift_slot=3, vaj=[300, 4000, 10000], **kwargs):
        # current joint
        current_joint = self.core.robot_api.joint()

        # new_joint
        new_joint = current_joint[:]
        new_joint[f"j{self.component.axis}"] += 360*turn + shift_slot*(360/self.component.num_slots)

        # motion
        self.core.robot_api.jmove(joint=new_joint, vel=self.component.vaj[0], accel=self.component.vaj[1], jerk=self.component.vaj[2])
        return True


    # roate the feeder to move to the nth slot from the current
    def move(self, step=1, **kwargs):
        # current joint
        current_joint = self.core.robot_api.joint()

        # new_joint
        new_joint = current_joint[:]
        new_joint[f"j{self.component.axis}"] += step*(360/self.component.num_slots)

        # motion
        self.core.robot_api.jmove(joint=new_joint, vel=self.component.vaj[0], accel=self.component.vaj[1], jerk=self.component.vaj[2])
        return True
