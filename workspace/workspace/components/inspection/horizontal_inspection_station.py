from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.inspection.inspection import Inspection


@register("horizontal_inspection_station")
class HorizontalInspectionStation(Inspection):
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "camera": [0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0]}},
        collision_box = {"body":[
                {"pose":[0,0,4,0,0,0], "scale":[64,64,6.5]},#[xyzabc] , [lx,ly,lz]
                {"pose":[3,0,80,0,0,0], "scale": [28,25.5,152]},
                {"pose":[23.5,0,170.5,0,0,0], "scale": [31,46,47]}
        ]},
        camera = {
            "stream": {"width":848, "height":480, "fps":15},
            "K": None,
            "D": None,
            "mode": "bgrd", 
            "filter": {}, 
            "exposure": None,
            "native_res": None,
        },
        # cfg
        camera_serial_number="",
        simulation=True,
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Inspection.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs
        
        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))
        
        # init
        super().__init__(name=name, workspace=workspace, **prm)