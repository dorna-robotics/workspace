from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid

from workspace.components.factory import register


@register("disc_22mm")
class Disc22mm:
    # The "place" anchor sits one disc-thickness above center, so when a
    # disc is attached at this anchor (via its own center) it lands exactly
    # on top of the previous one — same chaining pattern tubes/caps use.
    DEFAULTS = dict(
        anchors={"body": {
            "center": [0, 0, 0, 0, 0, 0],
            "top":    [0, 0, 0.254, 0, 0, 0],
        }},
        collision_box={"body": [
            {"pose": [0.0, 0.0, 0.254 / 2, 0.0, 0.0, 0.0], "scale": [21.59, 21.59, 0.254]},
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

        # box_for_grip=True keeps the disc's collision box out of the world
        # planning scene while it sits in the holder; the box is only emitted
        # (in the flange frame) when the disc is the one currently carried.
        self.assembly = {
            k: Solid(
                type=self.type,
                anchors=prm["anchors"][k],
                component=self.name,
                box_for_grip=True,
                **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {}),
            )
            for k in prm["anchors"]
        }
