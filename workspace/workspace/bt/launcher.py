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
import sys
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
    RecipeUnavailable,
    build_precedence,
    derive_capacity_spans,
    state_to_frozen,
)
from workspace.bt.engine import BTEngine, EngineConfig
from workspace.bt.phase import PhaseNotReady, current_phase as _current_phase_impl, pick_window as _pick_window_impl
from workspace.bt.protocol import Protocol, load_route
from workspace.planner import Replanner, make_schedule_builder, plan_route


log = logging.getLogger(__name__)

# Set once, by _configure_logging below.
_LOGGING_CONFIGURED = False


def _configure_logging() -> None:
    """Send the platform's ``log.info`` to stdout, once per process.

    Nothing in the platform ever configured a logging handler, so Python
    fell back to its last-resort handler — which emits WARNING and above
    only. Every ``log.info`` call went nowhere: 31 sites, including the
    launcher's slice windows, the MQTT connect/disconnect pair, and the
    CP-SAT summary line (action count, makespan, solver status, wall
    time). The orchestrator captures a launched project's stdout into
    ``<project_dir>/status/workspace.log`` and the dashboard tails that
    file, so making these visible costs nothing but this handler.

    SCOPED TO THE ``workspace`` LOGGER, NEVER THE ROOT. ``basicConfig``
    would raise the level for every third-party library too, and Tornado
    logs one ``tornado.access`` line per HTTP request — with the GUI
    polling the runtime server, the run log would become an access log
    with the useful lines buried in it. paho-mqtt is nearly as chatty.
    Third-party loggers stay on the WARNING fallback, exactly as before.

    ``propagate = False`` matters: without it every record is emitted
    twice, once here and once by the root fallback.

    Idempotent — run_protocol is called per workflow run, and a second
    handler would double every line.
    """
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return
    _LOGGING_CONFIGURED = True
    pkg = logging.getLogger("workspace")
    if not pkg.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        pkg.addHandler(h)
    pkg.setLevel(logging.INFO)
    pkg.propagate = False


def _load_route(ref: Any, actions_module: Any, *, project: str = "") -> Protocol:
    """Resolve ``launch.yaml``'s ``route:`` into the run's :class:`Protocol`.

    ``route:`` names the module holding ``ROUTE`` — ``phases.py`` for a
    phased project (its Phase classes carry their steps), unset for a
    flat one, whose ``ROUTE`` lives in the actions module. A path is
    resolved the way ``actions:`` and ``checks:`` are — by module name,
    the project directory already on sys.path because main.py imported
    ``actions`` from it.
    """
    if ref is None:
        return load_route(actions_module, project=project)
    if not isinstance(ref, str):
        raise TypeError(f"launch.yaml: route: expected a module path, got {type(ref).__name__}")
    name = ref.removesuffix(".py").replace("/", ".")
    mod = importlib.import_module(name)
    return load_route(mod, project=project)


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


class _RecipeDict(dict):
    """Alias -> recipe, remembering the ones that failed to build.

    Skipping a broken recipe and carrying on is deliberate (an alias no
    action uses must not stop the bench). What was NOT deliberate is
    what the skip turned into: the first action to reach for the alias
    died on a bare ``KeyError``, which the engine could not tell apart
    from a normal step failure, so it replanned — forever, into the
    identical plan, because the cause was in the scene file, not the
    world state.

    Recording the reason here fixes both halves: the error names the
    real cause, and its type marks it non-replannable.
    """

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.failed: Dict[str, str] = {}

    def __missing__(self, alias):
        why = self.failed.get(alias)
        if why:
            raise RecipeUnavailable(
                f"recipe {alias!r} failed to load at launch ({why}) — "
                f"replanning cannot fix it; fix the scene or the recipes file"
            )
        raise RecipeUnavailable(
            f"recipe {alias!r} is not defined in the recipes file "
            f"(defined: {sorted(self)})"
        )


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
    rcp = _RecipeDict()
    for alias, defn in defs.items():
        try:
            cls = _import_class(defn["class"])
            kwargs = dict(defn.get("kwargs") or {})
            # ``component`` is optional — the core-camera Inspector and similar
            # robot-camera-only recipes don't take one.
            comp_name = kwargs.pop("component", None)
            if comp_name is not None:
                try:
                    comp = workspace.components[comp_name]
                except KeyError:
                    log.warning(
                        "recipes.yaml[%s]: component %r not in scene — skipping",
                        alias, comp_name,
                    )
                    rcp.failed[alias] = f"component {comp_name!r} not in scene"
                    continue
                rcp[alias] = cls(workspace, core, comp, **kwargs)
            else:
                rcp[alias] = cls(workspace, core, **kwargs)
        except Exception as exc:
            log.exception("recipes.yaml[%s]: instantiation failed — skipping", alias)
            rcp.failed[alias] = f"{type(exc).__name__}: {exc}"
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
    slice_dim: Optional[str] = None,
    scheduler: str = "cpsat",
    route: Optional[str] = None,
    seed_facts: Any = None,
    until_phase: Optional[str] = None,
    reset_scene: bool = True,
    initial_tool: Optional[str] = None,
    **kwargs,
) -> py_trees.common.Status:
    """Default plan → schedule → BT tick lifecycle for any BT project.

    Steps:

      1. Calls ``actions_module.setup(**kwargs)`` to map operator
         kwargs into ``initial_facts`` / ``goal`` / ``objects``.
      2. Loads the project's ROUTE (``route:`` in launch.yaml, or the
         actions module) into a :class:`Protocol` — the steps, the
         phases, the scheduler meta and the leaf factory all come
         from it.
      3. Builds a :class:`WorkspaceContext` carrying the recipes dict
         the caller supplied.
      4. Wraps the body in ``replan_on_failure(...)``. Leaves are NOT
         retried implicitly (``max_attempts=1``) — a failure replans;
         retry only where a project explicitly opts in.
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
        plan_window: How many items one schedule holds. Default 4.
            Planning itself is a lookup and does not care; the window
            bounds the scheduler's model (``launch.yaml``, and per
            phase ``Phase.plan_window``).
        slice_dim: Which ``objects`` key to slice along. Default
            ``"tube"`` — matches the convention in lab protocols.
            Only meaningful when ``setup()`` returns ``item_done``.
        route: Path of the module holding ``ROUTE`` (``launch.yaml:
            route:``) — ``phases.py`` for a phased project. ``None``:
            the actions module's ``ROUTE``.
        scheduler: ``"cpsat"`` (default) uses the CP-SAT solver —
            optimal makespan, clusters same-tool actions automatically,
            packs work into idle robot windows. ``"greedy"`` uses the
            in-house earliest-start scheduler — fast, no dependency,
            but locally-optimal only. CP-SAT falls back to greedy
            automatically if ortools is missing or the solver errors.
        seed_facts: Extra facts added to the initial state — how
            ``workspace.bt.bench`` starts a run PAST earlier phases
            (their closure facts seeded instead of executed).
        until_phase: Name of a phase; the run ends as soon as that
            phase is reached instead of at the project's goal. With
            ``seed_facts`` this runs exactly one phase.
        reset_scene: Snap the scene back to its launch-time layout
            before planning (default). ``False`` when the caller has
            already put the model where the run starts — a bench that
            applied a phase's layout — so the reset does not undo it.
        initial_tool: Recipe alias of the tool on the flange when the
            run starts (``None`` = empty flange, the launch-scene
            truth). A bench running phases back to back passes what
            the last phase left mounted, so the first swap is real.
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
    _configure_logging()

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
    if reset_scene and hasattr(workspace, "reset_scene"):
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
    if seed_facts:
        initial_facts |= {tuple(f) for f in seed_facts}
        log.info("Launcher: %d seeded fact(s) — starting past earlier phases", len(seed_facts))
    objects       = dict(spec.get("objects") or {})

    # Goal MUST be a callable ``state -> bool``: is the run done.
    goal_fn = spec["goal"]
    if not callable(goal_fn):
        raise TypeError(
            f"setup() returned goal of type {type(goal_fn).__name__} — "
            "expected a callable: ``def goal(state): return ...``"
        )

    # `item_done(state, item) -> bool` — optional per-item completion
    # predicate. When present, the launcher windows the batch along
    # ``slice_dim`` so one schedule holds ``plan_window`` items.
    item_done = spec.get("item_done")
    if item_done is not None and not callable(item_done):
        raise TypeError(
            f"setup() returned item_done of type {type(item_done).__name__} — "
            "expected a callable: ``def item_done(state, item): return ...``"
        )

    # 2. The protocol: the ROUTE, resolved. Steps, phases, scheduler
    #    meta and leaves all come from here — the same object bt.replay
    #    and the bench build from.
    protocol = _load_route(route, actions_module, project=project_name)
    phases = list(protocol.phases)

    # 3. Context. Carries the live mutable facts dict + recipes +
    #    object pools (used by Action.param_iter to enumerate
    #    candidate bindings).
    event_publisher = None
    try:
        from workspace.runtime_server import _broadcast_schedule_event
        event_publisher = _broadcast_schedule_event
    except Exception:
        pass

    # Snapshot the full ``objects`` dict before windowing can mutate it
    # in-place. ``ctx.meta["objects"]`` follows the current window
    # (narrowed per replan); ``ctx.meta["all_objects"]`` always carries
    # the original un-sliced view — what a ``Start`` that seeds facts
    # for every item reads.
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
            "current_tool": initial_tool,
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

    meta         = protocol.meta()
    leaf_factory = protocol.leaf_factory(ctx)

    # ── Windowing (no-op when item_done absent and batch fits) ────────
    #
    # ``slice_dim`` — the ``objects`` key the run windows along. Unset,
    # a single-dimension protocol (one ``objects`` key — every lab
    # protocol) windows along that key. Only a multi-dimension protocol
    # names it (``launch.yaml: slice_dim``).
    if slice_dim is None:
        keys = list(objects.keys())
        slice_dim = keys[0] if len(keys) == 1 else None
        if slice_dim is not None:
            log.info("Launcher: auto-inferred slice_dim=%r", slice_dim)
        elif item_done is not None and len(keys) > 1:
            log.warning(
                "Launcher: item_done set but %d object dims %r — "
                "no slice_dim given, so planning is NOT windowed. Pass "
                "slice_dim (launch.yaml) to enable it.", len(keys), keys,
            )
    elif slice_dim not in objects:
        # A stale key would window over nothing and plan nothing — say so.
        raise ValueError(
            f"launch.yaml: slice_dim={slice_dim!r} is not an objects key of setup(): "
            f"{list(objects)} — name one of them, or drop the key for a "
            f"single-dimension protocol"
        )

    # Two views of the item dim:
    #   * ``all_items``  — full operator-supplied set (stays constant)
    #   * ``ctx.meta["objects"][slice_dim]`` — current window the
    #     planner thinks about. Re-assigned each rebuild.
    all_items: list = list(objects.get(slice_dim, [])) if slice_dim else []

    # ── Phases ────────────────────────────────────────────────────────
    #
    # A PHASE is a goal the whole batch reaches before any item moves
    # past it: "every tube barcoded", then "every tube weighed", and so
    # on — DEPTH, which item windows cannot bound. ROUTE gives their
    # order; each carries the steps its items take while it is open.
    # The phase machinery lives in workspace/bt/phase.py so bt.replay
    # walks phases with the SAME code as the live run.
    if phases:
        # A NON-MONOTONIC PHASE CANNOT BE A PHASE BOUNDARY. If some
        # action removes the fact again, "everyone has reached it" is
        # not a line the batch crosses once — it can un-cross, the
        # phase never closes, and the run stalls with no error (the
        # Sussman trap). Catching it at launch beats discovering it
        # mid-batch.
        mono = protocol.monotonic_predicates(ctx)
        for ph in phases:
            for nm in ph.fact_names(all_items):
                if nm not in mono:
                    log.warning(
                        "Launcher: phase %r names fact %r, which some action "
                        "removes — the phase may never close. Phases must be "
                        "facts that only ever get added.", ph.name, nm,
                    )
        log.info("Launcher: %d phase(s): %s",
                 len(phases), " -> ".join(ph.name for ph in phases))

    # The phase this replan plans toward, resolved once in _observe:
    # ``(phase, items in scope)`` or None.
    _frozen_phase = {"cur": None}

    def _current_phase(state):
        return _current_phase_impl(state, phases, all_items, item_done)

    # ``until_phase``: the run is over when THAT phase is reached for
    # its scope — every goal below defers to it first.
    _until = None
    if until_phase is not None:
        _until = protocol.phase(until_phase)
        if _until is None:
            raise ValueError(
                f"until_phase={until_phase!r} is not a phase of this project: "
                f"{[ph.name for ph in phases]}"
            )
    _until_warned = {"done": False}

    def _until_reached(state) -> bool:
        if _until is None:
            return False
        items = list(_until.scope(state, all_items))
        return (not items) or _until.reached(state, items)

    slicing_active = (
        item_done is not None
        and (len(all_items) > int(plan_window) or bool(phases))
    )

    def _pick_window(state) -> list:
        """Next ``plan_window`` items still outstanding, in order (phase.py)."""
        return _pick_window_impl(state, phases, all_items, item_done, plan_window)

    def _planning_goal(state) -> bool:
        """What this window's plan must reach.

        No windowing: the project's ``goal_fn`` (so tail goals such as
        ``parked`` are honored). A phase open: THAT phase, over the
        items in the window — the route stops at the boundary. Else the
        window's items done; on the final window also ``goal_fn`` so
        the tail (Park) is planned.
        """
        if _until_reached(state):
            return True
        if item_done is None:
            return goal_fn(state)
        if phases:
            cur = _frozen_phase["cur"]
            if cur is not None:
                ph, items = cur
                window = ctx.meta["objects"].get(slice_dim, [])
                scoped = [it for it in (window or all_items) if it in items]
                return ph.reached(state, scoped or items)
        window = ctx.meta["objects"].get(slice_dim, [])
        if not all(item_done(state, it) for it in window):
            return False
        remaining_outside = [
            it for it in all_items
            if it not in window and not item_done(state, it)
        ]
        if remaining_outside:
            return True
        return goal_fn(state)

    def _global_goal(state) -> bool:
        """Goal for the slice_check leaf — all items in the full batch done."""
        if _until_reached(state):
            return True
        if item_done is None:
            return goal_fn(state)
        return all(item_done(state, it) for it in all_items)

    def _observe(c) -> Any:
        """Observe + (when windowing) advance the window and the phase."""
        state = state_to_frozen(c.state)
        if slicing_active:
            if _until is not None:
                # A seeded start: before Start's own seeds land (capacity
                # facts, manifest facts) the target's pre can read as not
                # ready. Plan toward the target; the route puts Start
                # first because the target's steps need it.
                try:
                    _cur = _current_phase(state)
                except PhaseNotReady:
                    _cur = None
                if _cur is None:
                    _cur = (_until, list(_until.scope(state, all_items)))
                elif _cur[0].name != _until.name and not _until_warned["done"]:
                    _until_warned["done"] = True
                    log.warning(
                        "Launcher: until_phase=%r but phase %r is not reached yet "
                        "— running it first (the seeds did not cover it).",
                        _until.name, _cur[0].name,
                    )
                window = _pick_window_impl(state, [_cur[0]] if _cur[0] not in phases else phases,
                                           all_items, item_done, plan_window)
            else:
                _cur = _current_phase(state) if phases else None
                window = _pick_window(state)
            c.meta["objects"][slice_dim] = window
            c.meta["current_phase"] = _cur[0].name if _cur else None
            c.meta["schedule_budget"] = _cur[0].schedule_budget if _cur else None
            # FREEZE THE PHASE FOR THIS REPLAN: the planning goal reads
            # this, never _current_phase(state) again.
            _frozen_phase["cur"] = _cur
            log.info(
                "Launcher: slice window = %s (%d/%d done)",
                window,
                sum(1 for it in all_items if item_done(state, it)),
                len(all_items),
            )
        return state

    if slicing_active:
        log.info(
            "Launcher: windowing enabled — %d items, window=%d",
            len(all_items), int(plan_window),
        )

    def _plan(state):
        """The route lookup for this window: the run-level steps plus
        the open phase's, in ROUTE order, over the window's items."""
        cur = _frozen_phase["cur"] if slicing_active else None
        classes = protocol.candidates(cur[0].name if cur else None)
        templates = protocol.templates(ctx, classes)
        items = (ctx.meta["objects"].get(slice_dim, []) if slice_dim else [])
        return plan_route(templates, state, _planning_goal, list(items),
                          explain=protocol.explainer(ctx))

    # Precedence-aware scheduling — steps whose pre()/eff() are
    # causally independent overlap on different resources. The observed
    # state is threaded through so state-aware bodies see the world
    # they would at runtime when the precedence graph is derived.
    def _precedence(plan):
        facts = ctx.state.get("facts", frozenset())
        initial = facts if isinstance(facts, frozenset) else frozenset(facts)
        return build_precedence(plan, protocol, initial_state=initial, ctx=ctx)

    def _capacity(plan):
        facts = ctx.state.get("facts", frozenset())
        initial = facts if isinstance(facts, frozenset) else frozenset(facts)
        return derive_capacity_spans(plan, protocol, initial_state=initial, ctx=ctx)

    use_cpsat = (str(scheduler).lower() == "cpsat")
    build_schedule = make_schedule_builder(
        meta, use_cpsat=use_cpsat, precedence_fn=_precedence, capacity_fn=_capacity,
        # The tool on the flange when this window is scheduled — the
        # SwapLeaf keeps ctx.meta["current_tool"] true — so a window
        # never opens with a swap onto the tool it already holds.
        initial_tool_fn=lambda: ctx.meta.get("current_tool"),
        # The open phase's deterministic scheduling budget (Phase.schedule_budget).
        budget_fn=lambda: ctx.meta.get("schedule_budget"),
    )
    log.info("Launcher: scheduler=%s", "cpsat" if use_cpsat else "greedy")

    # 4. Tree: from_schedule + per-leaf retry + outer replan_on_failure.
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
        # swap-leaf can stamp its lifecycle events with the window it
        # belongs to. The schedule modal uses this to give per-window
        # parameterless actions (Start / Park — same self.name across
        # windows) distinct positions on the Gantt.
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
                    # Which phase this window belongs to, or None when the
                    # project declares no phases. The Gantt groups
                    # consecutive windows sharing a phase under one band.
                    "phase": ctx.meta.get("current_phase"),
                    "actions": [
                        {
                            "leaf_name": f"{n}(t{i})",
                            "name": n,
                            # Original Action subclass name (PascalCase) so
                            # the GUI can label blocks exactly as authored.
                            "class_name": (
                                protocol.get(n).__name__
                                if protocol.get(n) is not None else n
                            ),
                            "item": i,
                            # Parameterless actions (Start / Park) have one
                            # grounding regardless of items — flag so the
                            # GUI drops the misleading "(0)" label.
                            "parametrized": bool(
                                protocol.get(n).params
                                if protocol.get(n) is not None else True
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
            # max_attempts=1 = NO implicit retry — a failed action goes
            # straight to replan. Retry only where a project explicitly
            # opts in with its own with_retry wrapper.
            return with_retry(leaf_factory(action_name, item_index), max_attempts=1)
        # The plan's precedence, keyed like the tree's entries, so a leaf
        # in a parallel branch waits for what the schedule put before it.
        _plan_steps = list(replanner.last_plan or [])
        _key = lambda a: f"{a.name}(t{a.params[0] if a.params else 0})"
        _preds = _precedence(_plan_steps) if _plan_steps else []
        pred_names = {_key(_plan_steps[i]): {_key(_plan_steps[j]) for j in _preds[i]}
                      for i in range(len(_plan_steps))} if _plan_steps else None
        body = from_schedule(
            actions_list, _wrapped,
            swaps=swaps_list,
            swap_factory=_make_swap_leaf,
            durations=durations,
            resources=action_resources,
            name=f"{project_name}/body",
            predecessors=pred_names,
        )
        # When windowing is on, the end-of-window check decides "exit or
        # replan for the next window". When off, it's a no-op (the body
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
        plan=_plan,
        build_schedule=build_schedule,
        build_tree=build_tree,
    )

    # Park-cleanup tree: every Action subclass declaring
    # ``trigger = "park"``. When the operator clicks Park, the BT
    # engine completes the current action, then runs this subtree to
    # park the robot (release tools, return home, …) before exiting.
    park_classes = list(protocol.park_classes)

    def build_park_tree() -> Optional[py_trees.behaviour.Behaviour]:
        if not park_classes:
            return None
        # Park-trigger actions are scene-level (no per-item iteration)
        # so we instantiate exactly one leaf per class, item_index=0.
        leaves = [leaf_factory(name, 0) for name, _ in park_classes]
        return sequence(f"{project_name}/park", *leaves)

    root = replanner.rebuild()
    engine = BTEngine(
        root=root,
        rebuild=replanner.rebuild,
        build_park_tree=build_park_tree,
        runtime=ctx.runtime,
        # The cap counts consecutive zero-progress replans: the probe
        # resets it whenever the fact state moved between replans, so
        # window-completion replans (slicing) and operator-recovered
        # failures never accumulate toward it. Flat 50 suffices for any
        # batch size.
        progress_probe=lambda: frozenset(ctx.state.get("facts", frozenset())),
        config=EngineConfig(tick_hz=float(tick_hz), max_replans=50),
    )
    log.info(
        "%s: starting BT engine — %d action(s) in plan",
        project_name, len(replanner.last_plan or []),
    )
    # Replay fusion: make the previous run's seam recordings
    # consultable NOW — and only now. Mid-run recordings stay
    # invisible (snapshot rule, core.book_note), so this run's
    # lookups see exactly the book as it stood at its start.
    try:
        core.book_reload()
    except Exception:
        log.warning("%s: motion-book reload failed", project_name, exc_info=True)
    try:
        status = engine.run()
    finally:
        # Always clear the workspace's active-ctx pointer on exit so a
        # subsequent run (or stray fact mutation between runs) doesn't
        # leak into an old state dict.
        if hasattr(workspace, "clear_active_ctx"):
            workspace.clear_active_ctx()
    log.info("%s: BT engine finished with status=%s", project_name, status.name)
    # Continuous motion: nothing stays deferred past the run — a
    # deferred final Park must still park. A killed runtime DROPS the
    # held tail instead (never move after a kill; core also drops any
    # tail whose deposit pose no longer matches the live robot).
    try:
        if getattr(core, "_motion_tail", None) is not None:
            rt = getattr(workspace, "rt", None)
            if rt is not None and getattr(rt, "killed", False):
                core.tail_consume()
            else:
                core.tail_flush(reason="run ended")
    except Exception:
        log.warning("%s: end-of-run motion-tail flush failed", project_name, exc_info=True)
    # One line of fusion observability per run — which pass this was
    # (recording vs fused) and whether any seam had to re-learn.
    try:
        summary = core.fusion_summary()
        if summary:
            rt = getattr(workspace, "rt", None)
            if rt is not None:
                rt.step(summary)
            log.info("%s: %s", project_name, summary)
    except Exception:
        pass
    return status
