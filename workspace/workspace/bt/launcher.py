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
  * :func:`load_checks` — import the project's ``checks.py``,
    instantiate its ``Checks`` class, run its ``register`` method,
    and return a ``{name: callable}`` dict the BT framework uses to
    drive ``pre_check`` / ``post_check`` on every action.
  * :func:`run_protocol` — given workspace + core + actions module +
    recipes dict (+ optional checks dict), run plan → schedule → BT
    to completion.

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
    SwapLeaf,
    from_schedule,
    replan_on_failure,
    sequence,
    with_retry,
)
from workspace.bt.dsl import (
    ActionRegistry,
    build_precedence,
    state_to_frozen,
)
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


def read_yaml_or_j2(path: Path, **render_vars: Any) -> Optional[dict]:
    """Read ``path`` as YAML, rendering it as a Jinja2 template first if
    a ``.j2`` sibling exists. ``render_vars`` are passed to the template
    (so e.g. ``recipes.j2`` can use ``{{ speed_factor }}``).

    Same precedence pace_or's ``_load_yaml`` uses: ``foo.j2`` wins over
    ``foo.yaml`` so projects can drop in a template without removing
    their old YAML. Returns the parsed dict, or ``None`` if neither
    file exists.

    Public helper — project ``main.py`` uses it to read launch.j2 too.
    """
    base = path.with_suffix("")
    j2_path   = base.with_suffix(".j2")
    yaml_path = base.with_suffix(".yaml")
    if j2_path.is_file():
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(j2_path.parent)))
        rendered = env.get_template(j2_path.name).render(**render_vars)
        return yaml.safe_load(rendered) or {}
    if yaml_path.is_file():
        with open(yaml_path) as f:
            return yaml.safe_load(f) or {}
    return None


# Back-compat alias — internal callers used the underscore version.
_read_yaml_or_j2 = read_yaml_or_j2


def load_recipes(
    workspace: Any,
    core: Any,
    recipes_path: Path,
    **render_vars: Any,
) -> Dict[str, Any]:
    """Read a recipes definition file and instantiate each entry.

    Returns a ``{alias: recipe_instance}`` dict — same shape pace_or's
    BaseWorkflow produces. ``Action.execute(...)`` bodies access it
    via ``self.ctx.recipes[alias]``.

    The framework reads ``recipes.j2`` (rendered as Jinja2) when present,
    falling back to ``recipes.yaml`` — same precedence as pace_or. Pass
    either filename in ``recipes_path``; the suffix is replaced when
    looking for the j2 sibling.

    File schema (matches pace_or):

        gripper:
          class: workspace.recipes.tool_rack.ToolRack
          kwargs: {component: tool_rack_144mm_1, left_approach: true}

    Args:
        workspace: Workspace SDK root (used to resolve component names).
        core: Core component (passed to each recipe constructor).
        recipes_path: Path to ``recipes.yaml`` (or ``.j2``). Missing
            both → empty dict.
        **render_vars: Forwarded to the Jinja2 template (only matters
            for ``.j2`` files). Use to inject project-wide knobs like
            ``speed_factor=50`` so every recipe sees the same value.

    Behaviour on errors:
        * Missing file → empty dict, no log.
        * Missing component in scene → one-line warning per recipe,
          that entry skipped.
        * Anything else (import error, bad class kwargs) → traceback
          logged, that entry skipped. Other recipes continue.
    """
    defs = _read_yaml_or_j2(Path(recipes_path), **render_vars)
    if defs is None:
        return {}
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


# ── Checks loading (mirrors pace_or's Checks.register pattern) ────────────


class _CheckRegistrar:
    """Tiny stand-in for pace_or's runner — accumulates name → callable.

    pace_or's ``Checks.register(runner)`` calls
    ``runner.register_check(name, callable)`` for each method it wants
    exposed. The BT framework doesn't have a "runner" object — checks
    are just looked up by name in ``ctx.meta['checks']`` — so we hand
    the Checks instance an object whose ``register_check`` collects
    into a dict and return that dict.
    """

    def __init__(self) -> None:
        self.checks: Dict[str, Callable[..., Any]] = {}

    def register_check(self, name: str, fn: Callable[..., Any]) -> None:
        if name in self.checks:
            log.warning("checks: re-registering %r — overwriting", name)
        self.checks[name] = fn


def load_checks(
    workspace: Any,
    core: Any,
    recipes: Dict[str, Any],
    checks_module: Optional[Any] = None,
    **kwargs: Any,
) -> Dict[str, Callable[..., Any]]:
    """Build the ``{name: callable}`` dict the BT framework consults for
    ``pre_check`` / ``post_check`` on every action.

    Same pattern as pace_or:
      * Import the project's ``checks.py`` (caller does this; we accept
        the module so main.py shows the import).
      * Instantiate ``Checks(rcp=recipes, rt=workspace.rt, **kwargs)``.
      * Call ``checks_instance.register(registrar)`` — registrar
        collects ``name → bound_method`` into a dict.
      * Return that dict — ``run_protocol`` stuffs it into
        ``ctx.meta['checks']``.

    Args:
        workspace: Workspace SDK root.
        core: Core component.
        recipes: ``{alias: recipe_instance}`` dict (from
            :func:`load_recipes`). Checks usually drive cameras /
            sensors via recipes.
        checks_module: The imported ``checks`` module. ``None`` is OK
            (returns an empty dict — projects without checks just
            never reference any names in pre_check / post_check).
        **kwargs: Forwarded to ``Checks.__init__``.

    Returns:
        ``{name: callable}`` dict ready to live in ``ctx.meta['checks']``.
        Each callable takes a single ``item_index`` int and may return
        ``bool`` or ``(bool, message)`` — the framework handles both
        shapes (matches pace_or's ``(passed, msg)`` convention).
    """
    if checks_module is None:
        return {}
    if not hasattr(checks_module, "Checks"):
        log.warning(
            "%s has no Checks class — pre_check/post_check names will not resolve",
            checks_module.__name__,
        )
        return {}
    instance = checks_module.Checks(
        rcp=recipes,
        rt=getattr(workspace, "rt", None) or getattr(workspace, "runtime", None),
        **kwargs,
    )
    registrar = _CheckRegistrar()
    if hasattr(instance, "register"):
        instance.register(registrar)
    else:
        log.warning(
            "%s.Checks has no register() method — no checks will be wired",
            checks_module.__name__,
        )
    return registrar.checks


# ── Default protocol runner ───────────────────────────────────────────────


def run_protocol(
    workspace: Any,
    core: Any,
    actions_module: Any,
    *,
    recipes: Optional[Dict[str, Any]] = None,
    checks: Optional[Dict[str, Callable[..., Any]]] = None,
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
        checks: ``{name: callable}`` dict from :func:`load_checks`
            (or hand-built). Names here are referenced by
            ``Action.pre_check`` / ``Action.post_check``. ``None`` →
            empty dict (any check-name reference will log a warning
            and pass).
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
    objects       = dict(spec.get("objects") or {})

    # Goal MUST be a callable ``state -> bool``. The planner calls it
    # after every state expansion to check "are we done yet?". One
    # shape — for nuanced goals (disjunctions, thresholds, multi-branch
    # terminal actions) just write the predicate directly.
    goal_fn = spec["goal"]
    if not callable(goal_fn):
        raise TypeError(
            f"setup() returned goal of type {type(goal_fn).__name__} — "
            "expected a callable: ``def goal(state): return ...``"
        )

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
            "project":      project_name,
            "kwargs":       kwargs,
            "objects":      objects,
            "checks":       checks or {},
            # Tracks the tool currently held by the robot. _DSLActionLeaf
            # consults / updates this when an Action declares ``tool=``.
            # ``None`` = nothing held; populated by the auto-swap path.
            "current_tool": None,
        },
    )

    # 3. Registry artifacts (auto-populated when ``actions`` was imported).
    #    Tool-swap durations live per-action on the Action class
    #    (cls.tool_swap_duration); the scheduler reads them via
    #    ActionMeta. No global knob here.
    registry       = ActionRegistry.current()
    templates      = registry.to_templates(ctx)
    meta           = registry.to_meta()
    leaf_factory   = registry.leaf_factory(ctx)
    # Precedence-aware scheduling — actions whose pre()/eff() are
    # causally independent overlap on different resources.
    build_schedule = make_schedule_builder(
        meta, precedence_fn=lambda plan: build_precedence(plan, registry),
    )

    # 4. Default tree shape: from_schedule + per-leaf retry + outer
    #    replan_on_failure. Project can supply its own build_tree by
    #    calling run_protocol_with_tree() instead (advanced use).
    # Durations + resources tables for from_schedule's overlap
    # detection and resource-aware sub-grouping inside each phase.
    durations = {name: float(m.duration) for name, m in meta.items()}
    from workspace.planner.plan_scheduler import _resources as _resources_of
    action_resources = {
        name: _resources_of(m.resource) or ("robot",)
        for name, m in meta.items()
    }

    def _make_swap_leaf(from_tool, to_tool):
        return SwapLeaf(ctx=ctx, from_tool=from_tool, to_tool=to_tool)

    def build_tree(schedule, _ctx):
        # schedule_greedy returns (actions, swaps).
        actions_list, swaps_list = schedule
        def _wrapped(action_name, item_index):
            return with_retry(leaf_factory(action_name, item_index), max_attempts=2)
        body = from_schedule(
            actions_list, _wrapped,
            swaps=swaps_list,
            swap_factory=_make_swap_leaf,
            durations=durations,
            resources=action_resources,
            name=f"{project_name}/body",
        )
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

    # End-cleanup tree: collect every Action subclass declaring
    # ``trigger="end"`` (project-guide §9). When the operator clicks
    # End, the BT engine completes the current action, then runs this
    # subtree to perform cleanup (park tools, return home, …) before
    # exiting. The collection happens at engine-start so the registry
    # is already populated.
    end_classes = [
        (name, cls)
        for name, cls in sorted(registry._actions.items())
        if getattr(cls, "trigger", None) == "end"
    ]

    def build_end_tree() -> Optional[py_trees.behaviour.Behaviour]:
        if not end_classes:
            return None
        # trigger="end" actions are scene-level (no per-item iteration)
        # so we instantiate exactly one leaf per class, item_index=0.
        leaves = [leaf_factory(name, 0) for name, _ in end_classes]
        return sequence(f"{project_name}/end", *leaves)

    root = replanner.rebuild()
    engine = BTEngine(
        root=root,
        rebuild=replanner.rebuild,
        build_end_tree=build_end_tree,
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
