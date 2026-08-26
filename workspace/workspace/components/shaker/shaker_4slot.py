from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.shaker.shaker import Shaker
from dorna2 import Solid


# The four-slot head that replaced the two-slot one on the same shaker
# body — ONE clamp state for the whole head (a single output_open /
# output_close pair on the base Shaker, where the old head had one per
# slot), four vessel seats.
#
# `rotating` measured off shaker_rotating.glb, origin at the pivot (its
# `input` anchor, which mounts on the body's `output`), +z up at joint = 0:
#   -4.0 -   4.5  base plate      68 x 130 (x +/-34, y 0..130)
#    4.5 -  22.0  drive block     x -46.7..35.0, y 36.5..127.5 (motor bulges -x)
#   22.0 -  29.0  hub             70 x 91
#   29.0 -  37.5  gripper fingers 70 x 164 — two 31-wide jaws, x +/-35..+/-4,
#                                 top face at z = 37.000, 8.0 gap on the axis
#   37.5 -  64.0  posts           28 x 154
#   64.0 -  76.0  top plate       40 x 164, four d 28.40 bores, plus two M5
#                                 tapped holes on the axis at y = 40 / 124
# The four vessel axes are x = 0, y = 19 / 61 / 103 / 145 (42.0 pitch): the
# top-plate bores and the finger gap are the same four lines. place_1..4 sit
# at the finger top face (z = 37) — where the jaws actually hold the vessel —
# not at the top plate.
@register("shaker_4slot")
class Shaker4slot(Shaker):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "output":[0, 53, 160, 0, 0, 0], 
                "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0]},
                "rotating": {"center": [0, 0, 0, 0, 0, 0], "input":[0, 0, 0, 0, 0, 0], "place": [0, 0, 37, 0, 0, 0],
                    "A1": [0, 19, 37, 0, 0, 0], "A2": [0, 61, 37, 0, 0, 0],
                    "A3": [0, 103, 37, 0, 0, 0], "A4": [0, 145, 37, 0, 0, 0],
                    # Calibration anchors on the two M5 tapped holes through
                    # the top plate (x = 0, y = 40 / 124, 84.0 apart), on the
                    # z = 76 top face. Ordered by +y, like place_1..4.
                    "clb_0": [0, 40, 76, 0, 0, 0], "clb_1": [0, 124, 76, 0, 0, 0],
                    "top": [0, 82, 76, 0, 0, 0],}},
        # Measured from shaker_body.glb / shaker_rotating.glb.
        # The head box is taller than the part: it pivots about b at the
        # `output` anchor, so a gripper on it sweeps above 206.
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 3.0, 0.0, 0.0, 0.0],
                 "scale":[65.0, 65.0, 6.0], "padding_enabled": True},          # base plate
                {"pose":[0.0, 23.5, 106.0, 0.0, 0.0, 0.0],
                 "scale":[55.4, 59.0, 200.0], "padding_enabled": True},        # post + head mount
        ],
             "rotating":[
                {"pose":[-5.85, 82.0, 36.0, 0.0, 0.0, 0.0],
                 "scale":[81.7, 164.0, 80.0], "padding_enabled": True},
        ]},
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Shaker.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))
        
        # super
        super().__init__(
            name=name,
            workspace=workspace,
            **prm
        )

        # assembly
        self.assembly = {
            k: Solid(type=f"shaker_{k}", anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        }

        # slot
        self.slot = {
            "rotating": ["A1", "A2", "A3", "A4"],
        }
