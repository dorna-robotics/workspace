from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe, RecipeError

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
        component, solid_name = self._resolve_attached_component()

        return super().immerse(dist=dist, anchor=anchor, solid_name=solid_name, component=component, **kwargs)


    def retract(self, dist=0, anchor="place", **kwargs):
        """Lift the held load ``dist`` mm above the plate at this dosing site."""
        component, solid_name = self._resolve_attached_component()
        return super().retract(dist=dist, anchor=anchor, solid_name=solid_name, component=component, **kwargs)


    # ── Fluid path ────────────────────────────────────────────────────
    # A dosing site has a nozzle at one of two places, and the SCENE
    # says which: a fixed nozzle plumbed to this site's own component,
    # or a needle the robot carried here. Resolve in that order — a
    # site with its own plumbing owns its liquid; otherwise whatever
    # the arm is holding does. Both end at the same pump component,
    # which is the sole owner of the device (component-guide §7).

    def _fluid(self):
        link = getattr(self.component, "fluid", None)
        if link is not None and link.linked:
            return link
        tool = self.core.current_tool()
        link = getattr(tool, "fluid", None) if tool is not None else None
        if link is not None and link.linked:
            return link
        raise RecipeError(
            f"no fluid path for dosing site {getattr(self.component, 'name', '?')}: "
            f'neither the site nor the mounted tool declares `pump:` in the scene'
        )

    def aspirate(self, vol, **kwargs):
        """Draw ``vol`` µL in through this site's fluid path."""
        self.rt.checkpoint()
        return self._fluid().aspirate(vol, **kwargs)

    def dispense(self, vol, **kwargs):
        """Push ``vol`` µL out through this site's fluid path."""
        self.rt.checkpoint()
        return self._fluid().dispense(vol, **kwargs)

    def prime(self, cycles=2, **kwargs):
        """Flush air out of the path before dosing."""
        self.rt.checkpoint()
        return self._fluid().prime(cycles, **kwargs)

