from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.rack.rack import Rack


@register("capholder_amber_40ml_4x7")
class CapholderAmber40ml4x7(Rack):
    """Cap holder for the amber 40 mL 4x7 rack.

    Measured off capholder_amber_40ml_4x7.glb (mm, origin at the base,
    centered in XY): block 285.0 x 165.0 x 12.0, cap pocket floors at
    z=5.0, 7 columns (1-7, +x) by 4 rows (A-D, +y) on a 40.0 mm pitch,
    A1 at (-120.0, -60.0).
    """
    DEFAULTS = dict(
        anchors = {"body": {"center": [0, 0, 0, 0, 0, 0], "place": [0, 0, 5, 0, 0, 0], "top": [0, 0, 12, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 12/2, 0.0, 0.0, 0.0], "scale":[285.0, 165.0, 12.0], "padding_enabled": True},#[xyzabc] , [lx,ly,lz]
        ]},
        offset=[-40.0*(7-1)/2, -40.0*(4-1)/2, 5],
        pitch=[40.0, 40.0, 0],
        rvec_safe=[0, 0, 45],
        rows=[chr(c) for c in range(ord("A"), ord("D") + 1)],
        cols= [i for i in range(1, 7+1)],
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
