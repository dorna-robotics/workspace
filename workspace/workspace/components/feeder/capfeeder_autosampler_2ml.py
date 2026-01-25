from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.feeder.feeder import Feeder


@register("capfeeder_autosampler_2ml")
class FeederCap2ml(Feeder):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "place":[0, -20.569 , 171.66, -45, 0, 0], "top": [0, 87.972 , 147.972, -45, 0, 0],
                "hole_0":[50, 12.5, 0, 0, 0, 0], "holde_1": [-50, 12.5, 0, 0, 0, 0], "holse_2": [-50, -12.5, 0, 0, 0, 0], "holde_3": [50, -12.5, 0, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 2.0, 0.0, 0.0, 0.0], "scale":[116.0, 40.0, 13.0]},#[xyzabc] , [lx,ly,lz]
                {"pose":[0.0, -11.5, 35.5, 0.0, 0.0, 0.0], "scale":[75.0, 131.5, 75.0]},
                {"pose":[0.0, 13.5, 75.5, -45.0, 0.0, 0.0], "scale":[77.0, 60.0, 78.0]},
                {"pose":[0.0, 63.0, 122.0, -45.0, 0.0, 0.0], "scale":[215.0, 215.0, 72.0]}
        ]},
        # cfg
        axis = 7,
        num_slots = 16,
        vaj=[300, 4000, 10000],
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

