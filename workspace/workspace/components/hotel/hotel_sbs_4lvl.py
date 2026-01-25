from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.hotel.hotel import Hotel


@register("hotel_sbs_4lvl")
class HotelSBS4lvl(Hotel):
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "top": [0, 90, 8, 0, 0, 90], "place": [0, 90, 4.5, 0, 0, 90],
                        "hole_0":[25, 25, 0, 0, 0, 0], "holde_1": [-25, 25, 0, 0, 0, 0], "holse_2": [-25, -25, 0, 0, 0, 0], "holde_3": [25, -25, 0, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[-89.5, 0.0, 119.0, 0.0, 0.0, 0.0], "scale":[26.0, 26.0, 241.5]},#[xyzabc] , [lx,ly,lz]
                {"pose":[0.0, 0.0, 4.0, 0.0, 0.0, 0.0], "scale":  [155.5, 103.5, 9.5]},
                {"pose":[0.0, 0.0, 80.0, 0.0, 0.0, 0.0], "scale": [155.5, 103.5, 9.5]},
                {"pose":[0.0, 0.0, 156.5, 0.0, 0.0, 0.0], "scale":[155.5, 103.5, 9.5]},
                {"pose":[0.0, 0.0, 231.5, 0.0, 0.0, 0.0], "scale":[155.5, 103.5, 9.5]}
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