from copy import deepcopy

import numpy as np
from mergedeep import merge

import dorna2.pose as dorna_pose
from workspace.components.factory import register
from workspace.components.probe.probe import Probe


@register("probe_vertical")
class ProbeVertical(Probe):
    # tcp abc = Rx(180): the rod meets the pocket straight down (tool +Z into
    # pocket -Z). That rod-tip position + straight-down orientation live once in
    # the DEFAULTS "tcp" below, which is only the BASE for the tcp_<deg> yaw
    # family: __init__ builds tcp_0..tcp_180 from it (each = the base spun <deg>
    # about Z) and then drops the plain "tcp". tcp_0 (same orientation) is the
    # canonical anchor and the default calibration anchor; pick a yaw at
    # calibration time with recipe.calibrate(tool_anchor="tcp_90").
    TCP_YAWS = [0, 45, 90, 135, 180, -45, -90, -135]

    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp": [-15, 0, 28.5, 180, 0, 0], "tip": [-15, 0, 28.5, 180, 0, 0]}},
        collision_box=
            {"body": [
                {"pose": [0, 0.0, (28.5/2), 0.0, 0.0, 0.0], "scale": [43, 43, 28.5], "padding_enabled": True},  # [xyzabc] , [lx,ly,lz]
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

        # Build the tcp_<deg> yaw family from the base "tcp", then drop "tcp":
        # tcp_0 (same orientation) is the canonical anchor that replaces it.
        # Shallow-copy the body dict first so we never mutate the class DEFAULTS.
        body = dict(prm["anchors"]["body"])
        prm["anchors"]["body"] = body
        tcp_xyz, tcp_abc = list(body["tcp"][:3]), list(body["tcp"][3:6])
        base_R = np.array(dorna_pose.abc_to_rmat(tcp_abc))
        for deg in self.TCP_YAWS:
            R = base_R @ np.array(dorna_pose.abc_to_rmat([0, 0, deg]))
            body[f"tcp_{deg}"] = tcp_xyz + [round(float(a), 6) for a in dorna_pose.rmat_to_abc(R)]
        del body["tcp"]

        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        super().__init__(
            name=name,
            workspace=workspace,
            **prm
        )
