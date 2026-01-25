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
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        }

        # open and close
        self.enable = prm["output_enable"]
        self.disable = prm["output_disable"]
