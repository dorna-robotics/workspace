from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from dorna2 import Solid


@register("decapper")
class Decapper:
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "place":[0, 0, 45, 0, 0, 0], "top":[0, 0, 55, 0, 0, 0],
            "hole_0":[25, 25, 0, 0, 0, 0], "holde_1": [-25, 25, 0, 0, 0, 0], "holse_2": [-25, -25, 0, 0, 0, 0], "holde_3": [25, -25, 0, 0, 0, 0]},},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 23.85, 0.0, 0.0, 0.0], "scale":[76.1, 70.7, 63.21]}   #[xyzabc] , [lx,ly,lz]
        ]},
        # cfg
        output_enable = [[None, None, 0.1]], # [[pin, index, time]]
        output_disable = [[None, None, 0.1]], # [[pin, index, time]]
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        # init
        self.name = name
        self.workspace = workspace
        self.type = prm["type"]
        
        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name) for k in prm["anchors"]
        }

        # open and close
        self.enable = prm["output_enable"]
        self.disable = prm["output_disable"]
