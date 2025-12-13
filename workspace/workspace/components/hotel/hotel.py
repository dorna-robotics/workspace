"""
center is the bottom, where it seats when placing flat
height is the top
"""
from dorna2 import Solid
from dorna2 import Pose

class Hotel:
    """
    the tube_cap
    """

    def __init__(self, name: str, workspace,
            type=None,
            anchors={"solid_0":{"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0]}},
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

        hotel_anchor = {"center": [0, 0, 0, 0, 0, 0]}
        for i in range(level):
            # center
            hotel_anchor[f"center_{i}"] = p.pose("center", offset=[0, 0, i*self.shape[2], 0, 0, 0])
            # top
            hotel_anchor[f"top_{i}"] = p.pose("top", offset=[0, 0, i*self.shape[2], 0, 0, 0])
            #place
            hotel_anchor[f"place_{i}"] = p.pose("place", offset=[0, 0, i*self.shape[2], 0, 0, 0])
        self.assembly = {
            k: Solid(type=self.type, anchors=hotel_anchor, component=self.name) for k in anchors
        }