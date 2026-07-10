from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.rack.rack import Rack

@register("rack_amber_40ml_1x6")
class RackAmber40ml1x6(Rack):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 2, 0, 0, 0], "top": [0, 0, 30, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 15.0, 0.0, 0.0, 0.0], "scale":[245.0, 45.0, 30.0], "padding_enabled": True}#[xyzabc] , [lx,ly,lz]
        ]},
        offset=[-40*(6-1)/2, -40*(1-1)/2, 2],
        pitch=[40, 40, 0],
        rvec_safe=[0, 0, 45],
        rows=[chr(c) for c in range(ord("A"), ord("A") + 1)],
        cols= [i for i in range(1, 6+1)],
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