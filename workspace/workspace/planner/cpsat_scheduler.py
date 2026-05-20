"""CP-SAT scheduler — minimum-makespan schedule for a PDDL plan.

Drop-in replacement for :func:`schedule_greedy` with the same I/O
shape. Where greedy walks the plan in order and places each action
at the earliest feasible slot, CP-SAT searches the space of valid
schedules under three constraints:

  * **Causal precedence** — each action starts after every action
    in its predecessor set has finished.
  * **Per-resource non-overlap** — two actions claiming the same
    resource cannot overlap.
  * **Sequence-dependent tool-swap setup** — on the tool-holding
    resource (``robot``), consecutive actions with different tools
    have a setup gap of the incoming action's ``tool_swap_duration``.
    The solver naturally clusters same-tool actions to minimise the
    number of swaps.

The CP-SAT model uses OR-tools' ``AddCircuit`` to express the
"sequence on a resource with sequence-dependent setup" pattern
(textbook job-shop with setup times).

Lab-sized problems (a few dozen tasks per slice) solve in tens of
milliseconds; the time limit defaults to 5s as a safety net.
"""

from __future__ import annotations

import logging
import threading
import time as _time
from typing import List, Optional, Sequence, Tuple

from ortools.sat.python import cp_model

from workspace.planner.pddl import Action
from workspace.planner.plan_scheduler import ActionMeta, ActionMetaMap, _resources


log = logging.getLogger(__name__)


class _ImprovementTracker(cp_model.CpSolverSolutionCallback):
    """Tracks the wall-time of the last objective improvement.

    A separate watchdog thread polls this and calls ``solver.StopSearch()``
    once the gap since the last improvement exceeds a threshold — that
    way we get an optimal-or-near-optimal answer fast without burning
    the entire fixed time budget proving optimality.
    """

    def __init__(self):
        super().__init__()
        self._best: Optional[float] = None
        self.last_improve: float = _time.monotonic()
        self.num_solutions: int = 0

    def on_solution_callback(self) -> None:
        self.num_solutions += 1
        obj = self.ObjectiveValue()
        if self._best is None or obj < self._best:
            self._best = obj
            self.last_improve = _time.monotonic()


def _start_watchdog(
    solver: cp_model.CpSolver,
    tracker: _ImprovementTracker,
    idle_seconds: float,
) -> threading.Event:
    """Spawn a daemon thread that stops the solver after ``idle_seconds``
    of no objective improvement. Returns the stop event used to cancel
    the watchdog when the solver returns normally."""
    stop_event = threading.Event()

    def _watch():
        while not stop_event.wait(0.25):
            if _time.monotonic() - tracker.last_improve > idle_seconds:
                solver.StopSearch()
                return

    thread = threading.Thread(target=_watch, daemon=True)
    thread.start()
    return stop_event


def schedule_cpsat(
    actions: Sequence[Action],
    meta: ActionMetaMap,
    *,
    predecessors: Optional[List[set]] = None,
    tool_resource: str = "robot",
    time_limit_s: float = 30.0,
    no_improvement_s: float = 2.0,
) -> Tuple[
    List[Tuple[str, int, float]],
    List[Tuple[float, Optional[str], Optional[str], int]],
]:
    """Solve the plan-to-schedule problem optimally with CP-SAT.

    Args:
        actions: Output of the PDDL planner — totally-ordered plan.
        meta: ``action_name -> ActionMeta`` lookup.
        predecessors: Optional per-action predecessor index sets, from
            :func:`workspace.bt.dsl.build_precedence`. Without it the
            solver assumes every action is causally independent — which
            will allow ordering reversals that may invalidate runtime
            preconditions. Always pass this.
        tool_resource: Name of the resource that holds the tool changer.
            Sequence-dependent setup costs apply only to this resource.
        time_limit_s: Solver wall-clock budget. Lab batches solve far
            below this; the limit just guards against pathological
            instances.

    Returns:
        Same shape as :func:`schedule_greedy`:

        * ``actions`` = ``[(action_name, item_index, start_t), ...]``
        * ``swaps``   = ``[(start_t, from_tool, to_tool, duration), ...]``

    Raises:
        RuntimeError: if the solver can't find any feasible schedule
            within the time limit. The launcher catches this and falls
            back to greedy.
    """
    n = len(actions)
    if n == 0:
        return [], []

    # Pre-extract per-action info — pure dataflow, no model state yet.
    durations: List[int] = []
    resources_list: List[Tuple[str, ...]] = []
    tools: List[Optional[str]] = []
    swap_durations: List[int] = []
    items: List[int] = []
    for a in actions:
        m = meta.get(a.name) or ActionMeta(duration=1)
        durations.append(int(m.duration))
        resources_list.append(_resources(m.resource))
        tools.append(m.tool)
        swap_durations.append(int(m.tool_swap_duration))
        try:
            raw = a.params[m.item_arg_index]
            items.append(int(raw) if not isinstance(raw, int) else raw)
        except (IndexError, ValueError, TypeError):
            items.append(0)

    # Horizon: sum of every duration + every possible swap + slack.
    # CP-SAT requires integer bounds; this is generous enough that the
    # solver never hits it under reasonable inputs.
    horizon = sum(durations) + sum(swap_durations) + 100

    model = cp_model.CpModel()

    # ── Per-action interval variables ──────────────────────────────────
    starts: List[cp_model.IntVar] = []
    ends: List[cp_model.IntVar] = []
    intervals: List[cp_model.IntervalVar] = []
    for i in range(n):
        s = model.NewIntVar(0, horizon, f"start_{i}")
        e = model.NewIntVar(0, horizon, f"end_{i}")
        iv = model.NewIntervalVar(s, durations[i], e, f"interval_{i}")
        starts.append(s)
        ends.append(e)
        intervals.append(iv)

    # ── Causal precedence ──────────────────────────────────────────────
    if predecessors is not None:
        for i in range(n):
            for j in predecessors[i]:
                model.Add(starts[i] >= ends[j])

    # ── Per-resource non-overlap (except tool_resource) ────────────────
    # tool_resource is sequenced via AddCircuit (with setup times) which
    # implies non-overlap. Other resources use plain AddNoOverlap.
    all_resources: set = set()
    for rs in resources_list:
        all_resources.update(rs)
    for r in all_resources:
        if r == tool_resource:
            continue
        ivs = [intervals[i] for i in range(n) if r in resources_list[i]]
        if len(ivs) > 1:
            model.AddNoOverlap(ivs)

    # ── Tool resource: AddCircuit with sequence-dependent setup ────────
    # Standard encoding for job-shop with sequence-dependent setup
    # times. AddCircuit forces the active arc literals to form a single
    # Hamiltonian cycle 0 -> first -> ... -> last -> 0; each active
    # arc (i, j) carries a constraint
    #
    #     start[j] >= end[i] + setup(tool[i] -> tool[j])
    #
    # so the solver naturally clusters same-tool actions to minimise
    # the number of swaps that contribute to makespan.
    #
    # Cost: O(k^2) arc variables. For k ~ 30 the model gets slow enough
    # to need the warm-start hint below; we feed it the greedy schedule
    # as a feasible starting point so CP-SAT has something to improve
    # from t=0 rather than searching cold.
    tool_actions = [i for i in range(n) if tool_resource in resources_list[i]]
    k = len(tool_actions)
    arc_lits: List[cp_model.IntVar] = []          # all arc literals
    arc_pair: List[Tuple[int, int]] = []          # (from_node, to_node)
    if k > 0:
        arcs: List[Tuple[int, int, cp_model.IntVar]] = []
        for a_idx in range(k):
            i = tool_actions[a_idx]
            # dummy -> i: i is the first robot action
            arc_in = model.NewBoolVar(f"arc_dummy_{i}")
            arcs.append((0, a_idx + 1, arc_in))
            arc_lits.append(arc_in); arc_pair.append((0, a_idx + 1))
            if tools[i] is not None:
                model.Add(starts[i] >= swap_durations[i]).OnlyEnforceIf(arc_in)
            # i -> dummy: i is the last robot action
            arc_out = model.NewBoolVar(f"arc_{i}_dummy")
            arcs.append((a_idx + 1, 0, arc_out))
            arc_lits.append(arc_out); arc_pair.append((a_idx + 1, 0))
            for b_idx in range(k):
                if a_idx == b_idx:
                    continue
                j = tool_actions[b_idx]
                arc = model.NewBoolVar(f"arc_{i}_{j}")
                arcs.append((a_idx + 1, b_idx + 1, arc))
                arc_lits.append(arc); arc_pair.append((a_idx + 1, b_idx + 1))
                setup = 0
                if (tools[i] is not None
                        and tools[j] is not None
                        and tools[i] != tools[j]):
                    setup = swap_durations[j]
                model.Add(starts[j] >= ends[i] + setup).OnlyEnforceIf(arc)
        model.AddCircuit(arcs)

        # Warm-start from greedy.
        try:
            from workspace.planner.plan_scheduler import schedule_greedy
            g_actions, _ = schedule_greedy(
                actions, meta, predecessors=predecessors,
                tool_resource=tool_resource,
            )
            for i in range(n):
                model.AddHint(starts[i], int(g_actions[i][2]))
            greedy_robot_order = sorted(
                tool_actions, key=lambda i: g_actions[i][2],
            )
            pos = {ti: idx + 1 for idx, ti in enumerate(tool_actions)}
            prev_node = 0
            for i in greedy_robot_order:
                cur_node = pos[i]
                key = (prev_node, cur_node)
                if key in arc_pair:
                    model.AddHint(arc_lits[arc_pair.index(key)], 1)
                prev_node = cur_node
            if greedy_robot_order:
                final_key = (pos[greedy_robot_order[-1]], 0)
                if final_key in arc_pair:
                    model.AddHint(arc_lits[arc_pair.index(final_key)], 1)
        except Exception:
            log.debug("CP-SAT warm-start hint failed; solving cold.",
                      exc_info=True)

    # ── Objective: minimise makespan ──────────────────────────────────
    makespan = model.NewIntVar(0, horizon, "makespan")
    model.AddMaxEquality(makespan, ends)
    model.Minimize(makespan)

    # ── Solve ─────────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_s)
    # All cores. Pi 5 has 4. CP-SAT parallelises well on disjunctive
    # scheduling.
    solver.parameters.num_search_workers = 4
    solver.parameters.relative_gap_limit = 0.01

    # Watchdog: stop the solver after ``no_improvement_s`` of no
    # objective improvement, even if there's budget left. Saves us from
    # burning 25+ seconds proving optimality on solutions we already
    # found. ``time_limit_s`` is the hard cap; the watchdog is the
    # soft cap that activates earlier when search has plateaued.
    tracker = _ImprovementTracker()
    stop_event = _start_watchdog(solver, tracker, no_improvement_s)
    try:
        status = solver.Solve(model, tracker)
    finally:
        stop_event.set()

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(
            f"CP-SAT scheduler: status={solver.StatusName(status)} — "
            "no feasible schedule within time limit. Falling back to greedy."
        )

    log.info(
        "CP-SAT: %d actions, makespan=%d (%s, %.3fs wall)",
        n, solver.Value(makespan),
        solver.StatusName(status), solver.WallTime(),
    )

    # ── Extract action start times ────────────────────────────────────
    actions_out: List[Tuple[str, int, float]] = [
        (actions[i].name, items[i], float(solver.Value(starts[i])))
        for i in range(n)
    ]

    # ── Derive swap events from the solved tool_resource sequence ─────
    # Walk the tool_resource actions in chronological order; emit a
    # swap event whenever the held tool changes. The swap fills the
    # gap the solver left between consecutive different-tool actions.
    swaps_out: List[Tuple[float, Optional[str], Optional[str], int]] = []
    if k > 0:
        ordered = sorted(tool_actions, key=lambda i: solver.Value(starts[i]))
        current_tool: Optional[str] = None
        for i in ordered:
            if tools[i] is not None and tools[i] != current_tool:
                dur = swap_durations[i]
                # Place the swap so it ends exactly when action i starts.
                # The solver already reserved this gap via the setup
                # constraint, so start[i] - dur >= prev_end.
                swap_start = max(0.0, float(solver.Value(starts[i])) - dur)
                swaps_out.append((swap_start, current_tool, tools[i], dur))
                current_tool = tools[i]

    return actions_out, swaps_out
