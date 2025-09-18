# workspace/components/core.py
from dorna2 import Solid, Dorna
from workspace.components.factory import register


@register("microplate")
class microplate:
    """
    the microplate
    """

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.type = "microplate"
        self.assembly = {}

        anchors = {}
        anchors["center"] = [0,0,0,0,0,0]

        x_start = -49.8
        y_start = 31.5
        pitch = 9.0
        rows = [chr(c) for c in range(ord("A"), ord("H") + 1)]  # A..H
        cols = range(1, 13)  # 1..20
        for r_idx, r in enumerate(rows):
            y = y_start - r_idx * pitch
            for c in cols:
                x = x_start + (c - 1) * pitch
                anchors[f"{r}{c}"] = [x, y, 3.0, 0.0, 0.0, 0.0]







        self.assembly["microplate"] = Solid(name="microplate", type="microplate", anchors=anchors, component=self.name)