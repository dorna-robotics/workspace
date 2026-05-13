"""pace_bt — all atomic actions, in one file.

This is the canonical example of the framework's authoring style:
**one ``Action`` subclass per atomic step**. Each class declares its
preconditions, effects, duration, resource, and recipe call in one
place. No separate ``domain.py``, no ``conditions.py``, no
``schedule.META`` dict, no ``_LEAVES`` factory mapping — the framework
discovers all of that from the classes.

Reading order:
  1. Predicates declared at the top.
  2. ``initial_state`` + ``make_goal`` helpers (what t=0 looks like + what
     "done" means).
  3. One section per ``Action`` subclass.

If you're adding a new action: copy any existing class, rename it,
and edit the four authoring slots — ``params``, ``duration``,
``resource``, plus the ``pre`` / ``eff`` methods. Add an ``execute``
override only when wiring a real recipe in production; in sim mode
the framework sleeps for ``duration`` and returns success
automatically.
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


# ── 2. World setup ─────────────────────────────────────────────────────────


def initial_state(tubes, heavy=()):
    """Initial world: every tube in source rack, capped; flag heavy ones.

    Heavy/light is observed by ``Inspect`` in production, but for the
    first plan we declare it from the operator input. The replanner
    picks up the real observed weights after inspection.
    """
    heavy = set(heavy)
    facts = set()
    for t in tubes:
        facts.add((in_source.name, t))
        facts.add((has_cap.name, t))
        if t in heavy:
            facts.add((weight_heavy.name, t))
    return frozenset(facts)


def make_goal(tubes):
    """Goal: every tube ends up in the done rack."""
    tubes = set(tubes)
    def _goal(state):
        return all((in_done.name, t) in state for t in tubes)
    return _goal


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
