from dorna2 import Solid

class Gripper:
    def __init__(self, name: str, workspace,
            type=None,
            anchors = {"solid_0": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 0, 0, 0, 0]}},
            has_toolchanger = False,
            tool_connection=[0,0,0,0,0,0],
            toolchanger_connection=[0,0,-13,0,0,0],
            tool_rack_connection=[0,0,1.5,0,0,0],
            offset=[0, 0, 0, 0, 0, 0],
            enable=[],
            disable=[],
            **kwargs
            ):
        self.name = name
        self.type = type
        self.workspace = workspace
        
        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=anchors[k], component=self.name) for k in anchors
        }

        # has tool changer
        self.has_toolchanger = has_toolchanger
        if self.has_toolchanger:
            self.assembly["toolchanger_tool_side"] = Solid(type="toolchanger_tool_side", 
                                                    anchors= {
                                                        "tool_connection": tool_connection,
                                                        "toolchanger_connection": toolchanger_connection,
                                                        "tool_rack_connection": tool_rack_connection}, 
                                                    component=self.name)
            self.assembly[next(iter(self.assembly))].attach_to(parent=self.assembly["toolchanger_tool_side"], parent_anchor="tool_connection", child_anchor="center", offset=offset)
        
        # enable and disable
        self.enable = enable
        self.disable = disable

