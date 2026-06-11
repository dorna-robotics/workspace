from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid


# Flat X-Y grid of slots, same convention as Rack: rows are letters (A..)
# along Y, cols are numbers (1..) along X. An anchor is <row><col> — e.g.
# A1 (first cell) or A6 (row A, col 6). ``offset`` is the position of the
# first cell (A1) and ``pitch`` is the [x, y] step per (col, row);
# ``offset[2]`` sets the slot z height.
class StackHolder:
    DEFAULTS = dict(
        anchors = {"body": {"center": [0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0]}},
        size = [0, 0, 0], # [dx, dy, dz]
        offset= [0, 0, 0],                                    # A1 position [x, y, z]
        pitch=[9, 9],                                         # x step per col, y step per row
        rvec_safe = [0, 0, 0],
        rows=[chr(c) for c in range(ord("A"), ord("H") + 1)], # letters -> Y  (A..)
        cols= [i for i in range(1, 13)],                      # numbers -> X  (1..)
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

        # index anchors — Rack's X-Y grid (rows->Y letters, cols->X numbers);
        # anchor is <row><col> (e.g. A1, A2, ...)
        for r_idx, r in enumerate(self.rows):
            y = self.offset[1] + r_idx * self.pitch[1]
            for c_idx, c in enumerate(self.cols):
                x = self.offset[0] + c_idx * self.pitch[0]
                z = self.offset[2]
                prm["anchors"][next(iter(prm["anchors"]))][f"{r}{c}"] = [x, y, z] + self.rvec_safe

        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        }

        # slot
        self.slot = {
           next(iter(prm["anchors"])): [f"{r}{c}" for r in self.rows for c in self.cols]
        }
