from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.probe.probe import Probe


@register("probe_vertical_70")
class ProbeVertical70(Probe):
    # Straight-down 70 mm calibration rod. tcp/tip sit at the rod end
    # (z = 99.68 mm); Rx(180) tcp orientation so the rod meets the pocket
    # straight down — the length lives in the tcp position. Mesh:
    # static/CAD/probe_vertical_70.glb.
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp": [-15, 0, 99.68, 180, 0, 0], "tip": [-15, 0, 99.68, 180, 0, 0]}},
        collision_box=
            {"body": [
                {"pose": [0, 0.0, ((99.68)/2), 0.0, 0.0, 0.0], "scale": [43, 43.0, 99.68]},  # [xyzabc] , [lx,ly,lz]
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
