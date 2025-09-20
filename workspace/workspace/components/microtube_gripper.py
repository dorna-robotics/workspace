# workspace/components/core.py
from dorna2 import Solid, Dorna
from workspace.components.factory import register


@register("microtube_gripper")
class microtube_gripper:
    """
    the microtube gripper
    """

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.type = "microtube_gripper"
        self.assembly = {}

        anchors = {}
        anchors["center"] = [0,0,0,0,0,0]
        anchors["gripping_point"] = [0,0,51.35,0,0,0]
        self.assembly["microtube_gripper"] = Solid(name="microtube_gripper", type="microtube_gripper", anchors=anchors, component=self.name)
        self.has_toolchanger = cfg.get("has_toolchanger", False)
        if self.has_toolchanger:

            anchors_tc = {}
            anchors_tc["tool_connection"] = [0,0,0,0,0,0]
            anchors_tc["toolchanger_connection"]=[0,0,-13,180,0,0]
            anchors_tc["tool_rack_connection"] = [0,0,1.5,0,0,0]
            self.assembly["toolchanger_tool_side"] = Solid(name="toolchanger_tool_side", type="toolchanger_tool_side", anchors=anchors_tc, component=self.name)
            self.assembly["microtube_gripper"].attach_to(parent=self.assembly["toolchanger_tool_side"], parent_anchor="tool_connection", child_anchor="center", offset=[0, 0, 0, 0, 0, 0])
