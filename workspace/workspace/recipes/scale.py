from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe


class Scale(Recipe):
    DEFAULTS = dict(
        # ref joint
        target_anchor="place",
        base_distance = 100,
        rail_step=20, #10
        rail_span=5, # 5 
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

    def weight(self, stable: bool = True, timeout: float = 10.0, sim_return: float = 12.345):
        """Read the scale value (grams) via the component's device link.

        ``stable=True`` waits for a settled reading (MT-SICS S); the
        component / station are sim-agnostic, so this returns the sim
        value in sim and the real balance reading otherwise. Returns
        ``None`` when the scale is disconnected and not in sim.

        ``sim_return`` (device-guide §17) — its default IS the canned sim
        weight (grams), inline in the signature (the real weight() returns
        a float, so sim_return is one too); pass a ``float`` to inject a
        different one. Real mode ignores it."""
        return self.component.weight(stable=stable, timeout=timeout, sim_return=sim_return)