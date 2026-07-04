from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.probe.probe import Probe


@register("probe_vertical_60")
class ProbeVertical60(Probe):
    # Straight-down calibration rod. Anchors / collision box are carried over
    # from probe_vertical_100 for now — update them to the 60 mm rod's real
    # values. Same Rx(180) tcp orientation; the length lives in the tcp position.
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp": [-15, 0, 128.5-40, 180, 0, 0], "tip": [-15, 0, 128.5-40, 180, 0, 0]}},
        collision_box=
            {"body": [
                {"pose": [0, 0.0, ((128.5-40)/2), 0.0, 0.0, 0.0], "scale": [43, 43.0, 128.5-40]},
            ]},
        has_tool_changer=True,
        output_enable=[[None, None, 0]],
        output_disable=[[None, None, 0]],
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Probe.DEFAULTS)  # default
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
