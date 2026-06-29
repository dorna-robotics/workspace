from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.probe.probe import ProbeBase


@register("probe_vertical")
class ProbeVertical(ProbeBase):
    # tcp abc = Rx(180): the rod meets the pocket straight down (tool +Z into
    # pocket -Z). Orientation lives in the anchor, so calibration solves IK
    # straight to tcp with no extra rotation.
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp": [-15, 0, 28.5, 180, 0, 0], "tip": [-15, 0, 28.5, 180, 0, 0]}},
        collision_box=
            {"body": [
                {"pose": [0, 0.0, (28.5/2), 0.0, 0.0, 0.0], "scale": [43, 43, 28.5]},
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
