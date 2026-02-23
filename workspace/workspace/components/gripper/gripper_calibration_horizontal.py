from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper


@register("gripper_calibration_horizontal")
class GripperCalibrationHorizontal(Gripper):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[60, 0, 10.5, 69.28203230275508, -69.28203230275508, -69.2820323027551], "tip": [66, 0, 10.5, 69.28203230275508, -69.28203230275508, -69.2820323027551]}},
        collision_box = 
            {"body":[
                {"pose":[23, 0.0, 7.25, 0.0, 0.0, 0.0], "scale":[86.5, 43.0, 14.5]},#[xyzabc] , [lx,ly,lz]
        ]},
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