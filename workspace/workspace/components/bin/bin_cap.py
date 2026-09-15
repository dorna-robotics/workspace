from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
from workspace.components.factory import register


@register("bin_cap")
class BinCap:
    """Waste bin for discarded caps — an open tray the robot drops caps
    into from above.

    Measured off bin_cap.glb (mm, origin at the base, centered in XY):
    90.0 x 100.0 x 25.0, floor plate up to z=5, rim at z=25.

    Anchors: ``center`` at the origin, ``top`` at the rim, ``place``
    the same as ``top`` — drops release above it. ``hole_0``..``hole_3``
    are the four counterbored floor holes, a 75 x 75 square around
    center. One collision box over the whole bin: nothing is meant to
    enter the volume, caps fall in from above the rim.
    """

    DEFAULTS = dict(
        anchors =
            {"body": {
                "center": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                "top":    [0.0, 0.0, 25.0, 0.0, 0.0, 0.0],
                "place":  [0.0, 0.0, 25.0, 0.0, 0.0, 0.0],
                "hole_0": [37.5, 37.5, 0.0, 0.0, 0.0, 0.0],
                "hole_1": [-37.5, 37.5, 0.0, 0.0, 0.0, 0.0],
                "hole_2": [-37.5, -37.5, 0.0, 0.0, 0.0, 0.0],
                "hole_3": [37.5, -37.5, 0.0, 0.0, 0.0, 0.0],
        }},
        collision_box =
            {"body":[
                {"pose": [0.0, 0.0, 12.5, 0.0, 0.0, 0.0], "scale": [90.0, 100.0, 25.0], "padding_enabled": True},
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
