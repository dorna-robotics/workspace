from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
from workspace.components.factory import register


@register("rack_liquid_waste")
class RackLiquidWaste:
    """Liquid-waste container that rides in the waste-management stand.

    Measured off liquid_waste.glb (mm). The origin is partway up the
    body — NOT the bottom: the container hangs from z=-35.3 below the
    origin up to the cap top at z=+12.7, footprint 37.0 x 37.0 (the
    same as the stand it sits in).

    Anchors: ``center`` at the origin, ``top`` at the measured cap top,
    ``place`` the same as ``top``.
    """

    DEFAULTS = dict(
        anchors =
            {"body": {
                "center": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                "top":    [0.0, 0.0, 12.7, 0.0, 0.0, 0.0],
                "place":  [0.0, 0.0, 12.7, 0.0, 0.0, 0.0],
        }},
        collision_box =
            {"body":[
                {"pose": [0.0, 0.0, -11.3, 0.0, 0.0, 0.0], "scale": [37.0, 37.0, 48.0], "padding_enabled": True},
        ]},
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

        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        }
