from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.rack.rack import Rack

@register("rack_micronic_96_2")
class RackMicronic962(Rack):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 3, 0, 0, 0], "top": [0, 0, 19, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0, 0, 15, 0, 0, 0], "scale":[129,87,30]}#[xyzabc] , [lx,ly,lz]
        ]},
        offset=[-9*(12-1)/2, -9*(8-1)/2, 3],
        pitch=[9, 9, 0],
        rvec_safe=[0, 0, 0],
        rows=[chr(c) for c in range(ord("A"), ord("H") + 1)],
        cols= [i for i in range(1, 12+1)],
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
