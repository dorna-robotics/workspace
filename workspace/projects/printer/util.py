import json

from workspace.recipes.tool_rack import ToolRack
from workspace.recipes.rack import Rack
from workspace.recipes.printer import Printer
from workspace.recipes.pipetting import PipettingSite
from workspace.recipes.inspector import FixedInspector
from workspace.recipes.decapper import Decapper

def create_recipes(workspace, core, speed_factor=0.5):    
    return {
        "tool_rack_1": ToolRack(workspace, core, workspace.components["tool_rack_1"], left_approach=True, speed_factor=speed_factor),
        "tool_rack_0": ToolRack(workspace, core, workspace.components["tool_rack_0"], left_approach=True, speed_factor=speed_factor),

        "waste_bin": PipettingSite(workspace, core, workspace.components["adapter_sbs_1"], base_distance=250, speed_factor=speed_factor),
        "tip_rack": PipettingSite(workspace, core, workspace.components["adapter_sbs_0"], base_distance=250, speed_factor=speed_factor),
        "falcon_pipepette": PipettingSite(workspace, core, workspace.components["adapter_falcon"], base_distance=200, speed_factor=speed_factor),
        
        "falcon_rack": Rack(workspace, core, workspace.components["adapter_falcon"], base_distance=200, speed_factor=speed_factor),

        "decapper": Decapper(workspace, core, workspace.components["decapper"], base_distance=200, speed_factor=speed_factor),

        "printer": Printer(workspace=workspace, core=core, component=workspace.components["printer"], speed_factor=speed_factor),

        "inspector": FixedInspector(workspace, core, component=workspace.components["vision_station"], detection_preset={}, speed_factor=speed_factor),

    }