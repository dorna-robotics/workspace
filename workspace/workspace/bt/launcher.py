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
    plan_window: int = 4,
    slice_dim: str = "tube",
    scheduler: str = "cpsat",
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
        plan_window: How many items the planner thinks about at once.
            Default 4 — the safe limit for the in-house GBFS planner.
            Projects on a stronger planner (e.g. Fast Downward) can
            raise this in their ``launch.yaml``.
        slice_dim: Which ``objects`` key to slice along. Default
            ``"tube"`` — matches the convention in lab protocols.
            Only meaningful when ``setup()`` returns ``item_done``.
        scheduler: ``"cpsat"`` (default) uses the CP-SAT solver —
            optimal makespan, clusters same-tool actions automatically,
            packs work into idle robot windows. ``"greedy"`` uses the
            in-house earliest-start scheduler — fast, no dependency,
            but locally-optimal only. CP-SAT falls back to greedy
            automatically if ortools is missing or the solver errors.
        **kwargs: Operator-supplied parameters from the GUI; forwarded
            to ``actions_module.setup``.

    Slicing:
        If ``setup()`` returns ``item_done(state, item) -> bool`` AND
        the operator-supplied batch on the slice dimension exceeds
        ``plan_window``, the launcher transparently splits work into
        windows of ``plan_window`` items. After each window's tree
        succeeds, a ``slice_check`` leaf re-evaluates the global goal
        and either exits (all done) or raises ``ReplanRequested`` so
        the engine rebuilds for the next window. When the batch
        already fits in one window, slicing is a no-op.
    """
    if project_name is None:
        project_name = actions_module.__name__.split(".")[-1]

    # 0. Reset the scene to its launch-time layout. The BT framework
    # always plans from a fresh `setup()`-derived initial state (tubes
    # in_source, no caps in holder, etc.), but the scene's attach
    # graph carries over from the previous workflow run — tubes might
    # still be in the working rack, a tool on the robot, caps on the
    # autosampler. Resetting puts the physical layout back in sync
    # with the planner's view, so consecutive Starts work without
    # needing a Kill+Launch cycle. No-op on the very first run after
    # Launch (everything is already in its initial position).
    if hasattr(workspace, "reset_scene"):
        try:
            workspace.reset_scene()
            log.info("Launcher: scene reset to launch-time layout")
        except Exception:
            log.exception(
                "Launcher: scene reset raised — continuing with current layout"
            )

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
    # `goal_facts` — set of positive fact tuples used as the PDDL
    # planner's GBFS heuristic (``h = |goal_facts \ state|``). Projects
    # can supply their own list via setup(); otherwise the framework
    # auto-derives one from the action effects (every monotonic
    # predicate × every reachable parameter binding). Auto-derivation
    # handles ~all lab protocols correctly; override only if some
    # monotonic predicate is decorative and would mislead the search.
    goal_facts = spec.get("goal_facts")

    # `item_done(state, item) -> bool` — optional per-item completion
    # predicate. When present, the launcher enables slicing along
    # ``slice_dim`` so the planner only thinks about ``plan_window``
    # items at a time. Without it, all items must fit in one plan.
    item_done = spec.get("item_done")
    if item_done is not None and not callable(item_done):
        raise TypeError(
            f"setup() returned item_done of type {type(item_done).__name__} — "
            "expected a callable: ``def item_done(state, item): return ...``"
        )

    # 2. Context. Carries the live mutable facts dict + recipes +
    #    object pools (used by Action.param_iter to enumerate
    #    candidate bindings).
    # Default event publisher: hook the runtime server's schedule WS
    # broadcaster if it's importable. Lets the project's launch include
    # the framework without explicitly wiring the GUI plumbing; if the
    # server module is missing (headless tests, alt frontends) we just
    # leave it None and the publish hooks become no-ops.
    event_publisher = None
    try:
        from workspace.runtime_server import _broadcast_schedule_event
        event_publisher = _broadcast_schedule_event
    except Exception:
        pass

    # Snapshot the full ``objects`` dict before slicing can mutate it
    # in-place. ``ctx.meta["objects"]`` follows the planner's current
    # window (narrowed per replan when slicing is active);
    # ``ctx.meta["all_objects"]`` always carries the original
    # un-sliced view. Actions that need to seed state for the *entire*
    # batch (e.g. a ``Start`` action that adds ``in_source(t)`` for
    # every tube) must read from ``all_objects`` — reading from
    # ``objects`` would only seed the current window and leave later
    # slices stuck without their initial facts.
    all_objects = {k: list(v) for k, v in (objects or {}).items()}

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
            "all_objects":  all_objects,
            "checks":       checks or {},
            # Tracks the tool currently held by the robot. _DSLActionLeaf
            # consults / updates this when an Action declares ``tool=``.
            # ``None`` = nothing held; populated by the auto-swap path.
            "current_tool": None,
            # Optional event sink for schedule + execution events. The
            # GUI's /ws/schedule WebSocket consumes these. None = no-op.
            "event_publisher": event_publisher,
        },
    )

    # Register the ctx on the workspace so runtime fact-mutation APIs
    # (workspace.add_fact / remove_fact / facts) can reach the active
    # state. Cleared automatically when the protocol returns / raises
    # so a subsequent run starts from a clean slate.
    if hasattr(workspace, "set_active_ctx"):
        workspace.set_active_ctx(ctx)

    # 3. Registry artifacts (auto-populated when ``actions`` was imported).
    #    Tool-swap durations live per-action on the Action class
    #    (cls.tool_swap_duration); the scheduler reads them via
    #    ActionMeta. No global knob here.
    registry       = ActionRegistry.current()
    templates      = registry.to_templates(ctx)
    meta           = registry.to_meta()
    leaf_factory   = registry.leaf_factory(ctx)

    # ── Slicing wiring (no-op when item_done absent or batch fits) ────
    #
    # Two views of the slicing dim:
    #   * ``all_items``  — full operator-supplied set (stays constant)
    #   * ``ctx.meta["objects"][slice_dim]`` — current window the
    #     planner thinks about. Re-assigned each rebuild.
    all_items: list = list(objects.get(slice_dim, []))
    slicing_active = (
        item_done is not None
        and len(all_items) > int(plan_window)
    )

    def _pick_window(state) -> list:
        """Next ``plan_window`` items that aren't done yet, in order."""
        out: list = []
        for it in all_items:
            if item_done(state, it):
                continue
            out.append(it)
            if len(out) >= int(plan_window):
                break
        return out

    def _planning_goal(state) -> bool:
        """Goal for the planner.

        Default (no slicing): defer to the user's ``goal_fn`` so any
        global tail goals (e.g. ``parked``) are honored.

        Slicing active: the planner only sees the current window, so
        the in-window goal is ``all(item_done over window)``. But on
        the *final* slice — when no items remain outside the window —
        the planner should also satisfy the user's full ``goal_fn``,
        otherwise tail actions like ``Park`` (whose pre needs every
        item done) get skipped.
        """
        if item_done is None:
            return goal_fn(state)
        window = ctx.meta["objects"].get(slice_dim, [])
        if not all(item_done(state, it) for it in window):
            return False
        # Window done. If there are still items outside the window
        # (more slices to come), we're not at the tail yet — pass.
        remaining_outside = [
            it for it in all_items
            if it not in window and not item_done(state, it)
        ]
        if remaining_outside:
            return True
        # Final slice — every item is done. Require the user's full
        # goal_fn so tail actions like Park get planned.
        return goal_fn(state)

    def _global_goal(state) -> bool:
        """Goal for the slice_check leaf — all items in the full batch done."""
        if item_done is None:
            return goal_fn(state)
        return all(item_done(state, it) for it in all_items)

    def _observe(c) -> Any:
        """Observe + (when slicing) advance the planning window."""
        state = state_to_frozen(c.state)
        if slicing_active:
            window = _pick_window(state)
            c.meta["objects"][slice_dim] = window
            log.info(
                "Launcher: slice window = %s (%d/%d done)",
                window,
                sum(1 for it in all_items if item_done(state, it)),
                len(all_items),
            )
        return state

    if slicing_active:
        log.info(
            "Launcher: slicing enabled — %d items, window=%d (%d slices)",
            len(all_items), int(plan_window),
            (len(all_items) + int(plan_window) - 1) // int(plan_window),
        )

    # Auto-derive goal_facts if the project didn't supply one. Done
    # after the registry+ctx are ready so `param_iter` can read
    # ``ctx.meta["objects"]``. When slicing is active, derive lazily
    # (per rebuild) so the heuristic reflects the current window.
    if goal_facts is None:
        if slicing_active:
            goal_facts = lambda: registry.derive_goal_facts(ctx)
            log.info(
                "Launcher: goal_facts will be re-derived per slice "
                "(%d monotonic predicates)",
                len(registry.monotonic_predicates()),
            )
        else:
            goal_facts = registry.derive_goal_facts(ctx)
            log.info(
                "Launcher: auto-derived %d goal_facts across %d monotonic predicates",
                len(goal_facts), len(registry.monotonic_predicates()),
            )
    # Precedence-aware scheduling — actions whose pre()/eff() are
    # causally independent overlap on different resources. We thread
    # the current observed state through so state-aware ``eff()``
    # bodies see the world they would at runtime when computing the
    # precedence graph.
    def _precedence(plan):
        facts = ctx.state.get("facts", frozenset())
        initial = facts if isinstance(facts, frozenset) else frozenset(facts)
        return build_precedence(plan, registry, initial_state=initial, ctx=ctx)

    use_cpsat = (str(scheduler).lower() == "cpsat")
    build_schedule = make_schedule_builder(
        meta, use_cpsat=use_cpsat, precedence_fn=_precedence,
    )
    log.info("Launcher: scheduler=%s", "cpsat" if use_cpsat else "greedy")

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

    # Counter incremented every time build_tree fires — used as the
    # schedule's identifier in published events so the GUI can tell one
    # replan apart from the next.
    replan_counter = {"n": 0}

    def build_tree(schedule, _ctx):
        # schedule_greedy returns (actions, swaps).
        actions_list, swaps_list = schedule

        # Publish the just-built schedule to anyone listening (the WS
        # broadcaster, in particular). Enrich each entry with the
        # static meta the frontend needs to render: duration, resource,
        # tool. Resource list is normalised to a tuple-of-strings.
        replan_counter["n"] += 1
        # Expose the current replan_id on ctx.meta so every leaf /
        # swap-leaf can stamp its lifecycle events with the slice it
        # belongs to. The schedule modal uses this to give per-slice
        # parameterless actions (Start / Park / ShakerOne / ShakerTwo
        # — same self.name across slices) distinct positions on the
        # Gantt instead of letting later slices overwrite earlier
        # ones' placements.
        ctx.meta["current_replan_id"] = replan_counter["n"]
        pub = ctx.meta.get("event_publisher")
        if pub is not None:
            import time as _time
            try:
                from workspace.planner.plan_scheduler import _resources as _r
                pub({
                    "type": "schedule",
                    "replan_id": replan_counter["n"],
                    "wall_ts": _time.time(),
                    "tool_resource": "robot",
                    "actions": [
                        {
                            "leaf_name": f"{n}(t{i})",
                            "name": n,
                            # Original Action subclass name (PascalCase) so
                            # the GUI can label blocks exactly as authored.
                            "class_name": (
                                registry.get(n).__name__
                                if registry.get(n) is not None else n
                            ),
                            "item": i,
                            # Parameterless actions (Start / Park) have one
                            # grounding regardless of items — flag so the
                            # GUI drops the misleading "(0)" label.
                            "parametrized": bool(
                                registry.get(n).params
                                if registry.get(n) is not None else True
                            ),
                            "start_t": float(s),
                            "duration": float(meta[n].duration) if n in meta else 1.0,
                            "resources": list(_r(meta[n].resource)) if n in meta else [],
                            "tool": meta[n].tool if n in meta else None,
                        }
                        for n, i, s in actions_list
                    ],
                    "swaps": [
                        {
                            "leaf_name": f"swap({ft or '∅'}→{tt or '∅'})",
                            "from": ft,
                            "to": tt,
                            "start_t": float(s),
                            "duration": float(d),
                        }
                        for s, ft, tt, d in swaps_list
                    ],
                    "makespan": max(
                        [s + (meta[n].duration if n in meta else 1)
                         for n, _, s in actions_list]
                        + [s + d for s, _, _, d in swaps_list]
                        + [0.0]
                    ),
                })
            except Exception:
                log.exception("Failed to publish schedule event — continuing")

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
        # When slicing is on, end-of-slice check decides "exit or
        # replan for next window". When off, it's a no-op (the body
        # alone reaches SUCCESS naturally).
        if slicing_active:
            from workspace.bt.builder import slice_check
            root_seq = sequence(
                f"{project_name}/root",
                body,
                slice_check(ctx, _global_goal, name=f"{project_name}/slice_check"),
            )
        else:
            root_seq = sequence(f"{project_name}/root", body)
        return replan_on_failure(
            root_seq,
            reason="protocol step failed — replanning from observed state",
        )

    replanner = Replanner(
        ctx=ctx,
        observe=_observe,
        templates=templates,
        goal=_planning_goal,
        goal_facts=goal_facts,
        build_schedule=build_schedule,
        build_tree=build_tree,
        config=ReplanConfig(verbose=True),
    )

    # Park-cleanup tree: collect every Action subclass declaring
    # ``trigger = "park"``. When the operator clicks Park, the BT
    # engine completes the current action, then runs this subtree to
    # park the robot (release tools, return home, …) before exiting.
    # The collection happens at engine-start so the registry is
    # already populated.
    from workspace.bt.dsl import _is_park_trigger
    park_classes = [
        (name, cls)
        for name, cls in sorted(registry._actions.items())
        if _is_park_trigger(cls)
    ]

    def build_park_tree() -> Optional[py_trees.behaviour.Behaviour]:
        if not park_classes:
            return None
        # Park-trigger actions are scene-level (no per-item iteration)
        # so we instantiate exactly one leaf per class, item_index=0.
        leaves = [leaf_factory(name, 0) for name, _ in park_classes]
        return sequence(f"{project_name}/park", *leaves)

    root = replanner.rebuild()
    # Slicing turns every window-completion into a replan, so the cap
    # has to clear ceil(N/plan_window) plus headroom for real
    # world-drift replans. Add 50 to cover the latter.
    replan_cap = (
        max(50, (len(all_items) + int(plan_window) - 1) // int(plan_window) + 50)
        if slicing_active else 50
    )
    engine = BTEngine(
        root=root,
        rebuild=replanner.rebuild,
        build_park_tree=build_park_tree,
        runtime=ctx.runtime,
        config=EngineConfig(tick_hz=float(tick_hz), max_replans=replan_cap),
    )
    log.info(
        "%s: starting BT engine — %d action(s) in plan",
        project_name, len(replanner.last_plan or []),
    )
    try:
        status = engine.run()
    finally:
        # Always clear the workspace's active-ctx pointer on exit so a
        # subsequent run (or stray fact mutation between runs) doesn't
        # leak into an old state dict.
        if hasattr(workspace, "clear_active_ctx"):
            workspace.clear_active_ctx()
    log.info("%s: BT engine finished with status=%s", project_name, status.name)
    return status
