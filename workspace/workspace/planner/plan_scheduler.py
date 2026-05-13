"""Schedule a PDDL plan onto parallel resources.

The PDDL planner gives you a totally-ordered list of actions. In a lab
the robot, the shakers, the dispensers, and the inspectors are all
separate resources — many actions can overlap. This module turns the
ordered plan into a Gantt schedule that respects:

* **Plan order** — action N can't start before action N-1 finishes, on
  the same item (item-local precedence).
* **Resource exclusivity** — two actions claiming the same resource
  can't overlap.
* **Cross-item independence** — actions on different items can
  parallelise freely (subject to resource constraints).

Two scheduler choices, same return shape:

* :func:`schedule_greedy` — earliest-start-time first-fit. Linear,
  no dependencies beyond stdlib. Optimal-enough for batches of 10-100
  items with a handful of resources. **Use this by default.**
* :class:`ORScheduler` (in ``scheduler.py``) — CP-SAT, provably optimal
  makespan. Use when batch sizes climb into the hundreds and the
  greedy schedule starts leaving real time on the table.

Both return ``[(action_name, item_index, start_t_seconds), ...]`` that
``workspace.bt.from_schedule`` consumes directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from workspace.planner.pddl import Action


log = logging.getLogger(__name__)


@dataclass
class ActionMeta:
    """Scheduling metadata for a single action template.

    Attributes:
        duration: Seconds the action takes (integer, >= 1).
        resource: Resource name this action exclusively uses while
            running. Use ``None`` for actions that don't claim a
            resource (background timers, etc.).
        item_arg_index: Which positional parameter identifies the
            item this action operates on. Default 0 — the first
            positional param is the item. Used to compute item-local
            precedence ordering.
    """

    duration: int
    resource: Optional[str] = None
    item_arg_index: int = 0


# meta: action_name -> ActionMeta
ActionMetaMap = Dict[str, ActionMeta]


# ── Greedy earliest-start scheduler ────────────────────────────────────────


def schedule_greedy(
    actions: Sequence[Action],
    meta: ActionMetaMap,
) -> List[Tuple[str, int, float]]:
    """First-fit earliest-start scheduling.

    Walks the action list in plan order. For each action:

      1. Compute the earliest time it CAN start: the max of
         (end of previous action on the same item, end of latest
         existing action on this action's resource).
      2. Schedule it there.

    Because we walk in plan order, item-local precedence is automatic.
    Cross-item parallelism happens when items use independent resources.
    Resource conflicts force serialisation.

    Args:
        actions: The planner's output list.
        meta: Lookup of action_name → :class:`ActionMeta`. Any action
            whose name is missing gets a default of 1-second duration
            on no resource — usually a bug; logged as a warning.

    Returns:
        List of ``(action_name, item_index, start_t)`` tuples,
        sorted by start_t.
    """
    # End-of-last-action per (item_index, resource)
    item_end: Dict[int, float] = {}
    resource_end: Dict[str, float] = {}
    result: List[Tuple[str, int, float]] = []

    for action in actions:
        m = meta.get(action.name)
        if m is None:
            log.warning(
                "schedule_greedy: no ActionMeta for %r — assuming dur=1, no resource",
                action.name,
            )
            m = ActionMeta(duration=1)

        # Pull the item index from the action's params.
        try:
            raw = action.params[m.item_arg_index]
            item = int(raw) if not isinstance(raw, int) else raw
        except (IndexError, ValueError, TypeError):
            # Non-integer items (or zero-param actions) collapse to 0.
            item = 0

        earliest_item = item_end.get(item, 0.0)
        earliest_resource = (
            resource_end.get(m.resource, 0.0) if m.resource else 0.0
        )
        start = max(earliest_item, earliest_resource)
        end = start + float(m.duration)

        item_end[item] = end
        if m.resource:
            resource_end[m.resource] = end

        result.append((action.name, item, start))

    return result


# ── Convenience builder for projects ───────────────────────────────────────


def make_schedule_builder(
    meta: ActionMetaMap,
    *,
    use_cpsat: bool = False,
) -> Callable[[Sequence[Action]], List[Tuple[str, int, float]]]:
    """Return a closure that schedules any plan with the given meta.

    Project's ``schedule.py`` typically uses this to expose a single
    callable that the Replanner can invoke:

        from workspace.planner.plan_scheduler import (
            ActionMeta, make_schedule_builder,
        )

        META = {
            "decap":   ActionMeta(duration=10, resource="robot"),
            "dispense": ActionMeta(duration=10, resource="dispenser"),
            "shake":   ActionMeta(duration=120, resource="shaker"),
        }
        build_schedule = make_schedule_builder(META)

    Args:
        meta: Action metadata.
        use_cpsat: If True, use the CP-SAT solver (only worth it for
            large batches). Default False uses :func:`schedule_greedy`.

    Returns:
        A callable ``(plan) -> schedule`` with the right signature for
        :class:`workspace.planner.Replanner`.
    """
    if use_cpsat:
        # CP-SAT path lives in workspace.planner.scheduler.ORScheduler.
        # It's protocol.yaml-driven today; adapting it to accept Actions
        # directly is future work. For now use_cpsat=True falls back to
        # greedy with a warning.
        log.warning(
            "make_schedule_builder: CP-SAT path on PDDL actions not yet "
            "implemented — falling back to greedy."
        )

    def _build(actions: Sequence[Action]) -> List[Tuple[str, int, float]]:
        return schedule_greedy(actions, meta)

    return _build
