
"""
center: the bottom center of the plate
height: the height of the plate
height_place: the z offset of the center of the item placed in the rack
"""
from dorna2 import Solid


class Adapter:
    def __init__(self, name: str, workspace,
                type=None,
                anchors = {"solid_0": {"center": [0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0]}},
                height=19,
                heaight_seat=3,
                ):
        self.name = name
        self.workspace = workspace
        self.type = type
        self.height = height
        self.heaight_seat = heaight_seat

        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=anchors[k], component=self.name) for k in anchors
        }