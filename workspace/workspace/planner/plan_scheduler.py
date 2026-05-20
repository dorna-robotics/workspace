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
) -> Tuple[List[Tuple[str, int, float]], List[Tuple[float, Optional[str], Optional[str], int]]]:
    """First-fit earliest-start scheduling, precedence-aware.

    For each action in plan order:

      1. Causal earliest = max end times of predecessor actions.
      2. If the action requires a different tool, schedule a
         tool-swap event against ``tool_resource`` (the robot) as
         early as the robot is free. The swap is a pure motion that
         doesn't touch protocol state; it fills idle robot windows.
      3. Action start = max(causal, all-resources-free, post-swap).

    Args:
        actions: The planner's output list.
        meta: action_name → :class:`ActionMeta`.
        predecessors: Optional — ``predecessors[i]`` = indices that
            action ``i`` depends on. From
            :func:`workspace.bt.dsl.build_precedence`. If omitted,
            falls back to per-item-end serialisation (conservative).
        tool_resource: Name of the tool-holder resource. There's only
            one physical tool changer per robot, so this is a single
            global lock. Default ``"robot"``.

    Returns:
        Tuple ``(actions, swaps)`` where:

        * ``actions`` = list of ``(action_name, item_index, start_t)``
        * ``swaps``   = list of ``(start_t, from_tool, to_tool, duration)``
          — explicit tool-swap events for ``from_schedule`` to insert
          into the tree as :class:`SwapLeaf` nodes.
    """
    item_end: Dict[int, float] = {}
    resource_end: Dict[str, float] = {}
    action_end: List[float] = []
    # Global tool state — single tool changer.
    current_tool: Optional[str] = None
    actions_out: List[Tuple[str, int, float]] = []
    swaps_out: List[Tuple[float, Optional[str], Optional[str], int]] = []

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

        if predecessors is not None:
            earliest_causal = max(
                (action_end[j] for j in predecessors[i]), default=0.0,
            )
        else:
            earliest_causal = item_end.get(item, 0.0)

        resources = _resources(m.resource)
        earliest_resource = max(
            (resource_end.get(r, 0.0) for r in resources), default=0.0
        )

        # Tool swap event — emit as a first-class scheduled task.
        # Schedule it at the earliest moment the robot is idle. The
        # swap occupies the robot's timeline; the action waits for
        # it only if the action itself uses the robot.
        swap_end = 0.0
        if m.tool is not None and m.tool != current_tool:
            swap_duration = int(m.tool_swap_duration)
            swap_start = resource_end.get(tool_resource, 0.0)
            swap_end = swap_start + swap_duration
            swaps_out.append(
                (swap_start, current_tool, m.tool, swap_duration)
            )
            resource_end[tool_resource] = swap_end
            current_tool = m.tool

        # Only gate the action's start on swap_end if the action
        # actually uses the tool-holder resource (the robot). For
        # actions on other resources (shaker_1, scale, …) the tool
        # mount is a future-prep concern — the SwapLeaf runs in
        # parallel; the action proceeds on its own resource.
        gate_swap = swap_end if tool_resource in resources else 0.0
        start = max(earliest_causal, earliest_resource, gate_swap)
        end = start + float(m.duration)

        item_end[item] = max(item_end.get(item, 0.0), end)
        action_end.append(end)
        for r in resources:
            resource_end[r] = end

        actions_out.append((action.name, item, start))

    return actions_out, swaps_out


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
    def _build(actions: Sequence[Action]) -> List[Tuple[str, int, float]]:
        preds = precedence_fn(actions) if precedence_fn is not None else None
        if use_cpsat:
            try:
                # Local import — ortools is an optional dependency. If
                # the project doesn't have it, the failure surfaces here
                # and we fall back to greedy without crashing the run.
                from workspace.planner.cpsat_scheduler import schedule_cpsat
                return schedule_cpsat(actions, meta, predecessors=preds)
            except Exception as ex:
                log.warning(
                    "CP-SAT scheduler failed (%s: %s) — falling back to greedy",
                    type(ex).__name__, ex,
                )
        return schedule_greedy(actions, meta, predecessors=preds)

    return _build
