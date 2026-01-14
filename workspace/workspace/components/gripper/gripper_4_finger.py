from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper


@register("gripper_4_finger")
class Gripper4Finger(Gripper):
    DEFAULTS = dict(
        #anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0+0.7-0.3, 0.3+0.7, 66.5, 0, 0, -45], "tip": [0, 0, 69.5, 0, 0, -45]}},
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 66.5, 0, 0, -45], "tip": [0, 0, 69.5, 0, 0, -45]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 27.733, 0.0, 0.0, 0.0], "scale":[46.87, 46.87, 56.52]},#[xyzabc] , [lx,ly,lz]
                {"pose":[0.0, 0.0, 57.142, 0.0, 0.0, 0.0], "scale":[21.74, 21.74, 26.21]}
        ]},
        #cfg
        has_tool_changer = False,
        output_enable=[[0, 0, 0], [1, 0, 0.1]],
        output_disable=[[1, 1, 0.5], [0, 1, 0.75], [0, 0, 0.1]],
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