from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid


class Rack:
    DEFAULTS = dict(
        anchors = {"body": {"center": [0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0]}},
        #collision_box = {"body":[[[x,y,z,a,b,c], [lx, ly, lz]]]}
        size = [0, 0, 0], # [dx, dy, dz]
        offset= [0, 0, 0],
        pitch=[9, 9, 0],
        rvec_safe = [0, 0, 45],
        rows=[chr(c) for c in range(ord("A"), ord("H") + 1)],
        cols= [i for i in range(1, 13)],
        # False: rows advance along y, cols along x (the platform
        # convention). True: rows along x, cols along y — for labware
        # whose printed labels run across the SHORT axis (e.g. a glb
        # shipped rotated 90 deg, like vortex_genie_top_2ml_4x7).
        transpose=False,
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
        self.transpose = prm["transpose"]

        # index anchors — rows along y and cols along x by default;
        # transpose swaps the axes (rows along x, cols along y).
        for r_idx, r in enumerate(self.rows):
            for c_idx, c in enumerate(self.cols):
                if self.transpose:
                    x = self.offset[0] + r_idx * self.pitch[0]
                    y = self.offset[1] + c_idx * self.pitch[1]
                else:
                    x = self.offset[0] + c_idx * self.pitch[0]
                    y = self.offset[1] + r_idx * self.pitch[1]
                z = self.offset[2]
                prm["anchors"][next(iter(prm["anchors"]))][f"{r}{c}"] = [x, y, z]+self.rvec_safe

        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        }

        # slot
        self.slot = {
           next(iter(prm["anchors"])): [f"{r}{c}" for r in self.rows for c in self.cols] 
        }