# workspace/components/core.py
from dorna2 import Solid, Dorna
from workspace.components.factory import register


@register("SBS_adaptor")
class SBS_adaptor:
    """
    the SBS adaptor
    """

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.type = "SBS_adaptor"
        self.assembly = {}

        anchors = {}
        anchors["center"] = [0,0,0,0,0,0]
        anchors["hole_0"] = [-75/2,50,0,0,0,0]
        anchors["hole_1"] = [75/2,50,0,0,0,0]
        anchors["hole_2"] = [75/2,-50,0,0,0,0]
        anchors["hole_3"] = [-75/2,-50,0,0,0,0]

        self.assembly["SBS_adaptor"] = Solid(name="SBS_adaptor", type="SBS_adaptor", anchors=anchors, component=self.name)