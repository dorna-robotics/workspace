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
        

    def pick(self, anchor, **kwargs):
        """Pick from a rack sitting on this adapter plate.

        Resolves the rack component (attached at ``body/place`` of the adapter),
        then delegates to ``Recipe.pick`` with that rack as the target.
        ``anchor`` is the well/slot on the rack (e.g. "A1").
        """
        # find rack component
        solid_plate = self.solid_attached_to_anchor(self.component.assembly["body"], "place")
        component = self.workspace.components[solid_plate.component]
        solid_name = next(k for k, v in component.assembly.items() if v is solid_plate)

        # motion
        return super().pick(anchor, solid_name=solid_name, component=component, **kwargs)



    def place(self, anchor, soft_approach=True, **kwargs):
        """Place into a rack sitting on this adapter plate (soft-approach on by default)."""
        # find rack component
        solid_plate = self.solid_attached_to_anchor(self.component.assembly["body"], "place")
        component = self.workspace.components[solid_plate.component]
        solid_name = next(k for k, v in component.assembly.items() if v is solid_plate)

        # motion
        return super().place(anchor, solid_name=solid_name, component=component, soft_approach=soft_approach, **kwargs)

