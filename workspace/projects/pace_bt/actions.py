"""pace_bt protocol — faithful BT port of pace_or's PACE protocol.

Framework reference: ../../../docs/bt-framework-guide.md
"""

from __future__ import annotations

from workspace.bt import Action, predicate


# ── 1. Predicates ──────────────────────────────────────────────────────────

in_source        = predicate("in_source")        # 40ml tube in source rack
in_working       = predicate("in_working")       # 40ml tube in working rack
in_shaker        = predicate("in_shaker")        # 40ml tube on shaker
in_done          = predicate("in_done")          # 40ml tube back in source rack (done)
has_cap          = predicate("has_cap")          # 40ml has cap on
weighed          = predicate("weighed")          # weighed at inspected
dosed_40ml       = predicate("dosed_40ml")       # solvent dispensed into 40ml
shaken           = predicate("shaken")           # tube has been shaken
dosed_2ml        = predicate("dosed_2ml")        # transferred to 2ml vials
cap_in_holder    = predicate("cap_in_holder")    # autosampler-fed cap waiting
vial_2ml_capped  = predicate("vial_2ml_capped")  # 2ml vial capped and placed


# ── 2. setup — operator kwargs → planning inputs ───────────────────────────


def setup(**kwargs):
    """kwargs: batch_size (no upper bound — framework slices to ``plan_window``)."""
    batch_size = int(kwargs.get("batch_size", 1))
    tubes = list(range(batch_size))

    facts = set()
    for t in tubes:
        facts.add((in_source.name, t))
        facts.add((has_cap.name, t))

    def item_done(state, tube):
        return ((in_done.name, tube) in state
                and (vial_2ml_capped.name, tube) in state)

    def goal(state):
        return all(item_done(state, t) for t in tubes)

    # `shaken(t)` is added by a state-aware eff (ShakerOne/Two), so
    # auto-derivation can't see it — list per-tube progress markers
    # explicitly. (Framework's monotonic-probe limitation, see guide.)
    progress_preds = (
        weighed, dosed_40ml, shaken, dosed_2ml,
        in_done, vial_2ml_capped,
    )
    goal_facts = frozenset(
        (p.name, t) for p in progress_preds for t in tubes
    )

    return {
        "initial_facts": frozenset(facts),
        "goal":          goal,
        "item_done":     item_done,
        "goal_facts":    goal_facts,
        "objects":       {"tube": tubes},
    }


# ── 3. Slot tables — copied verbatim from pace_or/states.py ────────────────

SOURCE = [
    ("source_rack", "A1"),
    ("source_rack", "A2"),
    ("source_rack", "A3"),
    ("source_rack", "A4"),
]

WORKING = [
    ("working_rack", "B1"),
    ("working_rack", "B2"),
    ("working_rack", "B3"),
    ("working_rack", "B4"),
]

CAP_HOLDER = [
    "decapper_1",
    "decapper_2",
    "decapper_3",
    "decapper_4",
]

SHAKER_SLOTS = [
    ("shaker_1", "A1"),
    ("shaker_1", "A2"),
    ("shaker_2", "A1"),
    ("shaker_2", "A2"),
]

CAP_FEEDER = [
    ("cap_holder", "A1"),
    ("cap_holder", "A2"),
    ("cap_holder", "A3"),
    ("cap_holder", "A4"),
]

DOSING_40ML = [
    ("doser_40ml", "B1"),
    ("doser_40ml", "B2"),
    ("doser_40ml", "B3"),
    ("doser_40ml", "B4"),
]

DOSING_CLEAN = [
    ("doser_40ml", "A1"),
    ("doser_40ml", "A2"),
    ("doser_40ml", "A3"),
]

DOSING_WASTE = [
    ("doser_40ml", "A4"),
]

DOSING_2ML_END = [
    ("doser_2ml_end", "A1"),
    ("doser_2ml_end", "A2"),
    ("doser_2ml_end", "A3"),
    ("doser_2ml_end", "A4"),
]

DOSING_2ML_MIDDLE = [
    ("doser_2ml_middle", "A1"),
    ("doser_2ml_middle", "A2"),
    ("doser_2ml_middle", "A3"),
    ("doser_2ml_middle", "A4"),
]

RACK_2ML_END = [
    ("rack_2ml_end", "A1"),
    ("rack_2ml_end", "A2"),
    ("rack_2ml_end", "A3"),
    ("rack_2ml_end", "A4"),
]

# Run parameters
INSPECTION_FRQ      = 4
INSPECTION_ROT      = 90
IMMERSE_40ML_DIST   = 90
RETRACT_40ML_DIST   = 10
IMMERSE_2ML_DIST    = 25
RETRACT_2ML_DIST    = 10
SHAKE_DURATION      = 10


# ── 4. Action helpers — used by multiple Action.execute() bodies ───────────


def _inspect_tube(rcp):
    """Visual inspection — present to camera and rotate."""
    rcp["inspector"].present(approach=True)
    for _ in range(INSPECTION_FRQ):
        rcp["inspector"].rotate(rotation=INSPECTION_ROT)


def _rinse_needle(rcp):
    """Clean the needle by dispensing into clean → waste positions."""
    for clean in DOSING_CLEAN:
        rcp[clean[0]].immerse(dist=IMMERSE_40ML_DIST, anchor=clean[1])
        rcp[clean[0]].dispense(vol=10)
        rcp[clean[0]].retract(dist=RETRACT_40ML_DIST, anchor=clean[1])
        waste = DOSING_WASTE[0]
        rcp[waste[0]].immerse(dist=IMMERSE_40ML_DIST, anchor=waste[1], padding=10)
        rcp[waste[0]].dispense(vol=10)
        rcp[waste[0]].retract(dist=RETRACT_40ML_DIST, anchor=waste[1])


# ── 5. Actions — one per pace_or state ─────────────────────────────────────


class Inspected(Action):
    """Pick from source, visual inspect, weigh, decap, place in working rack."""
    params      = ["tube"]
    duration    = 10
    resource    = "robot"
    tool        = "gripper"
    pre_check   = "source_tube_present"
    post_check  = "tube_in_working_rack"

    def pre(self, tube):
        return in_source(tube) & has_cap(tube) & ~weighed(tube)

    def eff(self, tube):
        return {"inspected": (
            -in_source(tube), +in_working(tube),
            -has_cap(tube), +weighed(tube),
        )}

    def execute(self, tube):
        rcp = self.ctx.recipes
        rcp[SOURCE[tube][0]].pick(SOURCE[tube][1])
        _inspect_tube(rcp)
        rcp["scale"].place("place")
        rcp["scale"].weight()
        rcp["scale"].pick("place")
        rcp["decapper_5"].place(exit=False)
        rcp["decapper_5"].decap(approach=False)
        rcp[CAP_HOLDER[tube]].place()
        rcp["decapper_5"].pick()
        rcp[WORKING[tube][0]].place(WORKING[tube][1])
        return "inspected"


class Dosed40ml(Action):
    """Dose solvent into the working 40ml tube. Rinses needle after."""
    params      = ["tube"]
    duration    = 10
    resource    = "robot"
    tool        = "needle"
    pre_check   = "tube_in_working_rack"

    def pre(self, tube):
        return in_working(tube) & ~has_cap(tube) & ~dosed_40ml(tube)

    def eff(self, tube):
        return {"dosed_40ml": +dosed_40ml(tube)}

    def execute(self, tube):
        rcp = self.ctx.recipes
        site = DOSING_40ML[tube]
        rcp[site[0]].immerse(dist=IMMERSE_40ML_DIST, anchor=site[1])
        rcp[site[0]].dispense(vol=10)
        rcp[site[0]].retract(dist=RETRACT_40ML_DIST, anchor=site[1])
        _rinse_needle(rcp)
        return "dosed_40ml"


class LoadedShaker(Action):
    """Recap tube and place on shaker."""
    params      = ["tube"]
    duration    = 10
    resource    = "robot"
    tool        = "gripper"
    pre_check   = ["shaker_slot_empty", "tube_in_working_rack"]
    post_check  = "tube_on_shaker"

    def pre(self, tube):
        return (
            in_working(tube)
            & ~has_cap(tube)
            & dosed_40ml(tube)
            & ~in_shaker(tube)
        )

    def eff(self, tube):
        return {"loaded": (
            -in_working(tube), +in_shaker(tube), +has_cap(tube),
        )}

    def execute(self, tube):
        rcp = self.ctx.recipes
        rcp[WORKING[tube][0]].pick(WORKING[tube][1])
        rcp["decapper_5"].place()
        rcp[CAP_HOLDER[tube]].pick()
        rcp["decapper_5"].cap(exit=False)
        rcp["decapper_5"].pick(approach=False)
        rcp[SHAKER_SLOTS[tube][0]].place(SHAKER_SLOTS[tube][1])
        return "loaded"


class ShakerCycleBase(Action):
    """Per-shaker shake — one mechanical shake() shakes every tube on
    the device. Subclasses fix SHAKER + resource.

    ``schedule = {"register": False}`` marks this as an abstract base:
    the framework skips registration so the planner never instantiates
    it directly, only its concrete subclasses (``ShakerOne``,
    ``ShakerTwo``)."""
    schedule    = {"register": False}
    SHAKER:    str = ""
    params      = []
    duration    = SHAKE_DURATION
    post_check  = "stop_shaken"

    def _destined_tubes(self):
        return [t for t in self._ctx_objects().get("tube", [])
                if SHAKER_SLOTS[t][0] == self.SHAKER]

    def pre(self):
        destined = self._destined_tubes()
        if not destined:
            return False
        if all((shaken.name, t) in self.state for t in destined):
            return False
        expr = in_shaker(destined[0]) & ~shaken(destined[0])
        for t in destined[1:]:
            expr = expr & in_shaker(t) & ~shaken(t)
        return expr

    def eff(self):
        return {"shaken": tuple(
            +shaken(t) for t in self._destined_tubes()
            if (in_shaker.name, t) in self.state
        )}

    def execute(self):
        self.ctx.recipes[self.SHAKER].shake(duration=SHAKE_DURATION)
        return "shaken"


class ShakerOne(ShakerCycleBase):
    SHAKER   = "shaker_1"
    resource = "shaker_1"


class ShakerTwo(ShakerCycleBase):
    SHAKER   = "shaker_2"
    resource = "shaker_2"


class CapFed(Action):
    """Feed one cap from autosampler to the cap-holder slot."""
    params      = ["tube"]
    duration    = 10
    resource    = "robot"
    tool        = "feeder_tool"
    pre_check   = "cap_holder_empty"

    def pre(self, tube):
        return ~cap_in_holder(tube)

    def eff(self, tube):
        return {"fed": +cap_in_holder(tube)}

    def execute(self, tube):
        rcp = self.ctx.recipes
        self.ctx.runtime.step(
            f"Feeding cap {tube + 1} from autosampler"
        )
        rcp["autosampler"].above(anchor="plate_center")
        rcp["autosampler"].present_cap(rcp["inspector"])
        rcp["autosampler"].pick(approach=False)
        rcp[CAP_FEEDER[tube][0]].place(CAP_FEEDER[tube][1])
        return "fed"


class Retrieved(Action):
    """Pick from shaker, visual inspect, decap, place in working rack."""
    params      = ["tube"]
    duration    = 10
    resource    = "robot"
    tool        = "gripper"
    pre_check   = "tube_on_shaker"
    post_check  = "tube_in_working_rack"

    def pre(self, tube):
        return in_shaker(tube) & shaken(tube) & has_cap(tube)

    def eff(self, tube):
        return {"retrieved": (
            -in_shaker(tube), +in_working(tube), -has_cap(tube),
        )}

    def execute(self, tube):
        rcp = self.ctx.recipes
        rcp[SHAKER_SLOTS[tube][0]].pick(SHAKER_SLOTS[tube][1])
        _inspect_tube(rcp)
        rcp["decapper_5"].place(exit=False)
        rcp["decapper_5"].decap(approach=False)
        rcp[CAP_HOLDER[tube]].place()
        rcp["decapper_5"].pick()
        rcp[WORKING[tube][0]].place(WORKING[tube][1])
        return "retrieved"


class Dosed2ml(Action):
    """Dose from the 40ml tube into 2ml vials (middle + end). Rinses needle."""
    params      = ["tube"]
    duration    = 10
    resource    = "robot"
    tool        = "needle"
    pre_check   = "tube_in_working_rack"

    def pre(self, tube):
        return (
            in_working(tube)
            & ~has_cap(tube)
            & shaken(tube)
            & ~dosed_2ml(tube)
        )

    def eff(self, tube):
        return {"dosed_2ml": +dosed_2ml(tube)}

    def execute(self, tube):
        rcp = self.ctx.recipes
        site_40 = DOSING_40ML[tube]
        rcp[site_40[0]].immerse(
            dist=IMMERSE_40ML_DIST, anchor=site_40[1], padding=150,
        )
        rcp[site_40[0]].dispense(vol=10)
        rcp[site_40[0]].retract(dist=RETRACT_40ML_DIST, anchor=site_40[1])
        mid = DOSING_2ML_MIDDLE[tube]
        rcp[mid[0]].immerse(dist=IMMERSE_2ML_DIST, anchor=mid[1])
        rcp[mid[0]].dispense(vol=10)
        rcp[mid[0]].retract(dist=RETRACT_2ML_DIST, anchor=mid[1])
        end = DOSING_2ML_END[tube]
        rcp[end[0]].immerse(dist=IMMERSE_2ML_DIST, anchor=end[1])
        rcp[end[0]].dispense(vol=10)
        rcp[end[0]].retract(dist=RETRACT_2ML_DIST, anchor=end[1])
        _rinse_needle(rcp)
        return "dosed_2ml"


class RecappedFinal(Action):
    """Recap 40ml tube and return to source rack — protocol end for the 40ml."""
    params      = ["tube"]
    duration    = 10
    resource    = "robot"
    tool        = "gripper"

    def pre(self, tube):
        return in_working(tube) & ~has_cap(tube) & dosed_2ml(tube)

    def eff(self, tube):
        return {"recapped": (
            -in_working(tube), +in_done(tube), +has_cap(tube),
        )}

    def execute(self, tube):
        rcp = self.ctx.recipes
        rcp[WORKING[tube][0]].pick(WORKING[tube][1])
        rcp["decapper_5"].place()
        rcp[CAP_HOLDER[tube]].pick()
        rcp["decapper_5"].cap(exit=False)
        rcp["decapper_5"].pick(approach=False)
        rcp[SOURCE[tube][0]].place(SOURCE[tube][1])
        return "recapped"


class Capped2ml(Action):
    """Cap the 2ml vial, visual inspect, place in rack."""
    params      = ["tube"]
    duration    = 10
    resource    = "robot"
    tool        = "gripper_2ml"
    pre_check   = "cap_in_holder"
    post_check  = "tube_in_2ml_rack"

    def pre(self, tube):
        return (
            cap_in_holder(tube)
            & dosed_2ml(tube)
            & ~vial_2ml_capped(tube)
        )

    def eff(self, tube):
        return {"capped": (
            -cap_in_holder(tube), +vial_2ml_capped(tube),
        )}

    def execute(self, tube):
        rcp = self.ctx.recipes
        rcp[RACK_2ML_END[tube][0]].pick(RACK_2ML_END[tube][1])
        rcp["decapper_5"].place()
        rcp[CAP_FEEDER[tube][0]].pick(CAP_FEEDER[tube][1])
        rcp["decapper_5"].cap(exit=False)
        rcp["decapper_5"].pick(approach=False)
        rcp["decapper_5"].vibrate()
        _inspect_tube(rcp)
        rcp[RACK_2ML_END[tube][0]].place(RACK_2ML_END[tube][1])
        return "capped"


# ── 6. End-trigger actions ─────────────────────────────────────────────────


class ParkTool(Action):
    """Release whatever tool is held — runs on operator End."""
    params      = []
    duration    = 5
    resource    = "robot"
    tool        = None
    trigger     = "end"

    def execute(self):
        return "none"
