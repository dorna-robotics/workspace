from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe
import time
import threading


class Shaker(Recipe):
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

        # Stop signal — can be set from another thread (e.g. via
        # stop_shaking()) to interrupt an in-flight shake() early.
        self._stop_event = threading.Event()

    def shake(self, duration=5):
        """Block while toggling the shaker for ``duration`` seconds.

        Toggles the shaker back and forth until at least ``duration``
        seconds have elapsed AND the shaker is back at its start
        position. Always returns to the start position on exit.

        Synchronous by design — call from a worker thread if you need
        the caller free for other work (the BT framework already does
        this for action ``execute()`` bodies).

        Use :meth:`stop_shaking` from another thread to interrupt
        early (e.g. operator kill).
        """
        self._stop_event.clear()
        start = time.time()
        while not self._stop_event.is_set():
            # exit when duration elapsed and back at start position
            if time.time() - start >= duration and self.component.toggle_state() == "start":
                break
            self.component.toggle(stop_event=self._stop_event)
        self._go_to_start()

    def stop_shaking(self):
        """Signal an in-flight :meth:`shake` to exit early.

        Safe to call from another thread (e.g. the BT engine's
        terminate path, an operator Kill). If no shake is running,
        this is a no-op.
        """
        self._stop_event.set()

    def _go_to_start(self):
        """Move shaker to start position."""
        comp = self.component
        comp.joint = comp.toggle_range[0]
        comp.toggle_state("start")
        comp.update_pose()

    def pick(self, anchor="A1", solid_name="rotating", **kwargs):
        """Pick from a shaker well — targets the ``rotating`` solid so the
        pick follows the shaker's current orientation."""
        return super().pick(anchor=anchor, solid_name=solid_name, **kwargs)


    def place(self, anchor="A1", solid_name="rotating", **kwargs):
        """Place into a shaker well — targets the ``rotating`` solid."""
        return super().place(anchor=anchor, solid_name=solid_name, **kwargs)