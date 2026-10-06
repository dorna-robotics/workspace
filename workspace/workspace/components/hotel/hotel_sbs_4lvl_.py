from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.hotel.hotel import Hotel


@register("hotel_sbs_4lvl")
class HotelSBS4lvl(Hotel):
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 8, 0, 0, 90], "place": [0, 90, 4.5, 0, 0, 90], # plate pocket: centred 90 mm out from the post, floor 76 mm above each level
                        "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0]}},
        collision_box = 
            {"body":[
                {"pose":[0, 66.25, 314/2, 0.0, 0.0, 0.0], "scale":  [100, 197.5, 314.0], "padding_enabled": True},#[xyzabc] , [lx,ly,lz]
        ]},
        pitch=[0, 0, 76],
        size  = [190, 100, 76],  # size[0]: how far the Hotel recipe slides a plate out from place: past the tray tip (75 mm out) by the plate and a 50 mm margin
        level=4,
        # the Hotel recipe lifts a plate just clear of THIS shelf's rim
        # (top_<level>) before sliding it out — see recipes/hotel.py
        lift_from_shelf=True,
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