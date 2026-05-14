"""pace_bt — the only file you edit to change the protocol.

This is the canonical example of the framework's authoring style:
**one ``Action`` subclass per atomic step**, plus a single
``setup(**kwargs)`` function that maps operator kwargs into the
initial world state, the goal, and the object pools.

Reading order:
  1. Predicates declared at the top.
  2. ``setup(**kwargs)`` — converts GUI kwargs into the planning
     inputs the framework needs.
  3. SOURCE / WORKING / etc. — physical slot tables. Hand-edited per
     scene. Maps an action's integer ``tube`` index to the rack +
     slot the recipes layer needs.
  4. One section per ``Action`` subclass. Each one has:
       - ``params`` / ``duration`` / ``resource`` — scheduling info
       - ``pre`` — precondition expression over predicates
       - ``eff`` — effect: which facts get added (+) / removed (−)
       - ``execute`` — REAL-MODE robot-motion logic. Calls recipes
         from ``self.ctx.recipes`` (loaded from ``recipes.yaml``).

Sim vs. real mode is a **framework-level** concern, not a per-action
one. The framework reads ``core._simulation_mode`` once:

  * SIM mode → framework sleeps for ``duration`` per action and
    returns success. ``execute`` is NOT called. Useful for validating
    the protocol + plan + schedule end-to-end without hardware.
  * REAL mode → framework calls ``execute(*params)``. That's where
    recipes drive the robot.

Action classes don't carry a sim flag. They just define ``execute``
as the real-hardware logic. The sim path is identical for every
action and the framework handles it.

If you're adding a new action: copy any existing class, rename it,
edit ``params`` / ``duration`` / ``resource``, then the ``pre`` /
``eff`` / ``execute`` methods. Leaving ``execute`` unset is fine
during early sim development — fill it in before going to hardware.
"""

from __future__ import annotations

from workspace.bt import Action, predicate


# ── 1. Predicates ──────────────────────────────────────────────────────────
#
# A predicate is just a named relation. Apply it to args to get a fact:
# ``has_cap(tube)`` returns a fact you can put into pre/eff expressions
# and which the framework checks against the live world state.

in_source     = predicate("in_source")
in_working    = predicate("in_working")
in_done       = predicate("in_done")
has_cap       = predicate("has_cap")
weighed       = predicate("weighed")
weight_heavy  = predicate("weight_heavy")
dosed         = predicate("dosed")


# ── 2. setup — map operator kwargs into planning inputs ────────────────────


def _parse_heavy(value):
    """Normalise the ``heavy`` kwarg into a set of int tube indices.

    Accepts either an iterable of ints (programmatic callers) or a
    comma-separated string (GUI textarea). Empty / blank → empty set.
    """
    if value is None:
        return set()
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
        return {int(p) for p in parts if p}
    return {int(v) for v in value}


def setup(**kwargs):
    """Translate operator kwargs into the planning inputs the framework needs.

    Returns a dict with three keys:

      * ``initial_facts`` — frozenset of fact tuples describing the
        world at t=0.
      * ``goal`` — callable ``state -> bool`` checked at every PDDL
        expansion. The planner stops when this returns True.
      * ``objects`` — dict of named pools (``{param_name: [values]}``)
        the framework's ``Action.param_iter`` uses to enumerate
        candidate parameter bindings.

    For pace_bt:
      * ``batch_size`` — how many tubes.
      * ``heavy`` — which tubes (indices) come back heavy from
        Inspect. In production this is observed; declaring it up
        front lets the first plan pick the right dispense branch.
    """
    batch_size = int(kwargs.get("batch_size", 1))
    heavy = _parse_heavy(kwargs.get("heavy", ""))
    tubes = list(range(batch_size))

    facts = set()
    for t in tubes:
        facts.add((in_source.name, t))
        facts.add((has_cap.name, t))
        if t in heavy:
            facts.add((weight_heavy.name, t))

    def goal(state):
        return all((in_done.name, t) in state for t in tubes)

    return {
        "initial_facts": frozenset(facts),
        "goal":          goal,
        "objects":       {"tube": tubes},
    }


# ── 3. Slot tables — map tube index → physical rack + slot ────────────────
#
# Recipes operate on (component, slot) pairs (e.g. ``source_rack.pick("A1")``).
# Actions get an integer ``tube`` index from the planner. These tables
# translate. Edit when changing rack layout.

SOURCE   = ["A1", "A2", "A3", "A4", "A5", "A6", "A7"]    # source_rack slots
WORKING  = ["B1", "B2", "B3", "B4", "B5", "B6", "B7"]    # working_rack slots
CAPS     = [f"slot_{i}" for i in range(7)]                # cap_holder slots


# ── 4. Actions ─────────────────────────────────────────────────────────────
#
# Each class subclasses ``Action`` and declares the four required attrs
# (params, duration, resource) plus the three methods (pre, eff,
# execute). The framework's ``_DSLActionLeaf`` wraps each one as a BT
# leaf, runs ``execute`` on a worker thread in real mode, and applies
# the declared effects (eff) to ``ctx.state`` on success.


class Inspect(Action):
    """Pick from source, weigh, return. After this the planner knows the
    tube's weight bucket (heavy / light)."""
    params   = ["tube"]
    duration = 10
    resource = "robot"

    def pre(self, tube):
        return in_source(tube) & ~weighed(tube)

    def eff(self, tube):
        return (+weighed(tube),)

    def execute(self, tube):
        # Real-mode: pick from source, set on scale, weigh, return.
        # ``self.ctx.recipes`` is the dict loaded from recipes.yaml.
        rcp = self.ctx.recipes
        rcp["source_rack"].pick(SOURCE[tube])
        rcp["scale"].place("place")
        rcp["scale"].weight()                  # → reads weight into device bus
        rcp["scale"].pick("place")
        rcp["source_rack"].place(SOURCE[tube])
        return True


class Decap(Action):
    """Remove cap, transfer tube into the working rack."""
    params   = ["tube"]
    duration = 10
    resource = "robot"

    def pre(self, tube):
        return in_source(tube) & has_cap(tube) & weighed(tube)

    def eff(self, tube):
        return -has_cap(tube), -in_source(tube), +in_working(tube)

    def execute(self, tube):
        rcp = self.ctx.recipes
        rcp["source_rack"].pick(SOURCE[tube])
        rcp["decapper_5"].place(exit=False)
        rcp["decapper_5"].decap(approach=False)
        rcp["cap_holder"].place(CAPS[tube])     # park the cap
        rcp["decapper_5"].pick()                # pick tube back up
        rcp["working_rack"].place(WORKING[tube])
        return True


class DispenseLight(Action):
    """Dispense the 'light' solvent volume into an uncapped tube."""
    params   = ["tube"]
    duration = 10
    resource = "dispenser"

    def pre(self, tube):
        return (
            in_working(tube)
            & ~has_cap(tube)
            & ~weight_heavy(tube)
            & ~dosed(tube)
        )

    def eff(self, tube):
        return (+dosed(tube),)

    def execute(self, tube):
        rcp = self.ctx.recipes
        rcp["doser_40ml"].immerse(dist=90, anchor=WORKING[tube])
        rcp["doser_40ml"].dispense(vol=10)
        rcp["doser_40ml"].retract(dist=10, anchor=WORKING[tube])
        return True


class DispenseHeavy(Action):
    """Dispense the 'heavy' (larger) solvent volume."""
    params   = ["tube"]
    duration = 15
    resource = "dispenser"

    def pre(self, tube):
        return (
            in_working(tube)
            & ~has_cap(tube)
            & weight_heavy(tube)
            & ~dosed(tube)
        )

    def eff(self, tube):
        return (+dosed(tube),)

    def execute(self, tube):
        rcp = self.ctx.recipes
        rcp["doser_40ml"].immerse(dist=90, anchor=WORKING[tube])
        rcp["doser_40ml"].dispense(vol=20)      # heavier → bigger volume
        rcp["doser_40ml"].retract(dist=10, anchor=WORKING[tube])
        return True


class Recap(Action):
    """Put the cap back onto a dosed tube."""
    params   = ["tube"]
    duration = 10
    resource = "robot"

    def pre(self, tube):
        return dosed(tube) & ~has_cap(tube) & in_working(tube)

    def eff(self, tube):
        return (+has_cap(tube),)

    def execute(self, tube):
        rcp = self.ctx.recipes
        rcp["working_rack"].pick(WORKING[tube])
        rcp["decapper_5"].place()
        rcp["cap_holder"].pick(CAPS[tube])      # retrieve the cap
        rcp["decapper_5"].cap(exit=False)
        rcp["decapper_5"].pick(approach=False)
        rcp["working_rack"].place(WORKING[tube])
        return True


class Shelve(Action):
    """Move the finished tube into the done rack (back to source slot)."""
    params   = ["tube"]
    duration = 5
    resource = "robot"

    def pre(self, tube):
        return has_cap(tube) & dosed(tube) & in_working(tube)

    def eff(self, tube):
        return -in_working(tube), +in_done(tube)

    def execute(self, tube):
        rcp = self.ctx.recipes
        rcp["working_rack"].pick(WORKING[tube])
        rcp["source_rack"].place(SOURCE[tube])
        return True
