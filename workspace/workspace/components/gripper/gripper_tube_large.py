from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper


@register("gripper_tube_large")
class GripperTubeLarge(Gripper):
    DEFAULTS = dict(
        #anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 48, 0, 0, -90], "tip": [0, 0, 59.5, 0, 0, -90]}},
        # anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 48.5, 0, 0, -90], "tip": [0, 0, 59.5, 0, 0, -90]}},
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 49.5, 0, 0, -90], "tip": [0, 0, 59.5, 0, 0, -90]}},
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