import numpy as np
from dorna2 import Solid


class Tube:
    def __init__(self, name: str, workspace,
            type=None,
            anchors={
                "body": {"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 42, 0, 0, 0], "place":[0, 0, 0, 0, 0, 0]},
            },
            **kwargs
            ):
        self.name = name
        self.type = type
        self.workspace = workspace
        self.assembly = {
            k: Solid(type=self.type, anchors=anchors[k], component=self.name) for k in anchors
        }
