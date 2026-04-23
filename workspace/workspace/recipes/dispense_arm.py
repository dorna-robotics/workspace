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

    def down(self):
        """Extend the dispense arm downward (drives the component's output HIGH)."""
        rt = self.rt
        if self.component.output_state() != 1:
            rt.checkpoint()
            rt.output(config=self.component.output_enable)
            self.component.output_state(1)
        return True

    def up(self):
        """Retract the dispense arm upward (drives the component's output LOW)."""
        rt = self.rt
        if self.component.output_state() != 0:
            rt.checkpoint()
            rt.output(config=self.component.output_disable)
            self.component.output_state(0)
        return True

    def dispense(self, sleep=1.5):
        """Trigger a dispense cycle by holding for ``sleep`` seconds (pause-aware)."""
        rt = self.rt
        rt.checkpoint()
        rt.delay(sleep)
        return 0
