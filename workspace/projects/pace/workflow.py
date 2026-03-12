import json

from workspace.recipes.tool_rack import ToolRack
from workspace.recipes.feeder import Feeder
from workspace.recipes.rack import Rack
from workspace.recipes.decapper import Decapper
from workspace.recipes.inspector import FixedInspector
from workspace.recipes.scale import Scale
from workspace.recipes.doser import DosingSite
from workspace.recipes.shaker import Shaker


def create_recipes(workspace, core, speed_factor=0.5):
    """
    Instantiate and return all recipe objects for the PACE workflow.
    Each recipe wraps a workspace component and exposes high-level actions
    (pick, place, dispense, etc.) used in workflow_fn.
    """
    return {
        # ── Tool racks (gripper / tip change) ────────────────────────────────
        "tool_rack_1": ToolRack(workspace, core, workspace.components["tool_rack_144mm_1"], left_approach=True, speed_factor=speed_factor),
        "tool_rack_2": ToolRack(workspace, core, workspace.components["tool_rack_144mm_2"], left_approach=True, speed_factor=speed_factor),
        "tool_rack_3": ToolRack(workspace, core, workspace.components["tool_rack_144mm_3"], left_approach=True, speed_factor=speed_factor),
        "tool_rack_4": ToolRack(workspace, core, workspace.components["tool_rack_144mm_4"], left_approach=True, base_distance=300, rail_span=5, rail_step=5, speed_factor=speed_factor),

        # ── Cap handling ──────────────────────────────────────────────────────
        "cap_holder_1": Rack(workspace, core, workspace.components["adapter_plate_autosampler_2ml_5x10_1"], left_approach=False, base_distance=150, rail_step=5, rail_span=5, speed_factor=speed_factor),
        "feeder_1":     Feeder(workspace, core, workspace.components["capfeeder_autosampler_2ml_1"], left_approach=False, speed_factor=speed_factor),

        # ── 40 ml tube racks ─────────────────────────────────────────────────
        "rack_40ml_1": Rack(workspace, core, workspace.components["adapter_plate_amber_40ml_4x7_1"], base_distance=75, rail_span=5, rail_step=5, speed_factor=speed_factor),
        "rack_40ml_2": Rack(workspace, core, workspace.components["adapter_plate_amber_40ml_2x4_1"], base_distance=75, rail_span=5, rail_step=5, speed_factor=speed_factor),

        # ── Scale ─────────────────────────────────────────────────────────────
        "scale_1": Scale(workspace, core, workspace.components["scale_1"], base_distance=350, speed_factor=speed_factor, rail_step=5, rail_span=5),

        # ── Decappers ────────────────────────────────────────────────────────
        "decapper_1": Decapper(workspace, core, workspace.components["decapper_1"], base_distance=200, rail_step=5, rail_span=5, speed_factor=speed_factor),
        "decapper_2": Decapper(workspace, core, workspace.components["decapper_2"], base_distance=200, rail_step=5, rail_span=5, speed_factor=speed_factor),
        "decapper_3": Decapper(workspace, core, workspace.components["decapper_3"], base_distance=200, rail_step=5, rail_span=5, speed_factor=speed_factor),
        "decapper_4": Decapper(workspace, core, workspace.components["decapper_4"], base_distance=200, rail_step=5, rail_span=5, speed_factor=speed_factor),
        "decapper_5": Decapper(workspace, core, workspace.components["decapper_5"], base_distance=200, rail_step=5, rail_span=5, speed_factor=speed_factor),

        # ── 40 ml dosing sites ────────────────────────────────────────────────
        "doser_40ml_1": DosingSite(workspace, core, workspace.components["adapter_plate_amber_40ml_2x4_1"], base_distance=200, rail_span=5, rail_step=5, speed_factor=speed_factor),

        # ── Shakers ───────────────────────────────────────────────────────────
        "shaker_1": Shaker(workspace, core, workspace.components["shaker_2slot_1"], left_approach=False, base_distance=100, rail_step=5, rail_span=5, speed_factor=speed_factor),
        "shaker_2": Shaker(workspace, core, workspace.components["shaker_2slot_2"], left_approach=False, base_distance=100, rail_step=5, rail_span=5, speed_factor=speed_factor),

        # ── 2 ml dosing sites ─────────────────────────────────────────────────
        "doser_2ml_1": DosingSite(workspace, core, workspace.components["adapter_plate_autosampler_2ml_5x10_2"], base_distance=100, rail_span=5, rail_step=5, speed_factor=speed_factor),
        "doser_2ml_2": DosingSite(workspace, core, workspace.components["adapter_plate_autosampler_2ml_5x10_3"], base_distance=100, rail_span=5, rail_step=5, speed_factor=speed_factor),

        # ── 2 ml tube racks ───────────────────────────────────────────────────
        "rack_2ml_1": Rack(workspace, core, workspace.components["adapter_plate_autosampler_2ml_5x10_2"], base_distance=100, rail_span=5, rail_step=5, speed_factor=speed_factor),
        "rack_2ml_2": Rack(workspace, core, workspace.components["adapter_plate_autosampler_2ml_5x10_3"], base_distance=100, rail_span=5, rail_step=5, speed_factor=speed_factor),

        # ── Inspector ─────────────────────────────────────────────────────────
        "inspector_1": FixedInspector(workspace, core, component=workspace.components["inspection_horizontal_144mm_1"], base_distance=200, rail_span=5, rail_step=5, detection_preset={}, speed_factor=speed_factor),
    }


def workflow_fn(*, workspace, core):
    """
    Main PACE workflow:
      1. Weigh, inspect and decap source 40 ml tubes.
      2. Dose solvent into the 40 ml working tubes.
      3. Re-cap working tubes and load them onto the shakers.
      4. Start shaking; meanwhile feed new caps from the feeder.
      5. Stop shakers, decap, dose into 2 ml vials, re-cap and return.
      6. Cap, scan and return 2 ml vials.
    """

    # ── Tube / position mappings ──────────────────────────────────────────────

    # Source 40 ml tubes (read-only input rack)
    source = [
        ["rack_40ml_1", "A1"],
        #["rack_40ml_1", "A2"],
        #["rack_40ml_1", "A3"],
        #["rack_40ml_1", "A4"],
    ]

    # Working 40 ml tubes (intermediate rack used during processing)
    working = [
        ["rack_40ml_2", "B1"],
        ["rack_40ml_2", "B2"],
        ["rack_40ml_2", "B3"],
        ["rack_40ml_2", "B4"],
    ]

    # Decapper slots used to temporarily store caps during processing
    cap_holder = [
        "decapper_1",
        "decapper_2",
        "decapper_3",
        "decapper_4",
    ]

    # 40 ml dosing targets (sample tubes being dosed)
    dosing_40ml = [
        ["doser_40ml_1", "B1"],
        ["doser_40ml_1", "B2"],
        ["doser_40ml_1", "B3"],
        ["doser_40ml_1", "B4"],
    ]

    # 40 ml clean solvent positions (used to rinse the needle between doses)
    dosing_clean = [
        ["doser_40ml_1", "A1"],
        ["doser_40ml_1", "A2"],
        ["doser_40ml_1", "A3"],
    ]

    # 40 ml waste position (leftover solvent is dispensed here)
    dosing_waste = [
        ["doser_40ml_1", "A4"],
    ]

    # Shaker slots — two shakers, two slots each
    shaker = [
        ["shaker_1", "A1"],
        ["shaker_1", "A2"],
        ["shaker_2", "A1"],
        ["shaker_2", "A2"],
    ]

    # Cap feeder destination slots (caps picked from feeder go here)
    cap_feeder = [
        ["cap_holder_1", "A1"],
        ["cap_holder_1", "A2"],
        ["cap_holder_1", "A3"],
        ["cap_holder_1", "A4"],
    ]

    # 2 ml vial dosing targets — end rack
    dosing_2ml_end = [
        ["doser_2ml_1", "A1"],
        ["doser_2ml_1", "A2"],
        ["doser_2ml_1", "A3"],
        ["doser_2ml_1", "A4"],
    ]

    # 2 ml vial dosing targets — middle rack
    dosing_2ml_middle = [
        ["doser_2ml_2", "A1"],
        ["doser_2ml_2", "A2"],
        ["doser_2ml_2", "A3"],
        ["doser_2ml_2", "A4"],
    ]

    # 2 ml output racks
    rack_2ml_end = [
        ["rack_2ml_1", "A1"],
        ["rack_2ml_1", "A2"],
        ["rack_2ml_1", "A3"],
        ["rack_2ml_1", "A4"],
    ]

    rack_2ml_middle = [
        ["rack_2ml_2", "A1"],
        ["rack_2ml_2", "A2"],
        ["rack_2ml_2", "A3"],
        ["rack_2ml_2", "A4"],
    ]

    # ── Parameters ───────────────────────────────────────────────────────────

    immerse_40ml_dist  = 90    # mm — needle immersion depth for 40 ml tubes
    retract_40ml_dist  = 10    # mm — needle retract distance for 40 ml tubes
    immerse_2ml_dist   = 25    # mm — needle immersion depth for 2 ml vials
    retract_2ml_dist   = 10    # mm — needle retract distance for 2 ml vials
    shake_duration     = 10000 # seconds — total shaking time per batch
    inspection_frq     = 4     # number of rotation steps during tube inspection
    inspection_rot     = 90    # degrees — rotation angle per inspection step
    speed_factor       = 5     # global motion speed multiplier (1 = full speed)

    # ── Build recipes ─────────────────────────────────────────────────────────
    rcp = create_recipes(workspace, core, speed_factor=speed_factor)

    # =========================================================================
    # PHASE 1 — Weigh, inspect and decap source tubes
    # =========================================================================
    rcp["tool_rack_1"].pick()  # mount gripper

    for i in range(len(source)):
        rcp[source[i][0]].pick_from(source[i][1])   # pick source tube

        # inspect (rotate N times for full barcode / visual scan)
        rcp["inspector_1"].present(approach=True)
        for _ in range(inspection_frq):
            rcp["inspector_1"].rotate(rotation=inspection_rot)

        # weigh
        rcp["scale_1"].place_in("place")
        rcp["scale_1"].weight()
        rcp["scale_1"].pick_from("place")

        # decap and store cap
        rcp["decapper_5"].place(exit=False)
        rcp["decapper_5"].decap(approach=False)
        rcp[cap_holder[i]].place()                   # store cap in decapper slot

        # move uncapped tube to working rack
        rcp["decapper_5"].pick()
        rcp[working[i][0]].place_in(working[i][1])

    rcp["tool_rack_1"].place()  # unmount gripper

    # =========================================================================
    # PHASE 2 — Dose solvent into 40 ml working tubes
    # =========================================================================
    rcp["tool_rack_4"].pick()  # mount dosing needle

    for i in range(len(source)):
        # dose into sample tube
        rcp[dosing_40ml[i][0]].immerse(dist=immerse_40ml_dist, anchor=dosing_40ml[i][1])
        rcp[dosing_40ml[i][0]].dispense(vol=10)
        rcp[dosing_40ml[i][0]].retract(dist=retract_40ml_dist, anchor=dosing_40ml[i][1])

        # rinse needle: clean solution → waste
        for j in range(len(dosing_clean)):
            rcp[dosing_clean[j][0]].immerse(dist=immerse_40ml_dist, anchor=dosing_clean[j][1])
            rcp[dosing_clean[j][0]].dispense(vol=10)
            rcp[dosing_clean[j][0]].retract(dist=retract_40ml_dist, anchor=dosing_clean[j][1])

            rcp[dosing_waste[0][0]].immerse(dist=immerse_40ml_dist, anchor=dosing_waste[0][1], padding=10)
            rcp[dosing_waste[0][0]].dispense(vol=10)
            rcp[dosing_waste[0][0]].retract(dist=retract_40ml_dist, anchor=dosing_waste[0][1])

    rcp["tool_rack_4"].place()  # unmount dosing needle

    # =========================================================================
    # PHASE 3 — Re-cap working tubes and load onto shakers
    # =========================================================================
    rcp["tool_rack_1"].pick()  # mount gripper

    for i in range(len(source)):
        rcp[working[i][0]].pick_from(working[i][1])  # pick working tube

        # re-cap tube using stored cap
        rcp["decapper_5"].place()
        rcp[cap_holder[i]].pick()
        rcp["decapper_5"].cap(exit=False)
        rcp["decapper_5"].pick(approach=False)

        # load onto shaker
        rcp[shaker[i][0]].place(shaker[i][1])

    # start shaking all loaded slots (non-blocking — robot continues)
    for i in range(len(source)):
        rcp[shaker[i][0]].shake(duration=shake_duration)

    rcp["tool_rack_1"].place()  # unmount gripper

    # =========================================================================
    # PHASE 4 — Feed new caps while shakers run in background
    # =========================================================================
    rcp["tool_rack_3"].pick()  # mount suction cup

    for i in range(len(source)):
        rcp["feeder_1"].above(anchor="plate_center")        # position above feeder
        rcp["feeder_1"].present_cap(rcp["inspector_1"])     # detect and orient cap
        rcp["feeder_1"].pick(approach=False)                # pick oriented cap
        rcp[cap_feeder[i][0]].place_in(cap_feeder[i][1])   # store for later capping

    rcp["tool_rack_3"].place()  # unmount suction cup

    # =========================================================================
    # PHASE 5 — Stop shakers, decap, dose into 2 ml vials, re-cap and return
    # =========================================================================
    rcp["tool_rack_1"].pick()  # mount gripper

    # stop all shakers (blocking — waits for each to return to start position)
    for i in range(len(source)):
        rcp[shaker[i][0]].stop_shaking()

    for i in range(len(source)):
        rcp[shaker[i][0]].pick(shaker[i][1])    # pick tube from shaker

        # inspect
        rcp["inspector_1"].present(approach=True)
        for _ in range(inspection_frq):
            rcp["inspector_1"].rotate(rotation=inspection_rot)

        # decap and store cap
        rcp["decapper_5"].place(exit=False)
        rcp["decapper_5"].decap(approach=False)
        rcp[cap_holder[i]].place()

        # return tube to working rack
        rcp["decapper_5"].pick()
        rcp[working[i][0]].place_in(working[i][1])

    rcp["tool_rack_1"].place()  # unmount gripper

    # dose into 2 ml vials
    rcp["tool_rack_4"].pick()  # mount dosing needle

    for i in range(len(source)):
        # dose from 40 ml working tube
        rcp[dosing_40ml[i][0]].immerse(dist=immerse_40ml_dist, anchor=dosing_40ml[i][1], padding=150)
        rcp[dosing_40ml[i][0]].dispense(vol=10)
        rcp[dosing_40ml[i][0]].retract(dist=retract_40ml_dist, anchor=dosing_40ml[i][1])

        # dose into 2 ml middle vial
        rcp[dosing_2ml_middle[i][0]].immerse(dist=immerse_2ml_dist, anchor=dosing_2ml_middle[i][1])
        rcp[dosing_2ml_middle[i][0]].dispense(vol=10)
        rcp[dosing_2ml_middle[i][0]].retract(dist=retract_2ml_dist, anchor=dosing_2ml_middle[i][1])

        # dose into 2 ml end vial
        rcp[dosing_2ml_end[i][0]].immerse(dist=immerse_2ml_dist, anchor=dosing_2ml_end[i][1])
        rcp[dosing_2ml_end[i][0]].dispense(vol=10)
        rcp[dosing_2ml_end[i][0]].retract(dist=retract_2ml_dist, anchor=dosing_2ml_end[i][1])

        # rinse needle: clean solution → waste
        for j in range(len(dosing_clean)):
            rcp[dosing_clean[j][0]].immerse(dist=immerse_40ml_dist, anchor=dosing_clean[j][1])
            rcp[dosing_clean[j][0]].dispense(vol=10)
            rcp[dosing_clean[j][0]].retract(dist=retract_40ml_dist, anchor=dosing_clean[j][1])

            rcp[dosing_waste[0][0]].immerse(dist=immerse_40ml_dist, anchor=dosing_waste[0][1])
            rcp[dosing_waste[0][0]].dispense(vol=10)
            rcp[dosing_waste[0][0]].retract(dist=retract_40ml_dist, anchor=dosing_waste[0][1])

    rcp["tool_rack_4"].place()  # unmount dosing needle

    # re-cap 40 ml working tubes and return to source rack
    rcp["tool_rack_1"].pick()  # mount gripper

    for i in range(len(source)):
        rcp[working[i][0]].pick_from(working[i][1])

        rcp["decapper_5"].place()
        rcp[cap_holder[i]].pick()
        rcp["decapper_5"].cap(exit=False)
        rcp["decapper_5"].pick(approach=False)

        rcp[source[i][0]].place_in(source[i][1])    # return to source rack

    rcp["tool_rack_1"].place()  # unmount gripper

    # =========================================================================
    # PHASE 6 — Cap, inspect and return 2 ml end vials
    # =========================================================================
    rcp["tool_rack_2"].pick()  # mount gripper (2 ml compatible)

    for i in range(len(source)):
        rcp[rack_2ml_end[i][0]].pick_from(rack_2ml_end[i][1])

        # cap with pre-fed cap
        rcp["decapper_5"].place()
        rcp[cap_feeder[i][0]].pick_from(cap_feeder[i][1])
        rcp["decapper_5"].cap(exit=False)
        rcp["decapper_5"].pick(approach=False)

        # shake
        rcp["decapper_5"].shake()

        # final barcode / visual inspection
        rcp["inspector_1"].present(approach=True)
        for _ in range(inspection_frq):
            rcp["inspector_1"].rotate(rotation=inspection_rot)

        rcp[rack_2ml_end[i][0]].place_in(rack_2ml_end[i][1])   # return to output rack

    rcp["tool_rack_2"].place()  # unmount gripper
