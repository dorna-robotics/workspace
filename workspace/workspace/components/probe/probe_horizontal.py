from copy import deepcopy

from mergedeep import merge

from workspace.components.factory import register
from workspace.components.probe.probe import Probe


@register("probe_horizontal")
class ProbeHorizontal(Probe):
    # tcp abc = 120 deg about [1,1,1] (tool +X -> pocket +Z): the sideways rod
    # meets a pocket that opens horizontally. Orientation lives in the anchor.
    # The rod runs along -X to its tip 72 mm out (the rod on the bench; the mesh
    # still draws the earlier 70.7 mm one). One anchor, tcp_0 — named like
    # probe_vertical's so calibrate(tool_anchor="tcp_0") reads the same — and no
    # tcp_<deg> family: theirs spin about the probing axis, which for a vertical
    # rod is the flange axis (a wrist angle). Here the probing axis is the level
    # rod, so a spin would tilt the flange off vertical (tcp_180 hangs the robot
    # upside down), and a wrist turn would swing the rod off the pocket. tcp_0
    # hangs the tool flange-down, as the gripper is when it slides into a hotel.

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

        # "tcp" becomes tcp_0. Shallow-copy the body dict first so we never
        # mutate the class DEFAULTS.
        body = dict(prm["anchors"]["body"])
        prm["anchors"]["body"] = body
        body["tcp_0"] = body.pop("tcp")

        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        super().__init__(
            name=name,
            workspace=workspace,
            **prm
        )
