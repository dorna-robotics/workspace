from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
from workspace.components.factory import register


@register("scale")
class Scale:
    DEFAULTS = dict(
        anchors={
            "body": {"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 126, 0, 0, 0], "place":[0, 0, 96, 0, 0, 0],
                "hole_0": [75, 75, 0, 0, 0, 0], "hole_1": [-75, 75, 0, 0, 0, 0], "hole_2": [-75, -75, 0, 0, 0, 0], "hole_3": [75, -75, 0, 0, 0, 0]},
        },
        collision_box =
            {"body":[
                {"pose":[0.0, 45.0, 63.0, 0.0, 0.0, 0.0], "scale":[196.0, 320.0, 126.0]},
        ]},
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        # init
        self.name = name
        self.workspace = workspace
        self.type = prm.get("type")

        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        }

        # slot
        self.slot = {
            "body": ["place"]
        }
