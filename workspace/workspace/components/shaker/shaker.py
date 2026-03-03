from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid


class Shaker:
    DEFAULTS = dict(
        anchors={},
        # cfg
        radius = 0,
        output_enable = [[None, None, 0.1]], # [[pin, index, time]]
        output_disable = [[None, None, 0.1]], # [[pin, index, time]]
        output_close = [[[None, None, 0.1]]], # [[pin, index, time]]
        output_open = [[[None, None, 0.1]]], # [[pin, index, time]]
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
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        }


        # enable and disable
        self.output_enable = prm["output_enable"]
        self.output_disable = prm["output_disable"]
        self.output_close = prm["output_close"]
        self.output_open = prm["output_open"]
        # io state
        self._output_state = None

    
    # set or get output state
    def output_state(self, state=None):
        if state is None:
            return self._output_state
        self._output_state = state
        return self._output_state
