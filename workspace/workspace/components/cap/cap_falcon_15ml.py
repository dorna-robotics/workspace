from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.cap.cap import Cap


@register("cap_falcon_15ml")
class CapFalcon15ml(Cap):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 10, 0, 0, 0]}},
        cap_type="screw",
        twist=1000,
        pitch=2,
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Cap.DEFAULTS) # default
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