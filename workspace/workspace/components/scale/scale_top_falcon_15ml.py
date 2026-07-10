from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid

from workspace.components.factory import register


# ScaleTop Falcon — static fixture that sits on the scale pan. Placeholder
# anchors + collision box; fill in the real geometry.
#   anchors:  [x, y, z, a, b, c]  (position + orientation, relative to center)
#   collision_box: {"pose": [x, y, z, a, b, c], "scale": [lx, ly, lz]}
@register("scale_top_falcon_15ml")
class ScaleTopFalcon15ml:
    DEFAULTS = dict(
        anchors={"body": {
            "center": [0, 0, 0, 0, 0, 0],
            "place":  [0, 0, 10, 0, 0, 0],
            "top":    [0, 0, 70, 0, 0, 0],
        }},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 70.0/2, 0.0, 0.0, 0.0], "scale":[83.0, 83.0, 70.0], "padding_enabled": True}#[xyzabc] , [lx,ly,lz]
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
