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

from workspace.bt.engine import ReplanRequested


log = logging.getLogger(__name__)


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
    schedule: Sequence[Tuple[str, int, float]],
    leaf_factory: Callable[[str, int], py_trees.behaviour.Behaviour],
    *,
    name: str = "from_schedule",
    durations: Optional[Dict[str, float]] = None,
) -> py_trees.behaviour.Behaviour:
    """Build a Sequence-of-Parallel tree from a schedule.

    Groups actions whose time windows overlap into ``Parallel``
    composites (children run concurrently in their own worker
    threads); sequences groups whose windows are disjoint. The result
    is a tree that runs as concurrently as the schedule allows —
    correctness guaranteed by the scheduler having already enforced
    causal dependencies via :func:`build_precedence`.

    Args:
        schedule: Tuples from the scheduler.
        leaf_factory: Called as ``leaf_factory(action_name, item_index)``
            returning a Behaviour for that scheduled task.
        name: Top-level sequence name.
        durations: ``{action_name: duration_seconds}`` — used to
            compute each action's end-time for overlap grouping. If
            omitted, every action is treated as instantaneous and the
            output collapses to a flat Sequence (safe fallback).

    Returns:
        ``Sequence(memory=True)`` of phases. Each phase is either a
        single leaf or a ``Parallel(SuccessOnAll)`` of leaves that
        the scheduler said overlap.
    """
    durations = durations or {}
    ordered = sorted(schedule, key=lambda t: (t[2], t[0], t[1]))

    # Group into "overlap phases" — actions whose windows overlap go
    # into one phase that becomes a Parallel composite.
    phases: List[List[Tuple[str, int, float, float]]] = []
    current: List[Tuple[str, int, float, float]] = []
    current_end = -1.0
    for action_name, item_index, start in ordered:
        end = start + float(durations.get(action_name, 0))
        if current and start < current_end:
            current.append((action_name, item_index, start, end))
            current_end = max(current_end, end)
        else:
            if current:
                phases.append(current)
            current = [(action_name, item_index, start, end)]
            current_end = end
    if current:
        phases.append(current)

    # Build a tree node per phase: single leaf vs Parallel.
    phase_nodes: List[py_trees.behaviour.Behaviour] = []
    for idx, phase in enumerate(phases):
        leaves: List[py_trees.behaviour.Behaviour] = []
        for action_name, item_index, _s, _e in phase:
            try:
                leaves.append(leaf_factory(action_name, item_index))
            except KeyError:
                log.warning(
                    "from_schedule: no leaf factory for %r (item %d) — skipping",
                    action_name, item_index,
                )
        if not leaves:
            continue
        if len(leaves) == 1:
            phase_nodes.append(leaves[0])
        else:
            phase_nodes.append(py_trees.composites.Parallel(
                name=f"{name}/phase{idx}",
                policy=py_trees.common.ParallelPolicy.SuccessOnAll(),
                children=leaves,
            ))

    return py_trees.composites.Sequence(
        name=name, memory=True, children=phase_nodes
    )
