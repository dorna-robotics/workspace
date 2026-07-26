from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper


@register("gripper_needle")
class GripperNeedle(Gripper):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 146.27, 0, 0, 0],  "tip":[0, 0, 146.27, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 146.308/2, 0.0, 0.0, 0.0], "scale":[4.0, 4.0, 146.308]},
                {"pose":[0.0, 0.0, 35.50/2, 0.0, 0.0, 0.0], "scale":[43.0, 43.0, 35.50]},

        ]},
        #cfg
        has_tool_changer = False,
        output_enable=[[None, None, 0], [None, None, 0]],
        output_disable=[[None, None, 0], [None, None, 0]],
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
