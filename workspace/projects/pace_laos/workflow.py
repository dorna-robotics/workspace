# workflow.py
# LAOS layer dependencies:
#   1_scene/base.j2          → defines component names
#   2_params/params.yaml     → positions, run parameters (loaded at runtime)
#   2_params/recipes.yaml    → recipe alias → component + constructor kwargs
#   3_protocol/protocol.yaml → protocol stages (documentation only)
#   4_constraints/           → hard/soft rules (documentation only)

import time
import types
from pathlib import Path

import yaml

from workspace.recipes.tool_rack import ToolRack
from workspace.recipes.feeder import Feeder
from workspace.recipes.rack import Rack
from workspace.recipes.decapper import Decapper
from workspace.recipes.inspector import FixedInspector
from workspace.recipes.scale import Scale
from workspace.recipes.doser import DosingSite
from workspace.recipes.shaker import Shaker
from workspace.recipes.recipe import RecipeError

_PARAMS_DIR = Path(__file__).parent / "2_params"

_TYPE_MAP = {
    "ToolRack":      ToolRack,
    "Rack":          Rack,
    "Feeder":        Feeder,
    "Scale":         Scale,
    "Decapper":      Decapper,
    "DosingSite":    DosingSite,
    "Shaker":        Shaker,
    "FixedInspector": FixedInspector,
}


def _load_params() -> types.SimpleNamespace:
    """Load 2_params/params.yaml into a SimpleNamespace for attribute-style access."""
    with open(_PARAMS_DIR / "params.yaml") as f:
        data = yaml.safe_load(f)
    return types.SimpleNamespace(**data)


def _load_recipes(workspace, core, speed_factor: float) -> dict:
    """Instantiate all recipes defined in 2_params/recipes.yaml."""
    with open(_PARAMS_DIR / "recipes.yaml") as f:
        defs = yaml.safe_load(f)

    rcp = {}
    for alias, defn in defs.items():
        cls = _TYPE_MAP[defn["class"]]
        kwargs = dict(defn.get("kwargs") or {})
        comp = workspace.components[kwargs.pop("component")]
        rcp[alias] = cls(workspace, core, comp, speed_factor=speed_factor, **kwargs)
    return rcp


class Workflow:
    """
    PACE workflow — imperative sequential execution.
    Configuration is loaded from 2_params/params.yaml and 2_params/recipes.yaml.

    Usage:
        workflow_fn(workspace=ws, core=core)
    """

    def __init__(self, workspace, core):
        self.cfg = _load_params()
        self.rt = workspace.rt
        self.rcp = _load_recipes(workspace, core, self.cfg.speed_factor)
        self.n = len(self.cfg.source)

    # ── Shared helpers ───────────────────────────────────────────────────────

    def inspect_tube(self):
        self.rcp["inspector"].present(approach=True)
        for _ in range(self.cfg.inspection_frq):
            self.rcp["inspector"].rotate(rotation=self.cfg.inspection_rot)

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
        rcp["gripper"].pick()
        for i in range(self.n):
            rt.step(f"Tube {i+1}/{self.n} — pick, inspect, weigh, decap")
            rcp[cfg.source[i][0]].pick(cfg.source[i][1])
            self.inspect_tube()
            rcp["scale"].place("place")
            rcp["scale"].weight()
            rcp["scale"].pick("place")
            rcp["decapper_5"].place(exit=False)
            rcp["decapper_5"].decap(approach=False)
            rcp[cfg.cap_holder[i]].place()
            rcp["decapper_5"].pick()
            rcp[cfg.working[i][0]].place(cfg.working[i][1])
        rcp["gripper"].place()

    def phase2_dose_40ml(self):
        """Dose solvent into 40 ml working tubes."""
        rt, rcp, cfg = self.rt, self.rcp, self.cfg
        rt.step("Phase 2 — Dosing solvent into 40 ml tubes")
        rcp["needle"].pick()
        for i in range(self.n):
            rt.step(f"Dosing tube {i+1}/{self.n}")
            rcp[cfg.dosing_40ml[i][0]].immerse(dist=cfg.immerse_40ml_dist, anchor=cfg.dosing_40ml[i][1])
            rcp[cfg.dosing_40ml[i][0]].dispense(vol=10)
            rcp[cfg.dosing_40ml[i][0]].retract(dist=cfg.retract_40ml_dist, anchor=cfg.dosing_40ml[i][1])
            self.rinse_needle()
        rcp["needle"].place()

    def phase3_load_shakers(self):
        """Re-cap working tubes and load onto shakers."""
        rt, rcp, cfg = self.rt, self.rcp, self.cfg
        rt.step("Phase 3 — Re-capping and loading shakers")
        rcp["gripper"].pick()
        for i in range(self.n):
            rt.step(f"Loading tube {i+1}/{self.n} onto shaker")
            rcp[cfg.working[i][0]].pick(cfg.working[i][1])
            rcp["decapper_5"].place()
            rcp[cfg.cap_holder[i]].pick()
            rcp["decapper_5"].cap(exit=False)
            rcp["decapper_5"].pick(approach=False)
            rcp[cfg.shaker_slots[i][0]].place(cfg.shaker_slots[i][1])
        rcp["gripper"].place()

    def phase4_shake_and_feed(self):
        """Start shakers, feed caps while waiting, then stop shakers."""
        rt, rcp, cfg = self.rt, self.rcp, self.cfg

        rt.step("Phase 4 — Shaking started, feeding caps during idle time")
        for i in range(self.n):
            rcp[cfg.shaker_slots[i][0]].shake(duration=cfg.shake_duration)
        shake_start = time.time()

        rcp["feeder_tool"].pick()
        for i in range(self.n):
            rt.step(f"Feeding cap {i+1}/{self.n} from autosampler")
            rcp["autosampler"].above(anchor="plate_center")
            try:
                rcp["autosampler"].present_cap(rcp["inspector"])
                rcp["autosampler"].pick(approach=False)
                rcp[cfg.cap_feeder[i][0]].place(cfg.cap_feeder[i][1])
            except RecipeError:
                rt.step(f"Cap {i+1} feed skipped — autosampler empty", level="warning")
        rcp["feeder_tool"].place()

        elapsed = time.time() - shake_start
        remaining = cfg.shake_duration - elapsed
        if remaining > 0:
            rt.step(f"Waiting {remaining:.0f}s for shakers to finish")
            rt.delay(remaining)

        for i in range(self.n):
            rcp[cfg.shaker_slots[i][0]].stop_shaking()

    def phase5a_retrieve_decap(self):
        """Retrieve from shakers, inspect, decap, return to working rack."""
        rt, rcp, cfg = self.rt, self.rcp, self.cfg
        rt.step("Phase 5a — Retrieving from shakers and decapping")
        rcp["gripper"].pick()
        for i in range(self.n):
            rt.step(f"Retrieving tube {i+1}/{self.n} from shaker")
            rcp[cfg.shaker_slots[i][0]].pick(cfg.shaker_slots[i][1])
            self.inspect_tube()
            rcp["decapper_5"].place(exit=False)
            rcp["decapper_5"].decap(approach=False)
            rcp[cfg.cap_holder[i]].place()
            rcp["decapper_5"].pick()
            rcp[cfg.working[i][0]].place(cfg.working[i][1])
        rcp["gripper"].place()

    def phase5b_dose_2ml(self):
        """Dose from 40 ml working tubes into 2 ml vials."""
        rt, rcp, cfg = self.rt, self.rcp, self.cfg
        rt.step("Phase 5b — Dosing into 2 ml vials")
        rcp["needle"].pick()
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
        rcp["needle"].place()

    def phase5c_recap_return(self):
        """Re-cap 40 ml tubes and return to source rack."""
        rt, rcp, cfg = self.rt, self.rcp, self.cfg
        rt.step("Phase 5c — Re-capping and returning 40 ml tubes")
        rcp["gripper"].pick()
        for i in range(self.n):
            rt.step(f"Re-capping tube {i+1}/{self.n}")
            rcp[cfg.working[i][0]].pick(cfg.working[i][1])
            rcp["decapper_5"].place()
            rcp[cfg.cap_holder[i]].pick()
            rcp["decapper_5"].cap(exit=False)
            rcp["decapper_5"].pick(approach=False)
            rcp[cfg.source[i][0]].place(cfg.source[i][1])
        rcp["gripper"].place()

    def phase6_cap_inspect(self):
        """Cap, vibrate, inspect and return 2 ml vials."""
        rt, rcp, cfg = self.rt, self.rcp, self.cfg
        rt.step("Phase 6 — Capping and inspecting 2 ml vials")
        rcp["gripper_2ml"].pick()
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
        rcp["gripper_2ml"].place()

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
