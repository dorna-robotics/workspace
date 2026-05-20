"""Tree-construction helpers — the project's ``tree.py`` builds with these.

py_trees gives you the primitives (Sequence, Selector aka Fallback,
Parallel, decorators); these helpers wrap the common workspace patterns:

* ``guarded(...)`` — run an action only when a precondition holds,
  else skip to the alternative (or fail).
* ``with_retry(...)`` — try-N-times before declaring failure.
* ``with_recovery(...)`` — on failure, run a recovery subtree, then the
  parent decides whether to retry the protected subtree.
* ``replan_on_failure(...)`` — wrap a subtree so its FAILURE raises
  ``ReplanRequested`` to the engine instead of propagating up.
* ``from_schedule(...)`` — consume an OR-tools schedule (list of
  ``(action_name, item_index, start_t)`` tuples) and emit a Sequence /
  Parallel tree that respects parallelism.

Project trees compose these into ``build_tree()`` — typically <50 lines.

All helpers return py_trees Behaviours and can be composed freely.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import py_trees

from workspace.bt.behaviours import RecipeAction, WorkspaceContext
from workspace.bt.engine import ReplanRequested


log = logging.getLogger(__name__)


# ── SwapLeaf — explicit tool-swap node in the tree ─────────────────────────


class SwapLeaf(RecipeAction):
    """Behaviour that performs a tool swap (place current, pick next).

    Inserted by :func:`from_schedule` at the scheduled swap time so
    swaps fill robot idle windows (e.g. during a long shake) rather
    than firing implicitly inside the next action's ``_ensure_tool``.

    Updates ``ctx.meta["current_tool"]`` under the ctx lock so the
    next action's ``_ensure_tool`` sees the tool already mounted and
    becomes a no-op (no double swap).
    """

    def __init__(
        self,
        ctx: WorkspaceContext,
        from_tool: Optional[str],
        to_tool: Optional[str],
    ):
        name = f"swap({from_tool or '∅'}→{to_tool or '∅'})"
        super().__init__(name=name, ctx=ctx)
        self._from_tool = from_tool
        self._to_tool = to_tool

    def execute(self) -> bool:
        log.info("BT swap  START: %s", self.name)
        rcp = self.ctx.recipes or {}
        meta = self.ctx.meta if isinstance(self.ctx.meta, dict) else {}
        # Use the same ctx lock as _DSLActionLeaf._ensure_tool so
        # nothing races on current_tool.
        from workspace.bt.dsl import _ctx_lock
        with _ctx_lock(meta):
            current = meta.get("current_tool")
            if current == self._to_tool:
                # Nothing to do — already correct (e.g. an earlier
                # leaf swapped via _ensure_tool fallback).
                log.info("BT swap  SKIP : %s (already correct)", self.name)
                return True
            # Drop the current tool.
            if current is not None:
                old = rcp.get(current)
                if old is not None:
                    try:
                        old.place()
                    except Exception as ex:
                        log.warning("BT swap  RAISE: %s on place(%r) — %s",
                                    self.name, current, ex)
                        return False
            # Pick the new tool.
            if self._to_tool is not None:
                new = rcp.get(self._to_tool)
                if new is not None:
                    try:
                        new.pick()
                    except Exception as ex:
                        log.warning("BT swap  RAISE: %s on pick(%r) — %s",
                                    self.name, self._to_tool, ex)
                        return False
            meta["current_tool"] = self._to_tool
        log.info("BT swap  DONE : %s", self.name)
        return True

    def apply_effects(self, state: Dict[str, Any]) -> None:
        # Tool swaps don't change protocol state.
        pass


# ── Decorators ─────────────────────────────────────────────────────────────


class _ReplanOnFailure(py_trees.decorators.Decorator):
    """If the wrapped child returns FAILURE, raise ReplanRequested.

    The engine catches it, rebuilds the tree from the current observed
    state, and continues. Use this around subtrees where a failure
    means "the world is not what I thought" rather than "the action is
    broken" — typically big multi-step segments. Don't wrap individual
    leaves with this unless you really mean "any failure invalidates
    the whole plan".
    """

    def __init__(
        self,
        child: py_trees.behaviour.Behaviour,
        reason: str = "subtree failed",
        name: str = "replan_on_failure",
    ):
        super().__init__(child=child, name=name)
        self._reason = reason

    def update(self) -> py_trees.common.Status:
        if self.decorated.status == py_trees.common.Status.FAILURE:
            raise ReplanRequested(self._reason)
        # Mirror the child's status otherwise.
        return self.decorated.status


class _SliceCheck(py_trees.behaviour.Behaviour):
    """End-of-slice gate — succeed if the global goal is met, else replan.

    Slicing splits a long protocol (e.g. 48 tubes) into windows of
    ``plan_window`` items (default 4). After each slice's body finishes,
    this leaf checks the *global* goal:

      * Global goal met  → SUCCESS — engine exits.
      * Global goal unmet → raise ``ReplanRequested`` — engine asks
        the framework for the next slice's tree.

    Without this gate the engine would exit after the first slice
    succeeded (root SUCCESS = done). The replan signal lets the
    framework's rebuild path pick the next window transparently.
    """

    def __init__(
        self,
        ctx: WorkspaceContext,
        global_goal: Callable[[Any], bool],
        name: str = "slice_check",
    ):
        super().__init__(name=name)
        self._ctx = ctx
        self._global_goal = global_goal

    def update(self) -> py_trees.common.Status:
        # Local import to avoid a circular at module-load time —
        # state_to_frozen is in dsl which imports from builder transitively.
        from workspace.bt.dsl import state_to_frozen
        state = state_to_frozen(self._ctx.state)
        if self._global_goal(state):
            return py_trees.common.Status.SUCCESS
        raise ReplanRequested("slice complete — more items remain")


def slice_check(
    ctx: WorkspaceContext,
    global_goal: Callable[[Any], bool],
    name: str = "slice_check",
) -> py_trees.behaviour.Behaviour:
    """Public helper — append to the end of a slice body."""
    return _SliceCheck(ctx=ctx, global_goal=global_goal, name=name)


class _Retry(py_trees.decorators.Decorator):
    """Retry the wrapped child up to ``max_attempts`` times.

    Each FAILURE re-initialises the child. SUCCESS short-circuits.
    After ``max_attempts`` failures, propagates FAILURE.
    """

    def __init__(
        self,
        child: py_trees.behaviour.Behaviour,
        max_attempts: int = 3,
        name: Optional[str] = None,
    ):
        super().__init__(child=child, name=name or f"retry({max_attempts})")
        self._max = max_attempts
        self._attempts = 0

    def initialise(self) -> None:
        self._attempts = 0

    def update(self) -> py_trees.common.Status:
        s = self.decorated.status
        if s == py_trees.common.Status.SUCCESS:
            return py_trees.common.Status.SUCCESS
        if s == py_trees.common.Status.FAILURE:
            self._attempts += 1
            if self._attempts >= self._max:
                return py_trees.common.Status.FAILURE
            # Re-init the child for another attempt; py_trees does this
            # automatically on the next tick by virtue of the child being
            # in a non-RUNNING state, but we mark the decorator RUNNING
            # so the parent doesn't see a premature FAILURE.
            self.decorated.stop(py_trees.common.Status.INVALID)
            return py_trees.common.Status.RUNNING
        return py_trees.common.Status.RUNNING


# ── Public helper functions ────────────────────────────────────────────────


def guarded(
    name: str,
    condition: py_trees.behaviour.Behaviour,
    action: py_trees.behaviour.Behaviour,
    *,
    on_skip: Optional[py_trees.behaviour.Behaviour] = None,
) -> py_trees.behaviour.Behaviour:
    """Run ``action`` only if ``condition`` succeeds.

    Returns:
        A Sequence: condition → action. If condition fails and
        ``on_skip`` is provided, returns a Selector that tries the
        original sequence first, then ``on_skip``. Otherwise condition
        FAILURE bubbles up unchanged.
    """
    seq = py_trees.composites.Sequence(
        name=f"{name}/guard", memory=False, children=[condition, action]
    )
    if on_skip is None:
        return seq
    return py_trees.composites.Selector(
        name=f"{name}/guard?skip", memory=False, children=[seq, on_skip]
    )


def with_retry(
    action: py_trees.behaviour.Behaviour,
    *,
    max_attempts: int = 3,
) -> py_trees.behaviour.Behaviour:
    """Wrap ``action`` so it's retried up to N times on FAILURE."""
    return _Retry(child=action, max_attempts=max_attempts)


def with_recovery(
    name: str,
    action: py_trees.behaviour.Behaviour,
    recovery: py_trees.behaviour.Behaviour,
    *,
    retry_after: bool = True,
) -> py_trees.behaviour.Behaviour:
    """On failure of ``action``, run ``recovery``, then retry the action.

    Returns:
        A Sequence that runs ``action``; if it fails, falls through to
        a Sequence of ``recovery`` then ``action`` again. If the second
        attempt also fails, FAILURE propagates.

    If ``retry_after=False``, the recovery itself becomes the success
    path (use for cases like "if dispense fails, just skip this tube").
    """
    if retry_after:
        primary = action
        backup = py_trees.composites.Sequence(
            name=f"{name}/recover-then-retry",
            memory=True,
            children=[recovery, action],
        )
    else:
        primary = action
        backup = recovery
    return py_trees.composites.Selector(
        name=f"{name}/with_recovery", memory=False, children=[primary, backup]
    )


def replan_on_failure(
    subtree: py_trees.behaviour.Behaviour,
    *,
    reason: str = "subtree failed",
) -> py_trees.behaviour.Behaviour:
    """Wrap ``subtree`` so its FAILURE triggers an engine-level replan
    (the engine catches it and rebuilds the tree from observed state)."""
    return _ReplanOnFailure(child=subtree, reason=reason)


def sequence(
    name: str,
    *children: py_trees.behaviour.Behaviour,
    memory: bool = True,
) -> py_trees.composites.Sequence:
    """Shorthand for py_trees.composites.Sequence with memory=True default.

    ``memory=True`` is what you almost always want for protocol steps —
    once child N succeeds, you don't re-tick children 1..N-1 on the next
    tick. ``memory=False`` re-evaluates from the start every tick
    (useful for reactive trees).
    """
    return py_trees.composites.Sequence(
        name=name, memory=memory, children=list(children)
    )


def selector(
    name: str,
    *children: py_trees.behaviour.Behaviour,
    memory: bool = False,
) -> py_trees.composites.Selector:
    """Shorthand for py_trees.composites.Selector (a.k.a. Fallback).

    Default ``memory=False`` — re-checks earlier children every tick.
    That's correct for "is X available? else Y? else Z?" patterns where
    X might become available again.
    """
    return py_trees.composites.Selector(
        name=name, memory=memory, children=list(children)
    )


def parallel_any(
    name: str,
    *children: py_trees.behaviour.Behaviour,
) -> py_trees.composites.Parallel:
    """Parallel — succeeds when ANY child succeeds.

    Common use: "wait for either timeout or sensor reading".
    """
    return py_trees.composites.Parallel(
        name=name,
        policy=py_trees.common.ParallelPolicy.SuccessOnOne(),
        children=list(children),
    )


def parallel_all(
    name: str,
    *children: py_trees.behaviour.Behaviour,
) -> py_trees.composites.Parallel:
    """Parallel — succeeds only when ALL children succeed.

    Common use: "start the shaker AND log the start time AND notify
    the operator" — all must complete to call the step done.
    """
    return py_trees.composites.Parallel(
        name=name,
        policy=py_trees.common.ParallelPolicy.SuccessOnAll(),
        children=list(children),
    )


# ── Schedule → Tree ────────────────────────────────────────────────────────


def from_schedule(
    actions: Sequence[Tuple[str, int, float]],
    leaf_factory: Callable[[str, int], py_trees.behaviour.Behaviour],
    *,
    swaps: Sequence[Tuple[float, Optional[str], Optional[str], int]] = (),
    swap_factory: Optional[Callable[[Optional[str], Optional[str]], py_trees.behaviour.Behaviour]] = None,
    durations: Optional[Dict[str, float]] = None,
    resources: Optional[Dict[str, Tuple[str, ...]]] = None,
    tool_resource: str = "robot",
    name: str = "from_schedule",
) -> py_trees.behaviour.Behaviour:
    """Build a tree from a schedule (actions + swaps), resource-aware.

    Tree shape:
      * Top-level ``Sequence`` of **overlap phases** — actions whose
        time windows don't overlap each other run sequentially.
      * Each phase is either a single leaf (one entry in the phase)
        or a ``Parallel(SuccessOnAll)`` of **resource branches**.
      * Each resource branch is a ``Sequence`` of entries that share
        that resource (or a single leaf). Within a branch they're
        sequential; across branches they run concurrently — exactly
        what the scheduler said is safe.

    Args:
        actions: ``(action_name, item_index, start_t)`` tuples.
        leaf_factory: ``(name, item) → Behaviour`` for action leaves.
        swaps: Optional ``(swap_start, from_tool, to_tool, duration)``
            tuples from the scheduler. If empty, swaps stay implicit
            (handled by each action leaf's ``_ensure_tool``).
        swap_factory: ``(from_tool, to_tool) → Behaviour`` for swap
            leaves. Required if ``swaps`` is non-empty.
        durations: ``{action_name: duration}`` — used to compute end
            times for overlap grouping.
        resources: ``{action_name: tuple of resource names}`` — used
            to group entries within a Parallel phase. Actions sharing
            a resource sub-sequence inside the Parallel.
        tool_resource: The resource swaps run on (typically ``"robot"``).
        name: Top-level sequence name.
    """
    durations = durations or {}
    resources = resources or {}

    # Unify actions and swaps into a single "entry" representation
    # so the grouping pass can treat them uniformly. Each entry knows
    # its time window, resources, and how to make its leaf.
    entries: List[Dict[str, Any]] = []
    for action_name, item_index, start in actions:
        dur = float(durations.get(action_name, 0))
        entries.append({
            "kind":      "action",
            "name":      f"{action_name}(t{item_index})",
            "start":     float(start),
            "end":       float(start) + dur,
            "resources": tuple(resources.get(action_name, ())) or ("__none__",),
            "make_leaf": (lambda an=action_name, ii=item_index:
                          _safe_leaf(leaf_factory, an, ii)),
        })
    for swap_start, from_t, to_t, dur in swaps:
        if swap_factory is None:
            log.warning(
                "from_schedule: got swap event but no swap_factory — skipping",
            )
            continue
        entries.append({
            "kind":      "swap",
            "name":      f"swap({from_t or '∅'}→{to_t or '∅'})",
            "start":     float(swap_start),
            "end":       float(swap_start) + float(dur),
            "resources": (tool_resource,),
            "make_leaf": (lambda ft=from_t, tt=to_t:
                          swap_factory(ft, tt)),
        })

    # Stable sort by start time. Ties: swap before action (so the
    # tool is loaded before the action that needs it ticks).
    entries.sort(key=lambda e: (e["start"], 0 if e["kind"] == "swap" else 1))

    # Phase 1: group by overlap. Entries whose time windows overlap
    # any current-phase entry go in the same phase.
    phases: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_end = -1.0
    for e in entries:
        if current and e["start"] < current_end:
            current.append(e)
            current_end = max(current_end, e["end"])
        else:
            if current:
                phases.append(current)
            current = [e]
            current_end = e["end"]
    if current:
        phases.append(current)

    # Phase 2: build each phase as resource-grouped Parallel.
    phase_nodes: List[py_trees.behaviour.Behaviour] = []
    for idx, phase in enumerate(phases):
        node = _build_phase_node(phase, name=f"{name}/phase{idx}")
        if node is not None:
            phase_nodes.append(node)

    return py_trees.composites.Sequence(
        name=name, memory=True, children=phase_nodes,
    )


def _safe_leaf(
    factory: Callable[[str, int], py_trees.behaviour.Behaviour],
    action_name: str,
    item_index: int,
) -> Optional[py_trees.behaviour.Behaviour]:
    try:
        return factory(action_name, item_index)
    except KeyError:
        log.warning(
            "from_schedule: no leaf for %r (item %d) — skipping",
            action_name, item_index,
        )
        return None


def _build_phase_node(
    phase: List[Dict[str, Any]],
    *,
    name: str,
) -> Optional[py_trees.behaviour.Behaviour]:
    """Build one phase: resource-grouped Sequence-of-Sequences inside a Parallel.

    Within a phase, group entries by their **primary resource** (the
    first one in the entry's resources tuple). Each group becomes a
    sub-Sequence (or a single leaf if only one entry). The set of
    sub-trees becomes a Parallel; if there's only one resource group,
    we skip the Parallel and just emit the Sequence directly.
    """
    by_resource: Dict[str, List[Dict[str, Any]]] = {}
    for e in phase:
        primary = e["resources"][0] if e["resources"] else "__none__"
        by_resource.setdefault(primary, []).append(e)

    branches: List[py_trees.behaviour.Behaviour] = []
    for r, group in by_resource.items():
        # Sort the group sequentially within the resource.
        group.sort(key=lambda e: (e["start"], 0 if e["kind"] == "swap" else 1))
        leaves = [leaf for e in group if (leaf := e["make_leaf"]()) is not None]
        if not leaves:
            continue
        if len(leaves) == 1:
            branches.append(leaves[0])
        else:
            branches.append(py_trees.composites.Sequence(
                name=f"{name}/{r}", memory=True, children=leaves,
            ))

    if not branches:
        return None
    if len(branches) == 1:
        return branches[0]
    return py_trees.composites.Parallel(
        name=name,
        policy=py_trees.common.ParallelPolicy.SuccessOnAll(),
        children=branches,
    )
