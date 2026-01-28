from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid

class Arm:
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0]}},
        # cfg
        output_enable = [[None, None, 0.1]], # [[pin, index, time]]
        output_disable = [[None, None, 0.1]], # [[pin, index, time]]
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
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name) for k in prm["anchors"]
        }

        # enable and disable
        self.output_enable = prm["output_enable"]
        self.output_disable = prm["output_disable"]

        # io state
        self._output_state = None

    
    # set or get output state
    def output_state(self, state=None):
        if state is None:
            return self._output_state
        self._output_state = state
        return self._output_state

