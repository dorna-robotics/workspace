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
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
            # Never stop before a solution EXISTS. ``last_improve`` is
            # seeded at construction, so without this the watchdog
            # counts idle time against a search that simply hasn't
            # found its first answer yet — on a batch big enough to
            # need more than ``idle_seconds`` to get there, it killed
            # the solve, schedule_cpsat raised, and the launcher
            # silently fell back to greedy. Greedy never reorders, so
            # the whole batch then ran in raw plan order (item by
            # item, tool swap each way) with nothing in the logs
            # beyond one warning. The watchdog's job is "stop
            # polishing once we've plateaued" — that presupposes
            # something to polish.
            if tracker.num_solutions == 0:
                continue
            if _time.monotonic() - tracker.last_improve > idle_seconds:
                solver.StopSearch()
                return

    thread = threading.Thread(target=_watch, daemon=True)
    thread.start()
    return stop_event


def _phase_batched_order(
    actions: Sequence[Action],
    tools: List[Optional[str]],
    tool_required: List[bool],
) -> List[int]:
    """A good STARTING-POINT ordering for the warm-start hint below —
    not a constraint, just something close enough to the batched
    answer that CP-SAT confirms/polishes it instead of discovering the
    whole clustering from a cold, unguided search.

    Splits each item's own action indices (already in causally-valid
    relative order, since we scan the plan 0..n-1 and append per item
    as we go) into contiguous same-tool blocks, then interleaves
    items' blocks breadth-first by block position: every item's FIRST
    tool-phase, then every item's second, and so on — precisely the
    "gripper-phase for every item, then pipettor-phase for every item,
    then gripper-phase-2 for every item" pattern batching is after.
    Items are only ever reordered relative to EACH OTHER; no item's
    own action sequence is reordered relative to itself, so this stays
    close to a valid plan even before the solver checks it (hints are
    advisory — an imperfect heuristic here can only cost search time,
    never correctness, since the real constraints are enforced by the
    model regardless of what's hinted).

    Actions with no params (``Start``, ``Park`` — global, one-off
    bookends) aren't tied to any item; they're placed before/after the
    per-item block in their original relative position.
    """
    n = len(actions)
    itemless = [i for i in range(n) if not actions[i].params]
    by_item: "Dict[Any, List[int]]" = {}
    for i in range(n):
        if not actions[i].params:
            continue
        by_item.setdefault(actions[i].params[0], []).append(i)

    phase_lists = []
    # Declared order, not the order items happen to appear in the plan —
    # the hint should already look like the answer the ordering
    # constraint will insist on, so an early stop still lands on it.
    for _key in sorted(by_item, key=lambda k: (isinstance(k, str), k)):
        idxs = by_item[_key]
        blocks: List[List[int]] = []
        cur_block: List[int] = []
        cur_key: Any = object()  # sentinel != any real tool/None-first-time
        for i in idxs:
            key = tools[i] if tool_required[i] else None
            if cur_block and key != cur_key:
                blocks.append(cur_block)
                cur_block = []
            cur_block.append(i)
            cur_key = key
        if cur_block:
            blocks.append(cur_block)
        phase_lists.append(blocks)

    order: List[int] = []
    max_phases = max((len(b) for b in phase_lists), default=0)
    for p in range(max_phases):
        for blocks in phase_lists:
            if p < len(blocks):
                order.extend(blocks[p])

    first_item_idx = min((i for i in range(n) if actions[i].params), default=n)
    pre = [i for i in itemless if i < first_item_idx]
    post = [i for i in itemless if i >= first_item_idx]
    return pre + order + post


def _simulate_order(
    order: List[int],
    durations: List[int],
    resources_list: List[Tuple[str, ...]],
    tools: List[Optional[str]],
    tool_required: List[bool],
    swap_durations: List[int],
    predecessors: Optional[List[set]],
    tool_resource: str,
) -> "Dict[int, float]":
    """Simplified :func:`workspace.planner.plan_scheduler.schedule_greedy`
    timing pass, but driven by an arbitrary ``order`` (a permutation of
    ``range(len(durations))``) instead of the plan's own order — used
    to turn :func:`_phase_batched_order`'s permutation into concrete
    ``starts[i]`` hint VALUES the solver can check for consistency.
    Keyed throughout by the ORIGINAL action index, so ``predecessors``
    (indexed by original plan position) needs no remapping.
    """
    action_end: "Dict[int, float]" = {}
    resource_end: "Dict[str, float]" = {}
    current_tool: Optional[str] = None
    starts_hint: "Dict[int, float]" = {}
    for i in order:
        earliest_causal = max(
            (action_end.get(j, 0.0) for j in (predecessors[i] if predecessors else ())),
            default=0.0,
        )
        resources = resources_list[i] or ("robot",)
        earliest_resource = max(
            (resource_end.get(r, 0.0) for r in resources), default=0.0,
        )
        start = max(earliest_causal, earliest_resource)
        if tool_required[i] and tools[i] != current_tool:
            start += swap_durations[i]
            current_tool = tools[i]
        starts_hint[i] = start
        end = start + durations[i]
        action_end[i] = end
        for r in resources:
            resource_end[r] = end
    return starts_hint


def _add_capacity_mutex(
    model: cp_model.CpModel,
    starts: List[cp_model.IntVar],
    ends: List[cp_model.IntVar],
    capacity_spans: "dict[str, List[Tuple[int, int]]]",
    horizon: int,
) -> None:
    """Forbid two items' spans on the same capacity resource from
    overlapping in time — the mutual exclusion a ``capacity=True``
    fact implies, in place of the precedence edges
    :func:`workspace.bt.dsl.build_precedence` deliberately omits for
    it (see that function's docstring for the failure mode this
    replaces).

    Each span ``(first_idx, last_idx)`` — an item's exclusive
    occupancy, from :func:`workspace.bt.dsl.derive_capacity_spans` —
    becomes ONE derived interval running ``starts[first_idx] ..
    ends[last_idx]``; ``AddNoOverlap`` over a resource's spans lets the
    solver pick ANY order among them (unlike the tool circuit, a
    capacity mutex has no sequence-dependent setup cost to schedule,
    so plain disjunctive-scheduling non-overlap is the right, far
    cheaper primitive here — no permutation/circuit variables needed,
    unlike the tool sequence below which genuinely needs one because
    its arc cost depends on WHICH pair is adjacent).

    A resource with 0 or 1 span needs no constraint (nothing to
    exclude against) and is skipped.
    """
    for resource, spans in capacity_spans.items():
        if len(spans) < 2:
            continue
        intervals = []
        for idx, (first, last) in enumerate(spans):
            size = model.NewIntVar(0, horizon, f"cap_{resource}_size_{idx}")
            intervals.append(
                model.NewIntervalVar(starts[first], size, ends[last], f"cap_{resource}_span_{idx}")
            )
        model.AddNoOverlap(intervals)


def schedule_cpsat(
    actions: Sequence[Action],
    meta: ActionMetaMap,
    *,
    predecessors: Optional[List[set]] = None,
    capacity_spans: Optional["dict[str, List[Tuple[int, int]]]"] = None,
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
        capacity_spans: Optional ``{resource_name: [(first_idx, last_idx), ...]}``
            from :func:`workspace.bt.dsl.derive_capacity_spans` — one
            entry per item's exclusive-occupancy span of a
            ``capacity=True`` predicate (gripper payload, scale seat,
            …). Each resource's spans get a same-resource mutual-
            exclusion circuit so different items' visits can never
            interleave, WITHOUT tying them to the specific order the
            classical planner's plan happened to serialize them in —
            this is what lets the solver batch same-tool work across
            items again once an item revisits a tool a second time
            (see :func:`workspace.bt.dsl.build_precedence`'s docstring
            for the failure mode this replaces).
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
    tool_required: List[bool] = []
    swap_durations: List[int] = []
    items: List[int] = []
    has_item: List[bool] = []
    for a in actions:
        m = meta.get(a.name) or ActionMeta(duration=1)
        durations.append(int(m.duration))
        resources_list.append(_resources(m.resource))
        tools.append(m.tool)
        tool_required.append(bool(m.tool_required))
        swap_durations.append(int(m.tool_swap_duration))
        try:
            raw = a.params[m.item_arg_index]
            items.append(int(raw) if not isinstance(raw, int) else raw)
            has_item.append(True)
        except (IndexError, ValueError, TypeError):
            # Global bookends (Start / Park) belong to no item. They must
            # not be mistaken for item 0 — that would put Start's t=0 in
            # item 0's timeline and defeat the ordering below.
            items.append(0)
            has_item.append(False)

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

    # ── Capacity-resource mutual exclusion ─────────────────────────────
    # Replaces the precedence edges build_precedence deliberately omits
    # for capacity=True facts — see _add_capacity_mutex's docstring.
    if capacity_spans:
        _add_capacity_mutex(model, starts, ends, capacity_spans, horizon)

    # ── Per-resource non-overlap ───────────────────────────────────────
    # Every resource gets basic mutex via ``AddNoOverlap``. The tool
    # circuit below adds sequence-dependent setup times on top, but is
    # an *additional* constraint, not a replacement — actions on the
    # robot still need to non-overlap with each other on the resource.
    all_resources: set = set()
    for rs in resources_list:
        all_resources.update(rs)
    for r in all_resources:
        ivs = [intervals[i] for i in range(n) if r in resources_list[i]]
        if len(ivs) > 1:
            model.AddNoOverlap(ivs)

    # ── Tool circuit: AddCircuit with sequence-dependent setup ─────────
    # Every action that participates in the tool sequence joins the
    # circuit. Membership is *not* gated on which resource the action
    # uses — declaring ``tool=X`` on a shaker action also pulls it
    # into the tool sequence so the swap to X is properly reserved.
    # Tool-agnostic robot actions (``Start``-like) also join so their
    # interval anchors the chain at t=0 without forcing a swap.
    #
    # Setup cost on arc i→j (see swap-derivation pass for emission):
    #
    #     i agnostic, j agnostic : 0   (no tool change matters)
    #     i agnostic, j opinion  : swap_durations[j]
    #     i opinion,  j agnostic : 0   (j inherits the tool i set)
    #     i opinion,  j opinion  : 0 if same tool, else swap_durations[j]
    #
    # Cost: O(k^2) arc variables. For k ~ 30 the model gets slow enough
    # to need the warm-start hint below; we feed it the greedy schedule
    # as a feasible starting point so CP-SAT has something to improve
    # from t=0 rather than searching cold.
    tool_actions = [
        i for i in range(n)
        if tool_resource in resources_list[i] or tool_required[i]
    ]
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
            # Reserve swap time only if the first robot action is
            # itself tool-opinionated. A tool-agnostic first action
            # (e.g. ``Start``) doesn't need anything mounted, so the
            # circuit starts at t=0 with no setup penalty.
            if tool_required[i]:
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
                # See the table in the section header for the rule.
                if not tool_required[j]:
                    setup = 0                            # j doesn't care
                elif not tool_required[i]:
                    setup = swap_durations[j]            # agnostic → opinion
                else:
                    setup = 0 if tools[i] == tools[j] else swap_durations[j]
                model.Add(starts[j] >= ends[i] + setup).OnlyEnforceIf(arc)
        model.AddCircuit(arcs)

        # Warm-start: hint both the tool circuit and the capacity
        # circuits from ONE phase-batched reference order (see
        # _phase_batched_order) rather than the raw (unbatched) plan
        # order. Hinting the unbatched order is a valid, cheap-to-find
        # starting point, but it's exactly the solution the fix exists
        # to move the solver AWAY from — with a same-resource circuit
        # now sized like a second tool circuit (a real batch's
        # hand_empty circuit has as many nodes as the tool circuit
        # itself) and given nothing but that starting point, the
        # solver was spending 10s of seconds re-discovering the
        # clustering from scratch. Hinting the already-batched order
        # instead gives it something to confirm/polish.
        try:
            hint_order = _phase_batched_order(actions, tools, tool_required)
            hint_starts = _simulate_order(
                hint_order, durations, resources_list, tools, tool_required,
                swap_durations, predecessors, tool_resource,
            )
            for i in range(n):
                model.AddHint(starts[i], int(hint_starts[i]))
            hinted_tool_order = [i for i in hint_order if i in set(tool_actions)]
            pos = {ti: idx + 1 for idx, ti in enumerate(tool_actions)}
            prev_node = 0
            for i in hinted_tool_order:
                cur_node = pos[i]
                key = (prev_node, cur_node)
                if key in arc_pair:
                    model.AddHint(arc_lits[arc_pair.index(key)], 1)
                prev_node = cur_node
            if hinted_tool_order:
                final_key = (pos[hinted_tool_order[-1]], 0)
                if final_key in arc_pair:
                    model.AddHint(arc_lits[arc_pair.index(final_key)], 1)
            # Capacity resources use AddNoOverlap (see _add_capacity_mutex),
            # not a permutation circuit — it needs no arc hints of its
            # own; the starts[i] hint above already places every span
            # at the batched position _phase_batched_order chose.
        except Exception:
            log.debug("CP-SAT warm-start hint failed; solving cold.",
                      exc_info=True)

    # ── Interchangeable items start in declared order ──────────────────
    # Minimising makespan alone says nothing about WHICH of several
    # equally-fast schedules to return, so a batch of identical items
    # came back in whatever order the search happened to land on — and,
    # with four workers and a wall-clock cutoff, a different order on
    # the next launch. Operators read that as the robot skipping around.
    #
    # Two items whose action-name sequences are identical are isomorphic
    # in this model: swapping their variable assignments maps any
    # schedule to an equally-good one. Fixing their relative start order
    # therefore removes a symmetry, never a distinct solution — the
    # optimal makespan is provably unchanged, and pruning those
    # permutations usually makes the search converge sooner.
    #
    # Items whose sequences DIFFER (one tube already decapped, a
    # different tool path) are left unconstrained: they are not
    # interchangeable, so ordering them could cost makespan.
    _item_actions: "Dict[int, List[int]]" = {}
    for i in range(n):
        if has_item[i]:
            _item_actions.setdefault(items[i], []).append(i)

    _by_signature: "Dict[Tuple[str, ...], List[int]]" = {}
    for key, idxs in _item_actions.items():
        sig = tuple(actions[i].name for i in idxs)
        _by_signature.setdefault(sig, []).append(key)

    _first_start: "Dict[int, Any]" = {}
    _sym_pairs = 0
    for group in _by_signature.values():
        if len(group) < 2:
            continue
        group.sort()                    # declared order == item index order
        for key in group:
            if key not in _first_start:
                v = model.NewIntVar(0, horizon, f"first_start_i{key}")
                model.AddMinEquality(v, [starts[i] for i in _item_actions[key]])
                _first_start[key] = v
        for a_key, b_key in zip(group, group[1:]):
            model.Add(_first_start[a_key] <= _first_start[b_key])
            _sym_pairs += 1

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
    # Same plan in, same schedule out. Without a fixed seed two launches
    # of the identical batch could return different (equally optimal)
    # schedules, which makes a bench run irreproducible and the replay
    # gate's makespan drift between runs.
    solver.parameters.random_seed = 0

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
        "CP-SAT: %d actions, makespan=%d (%s, %.3fs wall, %d item-order pair%s)",
        n, solver.Value(makespan),
        solver.StatusName(status), solver.WallTime(),
        _sym_pairs, "" if _sym_pairs == 1 else "s",
    )

    # ── Extract action start times ────────────────────────────────────
    actions_out: List[Tuple[str, int, float]] = [
        (actions[i].name, items[i], float(solver.Value(starts[i])))
        for i in range(n)
    ]

    # ── Derive swap events from the solved tool_resource sequence ─────
    # Walk every robot action in chronological order. Tool-agnostic
    # ones don't change ``current_tool``; tool-opinionated ones emit a
    # swap whenever the held tool actually changes (including X→None
    # drops, so the GUI shows the drop step).
    swaps_out: List[Tuple[float, Optional[str], Optional[str], int]] = []
    if k > 0:
        ordered = sorted(tool_actions, key=lambda i: solver.Value(starts[i]))
        current_tool: Optional[str] = None
        for i in ordered:
            if not tool_required[i]:
                continue
            if tools[i] != current_tool:
                dur = swap_durations[i]
                # Place the swap so it ends exactly when action i starts.
                # The solver already reserved this gap via the setup
                # constraint, so start[i] - dur >= prev_end.
                swap_start = max(0.0, float(solver.Value(starts[i])) - dur)
                swaps_out.append((swap_start, current_tool, tools[i], dur))
                current_tool = tools[i]

    return actions_out, swaps_out
