from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.shaker.shaker import Shaker


@register("shaker_2slot")
class Shaker2slot(Shaker):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "place": [0, 94+20, 100, 0, 0, 0], "top": [0, 87.972 , 147.972, 0, 0, 0],
                          "A1": [35, 94+20, 100-45, 0, 0, 0], "A2": [-35, 94+20, 100-45, 0, 0, 0],
                "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0],
                "clb_0": [0, 0, 146, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0.0, 38.25, 85.0, 0.0, 0.0, 0.0], "scale":[140.0, 141.5, 170.0]},
        ]},
        # cfg
        num_slots = 16,
        vaj=[300, 4000, 10000],
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Shaker.DEFAULTS) # default
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

