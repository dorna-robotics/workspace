"""Entry point for the pace_bt project.

The framework does the heavy lifting — workflow.py just wires:

  1. The operator's kwargs into an initial world state and context.
  2. The ``ActionRegistry`` (auto-populated from ``actions.py``) into
     PDDL templates, scheduler meta, and a BT leaf factory.
  3. Plan → schedule → tree → tick loop, via :class:`Replanner` and
     :class:`BTEngine`.

That's it. No domain.py, no conditions.py, no schedule.py, no tree.py
to maintain — the registry derives them from the declarative ``Action``
classes.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

import py_trees

from workspace.bt import (
    ActionRegistry,
    BTEngine,
    EngineConfig,
    WorkspaceContext,
    bind_conditions,
    from_schedule,
    replan_on_failure,
    sequence,
    state_to_frozen,
    with_retry,
)
from workspace.planner import Replanner, ReplanConfig, make_schedule_builder

from projects.pace_bt import actions  # registers Inspect, Decap, … on import


log = logging.getLogger(__name__)


def run(
    workspace: Any,
    core: Any,
    *,
    batch_size: int = 4,
    heavy: Iterable[int] = (),
    tick_hz: float = 10.0,
    **_unused,
) -> py_trees.common.Status:
    """Plan, schedule, and execute the pace_bt protocol."""
    tubes = list(range(int(batch_size)))
    heavy_set = set(int(t) for t in heavy)

    # ── Initial observation (today: declared from kwargs; in production:
    #    populated from device-bus / vision before run()). ─────────────
    initial_facts = set(actions.initial_state(tubes, heavy_set))

    # ── Workspace context. Carries the live mutable facts dict that
    #    actions and conditions read/write through.  ──────────────────
    ctx = WorkspaceContext(
        workspace=workspace,
        core=core,
        runtime=getattr(workspace, "rt", None) or getattr(workspace, "runtime", None),
        state={"facts": initial_facts},
        recipes=getattr(workspace, "recipes", None),
        meta={
            "project": "pace_bt",
            "batch_size": batch_size,
            "heavy": heavy_set,
            # Action.param_iter enumerates from this dict by param name.
            "objects": {"tube": tubes},
        },
    )

    # ── Pull PDDL templates, scheduler meta, and leaf factory from the
    #    registry the actions.py module populated on import. ──────────
    registry = ActionRegistry.current()
    templates      = registry.to_templates(ctx)
    meta           = registry.to_meta()
    leaf_factory   = registry.leaf_factory(ctx)
    build_schedule = make_schedule_builder(meta)

    # ── Tree builder — small enough to inline here. ──────────────────
    def build_tree(schedule, _ctx):
        def _wrapped(action_name, item_index):
            return with_retry(leaf_factory(action_name, item_index), max_attempts=2)
        body = from_schedule(schedule, _wrapped, name="pace_bt/body")
        root = replan_on_failure(
            sequence("pace_bt/root", body),
            reason="protocol step failed — replanning from observed state",
        )
        bind_conditions(root, ctx)
        return root

    # ── Replanner: observe → plan → schedule → tree, as a callable
    #    the engine invokes on ReplanRequested. ───────────────────────
    replanner = Replanner(
        ctx=ctx,
        observe=lambda _ctx: state_to_frozen(_ctx.state),
        templates=templates,
        goal=actions.make_goal(tubes),
        build_schedule=build_schedule,
        build_tree=build_tree,
        config=ReplanConfig(verbose=True),
    )

    root = replanner.rebuild()
    engine = BTEngine(
        root=root,
        rebuild=replanner.rebuild,
        runtime=ctx.runtime,
        config=EngineConfig(tick_hz=tick_hz),
    )
    log.info("pace_bt: starting BT engine on %d tube(s)", batch_size)
    status = engine.run()
    log.info("pace_bt: BT engine finished with status=%s", status.name)
    return status
