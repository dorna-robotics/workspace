from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid


class Scale:
    DEFAULTS = dict(
        anchors={
            "body": {"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 106, 0, 0, 0], "place":[0, 0, 96, 0, 0, 0]},
            "hole_0": [75, 75, 0, 0, 0, 0], "hole_1": [-75, 75, 0, 0, 0, 0], "hole_2": [-75, -75, 0, 0, 0, 0], "hole_3": [75, -75, 0, 0, 0, 0],
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
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        }
