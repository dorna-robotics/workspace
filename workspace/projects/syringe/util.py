import json

from workspace.recipes.tool_rack import ToolRack
from workspace.recipes.hotel import Hotel
from workspace.recipes.adapter import Adapter
from workspace.recipes.feeder import Feeder
from workspace.recipes.rack import Rack
from workspace.recipes.decapper import Decapper
from workspace.recipes.inspector import MobileInspector
from workspace.recipes.dispense_arm import DispenseArm

def create_recipes(workspace, core, speed_factor=1):
    # read detection preset
    detection_preset = json.load(open("config/detection_preset.json"))
    # index list for the feeder
    index_list = [
            [0, {"limit": {"center": {"width": [476, 496], "height": [390, 410], "inv": 0}}}],
            [-1, {"limit": {"center": {"width": [436, 456], "height": [421, 441], "inv": 0}}}],
            [1, {"limit": {"center": {"width": [502, 522], "height": [346, 366], "inv": 0}}}],
            [-2, {"limit": {"center": {"width": [386, 406], "height": [434, 454], "inv": 0}}}],
            [2, {"limit": {"center": {"width": [510, 530], "height": [296, 316], "inv": 0}}}],
        ]

    return {
        "tool_rack_2": ToolRack(workspace, core, workspace.components["tool_rack_2"], left_approach=True, speed_factor=speed_factor),
        "tool_rack_1": ToolRack(workspace, core, workspace.components["tool_rack_1"], left_approach=True, speed_factor=speed_factor),
        "tool_rack_0": ToolRack(workspace, core, workspace.components["tool_rack_0"], left_approach=True, speed_factor=speed_factor),

        "hotel": Hotel(workspace, core, workspace.components["hotel_0"], left_approach=True, base_distance=150, speed_factor=speed_factor),

        "cap_holder": Rack(workspace, core, workspace.components["sbs_adapter_1"], left_approach=False, base_distance=50, speed_factor=speed_factor),
        "sbs_plate": Rack(workspace, core, workspace.components["sbs_adapter_0"], base_distance=75, rail_span=5, rail_step=5, speed_factor=speed_factor),

        "sbs_adapter": Adapter(workspace, core, workspace.components["sbs_adapter_0"], left_approach=True, speed_factor=speed_factor),

        "feeder": Feeder(workspace, core, workspace.components["feeder"], left_approach=False, speed_factor=speed_factor, index_list=index_list),

        "decapper": Decapper(workspace, core, workspace.components["decapper_0"], base_distance=50, speed_factor=speed_factor),
        "inspector": MobileInspector(workspace, core, detection_preset=detection_preset["cap"], speed_factor=speed_factor),
        "dispense_arm": DispenseArm(workspace, core, workspace.components["syringe_dispence_0"], base_distance=50, speed_factor=speed_factor)
    }