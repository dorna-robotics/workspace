from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid


class Rack:
    DEFAULTS = dict(
        anchors = {"body": {"center": [0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0]}},
        #collision_box = {"body":[[[x,y,z,a,b,c], [lx, ly, lz]]]}
        size = [0, 0, 0], # [dx, dy, dz]
        offset= [0, 0],
        pitch=[9, 9],
        rvec_safe = [0, 0, 45],
        rows=[chr(c) for c in range(ord("A"), ord("H") + 1)],
        cols= [i for i in range(1, 13)],
    )

    def __init__(self, name: str, workspace, type=None, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, kwargs) # self

        # init
        self.name = name
        self.workspace = workspace
        self.type = type

        # size
        self.size = prm["size"]

        # prm
        self.offset = prm["offset"] 
        self.pitch = prm["pitch"]
        self.rvec_safe = prm["rvec_safe"]
        self.rows = prm["rows"]
        self.cols = prm["cols"]

        # index anchors
        x_start = self.offset[0] - self.pitch[0]*(len(self.cols)-1)/2
        y_start = self.offset[1] + self.pitch[1]*(len(self.rows)-1)/2

        for r_idx, r in enumerate(self.rows):
            y = y_start - r_idx * self.pitch[1]
            for c in self.cols:
                x = x_start + (c - 1) * self.pitch[0]
                prm["anchors"][next(iter(prm["anchors"]))][f"{r}{c}"] = [x, y, prm["anchors"][next(iter(prm["anchors"]))]["place"][2]]+self.rvec_safe

        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name) for k in prm["anchors"]
        }