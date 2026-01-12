from workspace.recipes.tool_rack import ToolRack
from workspace.recipes.hotel import Hotel
from workspace.recipes.adapter import Adapter
from workspace.recipes.feeder import Feeder
from workspace.recipes.rack import Rack
from workspace.recipes.decapper import Decapper

def create_recipes(workspace, core):
    return {
        "tool_rack_2": ToolRack(workspace, core, workspace.components["tool_rack_2"], left_approach=True),
        "tool_rack_1": ToolRack(workspace, core, workspace.components["tool_rack_1"], left_approach=True),
        "tool_rack_0": ToolRack(workspace, core, workspace.components["tool_rack_0"], left_approach=True),

        "hotel": Hotel(workspace, core, workspace.components["hotel_0"], left_approach=True, base_distance=50),

        "cap_holder": Rack(workspace, core, workspace.components["sbs_adapter_1"], left_approach=False, base_distance=50),
        "sbs_plate": Rack(workspace, core, workspace.components["sbs_adapter_0"], base_distance=50),

        "sbs_adapter": Adapter(workspace, core, workspace.components["sbs_adapter_0"], left_approach=True),

        "feeder": Feeder(workspace, core, workspace.components["feeder"], left_approach=False),

        "decapper": Decapper(workspace, core, workspace.components["decapper_0"], base_distance=50),
    }
