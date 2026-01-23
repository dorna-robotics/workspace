import json

from workspace.recipes.tool_rack import ToolRack
from workspace.recipes.rack import Rack
from workspace.recipes.printer import Printer
from workspace.recipes.pipetting import PipettingSite
from workspace.recipes.inspector import FixedInspector
from workspace.recipes.decapper import Decapper

def create_recipes(workspace, core):    
    return {
        "tool_rack_1": ToolRack(workspace, core, workspace.components["tool_rack_1"], left_approach=True),
        "tool_rack_0": ToolRack(workspace, core, workspace.components["tool_rack_0"], left_approach=True),

        "waste_bin": PipettingSite(workspace, core, workspace.components["adapter_sbs_1"], base_distance=250),
        "tip_rack": PipettingSite(workspace, core, workspace.components["adapter_sbs_0"], base_distance=250),
        "falcon_pipepette": PipettingSite(workspace, core, workspace.components["adapter_falcon"], base_distance=200),
        
        "falcon_rack": Rack(workspace, core, workspace.components["adapter_falcon"], base_distance=200),

        "decapper": Decapper(workspace, core, workspace.components["decapper"], base_distance=200),

        "printer": Printer(workspace=workspace, core=core, component=workspace.components["printer"]),

        "inspector": FixedInspector(workspace, core, component=workspace.components["vision_station"], detection_preset={}),

    }