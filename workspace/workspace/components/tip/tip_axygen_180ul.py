from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.tip.tip import Tip

@register("tip_axygen_180ul")
class TipAxygen180ul(Tip):
    DEFAULTS = dict(
        anchors={
            "body": {"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 52, 0, 0, 0]},
        },
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 26.0, 0.0, 0.0, 0.0], "scale":[7.7, 7.7, 52.3]}
        ]},
    )
    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Tip.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs
        
        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))
        
        # init
        super().__init__(name=name, workspace=workspace, **prm)