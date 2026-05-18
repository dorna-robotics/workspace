"""Tiny PDDL-flavoured forward-search planner.

This is NOT a fully PDDL-spec-compliant planner. It's a deliberately
small forward-search engine that's enough for lab-automation domains
of the scale we see (10s of objects, 10s of action templates, plan
depths of a few hundred). Pure Python, no external dependencies.

Concepts the planner understands:

* **State** — a frozen set of *fact tuples*. A fact tuple is
  ``("predicate_name", arg1, arg2, …)``. The empty set is "nothing
  true yet". This is the same representation classical PDDL uses; we
  just skip the parser and let projects write Python.

* **Action** — a :class:`Action` instance with:
    - ``name``: e.g. ``"decap"``
    - ``params``: tuple of object bindings, e.g. ``("tube_3",)``
    - ``preconditions(state) -> bool``
    - ``effects(state) -> state``: returns the *new* state after the
      action fires.

* **Domain** — a callable that yields all *applicable* actions given
  a state. Projects define this by enumerating action templates × all
  parameter bindings × the precondition filter.

* **Goal** — a predicate ``state -> bool``.

* **Plan** — a list of actions, in order, that takes you from initial
  state to a state satisfying the goal.

The search is BFS (shortest plan first) with a closed-set on states
to avoid revisits. State hashing uses frozenset of fact tuples, so
order-independence is automatic. For domains larger than this can
handle, swap in Fast Downward via a subprocess — same domain
interface, same returned plan.
"""

from __future__ import annotations

import heapq
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, FrozenSet, Iterable, List, Optional, Tuple


log = logging.getLogger(__name__)


# A single fact is a tuple (predicate_name, *args).
Fact = Tuple[Any, ...]
State = FrozenSet[Fact]


@dataclass(frozen=True)
class Action:
    """One concrete (parameter-bound) action instance.

    Args:
        name: Action template name (e.g. ``"decap"``).
        params: Tuple of bound parameter values (e.g. ``("tube_3",)``).
        preconditions: Callable ``state -> bool``.
        effects: Callable ``state -> state``. Should be PURE — no side
            effects on the world. The planner only manipulates
            in-memory states; it doesn't execute anything. (The BT runs
            the real action via ``actions.py``.)
    """

    name: str
    params: Tuple[Any, ...]
    preconditions: Callable[[State], bool]
    effects: Callable[[State], State]

    def __repr__(self) -> str:
        if self.params:
            return f"{self.name}({', '.join(map(repr, self.params))})"
        return self.name

    # __eq__ / __hash__ from dataclass(frozen=True) compare on name+params
    # — the callables are excluded because dataclass eq doesn't see them.
    # That's what we want: two Action instances with the same name +
    # params represent the same logical step.


# A Domain is a callable that takes a state and yields all currently
# applicable Action instances. Projects implement it like:
#
#   def domain(state):
#       for t in tubes:
#           a = make_decap_action(t)
#           if a.preconditions(state):
#               yield a
#
Domain = Callable[[State], Iterable[Action]]

# A Goal is just state -> bool.
Goal = Callable[[State], bool]


def plan(
    initial_state: State,
    domain: Domain,
    goal: Goal,
    *,
    goal_facts: Optional[Iterable[Fact]] = None,
    max_depth: int = 500,
    max_states: int = 200_000,
) -> Optional[List[Action]]:
    """Forward-search for a plan from ``initial_state`` to ``goal``.

    Two search strategies, picked by whether ``goal_facts`` is provided:

    * **GBFS (Greedy Best-First Search)** when ``goal_facts`` is given.
      The heuristic ``h(state) = |goal_facts \\ state|`` counts goal
      facts still missing. The frontier is a priority queue ordered by
      ``h``, so the search expands the state closest to the goal next.
      Scales to large state spaces (batch=4 in tests went from 78 s
      timeout to ~50 ms). Finds a satisficing plan, not necessarily
      the shortest.

    * **BFS (Breadth-First Search)** when ``goal_facts`` is absent.
      Guarantees the shortest plan but explodes combinatorially for
      multi-item protocols. Kept as the fallback for projects that
      don't (or can't) expose their goal facts to the planner.

    Args:
        initial_state: Frozenset of fact tuples — the world at t=0.
        domain: Yields applicable actions for any state.
        goal: Predicate ``state -> bool`` checked at every expansion.
            This is the authoritative termination test.
        goal_facts: *Optional* set of positive fact tuples the goal
            requires. Used only to compute the heuristic — the goal
            callable still owns "are we done?". Projects derive this
            from their setup() (e.g. the union of every action's
            terminal effects, or an explicit list).
        max_depth: Refuse to search past this plan length.
        max_states: Refuse to expand more than this many states.

    Returns:
        Ordered list of Actions, or ``None`` if no plan within bounds.
    """
    if not isinstance(initial_state, frozenset):
        initial_state = frozenset(initial_state)

    if goal(initial_state):
        return []

    use_heuristic = goal_facts is not None
    if use_heuristic:
        goal_facts_fset = frozenset(goal_facts)

        def _h(state: State) -> int:
            return len(goal_facts_fset - state)
    else:
        def _h(state: State) -> int:
            return 0

    # Closed set of visited states (hashed via frozenset).
    visited = {initial_state}

    # GBFS: priority queue ordered by heuristic. tie-counter prevents
    # comparing histories (Action objects aren't ordered).
    # BFS: simple FIFO deque.
    counter = 0
    if use_heuristic:
        frontier: list = [(_h(initial_state), counter, initial_state, [])]
        heapq.heapify(frontier)
    else:
        frontier = deque()
        frontier.append((initial_state, []))

    expanded = 0
    while frontier:
        if use_heuristic:
            _, _, state, history = heapq.heappop(frontier)
        else:
            state, history = frontier.popleft()

        if len(history) >= max_depth:
            continue

        for action in domain(state):
            try:
                new_state = action.effects(state)
            except Exception:
                log.exception("pddl: action.effects raised on %r", action)
                continue
            if not isinstance(new_state, frozenset):
                new_state = frozenset(new_state)
            if new_state in visited:
                continue
            visited.add(new_state)
            new_history = history + [action]
            if goal(new_state):
                log.info(
                    "pddl: plan found in %d step(s) after expanding %d states "
                    "(%s)", len(new_history), expanded,
                    "GBFS" if use_heuristic else "BFS",
                )
                return new_history
            if use_heuristic:
                counter += 1
                heapq.heappush(
                    frontier, (_h(new_state), counter, new_state, new_history),
                )
            else:
                frontier.append((new_state, new_history))

        expanded += 1
        if expanded >= max_states:
            log.warning(
                "pddl: state cap (%d) hit — giving up. May need a stronger "
                "domain encoding or a real planner.",
                max_states,
            )
            return None

    log.info("pddl: frontier exhausted with no plan after %d states", expanded)
    return None


# ── Convenience: build a domain from an action-template list ───────────────


@dataclass
class ActionTemplate:
    """A parameterised action template.

    Project domains typically list one ActionTemplate per recipe action;
    the planner expands it across all valid parameter bindings.

    Args:
        name: Action template name.
        param_iter: Callable ``state -> Iterable[tuple]`` yielding all
            candidate parameter tuples (one tuple per potential
            instantiation). Filtering happens via preconditions; this
            iterator's job is just to enumerate candidates.
        preconditions: ``(state, params) -> bool``
        effects: ``(state, params) -> state``
    """

    name: str
    param_iter: Callable[[State], Iterable[Tuple[Any, ...]]]
    preconditions: Callable[[State, Tuple[Any, ...]], bool]
    effects: Callable[[State, Tuple[Any, ...]], State]

    def instantiate(self, state: State) -> Iterable[Action]:
        for params in self.param_iter(state):
            # Capture params in default-arg so the closure binds eagerly.
            pre = (lambda s, p=params: self.preconditions(s, p))
            eff = (lambda s, p=params: self.effects(s, p))
            yield Action(
                name=self.name,
                params=tuple(params),
                preconditions=pre,
                effects=eff,
            )


def domain_from_templates(templates: Iterable[ActionTemplate]) -> Domain:
    """Wrap a list of ActionTemplates into a Domain callable.

    Filters out actions whose preconditions don't hold in the given state.
    """
    tlist = list(templates)

    def _domain(state: State) -> Iterable[Action]:
        for t in tlist:
            for action in t.instantiate(state):
                if action.preconditions(state):
                    yield action

    return _domain
