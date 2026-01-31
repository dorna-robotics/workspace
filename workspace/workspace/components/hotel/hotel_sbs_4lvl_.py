from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.hotel.hotel import Hotel


@register("hotel_sbs_4lvl")
class HotelSBS4lvl(Hotel):
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 8, 0, 0, 90], "place": [0, 0, 4.5, 0, 0, 90],
                        "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0.0, 0.0, 3.0, 0.0, 0.0, 0.0], "scale":[65.0, 65.0, 6.0]},#[xyzabc] , [lx,ly,lz]
                {"pose":[0.0, 0.0, 159.0, 0.0, 0.0, 0.0], "scale":  [30.0, 30.0, 306.0]},
                {"pose":[0.0, 90.0, 80.0, 0.0, 0.0, 0.0], "scale": [101.0, 151.0, 8.0]},
                {"pose":[0.0, 90.0, 156.0, 0.0, 0.0, 0.0], "scale": [101.0, 151.0, 8.0]},
                {"pose":[0.0, 90.0, 232.0, 0.0, 0.0, 0.0], "scale": [101.0, 151.0, 8.0]},
                {"pose":[0.0, 90.0, 308.0, 0.0, 0.0, 0.0], "scale": [101.0, 151.0, 8.0]}
        ]},
        size=[150, 100, 76],
        level=4,
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