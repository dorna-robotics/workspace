"""
center is the bottom, where it seats when placing flat
height is the top
"""
from dorna2 import Solid

class Cap:
    def __init__(self, name: str, workspace,
            type=None,
            anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0]}},
            cap_type="screw", # "snap" or "screw"
            twist=720,
            pitch=2,
            **kwargs
            ):
        self.name = name
        self.type = type
        self.workspace = workspace
        self.assembly = {
            k: Solid(type=self.type, anchors=anchors[k], component=self.name) for k in anchors
        }

        # cap type
        self.cap_type = cap_type
        self.twist = twist
        self.pitch = pitch