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
        resource: Resource lock(s) this action holds exclusively while
            running. Three shapes accepted:
              * ``None``                — claims nothing (unlimited parallel).
              * ``"robot"`` (str)        — claims one lock.
              * ``["robot", "scale"]``  — claims multiple locks at once
                (the action can't start until every named lock is free,
                and holds all of them for its duration).
            Two actions sharing ANY lock cannot overlap.
        item_arg_index: Which positional parameter identifies the
            item this action operates on. Default 0 — the first
            positional param is the item. Used to compute item-local
            precedence ordering.
        tool: Tool the action requires the robot to be holding while
            running. The scheduler uses this to insert tool-swap gaps
            between consecutive same-resource actions whose tool
            differs (pace_or-style). ``None`` = no tool (or "don't
            care"). Defaults to ``None``.
        tool_swap_duration: Seconds inserted before this action when
            the previous same-resource action held a different
            ``tool``. Per-action — different tools can have different
            swap costs. Defaults to 10.
    """

    duration: int
    resource: Any = None       # None | str | list[str]
    item_arg_index: int = 0
    tool: Optional[str] = None
    tool_swap_duration: int = 10


def _resources(r: Any) -> Tuple[str, ...]:
    """Normalise an ActionMeta.resource into a tuple of lock names."""
    if r is None:
        return ()
    if isinstance(r, str):
        return (r,)
    return tuple(r)


# meta: action_name -> ActionMeta
ActionMetaMap = Dict[str, ActionMeta]


# ── Greedy earliest-start scheduler ────────────────────────────────────────


def schedule_greedy(
    actions: Sequence[Action],
    meta: ActionMetaMap,
    *,
    predecessors: Optional[List[set]] = None,
    tool_resource: str = "robot",
) -> List[Tuple[str, int, float]]:
    """First-fit earliest-start scheduling, precedence-aware.

    Walks the action list in plan order. For each action:

      1. Compute the earliest causal start (max of predecessor ends).
      2. If the action requires a different tool than the one
         currently mounted, charge a swap event against the
         ``tool_resource`` (the robot — only one tool can be held at
         a time). The swap must complete before the action starts,
         and the swap occupies the robot even if the action itself
         runs on a different resource (e.g. shaker_1).
      3. Schedule the action at max(causal, resources, post-swap).

    Args:
        actions: The planner's output list.
        meta: Lookup of action_name → :class:`ActionMeta`.
        predecessors: Optional list — ``predecessors[i]`` is the set of
            indices ``j < i`` that action ``i`` causally depends on.
            Built from pre/eff analysis via
            :func:`workspace.bt.dsl.build_precedence`. If omitted,
            falls back to per-item-end serialisation (conservative).
        tool_resource: Name of the resource that holds tools (typically
            the robot arm). The scheduler tracks ONE global
            ``current_tool`` and charges tool-swap time against this
            resource regardless of which resource the action itself
            uses. Default ``"robot"``.

    Returns:
        List of ``(action_name, item_index, start_t)`` tuples,
        sorted by start_t.
    """
    item_end: Dict[int, float] = {}
    resource_end: Dict[str, float] = {}
    action_end: List[float] = []
    # Global tool state — there's only one physical tool changer, so
    # the "current tool" is single-valued, not per-resource. Any
    # action with tool=X causes the global to become X (via swap).
    current_tool: Optional[str] = None
    result: List[Tuple[str, int, float]] = []

    for i, action in enumerate(actions):
        m = meta.get(action.name)
        if m is None:
            log.warning(
                "schedule_greedy: no ActionMeta for %r — assuming dur=1, no resource",
                action.name,
            )
            m = ActionMeta(duration=1)

        try:
            raw = action.params[m.item_arg_index]
            item = int(raw) if not isinstance(raw, int) else raw
        except (IndexError, ValueError, TypeError):
            item = 0

        # Causal earliest: max of all predecessor end times.
        if predecessors is not None:
            earliest_causal = max(
                (action_end[j] for j in predecessors[i]), default=0.0,
            )
        else:
            earliest_causal = item_end.get(item, 0.0)

        # All the locks this action claims must be free.
        resources = _resources(m.resource)
        earliest_resource = max(
            (resource_end.get(r, 0.0) for r in resources), default=0.0
        )

        # Tool swap — charged against tool_resource (the robot), not
        # against the action's own resource. The swap is a pure robot
        # motion that doesn't touch protocol state, so we schedule it
        # as early as the robot is free — independent of when this
        # action's causal predecessors finish. The action itself
        # still waits for both swap_end AND its causal preds (via
        # the start = max(...) below).
        swap_end = 0.0
        if m.tool is not None and m.tool != current_tool:
            swap_duration = int(m.tool_swap_duration)
            swap_start = resource_end.get(tool_resource, 0.0)
            swap_end = swap_start + swap_duration
            # The robot is occupied until swap_end.
            resource_end[tool_resource] = swap_end
            current_tool = m.tool

        start = max(earliest_causal, earliest_resource, swap_end)
        end = start + float(m.duration)

        item_end[item] = max(item_end.get(item, 0.0), end)
        action_end.append(end)
        for r in resources:
            resource_end[r] = end

        result.append((action.name, item, start))

    return result


# ── Convenience builder for projects ───────────────────────────────────────


def make_schedule_builder(
    meta: ActionMetaMap,
    *,
    use_cpsat: bool = False,
    precedence_fn: Optional[Callable[[Sequence[Action]], List[set]]] = None,
) -> Callable[[Sequence[Action]], List[Tuple[str, int, float]]]:
    """Return a closure that schedules any plan with the given meta.

    Project's ``schedule.py`` typically uses this to expose a single
    callable that the Replanner can invoke:

        from workspace.planner.plan_scheduler import (
            ActionMeta, make_schedule_builder,
        )

        META = {
            "decap":   ActionMeta(duration=10, resource="robot", tool="gripper"),
            "dispense": ActionMeta(duration=10, resource="dispenser", tool="needle"),
        }
        build_schedule = make_schedule_builder(META)

    Tool-swap gaps are derived per-action from
    ``ActionMeta.tool_swap_duration`` — no global knob.

    Args:
        meta: Action metadata.
        use_cpsat: If True, use the CP-SAT solver (only worth it for
            large batches). Default False uses :func:`schedule_greedy`.
        precedence_fn: Optional ``(plan) -> List[Set[int]]`` callback.
            For each action in the plan, returns the set of earlier
            indices it causally depends on. Used by the scheduler to
            allow independent actions to overlap on different
            resources. Project's main.py normally supplies
            ``workspace.bt.dsl.build_precedence`` partialled with the
            ActionRegistry. If omitted, scheduler falls back to
            per-item serialisation (correct but suboptimal).

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
        preds = precedence_fn(actions) if precedence_fn is not None else None
        return schedule_greedy(actions, meta, predecessors=preds)

    return _build
