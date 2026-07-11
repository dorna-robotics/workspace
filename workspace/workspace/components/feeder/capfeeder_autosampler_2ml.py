from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.feeder.feeder import Feeder


@register("capfeeder_autosampler_2ml")
class FeederCap2ml(Feeder):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "place":[0, -32.237, 188.277, -48, 0, 0], "top": [0, 102.114, 162.114, -48, 0, 0], "plate_center": [0, 48.02, 108.02, -45, 0, 0],
                "hole_0":[50, 12.5, 0, 0, 0, 0], "hole_1": [-50, 12.5, 0, 0, 0, 0], "hole_2": [-50, -12.5, 0, 0, 0, 0], "hole_3": [50, -12.5, 0, 0, 0, 0],
                "clb_0": [0, 7.008, 149.032, -45, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0.0, -11.5, 55, 0.0, 0.0, 0.0], "scale":[115, 131.5, 110.0], "padding_enabled": True},#[xyzabc] , [lx,ly,lz]
                {"pose":[0.0, 70.294, 130.294, -45.0, 0.0, 0.0], "scale":[247.0, 247.0, 90.0], "padding_enabled": True}
        ]},
        # cfg
        num_slots = 16,
        vaj=[300, 4000, 10000],
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Feeder.DEFAULTS) # default
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

