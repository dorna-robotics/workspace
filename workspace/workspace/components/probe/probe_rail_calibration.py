from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.tool_rack.tool_rack import ToolRack



@register("probe_rail_calibration")
class ProbeRailCalibration(ToolRack):
    DEFAULTS = dict(
        anchors = {"body": {"center": [0,0,0,0,0,0],
            "hole_0":[25, 25, 0, 0, 0, 0], "hole_1": [-25, 25, 0, 0, 0, 0], "hole_2": [-25, -25, 0, 0, 0, 0], "hole_3": [25, -25, 0, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0, 0, 150/2, 0.0, 0.0, 0.0], "scale":[65, 65, 150]}
        ]}
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(ToolRack.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))

        # init
        super().__init__(name=name, workspace=workspace, **prm)