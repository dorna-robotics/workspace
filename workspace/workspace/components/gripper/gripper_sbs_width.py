from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper

@register("gripper_sbs_width")
class gripperSBSWidth(Gripper):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[119, 0, 41, 0, 0, 0], "tip": [119, 0, 56, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 20.72, 0.0, 0.0, 0.0], "scale":[44.51, 73.53, 46.06]},#[xyzabc] , [lx,ly,lz]
                {"pose":[48.10, 49.69, 46.84, 0.0, 0.0, 0.0], "scale":[125.44, 37.44, 22.57]},
                {"pose":[48.10, -49.69, 46.84, 0.0, 0.0, 0.0], "scale":[125.44, 37.44, 22.57]}
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
