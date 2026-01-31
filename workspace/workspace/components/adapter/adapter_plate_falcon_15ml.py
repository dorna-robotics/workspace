from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


@register("adapter_plate_falcon_15ml")
class AdapterPlateFalcon15ml(Adapter):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 10, 0, 0, 0],
                "hole_0": [75, 50, 0, 0, 0, 0], "hole_1": [-75, 50, 0, 0, 0, 0], "hole_2": [-75, -50, 0, 0, 0, 0], "hole_3": [75, -50, 0, 0, 0, 0],
                "clb_0": [56, 42, 78, 0, 0, -45], "clb_1": [-56, 42, 78, 0, 0, -135], "clb_2": [-56, -42, 78, 0, 0, -45], "clb_3": [56, -42, 78, 0, 0, -135]}},
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
