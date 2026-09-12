"""Replanning — observation → fresh plan → schedule → tree.

The framework's loop:

  1. Observe the world (facts as the leaves left them, the device bus,
     the operator's mutations).
  2. Plan: the declared route, looked up against that world
     (``workspace.planner.route.plan_route``) — constant time, no search.
  3. Schedule the plan over the resources (CP-SAT or greedy).
  4. Build the BT from the schedule.
  5. Execute. When a leaf fails, an outcome differs from the nominal
     branch, or a window closes — observe again and repeat.

:class:`Replanner` bundles steps 1–4 into one ``rebuild()`` the
:class:`~workspace.bt.engine.BTEngine` calls whenever a leaf raises
``ReplanRequested``. Projects supply nothing here — ``run_protocol``
wires it from the project's ROUTE.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, List, Optional, Sequence, Tuple

import py_trees

from workspace.planner.route import State, Step


log = logging.getLogger(__name__)


Observe = Callable[[Any], State]                          # ctx -> state
Plan = Callable[[State], List[Step]]                      # state -> plan (raises RouteError)
ScheduleBuilder = Callable[[List[Step]], Sequence[Tuple[str, int, float]]]
TreeBuilder = Callable[[Sequence[Tuple[str, int, float]], Any], py_trees.behaviour.Behaviour]


class Replanner:
    """Single entry point for "go from now to a fresh BT".

    Failure modes:
        * ``observe`` raises → ``rebuild`` raises (engine aborts).
        * ``plan`` raises ``RouteError`` → ``rebuild`` raises; the
          engine aborts with the route's own explanation. Operator
          intervention required — the route, not a search, is wrong.
        * Empty plan (goal already satisfied) → a trivial tree that
          succeeds on first tick.
    """

    def __init__(
        self,
        *,
        ctx: Any,
        observe: Observe,
        plan: Plan,
        build_schedule: ScheduleBuilder,
        build_tree: TreeBuilder,
    ):
        self.ctx = ctx
        self._observe = observe
        self._plan = plan
        self._build_schedule = build_schedule
        self._build_tree = build_tree
        self.last_state: Optional[State] = None
        self.last_plan: Optional[List[Step]] = None
        self.last_schedule: Optional[Sequence[Tuple[str, int, float]]] = None
        self._calls = 0

    def rebuild(self) -> py_trees.behaviour.Behaviour:
        """Observe → plan → schedule → tree. Returns the new root behaviour."""
        self._calls += 1
        # 1. Observe.
        state = self._observe(self.ctx)
        if not isinstance(state, frozenset):
            state = frozenset(state)
        self.last_state = state

        # 2. Plan — the route lookup. A RouteError propagates: it names
        #    the item, the step and the facts, and the engine aborts.
        t0 = time.perf_counter()
        actions = self._plan(state)
        self.last_plan = actions
        log.info("Replanner[#%d]: plan=%d step(s) in %.0f ms",
                 self._calls, len(actions), (time.perf_counter() - t0) * 1000)

        # 3. Schedule.
        try:
            schedule = self._build_schedule(actions)
        except Exception:
            log.exception("Replanner: schedule builder raised")
            raise
        self.last_schedule = schedule

        # 4. Tree.
        try:
            root = self._build_tree(schedule, self.ctx)
        except Exception:
            log.exception("Replanner: tree builder raised")
            raise
        return root
