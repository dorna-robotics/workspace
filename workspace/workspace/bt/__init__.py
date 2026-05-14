"""Workspace BT framework — Behaviour Tree execution layer.

Drop-in, opinionated wrapper around ``py_trees`` for lab-automation
projects. Provides:

* :class:`WorkspaceBehaviour`, :class:`RecipeAction`,
  :class:`PredicateCondition`, :class:`DeviceCondition` — base classes
  every project leaf subclasses.
* :class:`BTEngine` — the rate-controlled tick loop with runtime
  pause/resume/kill awareness and a replan-on-signal hook.
* :class:`ReplanRequested` — the signal a leaf raises to ask the engine
  for a tree rebuild.
* Helpers (:func:`guarded`, :func:`with_retry`, :func:`with_recovery`,
  :func:`replan_on_failure`, :func:`from_schedule`, …) — the tree
  patterns every project assembles.

See ``docs/bt-framework-guide.md`` for the discipline document
(authoring rules, naming conventions, project skeleton).
"""

from __future__ import annotations

from workspace.bt.behaviours import (
    DeviceCondition,
    PredicateCondition,
    RecipeAction,
    WorkspaceBehaviour,
    WorkspaceContext,
)
from workspace.bt.builder import (
    from_schedule,
    guarded,
    parallel_all,
    parallel_any,
    replan_on_failure,
    selector,
    sequence,
    with_recovery,
    with_retry,
)
from workspace.bt.engine import (
    BTEngine,
    EngineConfig,
    ReplanRequested,
)
from workspace.bt.dsl import (
    Action,
    ActionRegistry,
    Expr,
    Fact,
    Predicate,
    bind_conditions,
    make_predicate_condition,
    predicate,
    state_to_frozen,
)
# Launcher uses Workspace + RuntimeServer at call time, but importing
# the module symbol up front is fine — its main() / run_protocol() use
# late imports so the heavy deps aren't pulled in until needed.
from workspace.bt import launcher as _launcher_module  # noqa: F401


__all__ = [
    # behaviours
    "WorkspaceContext",
    "WorkspaceBehaviour",
    "RecipeAction",
    "PredicateCondition",
    "DeviceCondition",
    # engine
    "BTEngine",
    "EngineConfig",
    "ReplanRequested",
    # builder helpers
    "guarded",
    "with_retry",
    "with_recovery",
    "replan_on_failure",
    "sequence",
    "selector",
    "parallel_any",
    "parallel_all",
    "from_schedule",
    # DSL — the declarative authoring layer
    "Action",
    "ActionRegistry",
    "Expr",
    "Fact",
    "Predicate",
    "predicate",
    "bind_conditions",
    "make_predicate_condition",
    "state_to_frozen",
]
