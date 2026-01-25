from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper


@register("suction_gripper")
class SuctionGripper(Gripper):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 126.51, 0, 0, 0],  "tip":[0, 0, 126.51, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 3.5, 0.0, 0.0, 0.0], "scale":[45.0, 45.0, 9.0]},#[xyzabc] , [lx,ly,lz]
                {"pose":[0.0, 0.0, 26.50, 0.0, 0.0, 0.0], "scale":[12.5, 44.5, 37.0]},
                {"pose":[0.0, 0.0, 76.5, 0.0, 0.0, 0.0], "scale":[12.5, 12.5, 69.5]},
                {"pose":[0.0, 0.0, 119.5, 0.0, 0.0, 0.0], "scale":[22.5, 22.5, 19.5]}
        ]},
        #cfg
        has_tool_changer = False,
        output_enable=[[None, None, 0]],
        output_disable=[[None, None, 0]],
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Gripper.DEFAULTS) # default
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
