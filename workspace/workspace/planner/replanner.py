"""Replanning helper — observation → fresh plan from current world state.

The framework's loop is:

  1. Observe world state (vision, weight, gripper, device-bus).
  2. Run PDDL plan from observed state → action sequence.
  3. Run OR scheduler over the sequence → timed schedule.
  4. Build BT from schedule.
  5. Execute BT. If world drifts (drip, technician intervention, device
     down) — observe again, plan again, rebuild tree.

The :class:`Replanner` bundles steps 1–4 into one ``rebuild()`` callable
that the :class:`BTEngine` can invoke whenever a leaf raises
``ReplanRequested``. Projects supply:

* an ``observe(ctx) -> state`` callable (reads sensors / device bus
  into a fresh ``State`` frozenset),
* a domain (templates) and goal,
* the schedule builder + tree builder.

The replanner stitches them together.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence, Tuple

import py_trees

from workspace.planner.pddl import (
    Action,
    ActionTemplate,
    Domain,
    Goal,
    State,
    domain_from_templates,
    plan,
)


log = logging.getLogger(__name__)


# Type aliases used in signatures.
Observe = Callable[[Any], State]            # ctx -> state
ScheduleBuilder = Callable[[List[Action]], Sequence[Tuple[str, int, float]]]
TreeBuilder = Callable[
    [Sequence[Tuple[str, int, float]], Any],
    py_trees.behaviour.Behaviour,
]


@dataclass
class ReplanConfig:
    """Tuning knobs for replanning.

    Attributes:
        max_plan_depth: Hard cap on plan length passed to the PDDL planner.
        max_plan_states: Hard cap on states the PDDL planner expands.
        verbose: If True, log every plan attempt with action count + duration.
    """

    max_plan_depth: int = 500
    max_plan_states: int = 200_000
    verbose: bool = True


class Replanner:
    """Single entry point for "go from now to a fresh BT".

    Typical wiring:

        replanner = Replanner(
            ctx=ctx,
            observe=my_observe_fn,
            templates=[decap_template, dispense_template, …],
            goal=my_goal_fn,
            build_schedule=ORScheduler(...).schedule_from_plan,
            build_tree=my_project.tree.build_tree,
        )
        root = replanner.rebuild()       # initial tree
        engine = BTEngine(root, rebuild=replanner.rebuild, runtime=rt)
        engine.run()

    Why all the callables instead of a god-class: projects vary in
    domain shape, scheduler usage, and tree composition. By taking
    those as injected functions the Replanner stays project-agnostic.

    Failure modes:
        * ``observe`` raises → ``rebuild`` raises (engine aborts).
        * ``plan`` returns None → ``rebuild`` raises ``RuntimeError``;
          engine aborts. Operator must intervene.
        * Empty plan (goal already satisfied) → returns a trivial tree
          that succeeds on first tick.
    """

    def __init__(
        self,
        *,
        ctx: Any,
        observe: Observe,
        templates: Sequence[ActionTemplate],
        goal: Goal,
        build_schedule: ScheduleBuilder,
        build_tree: TreeBuilder,
        config: Optional[ReplanConfig] = None,
        goal_facts: Optional[Any] = None,
    ):
        self.ctx = ctx
        self._observe = observe
        self._domain = domain_from_templates(templates)
        self._goal = goal
        self._goal_facts = goal_facts  # planner heuristic hint; None = BFS
        self._build_schedule = build_schedule
        self._build_tree = build_tree
        self._cfg = config or ReplanConfig()
        self.last_state: Optional[State] = None
        self.last_plan: Optional[List[Action]] = None
        self.last_schedule: Optional[Sequence[Tuple[str, int, float]]] = None
        self._calls = 0

    def rebuild(self) -> py_trees.behaviour.Behaviour:
        """Observe → plan → schedule → tree. Returns the new root behaviour."""
        self._calls += 1
        # 1. Observe.
        state = self._observe(self.ctx)
        if not isinstance(state, frozenset):
            state = frozenset(state)
        self.last_state = state

        # 2. Plan. If goal_facts is provided, GBFS uses it as a
        #    heuristic — scales to large state spaces. Otherwise BFS.
        actions = plan(
            state,
            self._domain,
            self._goal,
            goal_facts=self._goal_facts,
            max_depth=self._cfg.max_plan_depth,
            max_states=self._cfg.max_plan_states,
        )
        if actions is None:
            raise RuntimeError(
                "Replanner: PDDL search found no plan from the current state. "
                "The world is in a state from which the goal is unreachable "
                "under the current action templates. Operator intervention "
                "required."
            )
        self.last_plan = actions
        if self._cfg.verbose:
            log.info("Replanner[#%d]: plan=%d actions", self._calls, len(actions))

        # 3. Schedule.
        try:
            schedule = self._build_schedule(actions)
        except Exception:
            log.exception("Replanner: schedule builder raised")
            raise
        self.last_schedule = schedule

        # 4. Tree.
        try:
            root = self._build_tree(schedule, self.ctx)
        except Exception:
            log.exception("Replanner: tree builder raised")
            raise

        if self._cfg.verbose:
            log.info("Replanner[#%d]: tree rebuilt", self._calls)
        return root
