from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper

@register("sbs_gripper_width")
class SBS_gripper_width(Gripper):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[119, 0, 41, 0, 0, 0], "tip": [119, 0, 56, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 21.0, 0.0, 0.0, 0.0], "scale":[44.5, 73.5, 46.0]},#[xyzabc] , [lx,ly,lz]
                {"pose":[48.0, 50, 47.0, 0.0, 0.0, 0.0], "scale":[125.5, 37.5, 22.5]},
                {"pose":[48.0, -50, 47.0, 0.0, 0.0, 0.0], "scale":[125.5, 37.5, 22.5]}
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
