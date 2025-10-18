# workspace/components/SBS_adapter.py
from dorna2 import Solid, Dorna
from workspace.components.factory import register


@register("SBS_adapter")
class SBSAdapter:
    """
    the SBS adapter
    """

    def __init__(self, name: str, cfg: dict, workspace):
        self.name = name
        self.type = "SBS_adapter"
        self.workspace = workspace
        self.assembly = {}

        anchors = {}
        anchors["center"] = [0,0,0,0,0,0]
        anchors["hole_0"] = [-75/2,50,0,0,0,0]
        anchors["hole_1"] = [75/2,50,0,0,0,0]
        anchors["hole_2"] = [75/2,-50,0,0,0,0]
        anchors["hole_3"] = [-75/2,-50,0,0,0,0]

        self.assembly["SBS_adapter"] = Solid(name="SBS_adapter", type="SBS_adapter", anchors=anchors, component=self.name)