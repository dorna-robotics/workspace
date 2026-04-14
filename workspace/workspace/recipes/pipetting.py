from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe, RecipeError
from dorna2 import pose as dorna_pose
import time

class PipettingSite(Recipe):
    DEFAULTS = dict(
        # ref joints
        target_offset=[0, 0, 150, 0, 180, 0],
        # IK
        rail_step=20, # 5
        rail_span=5, # 10        
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


    def pick_tip(self, anchor="place", padding=70, **kwargs):
        # find plate component
        solid_plate = self.solid_attached_to_anchor(self.component.assembly["body"], "place")
        component = self.workspace.components[solid_plate.component]
        solid_name = next(k for k, v in component.assembly.items() if v is solid_plate)

        # motion
        if not self.pick(anchor=anchor, solid_name=solid_name, component=component, padding=padding, trigger_io=False, soft_approach=True, **kwargs):
            raise RecipeError("pick_tip failed — could not pick from anchor")
        
        # check if tip is there
        pipette = self.tool_attached_to_the_robot()
        if pipette is None:
            raise RecipeError("no pipette attached to the robot")
        
        # make sure tip exists
        if not pipette._simulation_mode:  
            return pipette.device.has_tip()
        return True


    # the action of ejecting tip
    def eject_tip(self, anchor="A1", shake_travel=5, **kwargs):
        # find rack component
        solid_plate = self.solid_attached_to_anchor(self.component.assembly["body"], "place")
        component = self.workspace.components[solid_plate.component]
        solid_name = next(k for k, v in component.assembly.items() if v is solid_plate)

        # find the pipette
        pipette = self.tool_attached_to_the_robot()
        if pipette is None:
            raise RecipeError("no pipette attached to the robot")
        
        actions = []
        # no simulation
        if not pipette._simulation_mode:
            actions = [[pipette.device.eject_tip, [], {}]]
        
        # motion prm
        motion_prm = self.place_setting(anchor=anchor, solid_name=solid_name, component=component, actions=actions, trigger_io=False, gravity_offset=0, **kwargs)

        # adjust exit_path
        motion_prm["exit_path"] = [[shake_travel, 0, motion_prm["height_load"], 0, 0, 0], [-shake_travel, 0, motion_prm["height_load"], 0, 0, 0], [shake_travel, 0, motion_prm["height_load"], 0, 0, 0]] + motion_prm["exit_path"]
        
        # run the motion
        motion_result = self.touch(**motion_prm)
        
        if not pipette._simulation_mode:
            # sleep
            time.sleep(0.25)

            # make sure tip is ejected
            return not pipette.device.has_tip()
        else:
            return motion_result


    # go on top of the source, and go down for the amount
    def immerse(self, anchor="place", depth=0, approach=True, padding=50, **kwargs):
        # find plate component
        solid_plate = self.solid_attached_to_anchor(self.component.assembly["body"], "place")
        component = self.workspace.components[solid_plate.component]
        solid_name = next(k for k, v in component.assembly.items() if v is solid_plate)

        # check if pipette is there
        pipette = self.tool_attached_to_the_robot()
        if pipette is None:
            raise RecipeError("no pipette attached to the robot")
        
        # tip solid
        tip_solid = self.solid_attached_to_tool(pipette)

        # tip length
        tip_length = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                from_frame=tip_solid.pose("center"),
                                to_frame=tip_solid.pose("top"))[2])

        # tool offset
        tool_tcp_z_offset = tip_length - depth
        tool_tip_z_offset = tip_length - depth

        # motion
        return self.pick(anchor=anchor, solid_name=solid_name, component=component, approach=approach, actions=[], exit=False, attachment=False, trigger_io=False, padding=padding, gap=2, tool_tcp_z_offset=tool_tcp_z_offset, tool_tip_z_offset=tool_tip_z_offset, **kwargs)


    # given the component, go on top of the source
    # anchor of the rack, plate or the item, look for the tube there
    def retract(self, anchor="place", padding=50, **kwargs):
        # find plate component
        solid_plate = self.solid_attached_to_anchor(self.component.assembly["body"], "place")
        component = self.workspace.components[solid_plate.component]
        solid_name = next(k for k, v in component.assembly.items() if v is solid_plate)

        # check if pipette is there
        pipette = self.tool_attached_to_the_robot()
        if pipette is None:
            raise RecipeError("no pipette attached to the robot")

        # tip solid
        tip_solid = self.solid_attached_to_tool(pipette)

        # tip length
        tip_length = abs(dorna_pose.transform_pose([0, 0, 0, 0, 0, 0], 
                                from_frame=tip_solid.pose("center"),
                                to_frame=tip_solid.pose("top"))[2])

        # tool offset
        tool_tcp_z_offset = tip_length
        tool_tip_z_offset = tip_length

        return self.above(anchor=anchor, solid_name=solid_name, component=component, padding=padding, tool_tcp_z_offset=tool_tcp_z_offset, tool_tip_z_offset=tool_tip_z_offset, **kwargs)


    
    # volume is in microliter
    def aspirate(self, vol, speed=200):
        # find the pipette
        pipette = self.tool_attached_to_the_robot()
        if pipette is None:
            raise RecipeError("no pipette attached to the robot")
        
        # simulation
        if pipette._simulation_mode:
            return True

        return pipette.device.aspirate(vol, speed)


    # volume is in microliter
    def dispense(self, vol, speed=500, blowout=False):
        # find the pipette
        pipette = self.tool_attached_to_the_robot()
        if pipette is None:
            raise RecipeError("no pipette attached to the robot")

        # simulation
        if pipette._simulation_mode:
            return True
        
        return pipette.device.dispense(vol, speed, blowout)


