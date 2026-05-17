"""pace_bt protocol — predicates, setup, and one Action per atomic step.

Framework reference: ../../../docs/bt-framework-guide.md
"""

from __future__ import annotations

from workspace.bt import Action, predicate


# ── 1. Predicates ──────────────────────────────────────────────────────────

in_source     = predicate("in_source")
in_working    = predicate("in_working")
in_done       = predicate("in_done")
has_cap       = predicate("has_cap")
weighed       = predicate("weighed")
weight_heavy  = predicate("weight_heavy")
dosed         = predicate("dosed")


# ── 2. setup — operator kwargs → planning inputs ───────────────────────────


def setup(**kwargs):
    """Project setup. kwargs: ``batch_size`` (number of tubes).

    Tube heaviness is observed at runtime by Inspect's sensing eff,
    not declared here.
    """
    batch_size = int(kwargs.get("batch_size", 1))
    tubes = list(range(batch_size))

    facts = set()
    for t in tubes:
        facts.add((in_source.name, t))
        facts.add((has_cap.name, t))

    def goal(state):
        return all((in_done.name, t) in state for t in tubes)

    return {
        "initial_facts": frozenset(facts),
        "goal":          goal,
        "objects":       {"tube": tubes},
    }


# ── 3. Slot tables — tube index → physical rack + slot ────────────────────

SOURCE   = ["A1", "A2", "A3", "A4", "A5", "A6", "A7"]    # source_rack slots
WORKING  = ["B1", "B2", "B3", "B4", "B5", "B6", "B7"]    # working_rack slots
CAPS     = [f"slot_{i}" for i in range(7)]                # cap_holder slots

HEAVY_THRESHOLD  = 50.0   # grams — above this routes to DispenseHeavy
INSPECTION_FRQ   = 4      # camera rotations per visual inspection
INSPECTION_ROT   = 90     # degrees per rotation


# ── 4. Actions ─────────────────────────────────────────────────────────────


class Inspect(Action):
    """Pick from source, weigh, return. Reports light or heavy."""
    params      = ["tube"]
    duration    = 10
    resource    = "robot"
    tool        = "gripper"
    pre_check   = "source_tube_present"

    def pre(self, tube):
        return in_source(tube) & ~weighed(tube)

    def eff(self, tube):
        return {
            "light": +weighed(tube),
            "heavy": (+weighed(tube), +weight_heavy(tube)),
        }

    def execute(self, tube):
        rcp = self.ctx.recipes
        rcp["source_rack"].pick(SOURCE[tube])

        # Visual inspection — present to camera and rotate
        rcp["inspector"].present(approach=True)
        for _ in range(INSPECTION_FRQ):
            rcp["inspector"].rotate(rotation=INSPECTION_ROT)

        # Weigh on scale
        rcp["scale"].place("place")
        weight = rcp["scale"].weight()
        rcp["scale"].pick("place")

        # Return to source — Decap will pick it up again
        rcp["source_rack"].place(SOURCE[tube])
        return "heavy" if (weight or 0) > HEAVY_THRESHOLD else "light"


class Decap(Action):
    """Remove cap, transfer tube into the working rack."""
    params      = ["tube"]
    duration    = 10
    resource    = "robot"
    tool        = "gripper"
    pre_check   = "source_tube_present"
    post_check  = "tube_in_working_rack"

    def pre(self, tube):
        return in_source(tube) & has_cap(tube) & weighed(tube)

    def eff(self, tube):
        return {"decapped": (-has_cap(tube), -in_source(tube), +in_working(tube))}

    def execute(self, tube):
        rcp = self.ctx.recipes
        rcp["source_rack"].pick(SOURCE[tube])
        rcp["decapper_5"].place(exit=False)
        rcp["decapper_5"].decap(approach=False)
        rcp["cap_holder"].place(CAPS[tube])
        rcp["decapper_5"].pick()
        rcp["working_rack"].place(WORKING[tube])
        return "decapped"


class DispenseLight(Action):
    """Dispense 10 mL into an uncapped light tube."""
    params      = ["tube"]
    duration    = 10
    resource    = "dispenser"
    tool        = "needle"
    pre_check   = "tube_in_working_rack"

    def pre(self, tube):
        return (
            in_working(tube)
            & ~has_cap(tube)
            & ~weight_heavy(tube)
            & ~dosed(tube)
        )

    def eff(self, tube):
        return {"dosed": +dosed(tube)}

    def execute(self, tube):
        rcp = self.ctx.recipes
        rcp["doser_40ml"].immerse(dist=90, anchor=WORKING[tube])
        rcp["doser_40ml"].dispense(vol=10)
        rcp["doser_40ml"].retract(dist=10, anchor=WORKING[tube])
        return "dosed"


class DispenseHeavy(Action):
    """Dispense 20 mL into an uncapped heavy tube."""
    params      = ["tube"]
    duration    = 15
    resource    = "dispenser"
    tool        = "needle"
    pre_check   = "tube_in_working_rack"

    def pre(self, tube):
        return (
            in_working(tube)
            & ~has_cap(tube)
            & weight_heavy(tube)
            & ~dosed(tube)
        )

    def eff(self, tube):
        return {"dosed": +dosed(tube)}

    def execute(self, tube):
        rcp = self.ctx.recipes
        rcp["doser_40ml"].immerse(dist=90, anchor=WORKING[tube])
        rcp["doser_40ml"].dispense(vol=20)
        rcp["doser_40ml"].retract(dist=10, anchor=WORKING[tube])
        return "dosed"


class Recap(Action):
    """Put the cap back onto a dosed tube."""
    params      = ["tube"]
    duration    = 10
    resource    = "robot"
    tool        = "gripper"
    pre_check   = "tube_in_working_rack"

    def pre(self, tube):
        return dosed(tube) & ~has_cap(tube) & in_working(tube)

    def eff(self, tube):
        return {"recapped": +has_cap(tube)}

    def execute(self, tube):
        rcp = self.ctx.recipes
        rcp["working_rack"].pick(WORKING[tube])
        rcp["decapper_5"].place()
        rcp["cap_holder"].pick(CAPS[tube])
        rcp["decapper_5"].cap(exit=False)
        rcp["decapper_5"].pick(approach=False)
        rcp["working_rack"].place(WORKING[tube])
        return "recapped"


class Shelve(Action):
    """Move the finished tube into the done rack (back to source slot)."""
    params      = ["tube"]
    duration    = 5
    resource    = "robot"
    tool        = "gripper"

    def pre(self, tube):
        return has_cap(tube) & dosed(tube) & in_working(tube)

    def eff(self, tube):
        return {"shelved": (-in_working(tube), +in_done(tube))}

    def execute(self, tube):
        rcp = self.ctx.recipes
        rcp["working_rack"].pick(WORKING[tube])
        rcp["source_rack"].place(SOURCE[tube])
        return "shelved"


# ── 5. End-trigger actions ─────────────────────────────────────────────────


class ParkTool(Action):
    """Release whatever tool is held — runs on operator End."""
    params      = []
    duration    = 5
    resource    = "robot"
    tool        = None
    trigger     = "end"

    def execute(self):
        return "none"
