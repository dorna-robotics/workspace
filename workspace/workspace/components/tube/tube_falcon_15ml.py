from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.tube.tube import Tube

@register("tube_falcon_15ml")
class TubeFalcon15ml(Tube):
    DEFAULTS = dict(
        anchors={
            "body": {"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 119, 0, 0, 0], "place":[0, 0, 109.5, 0, 0, 0]},
        },
        size = [16.85, 16.85, 119], # [dx, dy, dz]
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