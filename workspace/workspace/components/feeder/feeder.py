from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid


class Feeder:
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "place":[0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0]}},
        # cfg
        axis_cfg = {"axis": 7, "usem":1, "pprm":4000, "tprm":360, "usee":1, "ppre":4000, "tpre":360, "p": 0.01, "i": 0.0001, "d": 0},
        offset= 0,
        num_slots = 16,
        vaj=[300, 4000, 10000],
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

        # axis, offset cfg
        self.axis_cfg = prm["axis_cfg"]
        self.offset = prm["offset"]

        # number of positions
        self.num_slots = prm["num_slots"]

        # vaj
        self.vaj = prm["vaj"]
