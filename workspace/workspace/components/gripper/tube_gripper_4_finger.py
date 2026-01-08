from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper


@register("tube_gripper_4_finger")
class TubeGripper4Finger(Gripper):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 65, 0, 0, 0], "tip": [0, 0, 69.5, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 27.733, 0.0, 0.0, 0.0], "scale":[46.87, 46.87, 56.52]},#[xyzabc] , [lx,ly,lz]
                {"pose":[0.0, 0.0, 57.142, 0.0, 0.0, 0.0], "scale":[21.74, 21.74, 26.21]}
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