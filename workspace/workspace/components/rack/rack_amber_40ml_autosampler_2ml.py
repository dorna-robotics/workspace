from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
from workspace.components.factory import register


@register("rack_amber_40ml_autosampler_2ml")
class RackAmber40mlAutosampler2ml:
    """Two-vial rack: one 40 mL amber pocket, one 2 mL autosampler
    pocket, side by side on a single block.

    Measured off rack_amber_40ml_autosampler_2ml.glb (mm, origin at the
    base, centered in XY):

        block            62.0 x 45.0 x 12.0  (z 0..12)
        amber pocket     dia 29.5, center (-10.0, 0.0), floor z=2.0
        autosampler      dia 13.0, center (21.0, 0.0),  floor z=4.0
        mounting holes   dia 5.0 at (13.0, +/-12.5), z=0

    Anchors: ``center`` at the origin, ``top`` at z=12, ``place_0`` at
    the bottom of the 29.5 pocket, ``place_1`` at the bottom of the
    13.0 pocket, ``hole_0``/``hole_1`` at the 5 mm holes (-y first).

    The collision boxes are the block MINUS the two pocket openings
    (square cutouts around each bore) — a vial has to be able to
    descend into its pocket, so a single solid block would be wrong.
    Floor slabs cover the material under each pocket.
    """

    DEFAULTS = dict(
        anchors =
            {"body": {
                "center":  [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                "top":     [0.0, 0.0, 12.0, 0.0, 0.0, 0.0],
                "place_0": [-10.0, 0.0, 2.0, 0.0, 0.0, 0.0],
                "place_1": [21.0, 0.0, 4.0, 0.0, 0.0, 0.0],
                "hole_0":  [13.0, -12.5, 0.0, 0.0, 0.0, 0.0],
                "hole_1":  [13.0,  12.5, 0.0, 0.0, 0.0, 0.0],
        }},
        collision_box =
            {"body":[
                # floor slabs under the pockets
                {"pose": [-10.0, 0.0, 1.0, 0.0, 0.0, 0.0], "scale": [29.5, 29.5, 2.0], "padding_enabled": True},
                {"pose": [21.0, 0.0, 2.0, 0.0, 0.0, 0.0], "scale": [13.0, 13.0, 4.0], "padding_enabled": True},
                # full-height strips around the pocket cutouts
                {"pose": [-27.875, 0.0, 6.0, 0.0, 0.0, 0.0], "scale": [6.25, 45.0, 12.0], "padding_enabled": True},   # left edge
                {"pose": [9.625, 0.0, 6.0, 0.0, 0.0, 0.0], "scale": [9.75, 45.0, 12.0], "padding_enabled": True},     # between pockets
                {"pose": [29.25, 0.0, 6.0, 0.0, 0.0, 0.0], "scale": [3.5, 45.0, 12.0], "padding_enabled": True},      # right edge
                {"pose": [-10.0, 18.625, 6.0, 0.0, 0.0, 0.0], "scale": [29.5, 7.75, 12.0], "padding_enabled": True},  # behind amber pocket
                {"pose": [-10.0, -18.625, 6.0, 0.0, 0.0, 0.0], "scale": [29.5, 7.75, 12.0], "padding_enabled": True}, # front of amber pocket
                {"pose": [21.0, 14.5, 6.0, 0.0, 0.0, 0.0], "scale": [13.0, 16.0, 12.0], "padding_enabled": True},     # behind autosampler
                {"pose": [21.0, -14.5, 6.0, 0.0, 0.0, 0.0], "scale": [13.0, 16.0, 12.0], "padding_enabled": True},    # front of autosampler
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
