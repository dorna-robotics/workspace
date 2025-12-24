from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


@register("sbs_adapter")
class SBSAdapter(Adapter):

    def __init__(self, name: str, cfg: dict, workspace,
                anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 4.5, 0, 0, 0], "top": [0, 0, 8, 0, 0, 0]}},
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
