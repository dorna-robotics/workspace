from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid

from workspace.components.factory import register


# ScaleTop Amber 40ml — static fixture that sits on the scale pan; the same
# family as scale_top_falcon_15ml (identical d 83.0 base disc), stood taller
# for a 40 ml amber vial.
#
# Measured off scale_top_amber_40ml.glb (solid cross-sections, least-squares
# circle fits), z = 0 on the scale pan:
#    0.0 -  6.0  d 5.18 locating stem (drops into the pan)
#    6.0 - 11.0  d 83.00 base disc (83.0 x 82.77), 4 x d 4.22 at (+/-12.905, +/-12.905)
#   11.0 - 71.0  4 posts, d 6.98, on that same +/-12.905 square
#   71.0 - 83.0  40 x 40 top plate, d 29.50 guide bore with a 45 deg lead-in
#                opening to d 36.9 over the top 4 mm
# The vial drops through the guide bore and bottoms out on the disc, so
# `place` is the disc's top face at z = 11 — not the bore.
#   anchors:  [x, y, z, a, b, c]  (position + orientation, relative to center)
#   collision_box: {"pose": [x, y, z, a, b, c], "scale": [lx, ly, lz]}
@register("scale_top_amber_40ml")
class ScaleTopAmber40ml:
    DEFAULTS = dict(
        anchors={"body": {
            "center": [0, 0, 0, 0, 0, 0],
            "place":  [0, 0, 11, 0, 0, 0],   # disc top face — the vial stands here
            "top":    [0, 0, 83, 0, 0, 0],   # top plate face
            "clb_0":  [0, 0, 6, 0, 0, 0],   # bore axis at the top face
        }},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 6.0/2, 0.0, 0.0, 0.0], "scale":[5.2, 5.2, 6.0], "padding_enabled": True},          # locating stem
                {"pose":[0.0, 0.0, 6.0+(5.0/2), 0.0, 0.0, 0.0], "scale":[83.0, 82.768, 5.0], "padding_enabled": True},# base disc
                {"pose":[0.0, 0.0, 11.0+(72.0/2), 0.0, 0.0, 0.0], "scale":[40.0, 40.0, 72.0], "padding_enabled": True}# posts + top plate
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

        self.assembly = {
            k: Solid(
                type=self.type,
                anchors=prm["anchors"][k],
                component=self.name,
                **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {}),
            )
            for k in prm["anchors"]
        }
