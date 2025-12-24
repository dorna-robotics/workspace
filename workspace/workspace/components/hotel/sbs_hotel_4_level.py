from workspace.components.factory import register
from workspace.components.hotel.hotel import Hotel


@register("sbs_hotel_4_level")
class SBSHotel4Level(Hotel):

    def __init__(self, name: str, cfg: dict, workspace,
                anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 8, 0, 0, 0], "place": [0, 0, 4.5, 0, 0, 0]}},
                level=4,
                shape=[150, 100, 76],
                **kwargs
        ):

        type = getattr(self.__class__, "_registered_type", cfg.get("type"))

        super().__init__(
            name=name,
            workspace=workspace,
            type=type,
            anchors=anchors,
            level=level,
            shape=shape,
            **kwargs
        )
