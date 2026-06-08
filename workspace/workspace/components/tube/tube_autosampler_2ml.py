from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.tube.tube import Tube

@register("tube_autosampler_2ml")
class TubeAutosampler2ml(Tube):
    DEFAULTS = dict(
        anchors={
            "body": {"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 32, 0, 0, 0], "place":[0, 0, 28, 0, 0, 0], "cap_seat":[0, 0, 29.5-1, 0, 0, 0] },
        },
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 32/2, 0.0, 0.0, 0.0], "scale":[11.6, 11.6, 32]}#[xyzabc] , [lx,ly,lz]
        ]},
    )
    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Tube.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs
        
        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))
        
        # init
        super().__init__(name=name, workspace=workspace, **prm)