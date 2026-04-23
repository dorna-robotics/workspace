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


    def immerse(self, dist=0, anchor="place", **kwargs):
        """Dip ``dist`` mm into the plate currently sitting on this dosing site.

        Resolves the plate attached to ``body/place``, then delegates to
        ``Recipe.immerse`` with that plate as the target component.
        """
        # find plate component
        solid_plate = self.solid_attached_to_anchor(self.component.assembly["body"], "place")
        component = self.workspace.components[solid_plate.component]
        solid_name = next(k for k, v in component.assembly.items() if v is solid_plate)

        return super().immerse(dist=dist, anchor=anchor, solid_name=solid_name, component=component, **kwargs)


    def retract(self, dist=0, anchor="place", **kwargs):
        """Lift the held load ``dist`` mm above the plate at this dosing site."""
        # find plate component
        solid_plate = self.solid_attached_to_anchor(self.component.assembly["body"], "place")
        component = self.workspace.components[solid_plate.component]
        solid_name = next(k for k, v in component.assembly.items() if v is solid_plate)
        return super().retract(dist=dist, anchor=anchor, solid_name=solid_name, component=component, **kwargs)


    def aspirate(self, vol, speed=200):
        """Aspirate ``vol`` µL at ``speed``. Placeholder (blocks 0.5s), returns 0."""
        time.sleep(0.5)
        return 0


    def dispense(self, vol, speed=500, blowout=False):
        """Dispense ``vol`` µL at ``speed`` (optional ``blowout``). Placeholder."""
        time.sleep(0.5)
        return 0

