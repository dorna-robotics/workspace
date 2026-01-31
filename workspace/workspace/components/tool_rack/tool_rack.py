from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid


class ToolRack:
    DEFAULTS = dict(
        anchors = {"body": {"center": [0,0,0,0,0,0], "place": [0, 46, 153, 127.27922061357856, -127.27922061357854, 0],
            "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0],
            "clb_0": [0, 46, 148, 0, 0, -45]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 3.0, 0.0, 0.0, 0.0], "scale":[66.0, 66.0, 6.0]},#[xyzabc] , [lx,ly,lz]
                {"pose":[0.0, 0.0, 78.0, 0.0, 0.0, 0.0], "scale":[27.0, 27.0, 152.5]},
                {"pose":[0.0, 23.5, 145.0, 0.0, 0.0, 0.0],"scale":[54.0, 75.0, 19.5]}
        
        ]}
    )

    def __init__(self, name: str, workspace, type=None, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, kwargs) # kwargs

        # init
        self.name = name
        self.workspace = workspace
        self.type = type

        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        } 