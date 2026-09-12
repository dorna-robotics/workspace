"""The route planner — a declared route, looked up against the world.

A project DECLARES the route its items take: an ordered list of action
classes (``ROUTE`` in actions.py, or per phase in phases.py). Nothing
searches for it. Planning is a lookup:

    an item's next step is the first step of the route whose ``pre``
    holds in the world as it stands.

The plan for a window is that lookup applied to the units — the run's
itemless steps (``Start``, ``Park``) and the items in window order —
ONE STEP AT A TIME, always to the unit that is FURTHEST ALONG THE
ROUTE among those that can move (ties: the earlier unit; the itemless
unit only when no item can move). So an item mid-station finishes its
station before another item starts one, an item behind moves only
while the ones ahead cannot, and a step whose ``pre`` spans several
items (a shake that needs its whole bank seated) simply waits until
the others have caught up. That order is what keeps a pipeline free
of deadlock: an item never takes the hand or a seat while an item
further along could still finish its carry and release one (measured
both ways: bna's vial 4 picked for the shaker while bank 0 still
needed the hand to unload; the phased example's tube 0 picked for the
balance while tube 1 sat on it needing the hand). "Furthest along" is
read from the facts — the highest step of the route whose effects
already hold for the item — so a replan from an observed state ranks
the items the same way a fresh plan does. Each step's ``eff`` is
applied as it is taken, so every later ``pre`` is evaluated against
the world that step leaves — the same simulation ``bt.replay``
performs. The scheduler then overlaps the plan on the resources; the
plan's order is only a valid order, never the timing.

Constant time in the batch, deterministic for a given world and
window, and every failure is a NAMED failure of the route:

  * a step applies again after it ran — its ``pre`` does not turn
    false once the step is behind the item (guard it with the negation
    of a fact a later step asserts);
  * an item stalls — no step applies and the goal is not reached;
    the error names the item, its last step, and what the next steps
    in route order are missing;
  * every item finished the route and the goal still does not hold —
    the route does not assert the fact the goal (or the phase) waits
    for.

There is no fallback: a route that does not close is a project fault,
reported at replay, never a search on the bench.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, FrozenSet, Iterable, List, Optional, Sequence, Tuple


log = logging.getLogger(__name__)


# A single fact is a tuple (predicate_name, *args).
Fact = Tuple[Any, ...]
State = FrozenSet[Fact]
Goal = Callable[[State], bool]


@dataclass(frozen=True)
class Step:
    """One grounded step of a plan — an action bound to its parameters.

    ``pre(state) -> bool`` and ``eff(state) -> state`` are pure: the
    planner only moves in-memory states. ``__eq__`` / ``__hash__``
    compare on name + params (the dataclass excludes the callables),
    so two Steps with the same binding are the same logical step.
    """

    name: str
    params: Tuple[Any, ...]
    pre: Callable[[State], bool]
    eff: Callable[[State], State]

    def __repr__(self) -> str:
        if self.params:
            return f"{self.name}({', '.join(map(repr, self.params))})"
        return self.name


@dataclass
class Template:
    """One action class as the planner sees it.

    ``param_iter(state)`` enumerates the candidate bindings (the
    Cartesian product of the window's objects for the declared
    params); ``pre(state, params)`` / ``eff(state, params)`` are the
    class's contract, evaluated on a fresh state. Built by
    ``workspace.bt.protocol.Protocol.templates``.
    """

    name: str
    param_iter: Callable[[State], Iterable[Tuple[Any, ...]]]
    pre: Callable[[State, Tuple[Any, ...]], bool]
    eff: Callable[[State, Tuple[Any, ...]], State]

    def ground(self, params: Tuple[Any, ...]) -> Step:
        p = tuple(params)
        return Step(
            name=self.name,
            params=p,
            pre=(lambda s, _p=p, _t=self: _t.pre(s, _p)),
            eff=(lambda s, _p=p, _t=self: _t.eff(s, _p)),
        )


class RouteError(RuntimeError):
    """The declared route does not close from this world. The message
    names the item, the step and the facts, so the fix is in the
    project, not in a search."""


def _bindings_for(t: Template, state: State, unit: Any) -> Iterable[Tuple[Any, ...]]:
    """The template's candidate bindings for one unit: ``()`` for the
    itemless unit (``None``), else every binding whose first parameter
    is the item — by convention the first param is the item."""
    for params in t.param_iter(state):
        params = tuple(params)
        if unit is None:
            if not params:
                yield params
        elif params and params[0] == unit:
            yield params


def _next_step(templates: Sequence[Template], state: State, unit: Any) -> Optional[Step]:
    """The first step of the route that applies to ``unit`` now."""
    for t in templates:
        for params in _bindings_for(t, state, unit):
            try:
                ok = t.pre(state, params)
            except Exception as ex:
                raise RouteError(
                    f"{t.name}{params}: pre() raised {type(ex).__name__}: {ex}"
                ) from ex
            if ok:
                return t.ground(params)
    return None


def _progress(templates: Sequence[Template], state: State, unit: Any) -> int:
    """How far along the route ``unit`` is: the highest route index
    whose step's effects already hold for it (applying them changes
    nothing). ``-1`` for the itemless unit and for an item at the start."""
    if unit is None:
        return -1
    best = -1
    for i, t in enumerate(templates):
        for params in _bindings_for(t, state, unit):
            try:
                if t.eff(state, params) == state:
                    best = i
            except Exception:
                pass
            break
    return best


def plan_route(
    templates: Sequence[Template],
    state: State,
    goal: Goal,
    items: Sequence[Any],
    *,
    explain: Optional[Callable[[Template, State, Tuple[Any, ...]], str]] = None,
) -> List[Step]:
    """The plan for ``items`` along the route ``templates`` from ``state``.

    ``templates`` are the candidate steps IN ROUTE ORDER — the run's
    itemless steps plus the open phase's steps (``Protocol.candidates``).
    ``goal(state)`` is what this window must reach: the phase for its
    items, or the run's goal on the last window — and where the plan
    STOPS: no step is taken past it. ``explain`` renders, for the stall
    message, what a step's pre is missing.

    Returns the plan, or raises :class:`RouteError` — never ``None``.
    """
    if not isinstance(state, frozenset):
        state = frozenset(state)
    sim = state
    plan: List[Step] = []
    taken: set = set()
    last: dict = {}                       # unit -> last Step it took
    units: List[Any] = [None] + list(items)

    while not goal(sim):
        # ONE step: the unit furthest along the route among those that
        # can move; ties to the earlier unit; the itemless unit last.
        # The plan ends the moment the goal holds — a phase's window
        # stops at the boundary, a bench run at its phase; only the
        # run's last window carries on to Park.
        best = None
        for k, unit in enumerate(units):
            step = _next_step(templates, sim, unit)
            if step is None:
                continue
            key = (_progress(templates, sim, unit), -k)
            if best is None or key > best[0]:
                best = (key, unit, step)
        if best is None:
            break                                   # nothing moves
        _, unit, step = best
        key = (step.name, step.params)
        if key in taken:
            who = "the run" if unit is None else f"item {unit!r}"
            raise RouteError(
                f"{step!r} applies again after it ran for {who} in this plan: "
                f"a step's pre() must be false once the step is behind the "
                f"item — guard it with the negation of a fact a later step "
                f"asserts (~unloaded(t) on a load, ~done(t) on the last step)."
            )
        taken.add(key)
        try:
            nxt = step.eff(sim)
        except Exception as ex:
            raise RouteError(f"{step!r}: eff() raised {type(ex).__name__}: {ex}") from ex
        sim = nxt if isinstance(nxt, frozenset) else frozenset(nxt)
        plan.append(step)
        last[unit] = step

    if goal(sim):
        return plan

    # ── Nothing applies and the goal is not reached: name it ──────────
    lines = []
    for unit in units:
        who = "run-level steps" if unit is None else f"item {unit!r}"
        prev = last.get(unit)
        head = f"{who}: last step {prev!r}" if prev is not None else f"{who}: no step applied"
        wants = []
        for t in templates:
            for params in _bindings_for(t, sim, unit):
                try:
                    if t.pre(sim, params):
                        continue
                except Exception:
                    continue
                why = explain(t, sim, params) if explain is not None else ""
                wants.append(f"{t.name}{params} needs {why}" if why else f"{t.name}{params}")
                break
        if wants:
            head += "; not applicable: " + "; ".join(wants[:4])
        lines.append(head)
    raise RouteError(
        "the route does not reach the goal from this state — no step applies "
        "and the goal is not met:\n  " + "\n  ".join(lines)
    )
