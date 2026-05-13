"""BT tree builder for pace_bt.

Turns a scheduled action list into a py_trees tree. The framework's
``from_schedule`` does most of the work; we just wire it to our leaf
factory and add a thin recovery shell so the engine can replan on
failure of a big subtree.

The shape we produce:

    sequence(memory=True)
      ├── action_0    ← from the schedule, in start-time order
      ├── action_1
      ├── ...
      └── action_N

That's wrapped in ``replan_on_failure`` so any leaf failure asks the
engine for a fresh plan from the (now possibly drifted) world state
instead of crashing the whole protocol.
"""

from __future__ import annotations

from typing import Any, Sequence, Tuple

import py_trees

from workspace.bt import (
    WorkspaceContext,
    from_schedule,
    replan_on_failure,
    sequence,
    with_retry,
)

from projects.pace_bt.actions import make_leaf


def build_tree(
    schedule: Sequence[Tuple[str, int, float]],
    ctx: WorkspaceContext,
) -> py_trees.behaviour.Behaviour:
    """Assemble the root behaviour from a schedule.

    Per the framework discipline:
      * Schedule items become leaves via ``actions.make_leaf``.
      * Each leaf gets a small retry policy (1 retry on failure) so
        transient hiccups don't cause a full replan.
      * The whole sequence is wrapped in ``replan_on_failure`` so a
        non-recoverable failure surfaces to the engine for replan.
    """
    leaf_factory = make_leaf(ctx)

    def _wrapped_leaf(action_name: str, item_index: int):
        leaf = leaf_factory(action_name, item_index)
        return with_retry(leaf, max_attempts=2)

    body = from_schedule(schedule, _wrapped_leaf, name="pace_bt/body")
    return replan_on_failure(
        sequence("pace_bt/root", body),
        reason="protocol step failed — replanning from observed state",
    )
