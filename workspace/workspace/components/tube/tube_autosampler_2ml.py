from workspace.components.factory import register
from workspace.workspace.components.tube.tube import Tube

@register("tube_autosampler_2ml")
class TubeAutosampler2ml(Tube):

    def __init__(self, name: str, cfg: dict, workspace,
                anchors={
                    "body": {"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 32, 0, 0, 0], "place":[0, 0, 28, 0, 0, 0]},
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