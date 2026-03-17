from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe


class DispenseArm(Recipe):
    DEFAULTS = dict()

    def __init__(self, workspace, core, component, **kwargs):
        prm = deepcopy(Recipe.DEFAULTS)
        merge(prm, self.DEFAULTS)
        merge(prm, kwargs)

        super().__init__(
            workspace=workspace,
            core=core,
            component=component,
            **prm
        )

    # bring the arm down
    def down(self):
        rt = self.rt
        if self.component.output_state() != 1:
            rt.checkpoint()
            rt.output(config=self.component.output_enable)
            self.component.output_state(1)
        return True

    # bring the arm up
    def up(self):
        rt = self.rt
        if self.component.output_state() != 0:
            rt.checkpoint()
            rt.output(config=self.component.output_disable)
            self.component.output_state(0)
        return True

    # dispense (pause-aware)
    def dispense(self, sleep=1.5):
        rt = self.rt
        rt.checkpoint()
        rt.delay(sleep)
        return 0
