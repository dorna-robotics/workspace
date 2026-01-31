from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid

class Pipettor:
    DEFAULTS = dict(
        anchors = {"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 0, 0, 0, 0], "top": [0, 0, 0, 0, 0, 0]}},
        tool_connection=[0,0,0,0,0,0],
        tool_changer_connection=[0,0,-13,0,0,0],
        tool_rack_connection=[0,0,1.5,0,0,0],
        offset=[0, 0, 0, 0, 0, 0],
        #cfg
        has_tool_changer = True,
    )

    def __init__(self, name: str, workspace, type=None, **kwargs):
        # prm
        prm = deepcopy(self.DEFAULTS) # default
        merge(prm, kwargs) # kwargs

        # init
        self.name = name
        self.workspace = workspace
        self.type = type
        
        # assembly
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name, **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})) for k in prm["anchors"]
        }

        # has tool changer
        self.has_tool_changer = prm["has_tool_changer"]
        if self.has_tool_changer:
            self.assembly["tool_changer_tool_side"] = Solid(type="tool_changer_tool_side", 
                                                    anchors= {
                                                        "tool_connection": prm["tool_connection"],
                                                        "tool_changer_connection": prm["tool_changer_connection"],
                                                        "tool_rack_connection": prm["tool_rack_connection"]},
                                                    component=self.name)
            self.assembly[next(iter(self.assembly))].attach_to(parent=self.assembly["tool_changer_tool_side"], parent_anchor="tool_connection", child_anchor="center", offset=prm["offset"])