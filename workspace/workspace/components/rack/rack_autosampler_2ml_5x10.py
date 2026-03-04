from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.rack.rack import Rack

@register("rack_autosampler_2ml_5x10")
class RackAutosampler2ml5x10(Rack):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 8, 0, 0, 0], "top": [0, 0, 21, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 27/2, 0.0, 0.0, 0.0], "scale":[127.76, 85.48, 27]}#[xyzabc] , [lx,ly,lz]
        ]},
        offset=[0, 0],
        pitch=[19.18, 19.18],
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
        
        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))
        
        # init
        super().__init__(name=name, workspace=workspace, **prm)

