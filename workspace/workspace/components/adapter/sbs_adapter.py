from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


@register("sbs_adapter")
class SBSAdapter(Adapter):

    def __init__(self, name: str, cfg: dict, workspace,
                anchors={"solid_0": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 4.5, 0, 0, 0], "top": [0, 0, 8, 0, 0, 0],
                        "front":[0, 0, 4.5, 0, 0, 180], "back":[0, 0, 4.5, 0, 0, 0]},},
                height=19,
                heaight_seat=3,
                **kwargs
        ):

        type = getattr(self.__class__, "_registered_type", cfg.get("type"))

        super().__init__(
            name=name,
            workspace=workspace,
            type=type,
            anchors=anchors,
            height=height,
            heaight_seat=heaight_seat,
            **kwargs
        )
