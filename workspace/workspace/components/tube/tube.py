from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid


class Tube:
    DEFAULTS = dict(
        anchors={
            "body": {"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0], "place":[0, 0, 0, 0, 0, 0]},
        },
        size = [0, 0, 0] # [dx, dy, dz]
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

        # size
        self.size = prm["size"]