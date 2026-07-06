from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.rack.rack import Rack

@register("rack_tip_waste_bin")
class RackTipWasteBin(Rack):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 45, 0, 0, 0], "top": [0, 0, 66, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 66/2, 0.0, 0.0, 0.0], "scale":[127.4, 85, 66], "padding_enabled": True}#[xyzabc] , [lx,ly,lz]
        ]},
        offset=[0, 0, 45],
        pitch=[0, 0, 0],
        rvec_safe = [0, 0, 0],
        rows=[chr(c) for c in range(ord("A"), ord("A") + 1)],
        cols= [i for i in range(1, 1+1)],
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Rack.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs
        
        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))
        
        # init
        super().__init__(name=name, workspace=workspace, **prm)
