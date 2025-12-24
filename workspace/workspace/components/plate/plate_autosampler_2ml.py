from workspace.components.factory import register
from workspace.components.plate.plate import Plate

@register("plate_autosampler_2ml")
class PlateAutosampler2ml(Plate):

    def __init__(self, name: str, cfg: dict, workspace,
                anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 5, 0, 0, 0], "top": [0, 0, 25.5, 0, 0, 0]}},
                offset=[0, 0],
                pitch=[14, 14],
                rvec_safe=[0, 0, 45],
                rows=[chr(c) for c in range(ord("A"), ord("F") + 1)],
                cols= [i for i in range(1, 9)],
                **kwargs
        ):
        
        type = getattr(self.__class__, "_registered_type", cfg.get("type"))

        super().__init__(
            name=name,
            workspace=workspace,
            type=type,
            anchors=anchors,
            offset=offset,
            pitch=pitch,
            rvec_safe=rvec_safe,
            rows=rows,
            cols=cols,
            **kwargs
        )
