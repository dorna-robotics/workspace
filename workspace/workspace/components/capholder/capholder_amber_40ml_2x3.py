from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.rack.rack import Rack


@register("capholder_amber_40ml_2x3")
class CapholderAmber40ml2x3(Rack):
    """Cap holder for the amber 40 mL caps, 2x3.

    Measured off capholder_amber_40ml_2x3.glb (mm, origin at the base,
    centered in XY): block 125.0 x 85.0 x 12.0, cap seats at z=5.0,
    3 columns (1-3, +x) by 2 rows (A-B, +y) on a 40.0 mm pitch,
    A1 at (-40.0, -20.0). Pin holes (5 mm) at (+/-25, 0).
    """
    DEFAULTS = dict(
        anchors = {"body": {"center": [0, 0, 0, 0, 0, 0], "place": [0, 0, 5, 0, 0, 0], "top": [0, 0, 12, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 12/2, 0.0, 0.0, 0.0], "scale":[125.0, 85.0, 12.0], "padding_enabled": True},#[xyzabc] , [lx,ly,lz]
        ]},
        offset=[-40.0*(3-1)/2, -40.0*(2-1)/2, 5],
        pitch=[40.0, 40.0, 0],
        rvec_safe=[0, 0, 45],
        rows=[chr(c) for c in range(ord("A"), ord("B") + 1)],
        cols= [i for i in range(1, 3+1)],
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Rack.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        super().__init__(
            name=name,
            workspace=workspace,
            **prm
        )
