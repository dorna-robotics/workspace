from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe
from dorna2 import pose as dorna_pose
import time

class PipettingSite(Recipe):
    DEFAULTS = dict(
        # ref joints
        target_offset=[0, 0, 150, 0, 180, 0],
        # IK
        rail_step=20, # 5
        rail_span=5, # 10        
        # calibration
        calibration_targets={"body": ["clb_0", "clb_1", "clb_2", "clb_3"]}, # {solid_name: {anchor_1:..., anchor_2:...},...}
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


    def pick_tip(self, anchor="place", **kwargs):
        # find plate component
        solid_plate = self.solid_attached_to_anchor(self.component.assembly["body"], "place")
        component = self.workspace.components[solid_plate.component]
        solid_name = next(k for k, v in component.assembly.items() if v is solid_plate)

        # motion
        if not self.pick_from(anchor=anchor, solid_name=solid_name, component=component, trigger_io=False, **kwargs):
            return False
        
        # check if tip is there
        pipette = self.tool_attached_to_the_robot()
        if pipette is None:
            return False
        
        # make sure tip exists
        if not pipette.simulation:  
            return pipette.device.has_tip()
        return True


    # the action of ejecting tip
    def eject_tip(self, anchor="A1", **kwargs):
        # find rack component
        solid_plate = self.solid_attached_to_anchor(self.component.assembly["body"], "place")
        component = self.workspace.components[solid_plate.component]
        solid_name = next(k for k, v in component.assembly.items() if v is solid_plate)

        # find the pipette
        pipette = self.tool_attached_to_the_robot()
        if pipette is None:
            return False
        
        actions = []
        # no simulation
        if not pipette.simulation:
            actions = [[pipette.device.eject_tip(), [], {}]]
        
        # motion
        motion_result = self.place_in(anchor=anchor, solid_name=solid_name, component=component, actions=actions, trigger_io=False, gravity_offset=10, **kwargs)

        if not pipette.simulation:
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
            return False
        
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
        return self.pick_from(anchor=anchor, solid_name=solid_name, component=component, approach=approach, actions=[], exit=False, attachment=False, trigger_io=False, padding=padding, gap=2, tool_tcp_z_offset=tool_tcp_z_offset, tool_tip_z_offset=tool_tip_z_offset, **kwargs)


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
            return False

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
    def aspirate(self, volume, speed=200):
        # find the pipette
        pipette = self.tool_attached_to_the_robot()
        if pipette is None:
            return False
        
        # simulation
        if pipette.simulation:
            return True

        return pipette.device.aspirate(volume, speed)


    # volume is in microliter
    def dispense(self, volume, speed=500, blowout=False):
        # find the pipette
        pipette = self.tool_attached_to_the_robot()
        if pipette is None:
            return False

        # simulation
        if pipette.simulation:
            return True
        
        return pipette.device.dispense(volume, speed, blowout)


