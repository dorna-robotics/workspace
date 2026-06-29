from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.probe.probe import ProbeBase


@register("probe_horizontal")
class ProbeHorizontal(ProbeBase):
    # tcp abc = 120 deg about [1,1,1] (tool +X -> pocket +Z): the sideways rod
    # meets a pocket that opens horizontally. Orientation lives in the anchor.
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp": [-70.732, 0, 3, 69.28203230275508, 69.28203230275508, 69.28203230275508], "tip": [-70.732, 0, 3, 69.28203230275508, 69.28203230275508, 69.28203230275508]}},
        collision_box=
            {"body": [
                {"pose": [-25.866, 0.0, ((6)/2), 0.0, 0.0, 0.0], "scale": [88.732, 38, (6)]},
            ]},
        has_tool_changer=True,
        output_enable=[[None, None, 0]],
        output_disable=[[None, None, 0]],
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(ProbeBase.DEFAULTS)  # default
        merge(prm, self.DEFAULTS)  # self
        merge(prm, cfg)  # cfg
        merge(prm, kwargs)  # kwargs

        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        super().__init__(
            name=name,
            workspace=workspace,
            **prm
        )
