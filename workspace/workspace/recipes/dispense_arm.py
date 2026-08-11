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

    def dispense(self, volume_ul=None, sleep=1.5, **kwargs):
        """Dispense through the arm's own fluid path.

        Two shapes, decided by the SCENE, not by the caller:

        * the arm is plumbed to a syringe pump (``pump:`` in its scene
          entry) → ``volume_ul`` is pushed out through the arm's valve
          port and the real volume is returned;
        * the arm has no pump (a valve/solenoid rig, the historical
          case) → the old timed-hold behaviour, unchanged.

        The volume goes to the pump component, which is the sole owner
        of the device; this recipe only sequences it (component-guide
        §7). Pause-aware either way.
        """
        rt = self.rt
        rt.checkpoint()
        link = getattr(self.component, "fluid", None)
        if link is not None and link.linked and volume_ul is not None:
            return link.dispense(volume_ul, **kwargs)
        rt.delay(sleep)
        return 0

    def aspirate(self, volume_ul, **kwargs):
        """Draw ``volume_ul`` back in through the arm's port (pump-fed
        arms only — a solenoid rig cannot aspirate)."""
        self.rt.checkpoint()
        return self.component.fluid.aspirate(volume_ul, **kwargs)

    def prime(self, cycles=2, **kwargs):
        """Flush air out of the arm's fluid path before a run."""
        self.rt.checkpoint()
        return self.component.fluid.prime(cycles, **kwargs)
