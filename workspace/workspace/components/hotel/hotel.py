from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
from dorna2 import Pose

class Hotel:
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0]}},
        shape = [150, 100, 52],
        level=4,
    )

    def __init__(self, name: str, workspace, type=None, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, kwargs) # self

        # init
        self.name = name
        self.workspace = workspace
        self.type = type

        # dim
        self.shape = prm["shape"]
        # anchors
        p = Pose(anchors=prm["anchors"][next(iter(prm["anchors"]))])

        # levels
        for i in range(prm["level"]):
            # center
            prm["anchors"][next(iter(prm["anchors"]))][f"center_{i}"] = p.pose("center", offset=[0, 0, i*self.shape[2], 0, 0, 0])
            # top
            prm["anchors"][next(iter(prm["anchors"]))][f"top_{i}"] = p.pose("top", offset=[0, 0, i*self.shape[2], 0, 0, 0])
            #place
            prm["anchors"][next(iter(prm["anchors"]))][f"place_{i}"] = p.pose("place", offset=[0, 0, i*self.shape[2], 0, 0, 0])
        
        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name) for k in prm["anchors"]
        }