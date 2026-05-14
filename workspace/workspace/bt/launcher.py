"""Helpers a BT project's ``main.py`` calls to assemble the protocol.

This module is **explicit infrastructure**, not a magic launcher. A
project's ``main.py`` opens ``launch.yaml`` itself, calls
:func:`load_recipes` itself, imports ``actions`` itself, and hands
those three things to :func:`run_protocol` itself. The reader of
``main.py`` sees the full wiring without having to chase indirection
into the framework.

Public surface:

  * :func:`load_recipes` — read a ``recipes.yaml`` path and return a
    ``{alias: recipe_instance}`` dict, same shape as pace_or's
    ``BaseWorkflow._load_recipes``.
  * :func:`run_protocol` — given workspace + core + actions module +
    recipes dict, run plan → schedule → BT to completion.

The intentional missing piece: there is no ``main()`` here. Each
project owns its own ``main.py`` so an operator can read it and see
"this is where launch.yaml is read, this is where recipes.yaml is
loaded, this is where Workspace is started." The framework's job is
to provide reusable pieces, not to hide where they're glued together.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import py_trees
import yaml

from workspace.bt.behaviours import WorkspaceContext
from workspace.bt.builder import (
    from_schedule,
    replan_on_failure,
    sequence,
    with_retry,
)
from workspace.bt.dsl import ActionRegistry, state_to_frozen
from workspace.bt.engine import BTEngine, EngineConfig
from workspace.planner import ReplanConfig, Replanner, make_schedule_builder


log = logging.getLogger(__name__)


# ── Recipe loading (mirrors pace_or's BaseWorkflow._load_recipes) ─────────


def _import_class(dotted: str):
    """Import a dotted-path class reference. ``module.path:ClassName``
    or ``module.path.ClassName`` are both accepted."""
    if ":" in dotted:
        mod_name, attr = dotted.split(":", 1)
    else:
        mod_name, _, attr = dotted.rpartition(".")
    mod = importlib.import_module(mod_name)
    return getattr(mod, attr)


def load_recipes(workspace: Any, core: Any, recipes_path: Path) -> Dict[str, Any]:
    """Read a ``recipes.yaml`` file and instantiate each entry.

    Returns a ``{alias: recipe_instance}`` dict — same shape pace_or's
    BaseWorkflow produces. ``Action.execute(...)`` bodies access it
    via ``self.ctx.recipes[alias]``.

    File schema (matches pace_or):

        gripper:
          class: workspace.recipes.tool_rack.ToolRack
          kwargs: {component: tool_rack_144mm_1, left_approach: true}

    Args:
        workspace: Workspace SDK root (used to resolve component names).
        core: Core component (passed to each recipe constructor).
        recipes_path: Path to ``recipes.yaml``. Missing file → empty dict.

    Behaviour on errors:
        * Missing file → empty dict, no log.
        * Missing component in scene → one-line warning per recipe,
          that entry skipped.
        * Anything else (import error, bad class kwargs) → traceback
          logged, that entry skipped. Other recipes continue.
    """
    path = Path(recipes_path)
    if not path.is_file():
        return {}
    with open(path) as f:
        defs = yaml.safe_load(f) or {}
    rcp: Dict[str, Any] = {}
    for alias, defn in defs.items():
        try:
            cls = _import_class(defn["class"])
            kwargs = dict(defn.get("kwargs") or {})
            comp_name = kwargs.pop("component")
            try:
                comp = workspace.components[comp_name]
            except KeyError:
                log.warning(
                    "recipes.yaml[%s]: component %r not in scene — skipping",
                    alias, comp_name,
                )
                continue
            rcp[alias] = cls(workspace, core, comp, **kwargs)
        except Exception:
            log.exception("recipes.yaml[%s]: instantiation failed — skipping", alias)
    return rcp


# ── Default protocol runner ───────────────────────────────────────────────


def run_protocol(
    workspace: Any,
    core: Any,
    actions_module: Any,
    *,
    recipes: Optional[Dict[str, Any]] = None,
    tick_hz: float = 10.0,
    project_name: Optional[str] = None,
    **kwargs,
) -> py_trees.common.Status:
    """Default plan → schedule → BT tick lifecycle for any BT project.

    Steps:

      1. Calls ``actions_module.setup(**kwargs)`` to map operator
         kwargs into ``initial_facts`` / ``goal`` / ``objects``.
      2. Builds a :class:`WorkspaceContext` carrying the recipes dict
         the caller supplied.
      3. Pulls PDDL templates, scheduler meta, and a leaf factory
         from the auto-populated :class:`ActionRegistry`.
      4. Wraps every leaf in ``with_retry(max_attempts=2)`` and the
         body in ``replan_on_failure(...)``. (Override by using your
         own tree builder; this is just the default.)
      5. Runs the BT engine.

    Args:
        workspace: Workspace SDK root.
        core: Core component.
        actions_module: The project's ``actions`` module. Must expose
            ``setup(**kwargs) -> dict`` returning
            ``{"initial_facts", "goal", "objects"}``.
        recipes: ``{alias: recipe_instance}`` dict. Loaded by the
            caller via :func:`load_recipes` so main.py shows where
            it comes from. ``None`` → empty dict (sim-only).
        tick_hz: BT engine tick rate (Hz). Comes from launch.yaml kwargs.
        project_name: Display name for log lines / tree node names.
            Default = ``actions_module.__name__``.
        **kwargs: Operator-supplied parameters from the GUI; forwarded
            to ``actions_module.setup``.
    """
    if project_name is None:
        project_name = actions_module.__name__.split(".")[-1]

    # 1. Domain inputs derived from kwargs.
    if not hasattr(actions_module, "setup"):
        raise RuntimeError(
            f"{actions_module.__name__}.setup(**kwargs) is required — it returns "
            "the initial_facts / goal / objects derived from operator kwargs."
        )
    spec = actions_module.setup(**kwargs)
    initial_facts = set(spec["initial_facts"])
    goal_fn       = spec["goal"]
    objects       = dict(spec.get("objects") or {})

    # 2. Context. Carries the live mutable facts dict + recipes +
    #    object pools (used by Action.param_iter to enumerate
    #    candidate bindings).
    ctx = WorkspaceContext(
        workspace=workspace,
        core=core,
        runtime=getattr(workspace, "rt", None) or getattr(workspace, "runtime", None),
        state={"facts": initial_facts},
        recipes=recipes or {},
        meta={
            "project": project_name,
            "kwargs":  kwargs,
            "objects": objects,
        },
    )

    # 3. Registry artifacts (auto-populated when ``actions`` was imported).
    registry       = ActionRegistry.current()
    templates      = registry.to_templates(ctx)
    meta           = registry.to_meta()
    leaf_factory   = registry.leaf_factory(ctx)
    build_schedule = make_schedule_builder(meta)

    # 4. Default tree shape: from_schedule + per-leaf retry + outer
    #    replan_on_failure. Project can supply its own build_tree by
    #    calling run_protocol_with_tree() instead (advanced use).
    def build_tree(schedule, _ctx):
        def _wrapped(action_name, item_index):
            return with_retry(leaf_factory(action_name, item_index), max_attempts=2)
        body = from_schedule(schedule, _wrapped, name=f"{project_name}/body")
        return replan_on_failure(
            sequence(f"{project_name}/root", body),
            reason="protocol step failed — replanning from observed state",
        )

    replanner = Replanner(
        ctx=ctx,
        observe=lambda c: state_to_frozen(c.state),
        templates=templates,
        goal=goal_fn,
        build_schedule=build_schedule,
        build_tree=build_tree,
        config=ReplanConfig(verbose=True),
    )

    root = replanner.rebuild()
    engine = BTEngine(
        root=root,
        rebuild=replanner.rebuild,
        runtime=ctx.runtime,
        config=EngineConfig(tick_hz=float(tick_hz)),
    )
    log.info(
        "%s: starting BT engine — %d action(s) in plan",
        project_name, len(replanner.last_plan or []),
    )
    status = engine.run()
    log.info("%s: BT engine finished with status=%s", project_name, status.name)
    return status
