from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


@register("adapter_plate_autosampler_2ml_7x8")
class AdapterPlateAutosampler2ml7x8(Adapter):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 6, 0, 0, 0],
                "hole_0": [50, 25, 0, 0, 0, 0], "hole_1": [-50, 25, 0, 0, 0, 0], "hole_2": [-50, -25, 0, 0, 0, 0], "hole_3": [50, -25, 0, 0, 0, 0],
                "clb_0": [50-12.5, 25, 0, 0, 0, 0], "clb_1": [-50+12.5, 25, 0, 0, 0, 0], "clb_2": [-50+12.5, -25, 0, 0, 0, 0], "clb_3": [50-12.5, -25, 0, 0, 0, 0]}},
        collision_box=
            {"body":[
                {"pose":[0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "scale":[105.0, 55.0, 12.0]}#[xyzabc] , [lx,ly,lz]
        ]}
    )

    def __init__(self, name: str, cfg: dict, workspace,**kwargs):
        # prm
        prm = deepcopy(Adapter.DEFAULTS) # default
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
