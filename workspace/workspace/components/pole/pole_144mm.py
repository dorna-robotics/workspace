from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
from workspace.components.factory import register


@register("pole_144mm")
class Pole144mm:
    """Inspection post: a square pole on a tool-changer-style base,
    with mount points up one side.

    Measured off pole_144mm.glb (mm, origin at the base face,
    centered in XY):

        plate   65.0 x 65.0 x 6.0
        holes   dia 5.0 on the tool-changer +/-25 grid (the mesh shows
                one diagonal drilled; the anchors carry the full grid,
                like the tool rack)
        post    21.3 square shaft from the plate to the top at 150.0,
                widening to 24.0 square (rounded corners) at the
                z 75-80 collar and the top cap

    Anchors: ``center`` at the origin, ``top`` at z=150, ``place`` on
    the +x side of the post (x=12) at the top height z=150, and
    ``hole_0``-``hole_3`` on the +/-25 grid (tool-rack order).
    """

    DEFAULTS = dict(
        anchors =
            {"body": {
                "center": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                "top":    [0.0, 0.0, 150.0, 0.0, 0.0, 0.0],
                "hole_0": [25.0, 25.0, 0.0, 0.0, 0.0, 0.0],
                "hole_1": [-25.0, 25.0, 0.0, 0.0, 0.0, 0.0],
                "hole_2": [-25.0, -25.0, 0.0, 0.0, 0.0, 0.0],
                "hole_3": [25.0, -25.0, 0.0, 0.0, 0.0, 0.0],
                "place": [12.0, 0.0, 150.0, 0.0, 0.0, 0.0],
        }},
        # The 6 mm base plate, then one post box at the FULL 24-square
        # width of the collar and top cap (the 21.3 shaft sits 1.35 mm
        # inside it).
        collision_box =
            {"body":[
                {"pose": [0.0, 0.0, 3.0, 0.0, 0.0, 0.0], "scale": [65.0, 65.0, 6.0], "padding_enabled": True},
                {"pose": [0.0, 0.0, 78.0, 0.0, 0.0, 0.0], "scale": [24.0, 24.0, 144.0], "padding_enabled": True},
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
