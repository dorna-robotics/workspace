from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.plate.plate import Plate


@register("capholder_autosampler_2ml")
class CapholderAutosampler2ml(Plate):
    DEFAULTS = dict(
        anchors = {"body": {"center": [0, 0, 0, 0, 0, 0], "place": [0, 0, 3, 0, 0, 0], "top": [0, 0, 19, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 3.84, 0.0, 0.0, 0.0], "scale":[130.5, 88.6, 8.66]}#[xyzabc] , [lx,ly,lz]
        ]},
        offset=[0, 0],
        pitch=[14, 14],
        rvec_safe=[0, 0, 45],
        rows=[chr(c) for c in range(ord("A"), ord("F") + 1)],
        cols= [i for i in range(1, 9)],
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Plate.DEFAULTS) # default
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
