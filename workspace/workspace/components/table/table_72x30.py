from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid

from workspace.components.factory import register


# Table 72x30 — static fixture. Anchors and collision box are copied from table_48 as
# a starting point; update them for the 72x30's real geometry (e.g. length).
#   anchors:  [x, y, z, a, b, c]  (position + orientation, relative to center)
#   collision_box: {"pose": [x, y, z, a, b, c], "scale": [lx, ly, lz]}
@register("table_72x30")
class Table72x30:
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
