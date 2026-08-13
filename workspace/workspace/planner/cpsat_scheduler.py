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

Setup costs use the disjunctive form — one boolean per pair of
tool-opinionated actions wanting different tools, fixing which of the
two runs first — rather than a permutation circuit. See the "Tool
setup" section for why the circuit was replaced.

Lab-sized problems (a few dozen tasks per slice) solve in tens of
milliseconds; the time limit defaults to 30s as a safety net.
"""

from __future__ import annotations

import heapq
import logging
import time as _time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ortools.sat.python import cp_model

from workspace.planner.pddl import Action
from workspace.planner.plan_scheduler import ActionMeta, ActionMetaMap, _resources


log = logging.getLogger(__name__)


class _ImprovementTracker(cp_model.CpSolverSolutionCallback):
    """Tracks the wall-time of the last objective improvement.

    Kept for the solution count in the solve log; the search itself is
    bounded by ``max_deterministic_time``, not by this.
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


def _precedence_closure(
    predecessors: Optional[List[set]], n: int
) -> List[frozenset]:
    """Transitive closure of the precedence graph, as ``closure[i] =
    every action that must finish before i can start``.

    ``build_precedence`` returns DIRECT predecessors and is not
    guaranteed closed, so a two-hop "j precedes i" is invisible to a
    membership test on the raw sets. The tool circuit uses this to drop
    arcs it can prove impossible, and that proof has to see every hop.

    Computed in plan order, which is a topological order by
    construction, so one forward pass suffices — no fixpoint loop.
    """
    if not predecessors:
        return [frozenset()] * n
    closure: List[set] = [set() for _ in range(n)]
    for i in range(n):
        acc: set = set()
        for p in predecessors[i]:
            if p < n:
                acc.add(p)
                acc |= closure[p]
        closure[i] = acc
    return [frozenset(s) for s in closure]


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


def _augment_with_capacity_spans(
    predecessors: Optional[List[set]],
    capacity_spans: "Optional[dict[str, List[Tuple[int, int]]]]",
    n: int,
) -> List[set]:
    """``predecessors`` plus edges that serialise each capacity
    resource's spans, for use when building the warm-start hint.

    :func:`_add_capacity_mutex` forbids two items' spans on the same
    ``capacity=True`` resource from overlapping, but expresses it as
    ``AddNoOverlap`` over derived intervals — deliberately, so the
    solver may pick any order among them. That freedom means the
    constraint corresponds to NO precedence edge, so
    :func:`_simulate_order`, which knows only about ``predecessors`` and
    per-action resources, cannot see it and happily interleaves spans.

    The resulting hint is then infeasible, and CP-SAT discards an
    infeasible hint wholesale. Measured on bna batch 4: pinning the
    hint's arcs made the model INFEASIBLE, and dropping the capacity
    mutex made that same pinned model solve OPTIMAL in 0.16s — i.e. the
    capacity spans were the only thing wrong with the hint, and they
    were costing the full 19s cold search.

    For the hint only, we pick ONE order — plan order, by each span's
    first action — and chain the spans: ``last(span_i)`` precedes
    ``first(span_i+1)``. That is a valid choice, not a new constraint on
    the solve; the real model still gets the free-order NoOverlap.
    Chaining in plan order cannot create a cycle, because the spans are
    derived from a plan in which they already ran one after another.
    """
    aug: List[set] = [set(predecessors[i]) if predecessors else set()
                      for i in range(n)]
    for spans in (capacity_spans or {}).values():
        if len(spans) < 2:
            continue
        for (_, prev_last), (nxt_first, _) in zip(
            sorted(spans), sorted(spans)[1:]
        ):
            if 0 <= prev_last < n and 0 <= nxt_first < n:
                aug[nxt_first].add(prev_last)
    return aug


def _topological_by_priority(
    order: List[int], predecessors: Optional[List[set]], n: int
) -> List[int]:
    """``order`` repaired into a precedence-respecting sequence, staying
    as close to it as possible.

    :func:`_phase_batched_order` interleaves items to cluster tool use,
    but it only preserves each item's order relative to ITSELF — it is
    blind to cross-item edges (phase barriers, capacity spans, a shaker
    pair). So it routinely emits an order in which some action precedes
    its own predecessor.

    That is not a harmless imperfection. :func:`_simulate_order` reads an
    unscheduled predecessor's end as ``0.0``, so such an order yields
    ``starts`` that violate the model; CP-SAT then rejects the whole hint
    and searches cold. Measured on bna batch 4: 14 violated edges, and
    the solver's first feasible solution landed at 21.9s of a 24.0s
    solve — the warm start was contributing nothing.

    Greedy topological sort with ``order`` as the tie-break priority:
    among the actions whose predecessors are all placed, take whichever
    ``order`` wanted soonest. Precedence is respected exactly; the
    batching survives wherever it was compatible with it.
    """
    if not predecessors:
        return list(order)
    prio = {idx: pos for pos, idx in enumerate(order)}
    unplaced = [set(p for p in predecessors[i] if p < n) for i in range(n)]
    dependents: List[List[int]] = [[] for _ in range(n)]
    for i in range(n):
        for p in unplaced[i]:
            dependents[p].append(i)
    ready = [(prio.get(i, n), i) for i in range(n) if not unplaced[i]]
    heapq.heapify(ready)
    out: List[int] = []
    while ready:
        _, i = heapq.heappop(ready)
        out.append(i)
        for d in dependents[i]:
            unplaced[d].discard(i)
            if not unplaced[d]:
                heapq.heappush(ready, (prio.get(d, n), d))
    if len(out) != n:                      # cycle — fall back untouched
        log.warning("CP-SAT hint: precedence graph has a cycle; "
                    "hinting the raw batched order.")
        return list(order)
    return out


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
    deterministic_limit: float = 0.25,
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
        time_limit_s: Wall-clock SAFETY WALL only. The deterministic
            budget below is what normally stops the search; wall-clock
            must not be the binding limit or the result stops being
            reproducible.
        deterministic_limit: Search budget in the solver's own
            deterministic time units. Reproducible run to run, which
            wall-clock is not — see the note at ``interleave_search``.

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

    # ── Tool setup: pairwise disjunctive separation ────────────────────
    # Every action that participates in the tool sequence is considered —
    # membership is *not* gated on which resource the action uses, since
    # declaring ``tool=X`` on a shaker action also has to reserve the
    # swap to X.
    #
    # For each PAIR of tool-opinionated actions wanting DIFFERENT tools,
    # whichever runs second cannot start until the first has finished
    # plus its own ``tool_swap_duration``. One boolean picks the
    # direction; when precedence already fixes it, no boolean is needed
    # at all. Same-tool pairs cost nothing, which is exactly the pressure
    # that makes the solver cluster same-tool work.
    #
    # This is equivalent to charging the swap only between ADJACENT
    # actions: for a run i(gripper) k(gripper) j(needle), the (i,j) and
    # (k,j) constraints both hold, but only the adjacent (k,j) one binds
    # — the other is slack. So the count of swaps charged is the count
    # actually performed.
    #
    # WAS ``AddCircuit`` over a k-node graph with O(k^2) arc literals and
    # a reified time constraint on each. That is the textbook encoding
    # and it is correct, but a Hamiltonian circuit over k~94 nodes that
    # must simultaneously agree with precedence and no-overlap is a hard
    # FEASIBILITY problem, and CP-SAT spent its whole budget there rather
    # than on optimising. Measured on bna batch 4: 9'171 variables, first
    # feasible solution at 19.2s of a 22.6s solve. The disjunctive form
    # is 1'033 variables and solves in ~8s — and lands a BETTER makespan
    # at every batch size (3590 -> 3545 at batch 4), because the budget
    # goes into search instead of into finding any answer at all.
    #
    # The swap EVENTS are derived below from the solved start times, so
    # nothing downstream needed the circuit's arcs.
    tool_actions = [
        i for i in range(n)
        if tool_resource in resources_list[i] or tool_required[i]
    ]
    k = len(tool_actions)
    if k > 0:
        closure = _precedence_closure(predecessors, n)

        def _setup(lead: int, follow: int) -> int:
            """Gap owed between ``lead`` and ``follow`` when adjacent —
            the same table the circuit encoded on its arcs."""
            if not tool_required[follow]:
                return 0                       # follow doesn't care
            if not tool_required[lead]:
                return swap_durations[follow]  # agnostic -> opinionated
            return (0 if tools[lead] == tools[follow]
                    else swap_durations[follow])

        on_tool_res = [tool_resource in resources_list[t]
                       for t in tool_actions]
        for a_idx in range(k):
            i = tool_actions[a_idx]
            # The robot starts holding nothing, so the first tool-
            # opinionated action of the run cannot begin before its own
            # swap has happened. Slack for every later one.
            if tool_required[i]:
                model.Add(starts[i] >= swap_durations[i])
            for b_idx in range(a_idx + 1, k):
                j = tool_actions[b_idx]
                # A PAIR IS SKIPPED ONLY WHEN IT IS PROVABLY REDUNDANT:
                # both actions sit on ``tool_resource``, so AddNoOverlap
                # already forbids them overlapping, AND neither ordering
                # owes a setup gap. Then this constraint would say
                # nothing NoOverlap does not already say.
                #
                # Skipping more than that is a real bug, not a
                # relaxation: dropping the tool-agnostic pairs lets a
                # swap run underneath a tool-agnostic move, which
                # shortened every example's makespan by exactly one swap
                # (feeder 40 -> 35, capping 90 -> 85, ...). The circuit
                # put ALL of tool_actions on one cycle and thereby
                # totally ordered them, including the ones that are not
                # on ``tool_resource`` at all (a shaker action that
                # declares ``tool=``); those keep their pair constraint.
                if (on_tool_res[a_idx] and on_tool_res[b_idx]
                        and _setup(i, j) == 0 and _setup(j, i) == 0):
                    continue
                i_can_lead = j not in closure[i]
                j_can_lead = i not in closure[j]
                if i_can_lead and j_can_lead:
                    b = model.NewBoolVar(f"tool_prec_{i}_{j}")
                    model.Add(starts[j] >= ends[i] + _setup(i, j)
                              ).OnlyEnforceIf(b)
                    model.Add(starts[i] >= ends[j] + _setup(j, i)
                              ).OnlyEnforceIf(b.Not())
                elif i_can_lead:
                    model.Add(starts[j] >= ends[i] + _setup(i, j))
                elif j_can_lead:
                    model.Add(starts[i] >= ends[j] + _setup(j, i))

        # Warm-start from a phase-batched reference order (see
        # _phase_batched_order) — something close to the batched answer
        # for the solver to confirm and polish rather than rediscover.
        try:
            # The hint must satisfy the capacity mutex too, so both the
            # ordering and the timing pass run against the augmented
            # graph — see _augment_with_capacity_spans.
            hint_preds = _augment_with_capacity_spans(
                predecessors, capacity_spans, n)
            hint_order = _topological_by_priority(
                _phase_batched_order(actions, tools, tool_required),
                hint_preds, n,
            )
            hint_starts = _simulate_order(
                hint_order, durations, resources_list, tools, tool_required,
                swap_durations, hint_preds, tool_resource,
            )
            for i in range(n):
                model.AddHint(starts[i], int(hint_starts[i]))
            # Capacity resources use AddNoOverlap (see _add_capacity_mutex),
            # Capacity resources use AddNoOverlap (see
            # _add_capacity_mutex); the starts[i] hint above already
            # places every span at the batched position
            # _phase_batched_order chose.
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
    # ...AND THE SEED IS NOT ENOUGH ON ITS OWN. With several workers,
    # which one lands a solution first depends on thread timing, so the
    # returned schedule varied run to run (bna batch 4 came back at
    # 3520 / 3590 / 3610 / 3620 across four runs). ``interleave_search``
    # makes the portfolio round-robin deterministically, and the budget
    # below is counted in the solver's own deterministic time rather
    # than wall-clock, so the cutoff falls in the same place every run.
    solver.parameters.interleave_search = True
    solver.parameters.max_deterministic_time = float(deterministic_limit)
    # Wall-clock stays as a hard safety wall only; on a well-behaved
    # instance the deterministic budget is what actually stops the
    # search. A wall-clock stop is what made the result irreproducible,
    # so it must never be the binding limit.
    tracker = _ImprovementTracker()
    status = solver.Solve(model, tracker)

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
