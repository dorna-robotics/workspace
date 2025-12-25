from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
from workspace.components.factory import register


@register("fixture_plate")
class FixturePlate:
    DEFAULTS = dict(
        anchors = 
            {"body": {
                "center": [0.0, 0.0, 0, 0.0, 0.0, 0.0],
                "top": [0.0, 0.0, 7.0, 0.0, 0.0, 0.0],
                "corner_0": [-250.0, 125.0, 7.0, 0.0, 0.0, 0.0],
                "corner_1": [250.0, 125.0, 7.0, 0.0, 0.0, 0.0],
                "corner_2": [250.0, -125.0, 7.0, 0.0, 0.0, 0.0],
                "corner_3": [-250.0, -125.0, 7.0, 0.0, 0.0, 0.0],
        }},
        rows = [chr(c) for c in range(ord("A"), ord("J") + 1)],  # A..J
        cols = range(1, 21),  # 1..20
        x_start = -237.5,
        y_start = 112.5,
        pitch = 25.0,
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs
        
        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))

        # init
        self.name = name
        self.workspace = workspace
        self.type = prm["type"]

        # anchors        
        for r_idx, r in enumerate(prm["rows"]):
            y = prm["y_start"] - r_idx * prm["pitch"]
            for c in prm["cols"]:
                x = prm["x_start"] + (c - 1) * prm["pitch"]
                prm["anchors"][next(iter(prm["anchors"]))][f"{r}{c}"] = [x, y, 7.0, 0.0, 0.0, 0.0]

        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name) for k in prm["anchors"]
        }        
        