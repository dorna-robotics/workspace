"""Planner package — PDDL forward search + OR-tools scheduler + Replanner.

Three layers, each useful on its own and composable into the
plan-then-schedule-then-build-tree pipeline that pace_bt-style projects
follow:

* :mod:`workspace.planner.pddl` — BFS forward-search planner that
  consumes a state + action templates + goal and returns an ordered
  action list.
* :mod:`workspace.planner.scheduler` — OR-tools CP-SAT scheduler. Given
  the action list + durations + resource constraints, returns a Gantt
  schedule.
* :mod:`workspace.planner.replanner` — convenience glue: observe →
  plan → schedule → BT tree, packaged as a ``rebuild()`` callable the
  :class:`workspace.bt.BTEngine` invokes on replan events.

Projects only need to import what they use:

    from workspace.planner import plan, ActionTemplate, ORScheduler, Replanner
"""

from __future__ import annotations

from workspace.planner.pddl import (
    Action,
    ActionTemplate,
    Domain,
    Goal,
    State,
    domain_from_templates,
    plan,
)
from workspace.planner.plan_scheduler import (
    ActionMeta,
    make_schedule_builder,
    schedule_greedy,
)
from workspace.planner.replanner import (
    ReplanConfig,
    Replanner,
)


# scheduler is optional (depends on ortools); import lazily so projects
# that only use PDDL don't pay the import cost.
def __getattr__(name):
    if name == "ORScheduler":
        from workspace.planner.scheduler import ORScheduler
        return ORScheduler
    raise AttributeError(name)


__all__ = [
    "Action",
    "ActionTemplate",
    "Domain",
    "Goal",
    "State",
    "plan",
    "domain_from_templates",
    "ActionMeta",
    "schedule_greedy",
    "make_schedule_builder",
    "Replanner",
    "ReplanConfig",
    "ORScheduler",  # lazy
]
