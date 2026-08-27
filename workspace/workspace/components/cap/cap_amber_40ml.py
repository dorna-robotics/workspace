from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.cap.cap import Cap


@register("cap_amber_40ml")
class CapFalcon40ml(Cap):
    DEFAULTS = dict(
        anchors={"body": {"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 14, 0, 0, 0]}},
        collision_box={
            "body": [
                {"pose": [0.0, 0.0, 7, 0.0, 0.0, 0.0], "scale": [26.0, 26.0, 14.0]}
            ]
        },
        cap_type="screw",
        twist=1200,
        pitch=3.33
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

        self.assembly["body"].collision_box = prm["collision_box"]["body"]