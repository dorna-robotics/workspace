from workspace.components.factory import register
from workspace.components.body.body import Body

@register("body_autosampler_2ml")
class BodyAutosampler2ml(Body):

    def __init__(self, name: str, cfg: dict, workspace,
                anchors={
                    "solid_0": {"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 32, 0, 0, 0], "place":[0, 0, 28, 0, 0, 0]},
                },
                **kwargs
        ):
        
        type = getattr(self.__class__, "_registered_type", cfg.get("type"))
        super().__init__(
            name=name,
            workspace=workspace,
            type=type,
            anchors=anchors,
            **kwargs
        )