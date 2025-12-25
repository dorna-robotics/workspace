from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid


class Cap:
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0]}},
        cap_type="screw", # "snap" or "screw"
        twist=720, # overall twist in degrees for capping or decapping
        pitch=2, # cap travel (mm) when turing 360 degrees
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

        # cap features
        self.cap_type = prm["cap_type"]
        self.twist = prm["twist"]
        self.pitch = prm["pitch"]