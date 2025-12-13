from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper


@register("suction_gripper")
class SuctionGripper(Gripper):

    def __init__(self, name: str, cfg: dict, workspace,
                anchors={"solid_0": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 126.51, 0, 0, 0]}},
                has_toolchanger = False,
                offset=[0, 0, 0, 0, 0, 0],
                enable=[],
                disable=[],
                **kwargs
        ):
        
        # type
        type = getattr(self.__class__, "_registered_type", cfg.get("type"))
        
        # tool changer
        has_toolchanger = cfg.get("has_toolchanger", has_toolchanger)

        # enable and disable
        enable = cfg.get("enable", enable)
        disable = cfg.get("disable", disable)

        super().__init__(
            name=name,
            workspace=workspace,
            type=type,
            anchors=anchors,
            has_toolchanger=has_toolchanger,
            offset=offset,
            enable=enable,
            disable=disable,
            **kwargs
        )
