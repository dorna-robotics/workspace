from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.shaker.shaker import Shaker
from dorna2 import Solid


@register("shaker_2slot")
class Shaker2slot(Shaker):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "output":[0, 53, 160, 0, 0, 0], 
                "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0],
                "clb_0": [0, 0, 206, 0, 0, 0]},
                "rotating": {"center": [0, 0, 0, 0, 0, 0], "input":[0, 0, 0, 0, 0, 0], 
                    "A1": [38.5, 29.7, 20, 0, 0, 0], "A2": [-38.5, 29.7, 20, 0, 0, 0], "top": [0, 29.7 , 46, 0, 0, 0], "place": [0, 0, 20, 0, 0, 0],}},
        collision_box =
            {"body":[
                {"pose":[0.0, 39.75, (245.20/2), 0.0, 0.0, 0.0], "scale":[(85.2*2), 144.5, 245.20], "padding_enabled": True},#[xyzabc] , [lx,ly,lz]
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

        # slot
        self.slot = {
            "rotating": ["A1", "A2"],
        }






