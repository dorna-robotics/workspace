from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe
import time


class Shaker(Recipe):
    DEFAULTS = dict(
        # ref joint
        target_anchor="place",
        base_distance = 100,
        rail_step=20, #10
        rail_span=5, # 5 
        # calibration
        calibration_targets={"body": ["clb_0"]},
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
        

    def shake(self, duration=5):
        start = time.time()
        while True:
            # exit condition
            current = time.time()
            if current - start >= duration and self.component.toggle_state() == "start":
                break
            
            # toggle
            self.toggle()