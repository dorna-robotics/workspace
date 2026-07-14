from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.rack.rack import Rack


@register("capholder_autosampler_2ml_4x14")
class CapholderAutosampler2ml4x14(Rack):
    DEFAULTS = dict(
        anchors = {"body": {"center": [0, 0, 0, 0, 0, 0], "place": [0, 0, 4, 0, 0, 0], "top": [0, 0, 7.5, 0, 0, 0], "hole_0":[50,0,0,0,0,0], "hole_1":[-50,0,0,0,0,0]}},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 7.5/2, 0.0, 0.0, 0.0], "scale":[236.0, 71.0, 7.5]}#[xyzabc] , [lx,ly,lz]
        ]},
        offset=[-16.5*(14-1)/2, -16.5*(4-1)/2, 4],
        pitch=[16.5, 16.5, 0],
        rvec_safe=[0, 0, 45],
        rows=[chr(c) for c in range(ord("A"), ord("D") + 1)],
        cols= [i for i in range(1, 14+1)],
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
