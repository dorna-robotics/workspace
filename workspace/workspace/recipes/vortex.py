from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe, RecipeError
import threading


class Vortex(Recipe):
    """Vortexer station: pick/place on the plate + a timed run.

    The recipe's ``component`` is the SEAT PLATE (the 4x7 top the
    vials sit in — the pick/place target). The Genie body that owns
    the mains-switch IO is named by ``driver`` and resolved here, so
    ``run()`` can switch it while motion stays addressed to the plate.
    """

    DEFAULTS = dict(
        # component NAME of the vortex_genie_2 body carrying the
        # output_enable / output_disable rows (scene entry sets pins).
        driver="",
    )

    def __init__(self, workspace, core, component, **kwargs):
        # prm
        prm = deepcopy(Recipe.DEFAULTS)  # default
        merge(prm, self.DEFAULTS)        # self
        merge(prm, kwargs)               # kwargs

        super().__init__(
            workspace=workspace,
            core=core,
            component=component,
            **prm
        )

        driver = prm["driver"]
        if not driver or driver not in workspace.components:
            raise RecipeError(
                f"Vortex recipe needs driver=<vortex_genie_2 component name>, "
                f"got {driver!r}")
        self.driver = self.gated(workspace.components[driver])

        # Stop signal — settable from another thread (stop_run) to end
        # an in-flight run() early.
        self._stop_event = threading.Event()

    def run(self, duration=10):
        """Run the vortexer for ``duration`` seconds, then switch off.

        Workflow (the component owns each atomic op, this owns the
        order): execute any HELD exit first — the plate is about to
        shake, and a fused exit would leave the gripper parked on a
        moving vial (the Shaker rule) — then enable, wait, disable.
        ``duration`` is RUNNING time. A Pause switches the vortexer OFF
        and holds; Resume switches it back on for what is left — the
        plate never spins while the run is paused (an operator's hands
        may be near it), and paused time is not counted (the shaker's
        rule: its head stops at the end of its stroke). The switch-off
        is guaranteed on every exit path (Kill, a Replan drop, Park).

        Use :meth:`stop_run` from another thread to end early.
        """
        self._wire_verb("run")
        rt = self.rt
        self._stop_event.clear()
        self.driver.enable()
        try:
            deadline = float(duration)
            elapsed = 0.0
            while elapsed < deadline and not self._stop_event.is_set():
                if rt.paused:
                    self.driver.disable()
                    rt.checkpoint()          # holds until Resume (raises on Kill / cancel)
                    self.driver.enable()
                step = min(0.2, deadline - elapsed)
                rt.sleep(step, checkpoint=False)   # running time only — the pause is above
                elapsed += step
        finally:
            self.driver.disable()
        return True

    def stop_run(self):
        """Signal an in-flight :meth:`run` to switch off early.

        Safe from any thread (BT terminate path, operator Kill). No-op
        when nothing runs.
        """
        self._stop_event.set()
