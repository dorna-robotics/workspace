from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.hotel.hotel import Hotel


@register("hotel_sbs_100h_3lvl")
class HotelSBS100h3lvl(Hotel):
    # Three 9 mm slabs 102 mm apart (z 0, 102, 204), each with the plate pocket
    # 4 mm deep (floor at +5, rim at +9, x ±43.5 / y ±64.6), the base the first
    # shelf, no roof. Four 7 mm posts: one each side at y −9.9, two at the back
    # (y −67.5) — plates slide in and out along +y. Mounted by four M5 flat-head
    # screws through the base at (±12.5, ±37.5).
    DEFAULTS = dict(
        anchors={"body":{"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 9, 0, 0, 90], "place": [0, 0, 5, 0, 0, 90], "clb": [0, 74.9, 4.5, -90, 0, 0],
                "hole_0":[12.5, 37.5, 0, 0, 0, 0], "hole_1": [-12.5, 37.5, 0, 0, 0, 0], "hole_2": [-12.5, -37.5, 0, 0, 0, 0], "hole_3": [12.5, -37.5, 0, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0, 0, 213/2, 0.0, 0.0, 0.0], "scale":  [100, 150, 213], "padding_enabled": True},#[xyzabc] , [lx,ly,lz]
        ]},
        pitch=[0, 0, 102],
        size  = [150, 100, 102],
        level=3,
        # the Hotel recipe lifts a plate just clear of THIS shelf's rim
        # (top_<level>) before sliding it out — see recipes/hotel.py
        lift_from_shelf=True,
        # three plates: on the base (the first shelf) and the two shelves above
        first_level=0,
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
