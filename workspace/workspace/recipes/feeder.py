from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe
import numpy as np


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

        # mix sign
        self.mix_sign = 1

    #???
    # mix: mix the feeder for certain turns and shift the slots
    def mix(self, turn=1, shift_slot=5, vaj=[200, 600, 3000], direction_thr=10000, **kwargs):
        # current joint
        current_joint = self.core.robot_api.joint()

        # new_joint
        new_joint = current_joint[:]
        # change the direction if necessary
        if abs(new_joint[self.component.axis]) > direction_thr:
            self.mix_sign = -1 * self.mix_sign
        
        new_joint[self.component.axis] += self.mix_sign*(360*turn + shift_slot*(360/self.component.num_slots))

        # motion
        return self.core.robot_api.jmove(joint=new_joint, vel=vaj[0], accel=vaj[1], jerk=vaj[2])


    # roate the feeder to move to the nth slot from the current
    def move(self, step=1, vaj=[200, 600, 3000], **kwargs):
        # current joint
        current_joint = self.core.robot_api.joint()

        # new_joint
        new_joint = current_joint[:]
        new_joint[self.component.axis] += step*(360/self.component.num_slots)

        # motion
        return self.core.robot_api.jmove(joint=new_joint, vel=vaj[0], accel=vaj[1], jerk=vaj[2])
    

    def pick(self, anchor="place", solid_name="body", component=None, approach=True, actions=[], exit=True, attachment=True, trigger_io=True, padding=25, gap=2, tool_tcp_z_offset=0, tool_tip_z_offset=0, **kwargs):
        return self.pick_from(anchor=anchor, solid_name=solid_name, component=component, approach=approach, actions=actions, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, tool_tcp_z_offset=tool_tcp_z_offset, tool_tip_z_offset=tool_tip_z_offset, **kwargs)


    def above(self, anchor="place", solid_name="body", component=None, padding=25, gap=2, tool_tcp_z_offset=0, tool_tip_z_offset=0, **kwargs):
        return super().above(anchor=anchor, solid_name=solid_name, component=component, padding=padding, gap=gap, tool_tcp_z_offset=tool_tcp_z_offset, tool_tip_z_offset=tool_tip_z_offset, **kwargs)
    

    """
    # use inspector to check if cap is present
    # if not mix and run again
    # if yes, present that position to the pick position of the feeder
    # index_list contains a lsit of indices to check, each element is a index(step) and its preset
    """
    def present_cap(self, inspector, index_list=[], **kwargs):
        # empty index list
        if not index_list:
            return False
        
        # loop over index list
        for step, preset in index_list:
            # object exists
            if inspector.detect(**preset, **kwargs):
                # move the feeder to that position
                return self.move(step=step)
        
        # mix
        self.mix()
        
        # run recursively
        return self.present_cap(inspector=inspector, index_list=index_list, **kwargs)



