from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid

from workspace.components.factory import register


# Vortex-Genie 2ml tube top — static fixture that sits on the vortexer cup.
# Geometry off the CAD model: base plate z 0..4 (ears span y +/-39.22),
# boss r=10 up to z=19, tube socket bored r=6.5 with its floor at z=4 and a
# lead-in chamfer opening to r=8 at the z=19 rim. Socket is centred on (0, 0).
#   anchors:  [x, y, z, a, b, c]  (position + orientation, relative to center)
#   collision_box: {"pose": [x, y, z, a, b, c], "scale": [lx, ly, lz]}
@register("vortex_genie_top_2ml")
class VortexGenieTop2ml:
    DEFAULTS = dict(
        anchors={"body": {
            "center": [0, 0, 0, 0, 0, 0],
            "place":  [0, 0, 4, 0, 0, 0],    # bore floor — tube bottoms out here
            "top":    [0, 0, 24, 0, 0, 0],   # socket rim
        }},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 24/2, 0.0, 0.0, 0.0], "scale":[30.0, 78.442, 24], "padding_enabled": True}#[xyzabc] , [lx,ly,lz]
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

        # slot — the tube sits in the socket
        self.slot = {"body": ["place"]}
