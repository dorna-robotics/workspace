from workspace.components.factory import register
from workspace.components.inspection.inspection import Inspection


@register("horizontal_inspection_station")
class HorizontalInspectionStation(Inspection):

    def __init__(self, name: str, cfg: dict, workspace,
                anchors={"solid_0":{"center":[0, 0, 0, 0, 0, 0],
                                    "camera": [0, 0, 8, 0, 0, 0], 
                                    "place":[0, 0, 0, 0, 0, 0]}},
                serial_number="",
                stream= {"width":848, "height":480, "fps":15},
                K= None,
                D= None,
                mode="bgrd", 
                filter={}, 
                exposure=None,
                native_res=None,
                simulation=True,
                **kwargs
        ):

        # type
        type = getattr(self.__class__, "_registered_type", cfg.get("type"))

        # camera config
        serial_number = cfg.get("serial_number", serial_number)
        stream = cfg.get("stream", stream)
        K = cfg.get("K", K)
        D = cfg.get("D", D)
        mode = cfg.get("mode", mode)
        filter = cfg.get("filter", filter)
        exposure = cfg.get("exposure", exposure)
        native_res = cfg.get("native_res", native_res)

        # simulation
        simulation = cfg.get("simulation", simulation)
        
        super().__init__(
            name=name,
            workspace=workspace,
            type=type,
            anchors=anchors,
            serial_number=serial_number,
            stream= stream,
            K= K,
            D= D,
            mode=mode, 
            filter=filter, 
            exposure=exposure,
            native_res=native_res,
            simulation=simulation,
            **kwargs
        )
