"""
center is the bottom, where it seats when placing flat
height is the top
"""
from dorna2 import Solid
from dorna2 import Pose

class Hotel:
    def __init__(self, name: str, workspace,
            type=None,
            anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0]}},
            level=4,
            shape = [150, 100, 52],
            **kwargs
            ):
        self.name = name
        self.type = type
        self.workspace = workspace

        # dim
        self.shape = shape
        # anchors
        p = Pose(anchors=anchors[next(iter(anchors))])

        # levels
        for i in range(level):
            # center
            anchors[next(iter(anchors))][f"center_{i}"] = p.pose("center", offset=[0, 0, i*self.shape[2], 0, 0, 0])
            # top
            anchors[next(iter(anchors))][f"top_{i}"] = p.pose("top", offset=[0, 0, i*self.shape[2], 0, 0, 0])
            #place
            anchors[next(iter(anchors))][f"place_{i}"] = p.pose("place", offset=[0, 0, i*self.shape[2], 0, 0, 0])
        self.assembly = {
            k: Solid(type=self.type, anchors=anchors[k], component=self.name) for k in anchors
        }