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


def workflow_fn(*, workspace, core):
    """
    Pure workflow (NO wait_for_start, NO checkpoint).
    Runtime (rt.worker) handles waiting + pause/stop gating.
    """

    # ------------------------------------------------------------
    # Job config
    # ------------------------------------------------------------
    simulation = True
    speed_factor = 1

    tip_list = [f"{r}{c}" for r in "ABCDEFGH" for c in range(1, 13)]

    tube_list = [f"{r}{c}" for r in "AB" for c in range(1, 6)]
    num_processed_tube = 1
    falcon_rack_gravity_offset = 3

    cap_list = [f"{r}{c}" for r in "CD" for c in range(1, 6)]
    cap_offset = [0, 0, 111 - 2, 0, 0, 0]
    cap_gravity_offset = 1

    decapper_tool_tcp_z_offset = -1

    pipetting = True
    shake_travel = 7
    vol = 400  # ul
    immerse_depth = 20

    dry_run_count = 1
    printer_gravity_offset = 4
    print_job = True

    inspection_frq = 4
    inspection_rot = 90

    # ------------------------------------------------------------
    # Build recipes (recipes MUST use workspace.rt.* for robot calls)
    # ------------------------------------------------------------
    rcp = create_recipes(workspace, core, speed_factor=speed_factor)

    # ------------------------------------------------------------
    # Simulation toggles
    # ------------------------------------------------------------
    if not simulation:
        core.simulation(False)
        workspace.components["pipettor"].simulation(False)
        workspace.components["printer"].simulation(False)

    # ------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------
    for tip_index in tip_list:

        if pipetting:
            rcp["tool_rack_1"].pick()

            # pick tip
            rcp["tip_rack"].pick_tip(tip_index)

            for index in [num_processed_tube - 1, len(tube_list) - num_processed_tube]:
                aspirate_index = tube_list[index]
                dispense_index = tube_list[len(tube_list) - (index + 1)]

                rcp["falcon_pipepette"].immerse(anchor=aspirate_index, depth=immerse_depth)
                rcp["falcon_pipepette"].aspirate(vol=vol)
                rcp["falcon_pipepette"].retract(anchor=aspirate_index)

                rcp["falcon_pipepette"].immerse(anchor=dispense_index, depth=immerse_depth)
                rcp["falcon_pipepette"].dispense(vol=vol)
                rcp["falcon_pipepette"].retract(anchor=dispense_index)

            rcp["waste_bin"].eject_tip(shake_travel=shake_travel)
            rcp["tool_rack_1"].place()

        # pick gripper
        rcp["tool_rack_0"].pick()

        # cap/print/inspect
        for index in range(num_processed_tube):
            tube_index = tube_list[index]
            cap_index = cap_list[index]

            # print
            print("print tube")
            rcp["falcon_rack"].pick(tube_index)
            rcp["decapper"].place()

            rcp["falcon_rack"].pick(cap_index)

            rcp["decapper"].cap(exit=False)
            rcp["decapper"].pick(approach=False, tool_tcp_z_offset=decapper_tool_tcp_z_offset)

            rcp["printer"].place(exit=False, gravity_offset=printer_gravity_offset)

            if print_job:
                rcp["printer"].print_label("D-1783", code_type="code128", autorun=True, verify=True)
            else:
                rcp["printer"].dry_run_spin(count=dry_run_count)
            rcp["printer"].pick(approach=False)

            rcp["inspector"].present(approach=False)
            for _ in range(inspection_frq):
                rcp["inspector"].rotate(rotation=inspection_rot)

            rcp["falcon_rack"].place(
                tube_index,
                gravity_offset=falcon_rack_gravity_offset,
                soft_approach=True,
            )

        # decap
        for index in range(num_processed_tube):
            tube_index = tube_list[index]
            cap_index = cap_list[index]

            rcp["falcon_rack"].pick(tube_index)

            rcp["decapper"].place(exit=False)
            rcp["decapper"].decap(approach=False)

            rcp["falcon_rack"].place(
                cap_index,
                offset=cap_offset,
                soft_approach=True,
                gravity_offset=cap_gravity_offset,
            )

            rcp["decapper"].pick(tool_tcp_z_offset=decapper_tool_tcp_z_offset)

            rcp["falcon_rack"].place(
                tube_index,
                gravity_offset=falcon_rack_gravity_offset,
                soft_approach=True,
            )

        # place gripper
        rcp["tool_rack_0"].place()
