from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.tube.tube import Tube

@register("tube_amber_40ml")
class TubeAmber40ml(Tube):
    DEFAULTS = dict(
        anchors={
            "body": {"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 95, 0, 0, 0], "place":[0, 0, 87, 0, 0, 0], "place_cap":[0, 0, 88, 0, 0, 0]},
        },
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 47.5, 0.0, 0.0, 0.0], "scale":[27.5, 27.5, 95.0]}#[xyzabc] , [lx,ly,lz]
        ]},
        size = [26, 26, 95], # [dx, dy, dz]
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