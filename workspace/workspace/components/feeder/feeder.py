"""
center is the bottom of the feeder where it touches the plate
pick is the place where the center anchor of the cap sits on
height_wall is the clearance height of the wall of the feeder  starts from the pick and goes up to the edge of the wall
"""

from dorna2 import Solid

class Feeder:
    def __init__(self, name: str, workspace,
            type=None,
            anchors={"solid_0": {"center": [0, 0, 0, 0, 0, 0], "pick":[0, 0, 0, 0, 0, 0]}}, 
            height=0, # clearance height
            **kwargs
            ):
        self.name = name
        self.workspace = workspace
        self.type = type
        self.height = height
        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=anchors[k], component=self.name) for k in anchors
        }
