from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
import time

class Shaker:
    DEFAULTS = dict(
        anchors={},
        # limit
        toggle_range = [0, 180], # [start, end] angle in degree
        toggle_period = 1, # second, time to toggle from one state to another
        # cfg
        output_start = [[None, None, 0.1]], # [[pin, index, time]]
        output_end = [[None, None, 0.1]], # [[pin, index, time]]
        output_close = {
            "A1": [[None, None, 0.1]], # [[pin, index, time]]
            "A2": [[None, None, 0.1]], # [[pin, index, time]]
        },
        output_open ={
            "A1": [[None, None, 0.1]], # [[pin, index, time]]
            "A2": [[None, None, 0.1]], # [[pin, index, time]]
        },
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


    def toggle(self, stop_event=None):
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


    def update_pose(self):
        self.assembly["rotating"].attach_to(parent=self.assembly["body"], parent_anchor="output", child_anchor="input", offset=[0, 0, 0, 0, self.joint, 0], offset_frame="parent")