from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.feeder.feeder import Feeder


@register("capfeeder_autosampler_2ml")
class FeederCap2ml(Feeder):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "place":[0, -12.23473 , 152.56783, -45, 0, 0], "top": [0, -12.23473 , 160.56783, -45, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 1.84, 0.0, 0.0, 0.0], "scale":[115.74, 40.02, 12.813]},#[xyzabc] , [lx,ly,lz]
                {"pose":[0.0, -11.5, 35.5, 0.0, 0.0, 0.0], "scale":[74.67, 131.5, 74.97]},
                {"pose":[0.0, 13.38, 75.61, 45.0, 0.0, 0.0], "scale":[77.27, 59.59, 77.89]},
                {"pose":[0.0, 62.83, 121.75, 45.0, 0.0, 0.0], "scale":[215.14, 215.14, 71.75]}
        ]}
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Feeder.DEFAULTS) # default
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

