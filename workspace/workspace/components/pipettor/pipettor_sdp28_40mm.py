from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.pipettor.pipettor import Pipettor


@register("pipettor_sdp28_40mm")
class PipettorSDP2840mm(Pipettor):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, -104.2, 26.5, 90, 0, 0], "top": [0, -110.2, 26.5, 90, 0, 0]}},
        #cfg
        has_tool_changer = True,
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Pipettor.DEFAULTS) # default
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