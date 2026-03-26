# pace_or/steps.py
# All handler functions for the PACE protocol.
# Receives rcp, cfg, rt, n — no workflow or runner knowledge.
#
# Returns a dict: {state_name: handler_fn}
# Also returns cleanup dict: {state_name: cleanup_fn} for background states.

def make_steps(rcp, cfg, rt, n):
    """
    Build all step handlers.

    Args:
        rcp: recipe dict  (from BaseWorkflow.rcp)
        cfg: params       (from BaseWorkflow.cfg — params.yaml)
        rt:  runtime
        n:   number of items

    Returns:
        steps:   {state_name: fn(item_i)}
        cleanup: {state_name: fn()} — called after background timer expires
    """

    # ── Shared helpers ────────────────────────────────────────────────────────

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

    # ── Step handlers ─────────────────────────────────────────────────────────

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
        # Start shakers only — runner handles the wait and calls stop_shaken()
        for i in range(n):
            rcp[cfg.shaker_slots[i][0]].shake(duration=cfg.shake_duration)

    def stop_shaken():
        for i in range(n):
            rcp[cfg.shaker_slots[i][0]].stop_shaking()

    def cap_fed(i):
        rt.step(f"Feeding cap {i+1}/{n} from autosampler")
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

    # ── Return ────────────────────────────────────────────────────────────────

    steps = {
        "inspected":      inspected,
        "dosed_40ml":     dosed_40ml,
        "loaded_shaker":  loaded_shaker,
        "shaken":         shaken,
        "cap_fed":        cap_fed,
        "retrieved":      retrieved,
        "dosed_2ml":      dosed_2ml,
        "recapped_final": recapped_final,
        "capped_2ml":     capped_2ml,
    }

    cleanup = {
        "shaken": stop_shaken,
    }

    return steps, cleanup
