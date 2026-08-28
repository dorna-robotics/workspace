from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
import time

class Shaker:
    DEFAULTS = dict(
        anchors={},
        # limit
        toggle_range = [0, -180], # [start, end] angle in degree
        toggle_period = 1, # second, time to toggle from one state to another
        # cfg
        output_start = [[None, None, 0.1]], # [[pin, index, time]]
        output_end = [[None, None, 0.1]], # [[pin, index, time]]
        # 0.5 s dwell on the clamp rows: the head settles and the
        # liquid stops sloshing before/after the jaws actuate (bench:
        # the clamp opened the instant the shake ended).
        output_close = [[None, None, 0.5]], # [[pin, index, time]],
        output_open =[[None, None, 0.5]], # [[pin, index, time]],
    )
    def __init__(self, name: str, workspace, type=None, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, kwargs) # self

        # init
        self.name = name
        self.workspace = workspace
        self.type = type

        # assembly
        self.assembly = {}


        # enable and disable
        self.output_start = prm["output_start"]
        self.output_end = prm["output_end"]
        self.output_close = prm["output_close"]
        self.output_open = prm["output_open"]
        self._toggle_state = "start" # start or end
        self._clamp_state = None # 1 closed, 0 open, None unknown

        # motion
        self.toggle_range = prm["toggle_range"]
        self.toggle_period = prm["toggle_period"]
        self.joint = prm["toggle_range"][0] # current angle


    # set or get output state
    def toggle_state(self, state=None):
        if state is None:
            return self._toggle_state
        self._toggle_state = state
        return self._toggle_state

    # set or get clamp state
    def clamp_state(self, state=None):
        if state is None:
            return self._clamp_state
        self._clamp_state = state
        return self._clamp_state

    # ── atomic ops (component-guide §7) ───────────────────────────────
    # The component owns the IO, same as Decapper.enable / disable. The
    # Shaker recipe adds the workflow (shake-for-duration loop) and
    # delegates here. Idempotent clamp: a repeated call in the reached
    # state is a no-op.

    def close(self) -> bool:
        """Clamp the head onto the seated vessels."""
        if self.clamp_state() != 1:
            self.workspace.rt.output(config=self.output_close)
            self.clamp_state(1)
        return True

    def open(self) -> bool:
        """Release the head clamp."""
        if self.clamp_state() != 0:
            self.workspace.rt.output(config=self.output_open)
            self.clamp_state(0)
        return True

    def go_start(self) -> bool:
        """Drive the head to its start position and re-sync the model."""
        self.workspace.rt.output(config=self.output_start)
        self.joint = self.toggle_range[0]
        self._toggle_state = "start"
        self.update_pose()
        return True

    def toggle(self, stop_event=None):
        # Fire the stroke's IO first — output_end drives start→end,
        # output_start drives back. The animation below doubles as the
        # wait for the physical swing (toggle_period): the scene model
        # tracks the head while the actuator travels.
        self.workspace.rt.output(
            config=self.output_end if self._toggle_state == "start" else self.output_start)
        start = time.time()
        while True:
            current = time.time()
            if current - start >= self.toggle_period:
                break
            if stop_event is not None and stop_event.is_set():
                return  # exit mid-toggle cleanly; caller handles going to start
            ratio = (current - start) / self.toggle_period
            self.joint = self.toggle_range[0] + (self.toggle_range[1] - self.toggle_range[0]) * ratio if self._toggle_state == "start" else self.toggle_range[1] - (self.toggle_range[1] - self.toggle_range[0]) * ratio
            time.sleep(0.03)

        self.joint = self.toggle_range[1] if self._toggle_state == "start" else self.toggle_range[0]
        self._toggle_state = "end" if self._toggle_state == "start" else "start"

    def operator_actions(self) -> list[dict]:
        return [
            {"label": "Close",  "method": "close",    "icon": "link",     "group": "clamp"},
            {"label": "Open",   "method": "open",     "icon": "link-off", "group": "clamp"},
            {"label": "Toggle", "method": "toggle",   "icon": "rotate",   "group": "motor"},
            {"label": "Home",   "method": "go_start", "icon": "backward", "group": "motor"},
        ]


    def update_pose(self):
        self.assembly["rotating"].attach_to(parent=self.assembly["body"], parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, self.joint, 0], offset_frame="parent")