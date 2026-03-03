from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


@register("adapter_plate_amber_40ml_2x4")
class AdapterPlateAmber40ml2x4(Adapter):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0],
                "hole_0": [50, 0, 0, 0, 0, 0], "hole_1": [-50, 0, 0, 0, 0, 0],
                "clb_0": [60, 0, 30, 0, 0, 0], "clb_1": [-60, 0, 30, 0, 0, 0]}},
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
