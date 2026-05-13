"""PDDL domain for pace_bt — the world model.

Three sections in this fixed order (the framework discipline):

  1. Object types   — Python classes acting as type markers.
  2. Predicates     — list of fact-name constants. State facts are
                      tuples ``(predicate_name, *args)``.
  3. Actions        — one ActionTemplate per atomic recipe action.
                      Each has parameters, preconditions, and effects.
  4. Goal           — single function ``state -> bool``.

This file is the single source of truth for "what does this protocol
need to do, and what are the rules". Everything else (scheduler
durations, BT leaf classes, tree shape) is derived from it.

Reviewer reads this top-to-bottom and learns the protocol.
"""

from __future__ import annotations

from typing import Any, Iterable, Tuple

from workspace.planner import ActionTemplate, State


# ── 1. Object types ────────────────────────────────────────────────────────


class Tube:
    """A sample tube. Identified by its integer index in the source rack."""


# ── 2. Predicates ──────────────────────────────────────────────────────────
#
# Each predicate is a string constant used as the first element of a fact
# tuple. Predicate names are *fact-form* (no leading "is_", no verb).

P_IN_SOURCE        = "in_source"          # (tube,)        — tube is in the source rack
P_IN_WORKING       = "in_working"         # (tube,)        — tube is in the working rack
P_IN_DONE          = "in_done"            # (tube,)        — tube ended up in the done rack
P_HAS_CAP          = "has_cap"            # (tube,)        — tube is capped
P_WEIGHED          = "weighed"            # (tube,)        — weight is known
P_WEIGHT_HEAVY     = "weight_heavy"       # (tube,)        — known weight > threshold
P_DOSED            = "dosed"              # (tube,)        — has been dispensed into


# Convenience for building facts.
def fact(predicate: str, *args: Any) -> Tuple[Any, ...]:
    return (predicate, *args)


# ── 3. Actions ─────────────────────────────────────────────────────────────


def _make_for_each_tube(tubes: Iterable[int]):
    """Helper used by every template's param_iter — yield (tube,) for each."""
    tubes_list = list(tubes)
    def _iter(_state: State) -> Iterable[Tuple[Any, ...]]:
        for t in tubes_list:
            yield (t,)
    return _iter


def build_templates(tubes: Iterable[int]) -> list[ActionTemplate]:
    """Construct the action template list, parameterised by the tube IDs in
    the batch. Called once per planning episode."""
    for_each = _make_for_each_tube(tubes)

    # ── inspect: pick from source rack, place on scale, weigh.
    #            precondition: tube is in source, still capped.
    #            effect: tube weight is now known.
    def inspect_pre(s: State, p):
        (t,) = p
        return fact(P_IN_SOURCE, t) in s and not fact(P_WEIGHED, t) in s
    def inspect_eff(s: State, p):
        (t,) = p
        return s | {fact(P_WEIGHED, t)}

    inspect = ActionTemplate(
        name="inspect", param_iter=for_each,
        preconditions=inspect_pre, effects=inspect_eff,
    )

    # ── decap: remove cap, place tube in working rack.
    #           precondition: tube is in source and has cap and was weighed.
    #           effect: tube has no cap and is in working rack.
    def decap_pre(s: State, p):
        (t,) = p
        return (fact(P_IN_SOURCE, t) in s
                and fact(P_HAS_CAP, t) in s
                and fact(P_WEIGHED, t) in s)
    def decap_eff(s: State, p):
        (t,) = p
        s2 = set(s) - {fact(P_HAS_CAP, t), fact(P_IN_SOURCE, t)}
        s2.add(fact(P_IN_WORKING, t))
        return frozenset(s2)

    decap = ActionTemplate(
        name="decap", param_iter=for_each,
        preconditions=decap_pre, effects=decap_eff,
    )

    # ── dispense_light / dispense_heavy: branch on weight.
    #     precondition: tube in working rack, no cap, not dosed yet,
    #                   AND weight matches branch.
    #     effect: tube is dosed.
    def _disp_pre(want_heavy: bool):
        def _f(s: State, p):
            (t,) = p
            base = (fact(P_IN_WORKING, t) in s
                    and fact(P_HAS_CAP, t) not in s
                    and fact(P_DOSED, t) not in s)
            heavy = fact(P_WEIGHT_HEAVY, t) in s
            return base and (heavy if want_heavy else not heavy)
        return _f
    def _disp_eff(s: State, p):
        (t,) = p
        return s | {fact(P_DOSED, t)}

    dispense_light = ActionTemplate(
        name="dispense_light", param_iter=for_each,
        preconditions=_disp_pre(False), effects=_disp_eff,
    )
    dispense_heavy = ActionTemplate(
        name="dispense_heavy", param_iter=for_each,
        preconditions=_disp_pre(True), effects=_disp_eff,
    )

    # ── recap: put cap back on tube. Required to enter done rack.
    #           precondition: dosed and no cap and in working.
    #           effect: tube has cap.
    def recap_pre(s: State, p):
        (t,) = p
        return (fact(P_DOSED, t) in s
                and fact(P_HAS_CAP, t) not in s
                and fact(P_IN_WORKING, t) in s)
    def recap_eff(s: State, p):
        (t,) = p
        return s | {fact(P_HAS_CAP, t)}

    recap = ActionTemplate(
        name="recap", param_iter=for_each,
        preconditions=recap_pre, effects=recap_eff,
    )

    # ── shelve: move from working → done.
    #            precondition: capped + dosed + in working.
    #            effect: in done rack (no longer in working).
    def shelve_pre(s: State, p):
        (t,) = p
        return (fact(P_HAS_CAP, t) in s
                and fact(P_DOSED, t) in s
                and fact(P_IN_WORKING, t) in s)
    def shelve_eff(s: State, p):
        (t,) = p
        s2 = set(s) - {fact(P_IN_WORKING, t)}
        s2.add(fact(P_IN_DONE, t))
        return frozenset(s2)

    shelve = ActionTemplate(
        name="shelve", param_iter=for_each,
        preconditions=shelve_pre, effects=shelve_eff,
    )

    return [inspect, decap, dispense_light, dispense_heavy, recap, shelve]


# ── 4. Goal & initial state helpers ────────────────────────────────────────


def initial_state(tubes: Iterable[int], heavy_set: Iterable[int] = ()) -> State:
    """World at t=0: every tube in source, capped; ``heavy_set`` flags
    which ones come back from inspection as heavy.

    The heavy/light flag is normally observed *after* inspect runs, but
    for planning purposes we model the observation as already known to
    let the planner pick the right dispense branch. In practice the
    replanner re-runs after inspect with the actual observed weights.
    """
    facts = set()
    heavy_set = set(heavy_set)
    for t in tubes:
        facts.add(fact(P_IN_SOURCE, t))
        facts.add(fact(P_HAS_CAP, t))
        if t in heavy_set:
            facts.add(fact(P_WEIGHT_HEAVY, t))
    return frozenset(facts)


def make_goal(tubes: Iterable[int]):
    """Goal: every tube ends up in the done rack."""
    tubes_set = set(tubes)
    def _goal(state: State) -> bool:
        return all(fact(P_IN_DONE, t) in state for t in tubes_set)
    return _goal
