"""Single-call orchestrator entry point for BT projects.

A project's ``main.py`` is just two lines:

    from workspace.bt.launcher import main
    main(__file__)

Everything else lives here:

* parsing ``--port`` (and ``PORT`` env var) so the orchestrator's spawn
  contract still works,
* reading ``launch.yaml`` next to the caller's main.py,
* adding the project directory to ``sys.path`` so ``import actions``
  works inside the project,
* loading ``recipes.yaml`` (if present) into a name→recipe-instance
  dict identical to what pace_or's BaseWorkflow produces, so an
  ``Action.execute`` body can do ``self.ctx.recipes['gripper'].pick(...)``,
* instantiating ``Workspace`` + ``RuntimeServer`` and wiring a default
  ``workflow_fn`` that calls :func:`run_protocol`.

Per-project customisation: drop a ``workflow.py`` next to ``main.py``
with a ``run(workspace, core, **kwargs)`` function — the launcher
uses that instead of the default :func:`run_protocol`. Projects with
nothing custom don't need ``workflow.py`` at all.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict

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


# ── Per-project module loading ────────────────────────────────────────────


def _import_from(file_path: Path, module_name: str):
    """Import ``module_name.py`` from ``file_path``'s directory.

    Used because the project may not be on ``sys.path`` when the
    launcher is imported. We add the dir explicitly so subsequent
    ``import <module_name>`` calls inside the project find it.
    """
    proj_dir = str(file_path.resolve().parent)
    if proj_dir not in sys.path:
        sys.path.insert(0, proj_dir)
    return importlib.import_module(module_name)


def _maybe_import_from(file_path: Path, module_name: str):
    """Like :func:`_import_from` but returns ``None`` when the module
    doesn't exist. Used for optional files (e.g. ``workflow.py``)."""
    proj_dir = file_path.resolve().parent
    candidate = proj_dir / f"{module_name}.py"
    if not candidate.is_file():
        return None
    return _import_from(file_path, module_name)


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


def load_recipes(workspace: Any, core: Any, project_dir: Path) -> Dict[str, Any]:
    """Read ``recipes.yaml`` in ``project_dir`` and instantiate each entry.

    Returns a ``{alias: recipe_instance}`` dict — same shape pace_or's
    BaseWorkflow produces and that ``Action.execute`` bodies expect at
    ``self.ctx.recipes[alias]``.

    File schema (matches pace_or):

        gripper:
          class: workspace.recipes.tool_rack.ToolRack
          kwargs: {component: tool_rack_144mm_1, left_approach: true}

    If ``recipes.yaml`` is absent, returns an empty dict — sim-only
    projects don't need recipes.
    """
    path = project_dir / "recipes.yaml"
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
                # Common case in sim / partial scenes — the recipe refers
                # to a component that wasn't loaded. One-line warning,
                # no traceback. If everything fails, the operator will
                # see them as a list and can fix the scene or recipe.
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
    tick_hz: float = 10.0,
    project_dir: Path = None,
    **kwargs,
) -> py_trees.common.Status:
    """Default plan → schedule → BT tick lifecycle for any BT project.

    Steps (in order):

      1. Call ``actions_module.setup(**kwargs)`` to map operator kwargs
         into ``initial_facts`` / ``goal`` / ``objects``.
      2. Load ``recipes.yaml`` (if present) into a recipes dict.
      3. Build a :class:`WorkspaceContext` with everything wired in.
      4. Pull PDDL templates, scheduler meta, and a leaf factory from
         the auto-populated :class:`ActionRegistry`.
      5. Build a :class:`Replanner` and a default tree (per-leaf
         retry + outer ``replan_on_failure``).
      6. Run the BT engine.

    Args:
        workspace: Workspace SDK root.
        core: Core component.
        actions_module: The project's ``actions`` module. Must expose
            a ``setup(**kwargs) -> dict`` function returning
            ``{"initial_facts", "goal", "objects"}``.
        tick_hz: BT engine tick rate (Hz). Comes from launch.yaml kwargs.
        project_dir: Directory where ``recipes.yaml`` lives.  Defaults
            to the directory of the imported ``actions_module``.
        **kwargs: Operator-supplied parameters from the GUI; forwarded
            to ``actions_module.setup``.
    """
    if project_dir is None:
        project_dir = Path(actions_module.__file__).resolve().parent

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

    # 2. Recipes (real-mode hardware bindings).
    recipes = load_recipes(workspace, core, project_dir)

    # 3. Context. Carries the live mutable facts dict + recipes +
    #    object pools (used by Action.param_iter to enumerate
    #    candidate bindings).
    ctx = WorkspaceContext(
        workspace=workspace,
        core=core,
        runtime=getattr(workspace, "rt", None) or getattr(workspace, "runtime", None),
        state={"facts": initial_facts},
        recipes=recipes,
        meta={
            "project": project_dir.name,
            "kwargs":  kwargs,
            "objects": objects,
        },
    )

    # 4. Registry artifacts.
    registry       = ActionRegistry.current()
    templates      = registry.to_templates(ctx)
    meta           = registry.to_meta()
    leaf_factory   = registry.leaf_factory(ctx)
    build_schedule = make_schedule_builder(meta)

    # 5. Tree builder — default shape: from_schedule + per-leaf retry
    #    + outer replan_on_failure. Project can override by providing
    #    its own workflow.py (handled by main()).
    def build_tree(schedule, _ctx):
        def _wrapped(action_name, item_index):
            return with_retry(leaf_factory(action_name, item_index), max_attempts=2)
        body = from_schedule(schedule, _wrapped, name=f"{project_dir.name}/body")
        return replan_on_failure(
            sequence(f"{project_dir.name}/root", body),
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

    # 6. Run.
    root = replanner.rebuild()
    engine = BTEngine(
        root=root,
        rebuild=replanner.rebuild,
        runtime=ctx.runtime,
        config=EngineConfig(tick_hz=float(tick_hz)),
    )
    log.info(
        "%s: starting BT engine — %d action(s) in plan",
        project_dir.name, len(replanner.last_plan or []),
    )
    status = engine.run()
    log.info("%s: BT engine finished with status=%s", project_dir.name, status.name)
    return status


# ── Orchestrator entry — main(__file__) ───────────────────────────────────


def main(main_file: str) -> None:
    """Orchestrator entry point. Call from your project's ``main.py``:

        from workspace.bt.launcher import main
        main(__file__)

    What this does (mirrors pace_or's main.py):

      1. argparse ``--port`` (defaulting to ``PORT`` env var, then 5010).
      2. Read ``launch.yaml`` next to the caller's main.py.
      3. Add the caller's directory to ``sys.path`` so subsequent
         ``import actions`` (and optional ``import workflow``) inside
         the project resolve correctly.
      4. Import the project's ``actions`` module (required).
      5. Import the project's ``workflow`` module if it exists; use
         its ``run`` function as the ``workflow_fn``. Otherwise the
         default :func:`run_protocol` is used.
      6. Start :class:`Workspace` and :class:`RuntimeServer`.
    """
    # Late imports so this module doesn't drag in tornado/etc. until
    # main() is actually called. Lets sim tests import the launcher
    # without paying the Workspace dependency cost.
    from workspace.workspace import Workspace
    from workspace.runtime_server import RuntimeServer

    main_path = Path(main_file).resolve()
    project_dir = main_path.parent

    # --- argparse (preserve --port) ---
    p = argparse.ArgumentParser(prog=f"{project_dir.name}/main.py")
    p.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "5010")),
        help="HTTP port for the workspace server (default 5010, env PORT).",
    )
    args = p.parse_args()

    # --- launch.yaml ---
    with open(project_dir / "launch.yaml") as f:
        launch = yaml.safe_load(f) or {}
    scene_paths = launch.get("scene") or []

    # --- project-local imports (actions required, workflow optional) ---
    actions_module = _import_from(main_path, "actions")
    workflow_module = _maybe_import_from(main_path, "workflow")

    # --- wire workflow_fn ---
    if workflow_module is not None and hasattr(workflow_module, "run"):
        log.info("%s: using project-local workflow.run override", project_dir.name)
        _project_run: Callable[..., Any] = workflow_module.run

        def workflow_fn(*, workspace, core, **kwargs):
            return _project_run(workspace, core, **kwargs)
    else:
        def workflow_fn(*, workspace, core, **kwargs):
            return run_protocol(
                workspace, core, actions_module,
                project_dir=project_dir,
                **kwargs,
            )

    # --- launch workspace + runtime server ---
    ws = Workspace(config_path=scene_paths, port=args.port)
    RuntimeServer(runtime=ws.rt, workflow_fn=workflow_fn, workspace=ws).run()
