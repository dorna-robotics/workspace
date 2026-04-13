from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.rack.rack import Rack


@register("capholder_autosampler_2ml_5x10")
class CapholderAutosampler2ml5x10(Rack):
    DEFAULTS = dict(
        anchors = {"body": {"center": [0, 0, 0, 0, 0, 0], "place": [0, 0, 7, 0, 0, 0], "top": [0, 0, 10.5, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 10.5/2, 0.0, 0.0, 0.0], "scale":[194.0, 101.25, 10.5]}#[xyzabc] , [lx,ly,lz]
        ]},
        offset=[-19.18*(10-1)/2, -19.18*(5-1)/2, 7],
        pitch=[19.18, 19.18, 0],
        rvec_safe=[0, 0, 45],
        rows=[chr(c) for c in range(ord("A"), ord("E") + 1)],
        cols= [i for i in range(1, 10+1)],
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
