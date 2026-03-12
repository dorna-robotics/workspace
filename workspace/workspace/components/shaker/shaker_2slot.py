from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.shaker.shaker import Shaker
from dorna2 import Solid


@register("shaker_2slot")
class Shaker2slot(Shaker):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "output":[0, 51, 100, 0, 0, 0], "place": [0, 114, 100, 0, 0, 0],
                "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0],
                "clb_0": [0, 0, 146, 0, 0, 0]},
                "rotating": {"center": [0, 0, 0, 0, 0, 0], "input":[0, 0, 0, 0, 0, 0], 
                    "A1": [35, 63, -45, 0, 0, 0], "A2": [-35, 63, -45, 0, 0, 0], "top": [0, 36.972 , 47.972+20, 0, 0, 0], "place": [0, 0, -45, 0, 0, 0],}},
        collision_box =
            {"body":[
                {"pose":[0.0, 38.25, 85.0, 0.0, 0.0, 0.0], "scale":[140.0, 141.5, 170.0]},
        ]},
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Shaker.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))
        
        # super
        super().__init__(
            name=name,
            workspace=workspace,
            **prm
        )

        # assembly
        self.assembly = {
            k: Solid(type=f"shaker_{k}", anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        }

        #self.update_pose()






