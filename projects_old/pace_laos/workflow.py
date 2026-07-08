# workflow.py — grouped style
# Only handler registrations and phase methods live here.
# Everything else is in BaseWorkflow.

import time
from pathlib import Path

from workspace.rl.workflow import BaseWorkflow
from workspace.recipes.recipe import RecipeError

_BASE_DIR = Path(__file__).parent


class Workflow(BaseWorkflow):

    def __init__(self, workspace, core):
        super().__init__(workspace, core, _BASE_DIR, n_items=4)

    def _register_all(self):
        self.runner.register("inspected",      self._inspected)
        self.runner.register("dosed_40ml",     self._dosed_40ml)
        self.runner.register("loaded_shaker",  self._loaded_shaker)
        self.runner.register("shaken",         self._shaken)
        self.runner.register("cap_fed",        self._cap_fed)
        self.runner.register("retrieved",      self._retrieved)
        self.runner.register("dosed_2ml",      self._dosed_2ml)
        self.runner.register("recapped_final", self._recapped_final)
        self.runner.register("capped_2ml",     self._capped_2ml)

    # ── Shared helpers ───────────────────────────────────────────────────────

    def _inspect_tube(self):
        self.rcp["inspector"].present(approach=True)
        for _ in range(self.cfg.inspection_frq):
            self.rcp["inspector"].rotate(rotation=self.cfg.inspection_rot)

    def _rinse_needle(self):
        rcp, cfg = self.rcp, self.cfg
        for j in range(len(cfg.dosing_clean)):
            rcp[cfg.dosing_clean[j][0]].immerse(dist=cfg.immerse_40ml_dist, anchor=cfg.dosing_clean[j][1])
            rcp[cfg.dosing_clean[j][0]].dispense(vol=10)
            rcp[cfg.dosing_clean[j][0]].retract(dist=cfg.retract_40ml_dist, anchor=cfg.dosing_clean[j][1])
            rcp[cfg.dosing_waste[0][0]].immerse(dist=cfg.immerse_40ml_dist, anchor=cfg.dosing_waste[0][1], padding=10)
            rcp[cfg.dosing_waste[0][0]].dispense(vol=10)
            rcp[cfg.dosing_waste[0][0]].retract(dist=cfg.retract_40ml_dist, anchor=cfg.dosing_waste[0][1])

    # ── State handlers ───────────────────────────────────────────────────────

    def _inspected(self, i: int):
        rcp, cfg = self.rcp, self.cfg
        self._ensure_tool("gripper")
        rcp[cfg.source[i][0]].pick(cfg.source[i][1])
        self._inspect_tube()
        rcp["scale"].place("place")
        rcp["scale"].weight()
        rcp["scale"].pick("place")
        rcp["decapper_5"].place(exit=False)
        rcp["decapper_5"].decap(approach=False)
        rcp[cfg.cap_holder[i]].place()
        rcp["decapper_5"].pick()
        rcp[cfg.working[i][0]].place(cfg.working[i][1])

    def _dosed_40ml(self, i: int):
        rcp, cfg = self.rcp, self.cfg
        self._ensure_tool("needle")
        rcp[cfg.dosing_40ml[i][0]].immerse(dist=cfg.immerse_40ml_dist, anchor=cfg.dosing_40ml[i][1])
        rcp[cfg.dosing_40ml[i][0]].dispense(vol=10)
        rcp[cfg.dosing_40ml[i][0]].retract(dist=cfg.retract_40ml_dist, anchor=cfg.dosing_40ml[i][1])
        self._rinse_needle()

    def _loaded_shaker(self, i: int):
        rcp, cfg = self.rcp, self.cfg
        self._ensure_tool("gripper")
        rcp[cfg.working[i][0]].pick(cfg.working[i][1])
        rcp["decapper_5"].place()
        rcp[cfg.cap_holder[i]].pick()
        rcp["decapper_5"].cap(exit=False)
        rcp["decapper_5"].pick(approach=False)
        rcp[cfg.shaker_slots[i][0]].place(cfg.shaker_slots[i][1])

    def _shaken(self):
        rcp, cfg, rt = self.rcp, self.cfg, self.rt
        for i in range(self.n):
            rcp[cfg.shaker_slots[i][0]].shake(duration=cfg.shake_duration)
        shake_start = time.time()

        self._ensure_tool("feeder_tool")
        for i in range(self.n):
            rt.step(f"Feeding cap {i+1}/{self.n} from autosampler")
            rcp["autosampler"].above(anchor="plate_center")
            try:
                rcp["autosampler"].present_cap(rcp["inspector"])
                rcp["autosampler"].pick(approach=False)
                rcp[cfg.cap_feeder[i][0]].place(cfg.cap_feeder[i][1])
            except RecipeError:
                rt.step(f"Cap {i+1} feed skipped — autosampler empty", level="warning")

        remaining = cfg.shake_duration - (time.time() - shake_start)
        if remaining > 0:
            rt.step(f"Waiting {remaining:.0f}s for shakers to finish")
            rt.delay(remaining)

        for i in range(self.n):
            rcp[cfg.shaker_slots[i][0]].stop_shaking()

    def _cap_fed(self, i: int):
        pass

    def _retrieved(self, i: int):
        rcp, cfg = self.rcp, self.cfg
        self._ensure_tool("gripper")
        rcp[cfg.shaker_slots[i][0]].pick(cfg.shaker_slots[i][1])
        self._inspect_tube()
        rcp["decapper_5"].place(exit=False)
        rcp["decapper_5"].decap(approach=False)
        rcp[cfg.cap_holder[i]].place()
        rcp["decapper_5"].pick()
        rcp[cfg.working[i][0]].place(cfg.working[i][1])

    def _dosed_2ml(self, i: int):
        rcp, cfg = self.rcp, self.cfg
        self._ensure_tool("needle")
        rcp[cfg.dosing_40ml[i][0]].immerse(dist=cfg.immerse_40ml_dist, anchor=cfg.dosing_40ml[i][1], padding=150)
        rcp[cfg.dosing_40ml[i][0]].dispense(vol=10)
        rcp[cfg.dosing_40ml[i][0]].retract(dist=cfg.retract_40ml_dist, anchor=cfg.dosing_40ml[i][1])
        rcp[cfg.dosing_2ml_middle[i][0]].immerse(dist=cfg.immerse_2ml_dist, anchor=cfg.dosing_2ml_middle[i][1])
        rcp[cfg.dosing_2ml_middle[i][0]].dispense(vol=10)
        rcp[cfg.dosing_2ml_middle[i][0]].retract(dist=cfg.retract_2ml_dist, anchor=cfg.dosing_2ml_middle[i][1])
        rcp[cfg.dosing_2ml_end[i][0]].immerse(dist=cfg.immerse_2ml_dist, anchor=cfg.dosing_2ml_end[i][1])
        rcp[cfg.dosing_2ml_end[i][0]].dispense(vol=10)
        rcp[cfg.dosing_2ml_end[i][0]].retract(dist=cfg.retract_2ml_dist, anchor=cfg.dosing_2ml_end[i][1])
        self._rinse_needle()

    def _recapped_final(self, i: int):
        rcp, cfg = self.rcp, self.cfg
        self._ensure_tool("gripper")
        rcp[cfg.working[i][0]].pick(cfg.working[i][1])
        rcp["decapper_5"].place()
        rcp[cfg.cap_holder[i]].pick()
        rcp["decapper_5"].cap(exit=False)
        rcp["decapper_5"].pick(approach=False)
        rcp[cfg.source[i][0]].place(cfg.source[i][1])

    def _capped_2ml(self, i: int):
        rcp, cfg = self.rcp, self.cfg
        self._ensure_tool("gripper_2ml")
        rcp[cfg.rack_2ml_end[i][0]].pick(cfg.rack_2ml_end[i][1])
        rcp["decapper_5"].place()
        rcp[cfg.cap_feeder[i][0]].pick(cfg.cap_feeder[i][1])
        rcp["decapper_5"].cap(exit=False)
        rcp["decapper_5"].pick(approach=False)
        rcp["decapper_5"].vibrate()
        self._inspect_tube()
        rcp[cfg.rack_2ml_end[i][0]].place(cfg.rack_2ml_end[i][1])


def workflow_fn(*, workspace, core):
    wf = Workflow(workspace, core)
    wf.run()
