from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.hotel.hotel import Hotel


@register("sbs_hotel_4_level")
class SBSHotel4Level(Hotel):
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 8, 0, 0, 0], "place": [0, 0, 4.5, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[-89.44, 0.0, 118.89, 0.0, 0.0, 0.0], "scale":[25.9, 25.9, 241.39]},#[xyzabc] , [lx,ly,lz]
                {"pose":[0.0, 0.0, 4.20, 0.0, 0.0, 0.0], "scale":[155.6, 103.5, 9.25]},
                {"pose":[0.0, 0.0, 79.8, 0.0, 0.0, 0.0], "scale":[155.6, 103.5, 9.25]},
                {"pose":[0.0, 0.0, 156.6, 0.0, 0.0, 0.0], "scale":[155.6, 103.5, 9.25]},
                {"pose":[0.0, 0.0, 231.4, 0.0, 0.0, 0.0], "scale":[155.6, 103.5, 9.25]}
        ]},
        shape=[150, 100, 76],
        level=3,
    )

    def __init__(self, name: str, cfg: dict, workspace,**kwargs):
        # prm
        prm = deepcopy(Hotel.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs
        
        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        super().__init__(
            name=name,
            workspace=workspace,
            **prm
        )