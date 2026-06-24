from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid

from workspace.components.factory import register


# Table 48x30 — static fixture. Fill in the anchor poses and the collision box with
# the real geometry; placeholders are zeroed out.
#   anchors:  [x, y, z, a, b, c]  (position + orientation, relative to center)
#   collision_box: {"pose": [x, y, z, a, b, c], "scale": [lx, ly, lz]}
@register("table_48x30")
class Table48x30:
    DEFAULTS = dict(
        anchors={"body": {
            "center": [0, 0, 0, 0, 0, 0],
            "place":    [0, 0, 736.60, 0, 0, 0],
        }},
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
