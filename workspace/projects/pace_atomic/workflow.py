# workflow.py — atomic style
# Only handler registrations live here. Everything else is in BaseWorkflow.

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

        # ── Enum enforcement ────────────────────────────────────────────────
        # "current_tool" must match the constraint name in constraints.yaml.
        # values (gripper, needle, etc.) must match aliases in recipes.yaml.
        def swap_tool(old, new):
            if old:
                rcp[old].place()
            if new:
                rcp[new].pick()

        self.on_enum_change("current_tool", handler=swap_tool)

        # shorthand for _with("current_tool", value, fn)
        def tool(value, fn):
            return self._with("current_tool", value, fn)

        G  = "gripper"
        N  = "needle"
        F  = "feeder_tool"
        G2 = "gripper_2ml"

        # ── Inspect & decap ──────────────────────────────────────────────────
        r("source_rack.pick",   tool(G,  lambda i: rcp[cfg.source[i][0]].pick(cfg.source[i][1])))
        r("inspector.present",  tool(G,  lambda i: rcp["inspector"].present(approach=True)))
        r("scale.place",        tool(G,  lambda i: rcp["scale"].place("place")))
        r("scale.weight",       tool(G,  lambda i: rcp["scale"].weight()))
        r("scale.pick",         tool(G,  lambda i: rcp["scale"].pick("place")))
        r("decapper.place",     tool(G,  lambda i: rcp["decapper_5"].place(exit=False)))
        r("decapper.decap",     tool(G,  lambda i: rcp["decapper_5"].decap(approach=False)))
        r("cap_holder.place",   tool(G,  lambda i: rcp[cfg.cap_holder[i]].place()))
        r("decapper.pick",      tool(G,  lambda i: rcp["decapper_5"].pick()))
        r("working_rack.place", tool(G,  lambda i: rcp[cfg.working[i][0]].place(cfg.working[i][1])))

        # ── Dose 40ml ────────────────────────────────────────────────────────
        r("doser_40ml.immerse",  tool(N, lambda i: rcp[cfg.dosing_40ml[i][0]].immerse(dist=cfg.immerse_40ml_dist, anchor=cfg.dosing_40ml[i][1])))
        r("doser_40ml.dispense", tool(N, lambda i: rcp[cfg.dosing_40ml[i][0]].dispense(vol=10)))
        r("doser_40ml.retract",  tool(N, lambda i: rcp[cfg.dosing_40ml[i][0]].retract(dist=cfg.retract_40ml_dist, anchor=cfg.dosing_40ml[i][1])))

        # ── Load shaker ─────────────────────────────────────────────────────
        r("working_rack.pick",   tool(G, lambda i: rcp[cfg.working[i][0]].pick(cfg.working[i][1])))
        r("decapper.place_cap",  tool(G, lambda i: rcp["decapper_5"].place()))
        r("cap_holder.pick",     tool(G, lambda i: rcp[cfg.cap_holder[i]].pick()))
        r("decapper.cap",        tool(G, lambda i: rcp["decapper_5"].cap(exit=False)))
        r("decapper.pick_cap",   tool(G, lambda i: rcp["decapper_5"].pick(approach=False)))
        r("shaker.place",        tool(G, lambda i: rcp[cfg.shaker_slots[i][0]].place(cfg.shaker_slots[i][1])))

        # ── Shake (background) ───────────────────────────────────────────────
        r("shaker.shake", lambda _: [rcp[cfg.shaker_slots[i][0]].shake(duration=cfg.shake_duration) for i in range(self.n)])

        # ── Feed caps (optional) ─────────────────────────────────────────────
        r("autosampler.above",       tool(F, lambda i: rcp["autosampler"].above(anchor="plate_center")))
        r("autosampler.present_cap", tool(F, lambda i: rcp["autosampler"].present_cap(rcp["inspector"])))
        r("autosampler.pick",        tool(F, lambda i: rcp["autosampler"].pick(approach=False)))
        r("cap_feeder.place",        tool(F, lambda i: rcp[cfg.cap_feeder[i][0]].place(cfg.cap_feeder[i][1])))

        # ── Retrieve & decap ─────────────────────────────────────────────────
        r("shaker.pick",          tool(G, lambda i: rcp[cfg.shaker_slots[i][0]].pick(cfg.shaker_slots[i][1])))
        r("inspector.present_2",  tool(G, lambda i: rcp["inspector"].present(approach=True)))
        r("decapper.place_2",     tool(G, lambda i: rcp["decapper_5"].place(exit=False)))
        r("decapper.decap_2",     tool(G, lambda i: rcp["decapper_5"].decap(approach=False)))
        r("cap_holder.place_2",   tool(G, lambda i: rcp[cfg.cap_holder[i]].place()))
        r("decapper.pick_2",      tool(G, lambda i: rcp["decapper_5"].pick()))
        r("working_rack.place_2", tool(G, lambda i: rcp[cfg.working[i][0]].place(cfg.working[i][1])))

        # ── Dose 2ml ─────────────────────────────────────────────────────────
        r("doser_40ml.immerse_2",       tool(N, lambda i: rcp[cfg.dosing_40ml[i][0]].immerse(dist=cfg.immerse_40ml_dist, anchor=cfg.dosing_40ml[i][1], padding=150)))
        r("doser_40ml.dispense_2",      tool(N, lambda i: rcp[cfg.dosing_40ml[i][0]].dispense(vol=10)))
        r("doser_40ml.retract_2",       tool(N, lambda i: rcp[cfg.dosing_40ml[i][0]].retract(dist=cfg.retract_40ml_dist, anchor=cfg.dosing_40ml[i][1])))
        r("doser_2ml_middle.immerse",   tool(N, lambda i: rcp[cfg.dosing_2ml_middle[i][0]].immerse(dist=cfg.immerse_2ml_dist, anchor=cfg.dosing_2ml_middle[i][1])))
        r("doser_2ml_middle.dispense",  tool(N, lambda i: rcp[cfg.dosing_2ml_middle[i][0]].dispense(vol=10)))
        r("doser_2ml_middle.retract",   tool(N, lambda i: rcp[cfg.dosing_2ml_middle[i][0]].retract(dist=cfg.retract_2ml_dist, anchor=cfg.dosing_2ml_middle[i][1])))
        r("doser_2ml_end.immerse",      tool(N, lambda i: rcp[cfg.dosing_2ml_end[i][0]].immerse(dist=cfg.immerse_2ml_dist, anchor=cfg.dosing_2ml_end[i][1])))
        r("doser_2ml_end.dispense",     tool(N, lambda i: rcp[cfg.dosing_2ml_end[i][0]].dispense(vol=10)))
        r("doser_2ml_end.retract",      tool(N, lambda i: rcp[cfg.dosing_2ml_end[i][0]].retract(dist=cfg.retract_2ml_dist, anchor=cfg.dosing_2ml_end[i][1])))

        # ── Recap & return 40ml ──────────────────────────────────────────────
        r("working_rack.pick_2",   tool(G,  lambda i: rcp[cfg.working[i][0]].pick(cfg.working[i][1])))
        r("decapper.place_final",  tool(G,  lambda i: rcp["decapper_5"].place()))
        r("cap_holder.pick_2",     tool(G,  lambda i: rcp[cfg.cap_holder[i]].pick()))
        r("decapper.cap_final",    tool(G,  lambda i: rcp["decapper_5"].cap(exit=False)))
        r("decapper.pick_final",   tool(G,  lambda i: rcp["decapper_5"].pick(approach=False)))
        r("source_rack.place",     tool(G,  lambda i: rcp[cfg.source[i][0]].place(cfg.source[i][1])))

        # ── Cap & inspect 2ml ────────────────────────────────────────────────
        r("rack_2ml.pick",        tool(G2, lambda i: rcp[cfg.rack_2ml_end[i][0]].pick(cfg.rack_2ml_end[i][1])))
        r("decapper.place_2ml",   tool(G2, lambda i: rcp["decapper_5"].place()))
        r("cap_feeder.pick",      tool(G2, lambda i: rcp[cfg.cap_feeder[i][0]].pick(cfg.cap_feeder[i][1])))
        r("decapper.cap_2ml",     tool(G2, lambda i: rcp["decapper_5"].cap(exit=False)))
        r("decapper.pick_2ml",    tool(G2, lambda i: rcp["decapper_5"].pick(approach=False)))
        r("decapper.vibrate",     tool(G2, lambda i: rcp["decapper_5"].vibrate()))
        r("inspector.present_3",  tool(G2, lambda i: rcp["inspector"].present(approach=True)))
        r("rack_2ml.place",       tool(G2, lambda i: rcp[cfg.rack_2ml_end[i][0]].place(cfg.rack_2ml_end[i][1])))


def workflow_fn(*, workspace, core):
    wf = Workflow(workspace, core)
    wf.run()
