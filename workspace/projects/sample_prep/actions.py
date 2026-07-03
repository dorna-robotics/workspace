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
started          = predicate("started")          # workspace initialised (global, no args)
parked           = predicate("parked")           # robot returned to park pose (global, no args)


# ── 2. setup — operator kwargs → planning inputs ───────────────────────────


def setup(**kwargs):
    """kwargs: batch_size (no upper bound — framework slices to ``plan_window``)."""
    batch_size = int(kwargs.get("batch_size", 1))
    tubes = list(range(batch_size))

    # Initial state is *empty* — no per-tube facts yet. ``Start.eff``
    # seeds ``in_source(t)`` + ``has_cap(t)`` for every tube after the
    # operator's initialisation runs. That gates the entire plan
    # behind Start without having to add ``& started`` to every other
    # action's ``pre``.
    facts: set = set()

    def item_done(state, tube):
        return ((in_done.name, tube) in state
                and (vial_2ml_capped.name, tube) in state)

    def goal(state):
        return (
            (started.name,) in state
            and all(item_done(state, t) for t in tubes)
            and (parked.name,) in state
        )

    # `shaken(t)` is added by a state-aware eff (ShakerOne/Two), so
    # auto-derivation can't see it — list per-tube progress markers
    # explicitly. (Framework's monotonic-probe limitation, see guide.)
    progress_preds = (
        weighed, dosed_40ml, shaken, dosed_2ml,
        in_done, vial_2ml_capped,
    )
    # Per-tube progress markers + the two global bookend goals.
    # ``Start.pre`` requires ``~started`` so it runs first;
    # ``Park.pre`` requires every tube done so it runs last.
    goal_facts = frozenset(
        [(p.name, t) for p in progress_preds for t in tubes]
        + [(started.name,), (parked.name,)]
    )

    return {
        "initial_facts": frozenset(facts),
        "goal":          goal,
        "item_done":     item_done,
        "goal_facts":    goal_facts,
        "objects":       {"tube": tubes},
    }


# ── 3. Slot tables — slicing-safe ──────────────────────────────────────────
#
# Two flavours, distinguished by whether a tube has a *permanent* home
# in the slot (persistent) or just *passes through* (transient).
#
#   Persistent (SOURCE, RACK_2ML_END): one slot per tube for the entire
#       protocol. Tables sized to the physical scene capacity (28 source
#       slots, 50 2ml-rack slots) so any ``batch_size`` up to that limit
#       just works.
#
#   Transient (WORKING, CAP_HOLDER, SHAKER_SLOTS, DOSING_*): tubes
#       occupy the slot only while the planner is acting on them in the
#       current slice. The slot is freed by the time the next slice
#       starts, so it can be reused. Wrapped in ``_ReusableSlots`` which
#       wraps ``[]`` indexing through ``% len(slots)`` — tube ``t`` and
#       tube ``t + N`` (where ``N`` = slot count) share the same physical
#       position but never overlap in time.
#
# The reusable wrapper is the cleanest way to keep the existing
# ``TABLE[tube]`` call sites intact while making them safe for any
# ``batch_size``. Iteration over the wrapper exposes only the physical
# slots, so any code that does ``for site in TABLE`` (e.g. _rinse_needle)
# keeps working.


class _ReusableSlots:
    """List wrapper that maps any positive integer through ``% len`` —
    for slots that are released between slices and can be reused by a
    later tube without conflict."""

    __slots__ = ("_slots",)

    def __init__(self, slots):
        self._slots = list(slots)

    def __getitem__(self, t):
        return self._slots[t % len(self._slots)]

    def __len__(self):
        return len(self._slots)

    def __iter__(self):
        return iter(self._slots)


# Persistent — one slot per tube, sized for the full physical rack.
SOURCE = [("source_rack", f"{r}{c}") for r in "ABCD" for c in range(1, 8)]            # 4×7 = 28
RACK_2ML_END = [("rack_2ml_end", f"{r}{c}") for r in "ABCDE" for c in range(1, 11)]   # 5×10 = 50

# Transient — slot count = physical capacity per slice; wraps via modulo.
WORKING = _ReusableSlots(("working_rack", f"B{c}") for c in range(1, 5))              # 4 slots
CAP_HOLDER = _ReusableSlots(f"decapper_{i}" for i in range(1, 5))                     # 4 decappers
SHAKER_SLOTS = _ReusableSlots([
    ("shaker_1", "A1"),
    ("shaker_1", "A2"),
    ("shaker_2", "A1"),
    ("shaker_2", "A2"),
])
CAP_FEEDER = _ReusableSlots(("cap_holder", f"A{c}") for c in range(1, 5))
DOSING_40ML = _ReusableSlots(("doser_40ml", f"B{c}") for c in range(1, 5))
DOSING_2ML_END = _ReusableSlots(("doser_2ml_end", f"A{c}") for c in range(1, 5))
DOSING_2ML_MIDDLE = _ReusableSlots(("doser_2ml_middle", f"A{c}") for c in range(1, 5))

# Fixed positions — used by needle-rinse helper, no per-tube indexing.
DOSING_CLEAN = [
    ("doser_40ml", "A1"),
    ("doser_40ml", "A2"),
    ("doser_40ml", "A3"),
]
DOSING_WASTE = [
    ("doser_40ml", "A4"),
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


class Start(Action):
    """First action of every plan — operator's initialisation hook.

    Planned by the PDDL planner like any other action; ``pre`` requires
    ``~started`` so it runs exactly once, at the very start. ``eff``
    seeds the per-tube initial facts (``in_source``, ``has_cap``) so no
    per-tube action's pre is satisfiable until Start has run.

    Put your axis-init / homing / device-warmup calls in ``execute``.
    """
    params      = []
    duration    = 5
    resource    = "robot"

    def pre(self):
        # Expr form (not raw bool) so ``build_precedence`` can read the
        # dependency. Single negative fact — nothing must add ``started``
        # before this action.
        return ~started()

    def eff(self):
        # Seed the *full* tube list, not the current slice — otherwise
        # later slices wouldn't have ``in_source`` / ``has_cap`` and
        # would get stuck. ``_ctx_all_objects`` reads the launcher's
        # un-sliced snapshot for exactly this case.
        tubes = self._ctx_all_objects().get("tube", [])
        seeds = [+started()]
        for t in tubes:
            seeds.append(+in_source(t))
            seeds.append(+has_cap(t))
        return {"started": tuple(seeds)}

    def execute(self):
        rt = self.ctx.runtime
        rcp = self.ctx.recipes
        rt.motor(1)
        rcp["robot"].park(joint=[0, 45, -90, 0, -45, 0, 100], has_motion_plan=True)
        return "started"


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

    ``register = False`` marks this as an abstract base: the
    framework skips registration so the planner never instantiates it
    directly, only its concrete subclasses (``ShakerOne``,
    ``ShakerTwo``) — which redeclare ``register = True``."""
    register    = False
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
    register = True   # opt back in — parent is abstract


class ShakerTwo(ShakerCycleBase):
    SHAKER   = "shaker_2"
    resource = "shaker_2"
    register = True   # opt back in — parent is abstract


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


# ── 6. End-of-run park ─────────────────────────────────────────────────────


class Park(Action):
    """Final park step — runs after every tube has finished.

    Planned by the PDDL planner like any other action; ``pre`` requires
    that every tube is done so the planner schedules it last. Subclass
    and set ``trigger = "park"`` to reuse the same motion as an
    operator-initiated cleanup (see ``OperatorPark``).
    """
    params      = []
    duration    = 5
    resource    = "robot"
    tool        = None
    PARK_JOINTS = [0, 185, -94, 0, 0, 0, 100]  # override in subclasses if needed

    def pre(self):
        # Expr form (not raw bool) so ``build_precedence`` walks the
        # dependency tree and slots Park after the last per-tube
        # action. Returning a bare bool would hide the deps from the
        # scheduler, which would then place Park in any free robot
        # slot — including mid-pipeline.
        #
        # Uses ``_ctx_all_objects`` (un-sliced) so the precondition
        # references every tube in the batch, not just the current
        # slice. Otherwise Park could fire after the first slice and
        # later slices would have nothing to plan.
        tubes = self._ctx_all_objects().get("tube", [])
        if not tubes:
            return ~parked()
        expr = ~parked()
        for t in tubes:
            expr = expr & in_done(t) & vial_2ml_capped(t)
        return expr

    def eff(self):
        return {"parked": (+parked(),)}

    def execute(self):
        rt = self.ctx.runtime
        rcp = self.ctx.recipes
        rcp["robot"].park(joint=self.PARK_JOINTS, has_motion_plan=True)
        rt.motor(0)
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — runs when the user clicks Park.

    Inherits Park's motion + effects; the only difference is
    ``trigger = "park"``, which puts this outside the normal plan: the
    framework runs it as a one-off cleanup subtree when the operator
    clicks the Park button. It does *not* appear in the schedule and is
    *not* sequenced by the planner.
    """
    trigger = "park"
