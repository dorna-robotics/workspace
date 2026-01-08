from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe


class PipettingSite(Recipe):
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


    def pick_tip(self, anchor="place"):
        # unsuccessful motion
        if not self.pick_from(anchor=anchor, trigger_io=False):
            return False
        
        # check if tip is there
        pipette = self.tool_attached_to_the_robot()
        if pipette is None:
            return False
        
        # make sure tip exists
        return pipette.device.has_tip()


    # the action of ejecting tip
    def eject_tip(self, anchor="place"):
        # find the pipette
        pipette = self.tool_attached_to_the_robot()
        if pipette is None:
            return False

        # motion
        if not self.place_in(anchor=anchor, actions=[[pipette.device.eject_tip(), [], {}]], trigger_io=False):
            return False

        # make sure tip is ejected
        return pipette.device.has_no_tip()


    # go on top of the source, and go down for the amount
    # anchor of the rack, plate or the item, look for the tube there
    def immerse(self, anchor="place", depth=0, approach=True):
        pass

    # given the component, go on top of the source
    # anchor of the rack, plate or the item, look for the tube there
    def retract(self, anchor="place"):
        pass


    def aspirate(self, volume, speed=200):
        # find the pipette
        pipette = self.tool_attached_to_the_robot()
        if pipette is None:
            return False
        
        return pipette.device.aspirate(volume, speed)


    def dispense(self, volume, speed=500, blowout=False):
        # find the pipette
        pipette = self.tool_attached_to_the_robot()
        if pipette is None:
            return False
        
        return pipette.device.dispense(volume, speed, blowout)


