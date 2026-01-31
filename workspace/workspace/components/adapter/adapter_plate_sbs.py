from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


@register("adapter_plate_sbs")
class AdapterPlateSBS(Adapter):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 4.5, 0, 0, 0], "top": [0, 0, 8, 0, 0, 0],
                        "hole_0":[37.5, 37.5, 0, 0, 0, 0], "hole_1": [-37.5, 37.5, 0, 0, 0, 0], "hole_2": [-37.5, -37.5, 0, 0, 0, 0], "hole_3": [37.5, -37.5, 0, 0, 0, 0],
                        "clb_0": [60, 35.5, 8, 0, 0, -45], "clb_1": [-60, 35.5, 8, 0, 0, -135], "clb_2": [-60, -35.5, 8, 0, 0, -45], "clb_3": [60, -35.5, 8, 0, 0, -135]}},
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
