from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.rack.rack import Rack


@register("capholder_falcon_15ml")
class CapholderFalcon15ml(Rack):
    DEFAULTS = dict(
        anchors = {"body": {"center": [0, 0, 0, 0, 0, 0], "place": [0, 0, 2, 0, 0, 0], "top": [0, 0, 4, 0, 0, 0],
        "hole_0":[75, 50, 0, 0, 0, 0], "hole_1": [-75, 50, 0, 0, 0, 0], "hole_2": [-75, -50, 0, 0, 0, 0], "hole_3": [75, -50, 0, 0, 0, 0]}},      
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 4/2, 0.0, 0.0, 0.0], "scale":[165.00, 115.0, 4], "padding_enabled": True}#[xyzabc] , [lx,ly,lz]
        ]},
        offset=[-28*(5-1)/2, -28*(4-1)/2, 2],
        pitch=[28, 28, 0],
        rvec_safe=[0, 0, 45],
        rows=[chr(c) for c in range(ord("A"), ord("D") + 1)],
        cols= [i for i in range(1, 5+1)],
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Rack.DEFAULTS) # default
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
