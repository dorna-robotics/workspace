"""Dispense-arm recipe — the thin pause-aware shim over the arm
component.

The component owns the atomic ops (component-guide §7): ``down`` /
``up`` pneumatics live on ``needle_dispense_arm`` (operator buttons
included) and the fluid verbs come from its ``PumpedTool`` mixin.
This recipe only adds what a workflow needs on top: the ``rt``
checkpoint before each op, and the timed-hold fallback for
valve/solenoid rigs that have no pump behind the nozzle.

No robot motion is involved anywhere here — the arm is bench
furniture; vessels come to it. Recipe selection guide:
``docs/liquid-handling.md`` §4.
"""

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
        """Extend the arm downward (component pneumatics)."""
        self.rt.checkpoint()
        return self.component.down()

    def up(self):
        """Retract the arm upward (component pneumatics)."""
        self.rt.checkpoint()
        return self.component.up()

    def dispense(self, volume_ul=None, sleep=1.5, **kwargs):
        """Dispense through the arm's own fluid path.

        Two shapes, decided by the SCENE, not by the caller:

        * the arm is plumbed to a pump (``pump:`` in its scene entry)
          → ``volume_ul`` is pushed out through the arm's valve port;
        * the arm has no pump (a valve/solenoid rig, the historical
          case) → the old timed-hold behaviour, unchanged.
        """
        self.rt.checkpoint()
        link = getattr(self.component, "fluid", None)
        if link is not None and link.linked and volume_ul is not None:
            return self.component.dispense(volume_ul, **kwargs)
        self.rt.delay(sleep)
        return 0

    def aspirate(self, volume_ul, **kwargs):
        """Draw ``volume_ul`` back in through the arm's port (pump-fed
        arms only — a solenoid rig cannot aspirate)."""
        self.rt.checkpoint()
        return self.component.aspirate(volume_ul, **kwargs)

    def prime(self, cycles=None, **kwargs):
        """Flush air out of the arm's fluid path before a run.
        ``cycles=None`` → sized from the declared tube volumes."""
        self.rt.checkpoint()
        return self.component.prime(cycles, **kwargs)
