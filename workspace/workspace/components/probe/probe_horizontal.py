from copy import deepcopy

import numpy as np
from mergedeep import merge

import dorna2.pose as dorna_pose
from workspace.components.factory import register
from workspace.components.probe.probe import Probe


@register("probe_horizontal")
class ProbeHorizontal(Probe):
    # tcp abc = 120 deg about [1,1,1] (tool +X -> pocket +Z): the sideways rod
    # meets a pocket that opens horizontally. Orientation lives in the anchor.
    # The rod runs along -X to its tip 72 mm out (the rod on the bench; the mesh
    # still draws the earlier 70.7 mm one). Like probe_vertical / probe_rod, the
    # DEFAULTS "tcp" is only the BASE for the tcp_<deg> family: __init__ builds
    # tcp_0..tcp_180 from it (each = the base spun <deg> about its Z, the rod's
    # own axis) and then drops the plain "tcp". tcp_0 (same orientation) is the
    # canonical anchor; pick a spin at calibration time with
    # recipe.calibrate(tool_anchor="tcp_90").
    TCP_YAWS = [0, 45, 90, 135, 180, -45, -90, -135]

    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp": [-72, 0, 3, 69.28203230275508, 69.28203230275508, 69.28203230275508], "tip": [-72, 0, 3, 69.28203230275508, 69.28203230275508, 69.28203230275508]}},
        collision_box=
            {"body": [
                {"pose": [-26.5, 0.0, ((6)/2), 0.0, 0.0, 0.0], "scale": [90, 38, (6)]},
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

        # Build the tcp_<deg> family from the base "tcp", then drop "tcp":
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
