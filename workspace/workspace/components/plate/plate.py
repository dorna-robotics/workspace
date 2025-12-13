
"""
center: the bottom center of the plate
pitch: pitch in x and y direction
height: the z offset of the center of the object trying to place in the seat
height_place: the z offset of the center of the object place on this plate
rvec_safe: rotation vector which is collision free for picking the item attached to the plate
rows, cols: the name of anchors where the items aatched to the plate
"""
import numpy as np
from dorna2 import Solid


class Plate:
    def __init__(self, name: str, workspace,
                type=None,
                anchors = {"solid_0": {"center": [0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0]}},
                offset= [0, 0],
                pitch=[9, 9],
                rvec_safe = [0, 0, 45],
                rows=[chr(c) for c in range(ord("A"), ord("H") + 1)],
                cols= [i for i in range(1, 13)],
                **kwargs,
                ):
        self.name = name
        self.workspace = workspace
        self.type = type
        self.offset = offset[:]
        self.pitch = pitch[:]
        self.height = np.linalg.norm(np.array(anchors[next(iter(anchors))]["center"][0:3]) - np.array(anchors[next(iter(anchors))]["top"][0:3]))
        self.height_place = np.linalg.norm(np.array(anchors[next(iter(anchors))]["center"][0:3]) - np.array(anchors[next(iter(anchors))]["place"][0:3]))
        self.rvec_safe = rvec_safe[:]
        self.rows = rows[:]
        self.cols = cols[:]

        # index anchors
        x_start = self.offset[0] - self.pitch[0]*(len(self.cols)-1)/2
        y_start = self.offset[1] + self.pitch[1]*(len(self.rows)-1)/2

        for r_idx, r in enumerate(self.rows):
            y = y_start - r_idx * self.pitch[1]
            for c in self.cols:
                x = x_start + (c - 1) * self.pitch[0]
                anchors[next(iter(anchors))][f"{r}{c}"] = [x, y, self.height_place]+self.rvec_safe

        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=anchors[k], component=self.name) for k in anchors
        }

        # enable, disable
        self.enable = []
        self.disable = []