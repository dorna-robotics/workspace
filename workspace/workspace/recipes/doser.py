from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe
import time

class DosingSite(Recipe):
    DEFAULTS = dict(
        # ref joints
        target_offset=[0, 0, 150, 0, 180, 0],
        # IK
        rail_step=20, # 5
        rail_span=5, # 10        
        # calibration
        calibration_targets={}
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


    # go on top of the source, and go down for the amount
    def immerse(self, dist=0, anchor="place", **kwargs):
        # find plate component
        solid_plate = self.solid_attached_to_anchor(self.component.assembly["body"], "place")
        component = self.workspace.components[solid_plate.component]
        solid_name = next(k for k, v in component.assembly.items() if v is solid_plate)

        return super().immerse(dist=dist, anchor=anchor, solid_name=solid_name, component=component, **kwargs)


    # given the component, go on top of the source
    # anchor of the rack, plate or the item, look for the tube there
    def retract(self, dist=0, anchor="place", **kwargs):
        # find plate component
        solid_plate = self.solid_attached_to_anchor(self.component.assembly["body"], "place")
        component = self.workspace.components[solid_plate.component]
        solid_name = next(k for k, v in component.assembly.items() if v is solid_plate)
        return super().retract(dist=dist, anchor=anchor, solid_name=solid_name, component=component, **kwargs)


    # volume is in microliter
    def aspirate(self, vol, speed=200):
        time.sleep(0.5)
        return 0


    # volume is in microliter
    def dispense(self, vol, speed=500, blowout=False):
        time.sleep(0.5)
        return 0

