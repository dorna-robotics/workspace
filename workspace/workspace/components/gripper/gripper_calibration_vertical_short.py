from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper


@register("gripper_calibration_vertical_short")
class GripperCalibrationVerticalShort(Gripper):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 6.5, 166.2983158520316, -68.88301782571617, 0], "tip": [0, 0, 12.5, 0, 0, -45]}},
        #anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 6.5+24, 0, 0, -45], "tip": [0, 0, 12.5, 0, 0, 0]}},
        #cfg
        has_tool_changer = True,
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