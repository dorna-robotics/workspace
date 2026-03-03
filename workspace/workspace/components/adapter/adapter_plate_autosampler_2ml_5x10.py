from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


@register("adapter_plate_autosampler_2ml_5x10")
class AdapterPlateAutosampler2ml5x10(Adapter):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0],
                        "hole_0":[75, 25, 0, 0, 0, 0], "hole_1": [-75, 25, 0, 0, 0, 0], "hole_2": [-75, -25, 0, 0, 0, 0], "hole_3": [75, -25, 0, 0, 0, 0],
                        "clb_0": [67.13, 19.18, 21, 0, 0, -45], "clb_1": [-67.13, 19.18, 21, 0, 0, -135], "clb_2": [-67.13, -19.18, 21, 0, 0, -45], "clb_3": [67.13, -19.18, 21, 0, 0, -135]}},
        collision_box= 
            {"body":[
                {"pose":[0.0, 0.0, 4.0, 0.0, 0.0, 0.0], "scale":[150.0, 100.0, 8.0]}#[xyzabc] , [lx,ly,lz]
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
