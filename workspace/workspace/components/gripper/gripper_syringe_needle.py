from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper


@register("gripper_syringe_needle")
class GripperSyringeNeedle(Gripper):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 157.751, 0, 0, 0],  "tip":[0, 0, 157.751, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, 31.50/2, 0.0, 0.0, 0.0], "scale":[43.0, 43.0, 31.50]},
                {"pose":[0.0, 0.0, 31.50+(14.0/2), 0.0, 0.0, 0.0], "scale":[40.0, 66.0, 14.0]},
                {"pose":[0.0, 0.0, 45.50+(98.251/2), 0.0, 0.0, 0.0], "scale":[7.0, 63.0, 98.251]},
                {"pose":[0.0, 0.0, 143.751+(14.0/2), 0.0, 0.0, 0.0], "scale":[66.0, 66.0, 14.0]},

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
