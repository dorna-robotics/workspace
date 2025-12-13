from workspace.components.factory import register
from workspace.components.feeder.feeder import Feeder


@register("capfeeder_autosampler_2ml")
class FeederCap2ml(Feeder):

    def __init__(self, name: str, cfg: dict, workspace,
                anchors={"solid_0": {"center": [0, 0, 0, 0, 0, 0], "pick":[0, -12.23473 , 152.56783, -45, 0, 0]}},
                height=50,
                **kwargs
        ):

        type = getattr(self.__class__, "_registered_type", cfg.get("type"))

        super().__init__(
            name=name,
            workspace=workspace,
            type=type,
            anchors=anchors,
            height=height,
            **kwargs
        )
