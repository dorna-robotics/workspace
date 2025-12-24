from workspace.components.factory import register
from workspace.components.cap.cap import Cap

@register("cap_autosampler_2ml")
class CapAutosampler2ml(Cap):

    def __init__(self, name: str, cfg: dict, workspace,
                anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 6, 0, 0, 0]}},
                cap_type="screw",
                twist=390,
                pitch=1,
                **kwargs
        ):
        
        type = getattr(self.__class__, "_registered_type", cfg.get("type"))
        super().__init__(
            name=name,
            cfg=cfg,
            workspace=workspace,
            type=type,
            anchors=anchors,
            cap_type=cap_type,
            twist=twist,
            pitch=pitch,
            **kwargs
        )