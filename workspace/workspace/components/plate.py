# workspace/components/plate.py
from dorna2 import Solid, Dorna
from workspace import workspace
from workspace.components.factory import register


@register("plate")
class Plate:
    """
    the plate
    """

    def __init__(self, name: str, cfg: dict, workspace):
        self.name = name
        self.type = "plate"
        self.workspace = workspace
        self.assembly = {}
        # --------- plate
        plate_anchors = {}
        # 10 x 20 grid (A..J, 1..20), 25mm pitch, + convenience anchors
        plate_x_start = -237.5
        plate_y_start = 112.5
        plate_pitch = 25.0
        rows = [chr(c) for c in range(ord("A"), ord("J") + 1)]  # A..J
        cols = range(1, 21)  # 1..20
        for r_idx, r in enumerate(rows):
            y = plate_y_start - r_idx * plate_pitch
            for c in cols:
                x = plate_x_start + (c - 1) * plate_pitch
                plate_anchors[f"{r}{c}"] = [x, y, 7.0, 0.0, 0.0, 0.0]
        plate_anchors["corner_0"] = [-250.0, 125.0, 7.0, 0.0, 0.0, 0.0]
        plate_anchors["corner_1"] = [250.0, 125.0, 7.0, 0.0, 0.0, 0.0]
        plate_anchors["corner_2"] = [250.0, -125.0, 7.0, 0.0, 0.0, 0.0]
        plate_anchors["corner_3"] = [-250.0, -125.0, 7.0, 0.0, 0.0, 0.0]
        plate_anchors["center"] = [0.0, 0.0, 7.0, 0.0, 0.0, 0.0]

        self.assembly["plate"] = Solid(name="plate", type="plate", anchors=plate_anchors, component=self.name)
        
        