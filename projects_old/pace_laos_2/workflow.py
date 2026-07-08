# workflow.py — grouped states
# Each handler contains multiple recipe calls (a full phase).
# Tool swaps handled by _with("current_tool", ...).

import time
from pathlib import Path
from workspace.rl.workflow import BaseWorkflow
from workspace.recipes.recipe import RecipeError

_BASE_DIR = Path(__file__).parent


class Workflow(BaseWorkflow):

    def __init__(self, workspace, core):
        super().__init__(workspace, core, _BASE_DIR, n_items=4)

    def _register_all(self):
        r   = self.runner.register
        rcp = self.rcp
        cfg = self.cfg
        rt  = self.rt

        # ── Enum enforcement ──────────────────────────────────────────────
        def swap_tool(old, new):
            if old:
                rcp[old].place()
            if new:
                rcp[new].pick()

        self.on_enum_change("current_tool", handler=swap_tool)

        def tool(value, fn):
            return self._with("current_tool", value, fn)

        G  = "gripper"
        N  = "needle"
        F  = "feeder_tool"
        G2 = "gripper_2ml"

        # ── Shared helpers ────────────────────────────────────────────────
        def _inspect_tube():
            rcp["inspector"].present(approach=True)
            for _ in range(cfg.inspection_frq):
                rcp["inspector"].rotate(rotation=cfg.inspection_rot)

        def _rinse_needle():
            for j in range(len(cfg.dosing_clean)):
                rcp[cfg.dosing_clean[j][0]].immerse(dist=cfg.immerse_40ml_dist, anchor=cfg.dosing_clean[j][1])
                rcp[cfg.dosing_clean[j][0]].dispense(vol=10)
                rcp[cfg.dosing_clean[j][0]].retract(dist=cfg.retract_40ml_dist, anchor=cfg.dosing_clean[j][1])
                rcp[cfg.dosing_waste[0][0]].immerse(dist=cfg.immerse_40ml_dist, anchor=cfg.dosing_waste[0][1], padding=10)
                rcp[cfg.dosing_waste[0][0]].dispense(vol=10)
                rcp[cfg.dosing_waste[0][0]].retract(dist=cfg.retract_40ml_dist, anchor=cfg.dosing_waste[0][1])

        # ── Phase handlers ────────────────────────────────────────────────

        def inspected(i):
            rcp[cfg.source[i][0]].pick(cfg.source[i][1])
            _inspect_tube()
            rcp["scale"].place("place")
            rcp["scale"].weight()
            rcp["scale"].pick("place")
            rcp["decapper_5"].place(exit=False)
            rcp["decapper_5"].decap(approach=False)
            rcp[cfg.cap_holder[i]].place()
            rcp["decapper_5"].pick()
            rcp[cfg.working[i][0]].place(cfg.working[i][1])

        def dosed_40ml(i):
            rcp[cfg.dosing_40ml[i][0]].immerse(dist=cfg.immerse_40ml_dist, anchor=cfg.dosing_40ml[i][1])
            rcp[cfg.dosing_40ml[i][0]].dispense(vol=10)
            rcp[cfg.dosing_40ml[i][0]].retract(dist=cfg.retract_40ml_dist, anchor=cfg.dosing_40ml[i][1])
            _rinse_needle()

        def loaded_shaker(i):
            rcp[cfg.working[i][0]].pick(cfg.working[i][1])
            rcp["decapper_5"].place()
            rcp[cfg.cap_holder[i]].pick()
            rcp["decapper_5"].cap(exit=False)
            rcp["decapper_5"].pick(approach=False)
            rcp[cfg.shaker_slots[i][0]].place(cfg.shaker_slots[i][1])

        def shaken(_):
            for i in range(self.n):
                rcp[cfg.shaker_slots[i][0]].shake(duration=cfg.shake_duration)
            shake_start = time.time()
            # RL fills idle time with cap_fed actions between here and retrieved
            remaining = cfg.shake_duration - (time.time() - shake_start)
            if remaining > 0:
                rt.step(f"Waiting {remaining:.0f}s for shakers to finish")
                rt.delay(remaining)
            for i in range(self.n):
                rcp[cfg.shaker_slots[i][0]].stop_shaking()

        def cap_fed(i):
            rt.step(f"Feeding cap {i+1}/{self.n} from autosampler")
            rcp["autosampler"].above(anchor="plate_center")
            rcp["autosampler"].present_cap(rcp["inspector"])
            rcp["autosampler"].pick(approach=False)
            rcp[cfg.cap_feeder[i][0]].place(cfg.cap_feeder[i][1])

        def retrieved(i):
            rcp[cfg.shaker_slots[i][0]].pick(cfg.shaker_slots[i][1])
            _inspect_tube()
            rcp["decapper_5"].place(exit=False)
            rcp["decapper_5"].decap(approach=False)
            rcp[cfg.cap_holder[i]].place()
            rcp["decapper_5"].pick()
            rcp[cfg.working[i][0]].place(cfg.working[i][1])

        def dosed_2ml(i):
            rcp[cfg.dosing_40ml[i][0]].immerse(dist=cfg.immerse_40ml_dist, anchor=cfg.dosing_40ml[i][1], padding=150)
            rcp[cfg.dosing_40ml[i][0]].dispense(vol=10)
            rcp[cfg.dosing_40ml[i][0]].retract(dist=cfg.retract_40ml_dist, anchor=cfg.dosing_40ml[i][1])
            rcp[cfg.dosing_2ml_middle[i][0]].immerse(dist=cfg.immerse_2ml_dist, anchor=cfg.dosing_2ml_middle[i][1])
            rcp[cfg.dosing_2ml_middle[i][0]].dispense(vol=10)
            rcp[cfg.dosing_2ml_middle[i][0]].retract(dist=cfg.retract_2ml_dist, anchor=cfg.dosing_2ml_middle[i][1])
            rcp[cfg.dosing_2ml_end[i][0]].immerse(dist=cfg.immerse_2ml_dist, anchor=cfg.dosing_2ml_end[i][1])
            rcp[cfg.dosing_2ml_end[i][0]].dispense(vol=10)
            rcp[cfg.dosing_2ml_end[i][0]].retract(dist=cfg.retract_2ml_dist, anchor=cfg.dosing_2ml_end[i][1])
            _rinse_needle()

        def recapped_final(i):
            rcp[cfg.working[i][0]].pick(cfg.working[i][1])
            rcp["decapper_5"].place()
            rcp[cfg.cap_holder[i]].pick()
            rcp["decapper_5"].cap(exit=False)
            rcp["decapper_5"].pick(approach=False)
            rcp[cfg.source[i][0]].place(cfg.source[i][1])

        def capped_2ml(i):
            rcp[cfg.rack_2ml_end[i][0]].pick(cfg.rack_2ml_end[i][1])
            rcp["decapper_5"].place()
            rcp[cfg.cap_feeder[i][0]].pick(cfg.cap_feeder[i][1])
            rcp["decapper_5"].cap(exit=False)
            rcp["decapper_5"].pick(approach=False)
            rcp["decapper_5"].vibrate()
            _inspect_tube()
            rcp[cfg.rack_2ml_end[i][0]].place(cfg.rack_2ml_end[i][1])

        # ── Register ──────────────────────────────────────────────────────
        r("inspected",      tool(G,  inspected))
        r("dosed_40ml",     tool(N,  dosed_40ml))
        r("loaded_shaker",  tool(G,  loaded_shaker))
        r("shaken",         tool(F,  shaken))
        r("cap_fed",        tool(F,  cap_fed))
        r("retrieved",      tool(G,  retrieved))
        r("dosed_2ml",      tool(N,  dosed_2ml))
        r("recapped_final", tool(G,  recapped_final))
        r("capped_2ml",     tool(G2, capped_2ml))


def workflow_fn(*, workspace, core):
    wf = Workflow(workspace, core)
    wf.run()
