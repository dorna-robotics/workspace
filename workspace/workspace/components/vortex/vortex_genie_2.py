from __future__ import annotations
from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
from workspace.components.factory import register


@register("vortex_genie_2")
class VortexGenie2:

    DEFAULTS = dict(
        anchors={
            "body": {
                "center": [0, 0, 0, 0, 0, 0],
                "top":    [0, 0, 164.4, 0, 0, 0],   # cup platform, top face
                "place":  [0, 37, 160.0, 0, 0, 0],  # item sits on the cup
                "hole_0": [ 25,  75, 0, 0, 0, 0],  # placeholder
                "hole_1": [-25,  75, 0, 0, 0, 0],  # placeholder
                "hole_2": [-25, -75, 0, 0, 0, 0],  # placeholder
                "hole_3": [ 25, -75, 0, 0, 0, 0],  # placeholder
            },
        },
        collision_box={
            "body": [
                # full-body AABB measured from CAD; [x,y,z,a,b,c], [lx,ly,lz]
                {"pose": [0.0, -6.119, 78.0, 0.0, 0.0, 0.0], "scale": [120.0, 177.238, 156.0], "padding_enabled": True},
            ],
        },
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        self.name = name
        self.workspace = workspace
        self.type = prm["type"]

        # assembly
        self.assembly = {
            k: Solid(
                type=self.type,
                anchors=prm["anchors"][k],
                component=self.name,
                **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {}),
            )
            for k in prm["anchors"]
        }

        # slot — you place items on the cup
        self.slot = {"body": ["place"]}
