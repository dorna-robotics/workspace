from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


@register("adapter_plate_amber_40ml_4x7")
class AdapterPlateAmber40ml4x7(Adapter):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0],
                "hole_0": [100, 50, 0, 0, 0, 0], "hole_1": [-100, 50, 0, 0, 0, 0], "hole_2": [-100, -50, 0, 0, 0, 0], "hole_3": [100, -50, 0, 0, 0, 0],
                "clb_0": [120, 40, 30, 0, 0, 0], "clb_1": [-120, 40, 30, 0, 0, 0], "clb_2": [-120, -40, 30, 0, 0, 0], "clb_3": [120, -40, 30, 0, 0, 0]}},
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
