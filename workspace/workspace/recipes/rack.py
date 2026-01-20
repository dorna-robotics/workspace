from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe

"""
component: is the adapter plate for the rack
"""
class Rack(Recipe):
    DEFAULTS = dict(
        # IK
        base_distance=50,
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
        

    def pick_from(self, anchor, **kwargs):
        # find rack component
        solid_plate = self.solid_attached_to_anchor(self.component.assembly["body"], "place")
        component = self.workspace.components[solid_plate.component]
        solid_name = next(k for k, v in component.assembly.items() if v is solid_plate)

        # motion
        return super().pick_from(anchor, solid_name=solid_name, component=component, **kwargs)
   
             

    def place_in(self, anchor, **kwargs):
        # find rack component
        solid_plate = self.solid_attached_to_anchor(self.component.assembly["body"], "place")
        component = self.workspace.components[solid_plate.component]
        solid_name = next(k for k, v in component.assembly.items() if v is solid_plate)

        # motion
        return super().place_in(anchor, solid_name=solid_name, component=component, **kwargs)

