from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.adapter.adapter import Adapter


@register("adapter_disc_holder")
class AdapterDiscHolder(Adapter):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "place": [0, 0, 0, 0, 0, 0], "top": [0, 0, 6, 0, 0, 0],
                        "hole_0":[-112.5, -12.5, 0, 0, 0, 0], "hole_1": [112.5, 12.5, 0, 0, 0, 0],
                        "clb_0": [-87.5, 0, 0, 0, 0, 0], "clb_1": [87.5, 0, 0, 0, 0, 0]}},
        collision_box=
            {"body":[
                {"pose":[-112.5, -12.5, 3.0, 0.0, 0.0, 0.0], "scale":[5.0, 5.0, 6.0]},#[xyzabc] , [lx,ly,lz]
                {"pose":[112.5, 12.5, 3.0, 0.0, 0.0, 0.0], "scale":[5.0, 5.0, 6.0]}
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
