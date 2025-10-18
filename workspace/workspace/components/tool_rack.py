# workspace/components/tool_rack.py
from dorna2 import Solid, Dorna
from dorna2 import pose
from workspace.components.factory import register


@register("tool_rack")
class ToolRack:
    """
    the tool rack
    """

    def __init__(self, name: str, cfg: dict, workspace):
        self.name = name
        self.type = "tool_rack"
        self.workspace = workspace
        self.assembly = {}

        anchors = {}
        anchors["center"] = [0,0,0,0,0,0]

        initial_tool_connection = [180,0,0]
        rotated_tool_connection = pose.rotate_abc(initial_tool_connection, axis=[0,0,1], angle=90, local=True)


        anchors["tool_connection"] = [0,46,153,rotated_tool_connection[0],rotated_tool_connection[1],rotated_tool_connection[2]]

        self.assembly["tool_rack"] = Solid(name="tool_rack", type="tool_rack", anchors=anchors, component=self.name)