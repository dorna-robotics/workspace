"""pace_bt — the only file you edit to change the protocol.

This is the canonical example of the framework's authoring style:
**one ``Action`` subclass per atomic step**, plus a single
``setup(**kwargs)`` function that maps operator kwargs into the
initial world state, the goal, and the object pools.

Reading order:
  1. Predicates declared at the top.
  2. ``setup(**kwargs)`` — converts GUI kwargs into the planning
     inputs the framework needs.
  3. One section per ``Action`` subclass.

If you're adding a new action: copy any existing class, rename it,
and edit the four authoring slots — ``params``, ``duration``,
``resource``, plus the ``pre`` / ``eff`` methods. Override ``execute``
only when wiring a real recipe in production; in sim mode the
framework sleeps for ``duration`` and returns success automatically.
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


# ── 3. Actions ─────────────────────────────────────────────────────────────


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


class Decap(Action):
    """Remove cap, transfer tube into the working rack."""
    params   = ["tube"]
    duration = 10
    resource = "robot"

    def pre(self, tube):
        return in_source(tube) & has_cap(tube) & weighed(tube)

    def eff(self, tube):
        return -has_cap(tube), -in_source(tube), +in_working(tube)


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


class Recap(Action):
    """Put the cap back onto a dosed tube."""
    params   = ["tube"]
    duration = 10
    resource = "robot"

    def pre(self, tube):
        return dosed(tube) & ~has_cap(tube) & in_working(tube)

    def eff(self, tube):
        return (+has_cap(tube),)


class Shelve(Action):
    """Move the finished tube into the done rack."""
    params   = ["tube"]
    duration = 5
    resource = "robot"

    def pre(self, tube):
        return has_cap(tube) & dosed(tube) & in_working(tube)

    def eff(self, tube):
        return -in_working(tube), +in_done(tube)
