from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid

from workspace.components.factory import register


# Static rotating-cylinder arm mount. Single solid, no motion, no IO.
@register("cathode")
class Cathode:
    DEFAULTS = dict(
        anchors={
            "body": {
                "center": [0, 0, 0, 0, 0, 0],
            },
        },
        collision_box={
            "body": [
                {"pose": [(132.25 / 2) - 17, 0, 10/2, 0.0, 0.0, 0.0], "scale": [132.25, 32, 40]},
            ],
        },
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
