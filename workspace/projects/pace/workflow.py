from dataclasses import dataclass, field
import time

from workspace.recipes.tool_rack import ToolRack
from workspace.recipes.feeder import Feeder
from workspace.recipes.rack import Rack
from workspace.recipes.decapper import Decapper
from workspace.recipes.inspector import FixedInspector
from workspace.recipes.scale import Scale
from workspace.recipes.doser import DosingSite
from workspace.recipes.shaker import Shaker
from workspace.recipes.recipe import RecipeError


@dataclass
class Config:
    # ── Tube / position mappings ─────────────────────────────────────────
    source: list = field(default_factory=lambda: [
        ["rack_40ml_1", "A1"],
        ["rack_40ml_1", "A2"],
        ["rack_40ml_1", "A3"],
        ["rack_40ml_1", "A4"],
    ])
    working: list = field(default_factory=lambda: [
        ["rack_40ml_2", "B1"],
        ["rack_40ml_2", "B2"],
        ["rack_40ml_2", "B3"],
        ["rack_40ml_2", "B4"],
])
    cap_holder: list = field(default_factory=lambda: [
        "decapper_1",
        "decapper_2",
        "decapper_3",
        "decapper_4",
    ])
    dosing_40ml: list = field(default_factory=lambda: [
        ["doser_40ml_1", "B1"],
        ["doser_40ml_1", "B2"],
        ["doser_40ml_1", "B3"],
        ["doser_40ml_1", "B4"],
    ])
    dosing_clean: list = field(default_factory=lambda: [
        ["doser_40ml_1", "A1"],
        ["doser_40ml_1", "A2"],
        ["doser_40ml_1", "A3"],
    ])
    dosing_waste: list = field(default_factory=lambda: [
        ["doser_40ml_1", "A4"],
    ])
    shaker_slots: list = field(default_factory=lambda: [
        ["shaker_1", "A1"],
        ["shaker_1", "A2"],
        ["shaker_2", "A1"],
        ["shaker_2", "A2"],
    ])
    cap_feeder: list = field(default_factory=lambda: [
        ["cap_holder_1", "A1"],
        ["cap_holder_1", "A2"],
        ["cap_holder_1", "A3"],
        ["cap_holder_1", "A4"],
    ])
    dosing_2ml_end: list = field(default_factory=lambda: [
        ["doser_2ml_1", "A1"],
        ["doser_2ml_1", "A2"],
        ["doser_2ml_1", "A3"],
        ["doser_2ml_1", "A4"],
    ])
    dosing_2ml_middle: list = field(default_factory=lambda: [
        ["doser_2ml_2", "A1"],
        ["doser_2ml_2", "A2"],
        ["doser_2ml_2", "A3"],
        ["doser_2ml_2", "A4"],
    ])
    rack_2ml_end: list = field(default_factory=lambda: [
        ["rack_2ml_1", "A1"],
        ["rack_2ml_1", "A2"],
        ["rack_2ml_1", "A3"],
        ["rack_2ml_1", "A4"],
    ])

    # ── Parameters ───────────────────────────────────────────────────────
    immerse_40ml_dist: float = 90
    retract_40ml_dist: float = 10
    immerse_2ml_dist: float = 25
    retract_2ml_dist: float = 10
    shake_duration: float = 120
    inspection_frq: int = 4
    inspection_rot: float = 90
    speed_factor: float = 10


class Workflow:
    """
    PACE workflow — imperative sequential execution.

    Usage:
        workflow_fn(workspace=ws, core=core)

        # Or with custom config:
        cfg = Config(source=[["rack_40ml_1", "A1"]], shake_duration=60)
        wf = Workflow(workspace, core, cfg)
        wf.run()
    """

    def __init__(self, workspace, core, config=None):
        self.cfg = config or Config()
        self.rt = workspace.rt
        self.rcp = self.create_recipes(workspace, core)
        self.n = len(self.cfg.source)

    def create_recipes(self, workspace, core):
        sf = self.cfg.speed_factor
        return {
            # ── Tool racks ───────────────────────────────────────────────
            "tool_rack_1": ToolRack(workspace, core, workspace.components["tool_rack_144mm_1"], left_approach=True, speed_factor=sf),
            "tool_rack_2": ToolRack(workspace, core, workspace.components["tool_rack_144mm_2"], left_approach=True, speed_factor=sf),
            "tool_rack_3": ToolRack(workspace, core, workspace.components["tool_rack_144mm_3"], left_approach=True, speed_factor=sf),
            "tool_rack_4": ToolRack(workspace, core, workspace.components["tool_rack_144mm_4"], left_approach=True, base_distance=300, rail_span=5, rail_step=5, speed_factor=sf),

            # ── Cap handling ─────────────────────────────────────────────
            "cap_holder_1": Rack(workspace, core, workspace.components["adapter_plate_autosampler_2ml_5x10_1"], left_approach=False, base_distance=150, rail_step=5, rail_span=5, speed_factor=sf),
            "feeder_1":     Feeder(workspace, core, workspace.components["capfeeder_autosampler_2ml_1"], left_approach=False, speed_factor=sf),

            # ── 40 ml tube racks ─────────────────────────────────────────
            "rack_40ml_1": Rack(workspace, core, workspace.components["adapter_plate_amber_40ml_4x7_1"], base_distance=75, rail_span=5, rail_step=5, speed_factor=sf),
            "rack_40ml_2": Rack(workspace, core, workspace.components["adapter_plate_amber_40ml_2x4_1"], base_distance=75, rail_span=5, rail_step=5, speed_factor=sf),

            # ── Scale ────────────────────────────────────────────────────
            "scale_1": Scale(workspace, core, workspace.components["scale_1"], base_distance=350, speed_factor=sf, rail_step=5, rail_span=5),

            # ── Decappers ────────────────────────────────────────────────
            "decapper_1": Decapper(workspace, core, workspace.components["decapper_1"], base_distance=200, rail_step=5, rail_span=5, speed_factor=sf),
            "decapper_2": Decapper(workspace, core, workspace.components["decapper_2"], base_distance=200, rail_step=5, rail_span=5, speed_factor=sf),
            "decapper_3": Decapper(workspace, core, workspace.components["decapper_3"], base_distance=200, rail_step=5, rail_span=5, speed_factor=sf),
            "decapper_4": Decapper(workspace, core, workspace.components["decapper_4"], base_distance=200, rail_step=5, rail_span=5, speed_factor=sf),
            "decapper_5": Decapper(workspace, core, workspace.components["decapper_5"], base_distance=200, rail_step=5, rail_span=5, speed_factor=sf),

            # ── 40 ml dosing sites ───────────────────────────────────────
            "doser_40ml_1": DosingSite(workspace, core, workspace.components["adapter_plate_amber_40ml_2x4_1"], base_distance=200, rail_span=5, rail_step=5, speed_factor=sf),

            # ── Shakers ──────────────────────────────────────────────────
            "shaker_1": Shaker(workspace, core, workspace.components["shaker_2slot_1"], left_approach=False, base_distance=100, rail_step=5, rail_span=5, speed_factor=sf),
            "shaker_2": Shaker(workspace, core, workspace.components["shaker_2slot_2"], left_approach=False, base_distance=100, rail_step=5, rail_span=5, speed_factor=sf),

            # ── 2 ml dosing sites ────────────────────────────────────────
            "doser_2ml_1": DosingSite(workspace, core, workspace.components["adapter_plate_autosampler_2ml_5x10_2"], base_distance=100, rail_span=5, rail_step=5, speed_factor=sf),
            "doser_2ml_2": DosingSite(workspace, core, workspace.components["adapter_plate_autosampler_2ml_5x10_3"], base_distance=100, rail_span=5, rail_step=5, speed_factor=sf),

            # ── 2 ml tube racks ──────────────────────────────────────────
            "rack_2ml_1": Rack(workspace, core, workspace.components["adapter_plate_autosampler_2ml_5x10_2"], base_distance=100, rail_span=5, rail_step=5, speed_factor=sf),
            "rack_2ml_2": Rack(workspace, core, workspace.components["adapter_plate_autosampler_2ml_5x10_3"], base_distance=100, rail_span=5, rail_step=5, speed_factor=sf),

            # ── Inspector ────────────────────────────────────────────────
            "inspector_1": FixedInspector(workspace, core, component=workspace.components["inspection_horizontal_144mm_1"], base_distance=200, rail_span=5, rail_step=5, detection_preset={}, speed_factor=sf),
        }

    # ── Shared helpers ───────────────────────────────────────────────────────

    def inspect_tube(self):
        self.rcp["inspector_1"].present(approach=True)
        for _ in range(self.cfg.inspection_frq):
            self.rcp["inspector_1"].rotate(rotation=self.cfg.inspection_rot)

    def rinse_needle(self):
        rcp, cfg = self.rcp, self.cfg
        for j in range(len(cfg.dosing_clean)):
            rcp[cfg.dosing_clean[j][0]].immerse(dist=cfg.immerse_40ml_dist, anchor=cfg.dosing_clean[j][1])
            rcp[cfg.dosing_clean[j][0]].dispense(vol=10)
            rcp[cfg.dosing_clean[j][0]].retract(dist=cfg.retract_40ml_dist, anchor=cfg.dosing_clean[j][1])
            rcp[cfg.dosing_waste[0][0]].immerse(dist=cfg.immerse_40ml_dist, anchor=cfg.dosing_waste[0][1], padding=10)
            rcp[cfg.dosing_waste[0][0]].dispense(vol=10)
            rcp[cfg.dosing_waste[0][0]].retract(dist=cfg.retract_40ml_dist, anchor=cfg.dosing_waste[0][1])

    # ── Phase methods ────────────────────────────────────────────────────────

    def phase1_inspect_decap(self):
        """Weigh, inspect and decap source tubes."""
        rt, rcp, cfg = self.rt, self.rcp, self.cfg
        rt.step("Phase 1 — Weighing, inspecting, and decapping source tubes")
        rcp["tool_rack_1"].pick()
        for i in range(self.n):
            rt.step(f"Tube {i+1}/{self.n} — pick, inspect, weigh, decap")
            rcp[cfg.source[i][0]].pick(cfg.source[i][1])
            self.inspect_tube()
            rcp["scale_1"].place("place")
            rcp["scale_1"].weight()
            rcp["scale_1"].pick("place")
            rcp["decapper_5"].place(exit=False)
            rcp["decapper_5"].decap(approach=False)
            rcp[cfg.cap_holder[i]].place()
            rcp["decapper_5"].pick()
            rcp[cfg.working[i][0]].place(cfg.working[i][1])
        rcp["tool_rack_1"].place()

    def phase2_dose_40ml(self):
        """Dose solvent into 40 ml working tubes."""
        rt, rcp, cfg = self.rt, self.rcp, self.cfg
        rt.step("Phase 2 — Dosing solvent into 40 ml tubes")
        rcp["tool_rack_4"].pick()
        for i in range(self.n):
            rt.step(f"Dosing tube {i+1}/{self.n}")
            rcp[cfg.dosing_40ml[i][0]].immerse(dist=cfg.immerse_40ml_dist, anchor=cfg.dosing_40ml[i][1])
            rcp[cfg.dosing_40ml[i][0]].dispense(vol=10)
            rcp[cfg.dosing_40ml[i][0]].retract(dist=cfg.retract_40ml_dist, anchor=cfg.dosing_40ml[i][1])
            self.rinse_needle()
        rcp["tool_rack_4"].place()

    def phase3_load_shakers(self):
        """Re-cap working tubes and load onto shakers."""
        rt, rcp, cfg = self.rt, self.rcp, self.cfg
        rt.step("Phase 3 — Re-capping and loading shakers")
        rcp["tool_rack_1"].pick()
        for i in range(self.n):
            rt.step(f"Loading tube {i+1}/{self.n} onto shaker")
            rcp[cfg.working[i][0]].pick(cfg.working[i][1])
            rcp["decapper_5"].place()
            rcp[cfg.cap_holder[i]].pick()
            rcp["decapper_5"].cap(exit=False)
            rcp["decapper_5"].pick(approach=False)
            rcp[cfg.shaker_slots[i][0]].place(cfg.shaker_slots[i][1])
        rcp["tool_rack_1"].place()

    def phase4_shake_and_feed(self):
        """Start shakers, feed caps while waiting, then stop shakers."""
        rt, rcp, cfg = self.rt, self.rcp, self.cfg

        # start shakers (non-blocking)
        rt.step("Phase 4 — Shaking started, feeding caps during idle time")
        for i in range(self.n):
            rcp[cfg.shaker_slots[i][0]].shake(duration=cfg.shake_duration)
        shake_start = time.time()

        # feed caps while shakers run
        rcp["tool_rack_3"].pick()
        for i in range(self.n):
            rt.step(f"Feeding cap {i+1}/{self.n} from feeder")
            rcp["feeder_1"].above(anchor="plate_center")
            try:
                rcp["feeder_1"].present_cap(rcp["inspector_1"])
                rcp["feeder_1"].pick(approach=False)
                rcp[cfg.cap_feeder[i][0]].place(cfg.cap_feeder[i][1])
            except RecipeError:
                rt.step(f"Cap {i+1} feed skipped — feeder empty", level="warning")
        rcp["tool_rack_3"].place()

        # wait for remaining shake time
        elapsed = time.time() - shake_start
        remaining = cfg.shake_duration - elapsed
        if remaining > 0:
            rt.step(f"Waiting {remaining:.0f}s for shakers to finish")
            rt.delay(remaining)

        # stop shakers
        for i in range(self.n):
            rcp[cfg.shaker_slots[i][0]].stop_shaking()

    def phase5a_retrieve_decap(self):
        """Retrieve from shakers, inspect, decap, return to working rack."""
        rt, rcp, cfg = self.rt, self.rcp, self.cfg
        rt.step("Phase 5a — Retrieving from shakers and decapping")
        rcp["tool_rack_1"].pick()
        for i in range(self.n):
            rt.step(f"Retrieving tube {i+1}/{self.n} from shaker")
            rcp[cfg.shaker_slots[i][0]].pick(cfg.shaker_slots[i][1])
            self.inspect_tube()
            rcp["decapper_5"].place(exit=False)
            rcp["decapper_5"].decap(approach=False)
            rcp[cfg.cap_holder[i]].place()
            rcp["decapper_5"].pick()
            rcp[cfg.working[i][0]].place(cfg.working[i][1])
        rcp["tool_rack_1"].place()

    def phase5b_dose_2ml(self):
        """Dose from 40 ml working tubes into 2 ml vials."""
        rt, rcp, cfg = self.rt, self.rcp, self.cfg
        rt.step("Phase 5b — Dosing into 2 ml vials")
        rcp["tool_rack_4"].pick()
        for i in range(self.n):
            rt.step(f"Dosing 2 ml vials for tube {i+1}/{self.n}")
            rcp[cfg.dosing_40ml[i][0]].immerse(dist=cfg.immerse_40ml_dist, anchor=cfg.dosing_40ml[i][1], padding=150)
            rcp[cfg.dosing_40ml[i][0]].dispense(vol=10)
            rcp[cfg.dosing_40ml[i][0]].retract(dist=cfg.retract_40ml_dist, anchor=cfg.dosing_40ml[i][1])
            rcp[cfg.dosing_2ml_middle[i][0]].immerse(dist=cfg.immerse_2ml_dist, anchor=cfg.dosing_2ml_middle[i][1])
            rcp[cfg.dosing_2ml_middle[i][0]].dispense(vol=10)
            rcp[cfg.dosing_2ml_middle[i][0]].retract(dist=cfg.retract_2ml_dist, anchor=cfg.dosing_2ml_middle[i][1])
            rcp[cfg.dosing_2ml_end[i][0]].immerse(dist=cfg.immerse_2ml_dist, anchor=cfg.dosing_2ml_end[i][1])
            rcp[cfg.dosing_2ml_end[i][0]].dispense(vol=10)
            rcp[cfg.dosing_2ml_end[i][0]].retract(dist=cfg.retract_2ml_dist, anchor=cfg.dosing_2ml_end[i][1])
            self.rinse_needle()
        rcp["tool_rack_4"].place()

    def phase5c_recap_return(self):
        """Re-cap 40 ml tubes and return to source rack."""
        rt, rcp, cfg = self.rt, self.rcp, self.cfg
        rt.step("Phase 5c — Re-capping and returning 40 ml tubes")
        rcp["tool_rack_1"].pick()
        for i in range(self.n):
            rt.step(f"Re-capping tube {i+1}/{self.n}")
            rcp[cfg.working[i][0]].pick(cfg.working[i][1])
            rcp["decapper_5"].place()
            rcp[cfg.cap_holder[i]].pick()
            rcp["decapper_5"].cap(exit=False)
            rcp["decapper_5"].pick(approach=False)
            rcp[cfg.source[i][0]].place(cfg.source[i][1])
        rcp["tool_rack_1"].place()

    def phase6_cap_inspect(self):
        """Cap, vibrate, inspect and return 2 ml vials."""
        rt, rcp, cfg = self.rt, self.rcp, self.cfg
        rt.step("Phase 6 — Capping and inspecting 2 ml vials")
        rcp["tool_rack_2"].pick()
        for i in range(self.n):
            rt.step(f"Capping 2 ml vial {i+1}/{self.n}")
            rcp[cfg.rack_2ml_end[i][0]].pick(cfg.rack_2ml_end[i][1])
            rcp["decapper_5"].place()
            rcp[cfg.cap_feeder[i][0]].pick(cfg.cap_feeder[i][1])
            rcp["decapper_5"].cap(exit=False)
            rcp["decapper_5"].pick(approach=False)
            rcp["decapper_5"].vibrate()
            self.inspect_tube()
            rcp[cfg.rack_2ml_end[i][0]].place(cfg.rack_2ml_end[i][1])
        rcp["tool_rack_2"].place()

    # ── Run ───────────────────────────────────────────────────────────────────

    def run(self):
        """Execute all phases sequentially."""
        self.phase1_inspect_decap()
        self.phase2_dose_40ml()
        self.phase3_load_shakers()
        self.phase4_shake_and_feed()
        self.phase5a_retrieve_decap()
        self.phase5b_dose_2ml()
        self.phase5c_recap_return()
        self.phase6_cap_inspect()
        self.rt.step("Workflow complete — all vials processed", level="success")


# =============================================================================
# Entry point — called by runtime
# =============================================================================

def workflow_fn(*, workspace, core):
    wf = Workflow(workspace, core)
    wf.run()
