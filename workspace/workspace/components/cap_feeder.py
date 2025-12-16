# workspace/components/cap_feeder.py
from dorna2 import Solid, Dorna
from workspace.components.factory import register


@register("cap_feeder")
class CapFeeder:
    """
    cap feeder
    """

    def __init__(self, name: str, cfg: dict, workspace):
        self.name = name
        self.type = "cap_feeder"
        self.workspace = workspace
        self.assembly = {}

        anchors = {}
        anchors["center"] = [0,0,0,0,0,0]
        anchors["pick"]= [0, -12.23473,152.56783,-45,0,0]



        self.assembly["cap_feeder"] = Solid(name="cap_feeder", type="cap_feeder", anchors=anchors, component=self.name)