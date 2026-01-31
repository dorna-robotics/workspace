from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.hotel.hotel import Hotel


@register("hotel_sbs_52h_4lvl")
class HotelSBS52h4lvl(Hotel):
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 8, 0, 0, 90], "place": [0, 0, 4.5, 0, 0, 90],
                "hole_0":[37.5, 37.5, 0, 0, 0, 0], "hole_1": [-37.5, 37.5, 0, 0, 0, 0], "hole_2": [-37.5, -37.5, 0, 0, 0, 0], "hole_3": [37.5, -37.5, 0, 0, 0, 9]}},
        collision_box =
            {"body":[
                {"pose":[-89.44, 0.0, 118.89, 0.0, 0.0, 0.0], "scale":[25.9, 25.9, 241.39]},#[xyzabc] , [lx,ly,lz]
                {"pose":[0.0, 0.0, 4.20, 0.0, 0.0, 0.0], "scale":[155.6, 103.5, 9.25]},
                {"pose":[0.0, 0.0, 79.8, 0.0, 0.0, 0.0], "scale":[155.6, 103.5, 9.25]},
                {"pose":[0.0, 0.0, 156.6, 0.0, 0.0, 0.0], "scale":[155.6, 103.5, 9.25]},
                {"pose":[0.0, 0.0, 231.4, 0.0, 0.0, 0.0], "scale":[155.6, 103.5, 9.25]}
        ]},
        size=[150, 100, 52],
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