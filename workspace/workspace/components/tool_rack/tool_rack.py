from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
from workspace.components.factory import register


@register("tool_rack")
class ToolRack:
    DEFAULTS = dict(
        anchors = {"body": {"center": [0,0,0,0,0,0], "place": [0, 46, 153, 127.27922061357856, -127.27922061357854, 0],
            "hole_0":[25, 25, 0, 0, 0, 0], "holde_1": [-25, 25, 0, 0, 0, 0], "holse_2": [-25, -25, 0, 0, 0, 0], "holde_3": [25, -25, 0, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 3.0, 0.0, 0.0, 0.0], "scale":[66.0, 66.0, 6.0]},#[xyzabc] , [lx,ly,lz]
                {"pose":[0.0, 0.0, 78.0, 0.0, 0.0, 0.0], "scale":[27.0, 27.0, 152.5]},
                {"pose":[0.0, 23.5, 145.0, 0.0, 0.0, 0.0],"scale":[54.0, 75.0, 19.5]}
        
        ]}
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))

        # init
        self.name = name
        self.workspace = workspace
        self.type = prm["type"]

        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        } 