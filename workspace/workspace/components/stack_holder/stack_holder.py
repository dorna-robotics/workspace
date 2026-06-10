from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid


# 3D analog of Rack. Rack lays a flat X-Y grid; StackHolder adds a Z axis on
# top while keeping Rack's X-Y convention exactly: rows are letters (A..)
# along Y, cols are numbers (1..) along X. On top of that a layer index runs
# along Z, appended after an underscore and counted from 0. So an anchor is
# <row><col>_<layer> — e.g. A1_0 (first cell, bottom layer) or A6_2 (row A,
# col 6, third layer up). Everything else mirrors Rack
# (offset/pitch/rvec_safe/slot); pitch is the [x, y, z] step per (col, row, layer).
class StackHolder:
    DEFAULTS = dict(
        anchors = {"body": {"center": [0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0]}},
        size = [0, 0, 0], # [dx, dy, dz]
        offset= [0, 0, 0],
        pitch=[9, 9, 1],                  # x step per col, y step per row, z step per layer
        rvec_safe = [0, 0, 0],
        rows=[chr(c) for c in range(ord("A"), ord("H") + 1)], # letters -> Y  (A..)
        cols= [i for i in range(1, 13)],                      # numbers -> X  (1..)
        layers=[i for i in range(0, 6)],                      # Z -> _<n>     (0..)
    )

    def __init__(self, name: str, workspace, type=None, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, kwargs) # self

        # init
        self.name = name
        self.workspace = workspace
        self.type = type
        self.size = prm["size"]
        self.offset = prm["offset"]
        self.pitch = prm["pitch"]
        self.rvec_safe = prm["rvec_safe"]
        self.rows = prm["rows"]
        self.cols = prm["cols"]
        self.layers = prm["layers"]

        # index anchors — Rack's X-Y grid (rows->Y letters, cols->X numbers)
        # plus a Z layer appended as _<n> (from 0); anchor is <row><col>_<layer>
        for r_idx, r in enumerate(self.rows):
            y = self.offset[1] + r_idx * self.pitch[1]
            for c_idx, c in enumerate(self.cols):
                x = self.offset[0] + c_idx * self.pitch[0]
                for l_idx, l in enumerate(self.layers):
                    z = self.offset[2] + l_idx * self.pitch[2]
                    prm["anchors"][next(iter(prm["anchors"]))][f"{r}{c}_{l}"] = [x, y, z] + self.rvec_safe

        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        }

        # slot
        self.slot = {
           next(iter(prm["anchors"])): [f"{r}{c}_{l}" for r in self.rows for c in self.cols for l in self.layers]
        }
