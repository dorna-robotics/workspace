from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe

class Feeder(Recipe):
    DEFAULTS = dict(
        # IK
        base_distance=100,
        # calibration
        calibration_targets={"body":["clb_0"]}, # {solid_name: {anchor_1:..., anchor_2:...},...}
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
        self.core.robot_api.jmove(joint=new_joint, vel=vaj[0], accel=vaj[1], jerk=vaj[2])
        return True


    # roate the feeder to move to the nth slot from the current
    def move(self, step=1, vaj=[300, 4000, 10000], **kwargs):
        # current joint
        current_joint = self.core.robot_api.joint()

        # new_joint
        new_joint = current_joint[:]
        new_joint[f"j{self.component.axis}"] += step*(360/self.component.num_slots)

        # motion
        self.core.robot_api.jmove(joint=new_joint, vel=vaj[0], accel=vaj[1], jerk=vaj[2])
        return True
