from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid

from workspace.components.factory import register

@register("anode")
class Anode:
    DEFAULTS = dict(
        anchors={"body": {
            "center": [0, 0, 0, 0, 0, 0],
            "place":  [0, 6.25, 106.24, 0, 0, 0],
            "top":    [0, 6.25, 106.24, 0, 0, 0],
            "hole_0":  [ 75,  37.5, 0, 0, 0, 0],
            "hole_1":  [-75,  37.5, 0, 0, 0, 0],
            "hole_2":  [-75, -37.5, 0, 0, 0, 0],
            "hole_3":  [ 75, -37.5, 0, 0, 0, 0],
            "clb_0":    [50, 6.25, 106.24, 0, 0, 90],
            "clb_1":    [-50, 6.25, 106.24, 0, 0, 90],

        }},

        collision_box={"body": [
            {"pose": [0.0, 9.625, ((106.24/2)+2), 0.0, 0.0, 0.0], "scale": [168.5, 101.5+6.75, 108.24]},  # placeholder — set from CAD
        ]},
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        prm = deepcopy(self.DEFAULTS)
        merge(prm, cfg)
        merge(prm, kwargs)
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        self.name = name
        self.workspace = workspace
        self.type = prm["type"]

        self.assembly = {
            k: Solid(
                type=self.type,
                anchors=prm["anchors"][k],
                component=self.name,
                **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {}),
            )
            for k in prm["anchors"]
        }
