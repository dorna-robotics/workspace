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

        self._shake_thread = None
        self._stop_event = threading.Event()

    @property
    def is_shaking(self):
        return self._shake_thread is not None and self._shake_thread.is_alive()

    def shake(self, duration=5):
        """Non-blocking shake — runs in background thread."""
        if self.is_shaking:
            return  # already running

        self._stop_event.clear()

        def _run():
            start = time.time()
            while not self._stop_event.is_set():
                # exit when duration elapsed and back at start position
                if time.time() - start >= duration and self.component.toggle_state() == "start":
                    break
                self.component.toggle(stop_event=self._stop_event)

            # always return to start position on exit
            self._go_to_start()

        self._shake_thread = threading.Thread(target=_run, daemon=True)
        self._shake_thread.start()

    def stop_shaking(self, wait=True):
        """Stop shaking and return to start position."""
        if not self.is_shaking:
            return
        self._stop_event.set()
        if wait:
            self._shake_thread.join()

    def _go_to_start(self):
        """Move shaker to start position."""
        comp = self.component
        comp.joint = comp.toggle_range[0]
        comp.toggle_state("start")
        comp.update_pose()

    def pick(self, anchor="A1", solid_name="rotating", **kwargs):
        return super().pick(anchor=anchor, solid_name=solid_name, **kwargs)


    def place(self, anchor="A1", solid_name="rotating", **kwargs):
        return super().place(anchor=anchor, solid_name=solid_name, **kwargs)