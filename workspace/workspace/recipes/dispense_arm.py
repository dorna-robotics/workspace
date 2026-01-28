from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe


class DispenseArm(Recipe):
    DEFAULTS = dict()

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
            

    # bring the arm down
    def down(self):
        if self.component.output_state() != 1:
            self.core.robot_api.output(config=self.component.output_enable)
            self.component.output_state(1)
        return True


    # bring the arm down
    def up(self):
        if self.component.output_state() != 0:
            self.core.robot_api.output(config=self.component.output_disable)
            self.component.output_state(0)
        return True
