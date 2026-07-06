from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.rack.rack import Rack


@register("capholder_autosampler_2ml")
class CapholderAutosampler2ml(Rack):
    DEFAULTS = dict(
        anchors = {"body": {"center": [0, 0, 0, 0, 0, 0], "place": [0, 0, 3.5, 0, 0, 0], "top": [0, 0, 7, 0, 0, 0],
                "clb_0": [60, 35.5, 3.5, 0, 0, -45], "clb_1": [-60, 35.5, 3.5, 0, 0, -135], "clb_2": [-60, -35.5, 3.5, 0, 0, -45], "clb_3": [60, -35.5, 3.5, 0, 0, -135]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 4.0, 0.0, 0.0, 0.0], "scale":[127.76, 85.48, 9.0], "padding_enabled": True},#[xyzabc] , [lx,ly,lz]
        ]},
        offset=[-14*(8-1)/2, -14*(6-1)/2, 3.5],
        pitch=[14, 14, 0],
        rvec_safe=[0, 0, 45],
        rows=[chr(c) for c in range(ord("A"), ord("F") + 1)],
        cols= [i for i in range(1, 8+1)],
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
