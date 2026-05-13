"""Entry point for the pace_bt project.

Hands the operator's batch description to the planner → scheduler →
tree builder → BT engine. Roughly 40 lines because the framework does
the heavy lifting.

Call signature mirrors other project workflows:

    workflow.run(workspace, core, **kwargs)

with kwargs accepting at least:
    batch_size:  number of tubes (default 4)
    heavy:       iterable of tube indices that come back "heavy" from
                 the inspect step. For the first cut these are declared
                 up-front; later, the real inspect action observes
                 them and a replan re-derives the right dispense
                 branch from observed state.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

import py_trees

from workspace.bt import BTEngine, EngineConfig, WorkspaceContext
from workspace.planner import Replanner, ReplanConfig

from projects.pace_bt import domain, schedule, tree


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

    # ── Initial observation ─────────────────────────────────────────
    # In production this is filled by reading vision + device bus. For
    # the first cut we declare it from the batch description.
    initial_facts = set(domain.initial_state(tubes, heavy_set))

    # The workspace context carries shared state. Conditions and actions
    # mutate ``state["facts"]`` as the world evolves.
    ctx = WorkspaceContext(
        workspace=workspace,
        core=core,
        runtime=getattr(workspace, "rt", None) or getattr(workspace, "runtime", None),
        state={"facts": initial_facts},
        recipes=getattr(workspace, "recipes", None),
        meta={"project": "pace_bt", "batch_size": batch_size, "heavy": heavy_set},
    )

    def observe(_ctx: WorkspaceContext):
        """Return current world state as a frozenset of fact tuples."""
        return frozenset(_ctx.state.get("facts", set()))

    replanner = Replanner(
        ctx=ctx,
        observe=observe,
        templates=domain.build_templates(tubes),
        goal=domain.make_goal(tubes),
        build_schedule=schedule.build_schedule,
        build_tree=tree.build_tree,
        config=ReplanConfig(verbose=True),
    )

    # First build is just a replanner call.
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
