# workspace/components/core.py
from dorna2 import Solid, Dorna
from workspace.components.factory import register


@register("tool_rack")
class tool_rack:
    """
    the tool rack
    """

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.type = "tool_rack"
        self.assembly = {}

        anchors = {}
        anchors["center"] = [0,0,0,0,0,0]
        anchors["tool_connection"] = [0,46,153,180,0,0]

        self.assembly["tool_rack"] = Solid(name="tool_rack", type="tool_rack", anchors=anchors, component=self.name)