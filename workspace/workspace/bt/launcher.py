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
    ActionRegistry,
    RecipeUnavailable,
    build_precedence,
    derive_capacity_spans,
    state_to_frozen,
)
from workspace.bt.engine import BTEngine, EngineConfig
from workspace.planner import ReplanConfig, Replanner, make_schedule_builder


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


def _load_phases(ref: Any, kwargs: Optional[dict] = None) -> Any:
    """Resolve ``launch.yaml``'s ``phases:`` into a phase list.

    Accepts what the key can reasonably hold:

      * ``None``                  — no phases; the launcher behaves as before.
      * a list                    — used as-is (inline in launch.yaml).
      * ``"phases.py"`` / dotted  — imported; the module must expose
        ``PHASES`` (a list) or ``phases`` (a list, or a callable taking
        the run kwargs so the list can depend on batch size).

    A path is resolved the same way ``actions:`` and ``checks:`` are —
    by module name, with the project directory already on sys.path
    because main.py imported ``actions`` from it.
    """
    if ref is None or isinstance(ref, (list, tuple)):
        return ref
    if not isinstance(ref, str):
        log.warning("Launcher: phases: expected a path or list, got %s — ignored.",
                    type(ref).__name__)
        return None
    name = ref.removesuffix(".py").replace("/", ".")
    try:
        mod = importlib.import_module(name)
    except Exception:
        log.exception("Launcher: phases: could not import %r — running without phases.", ref)
        return None
    # PHASE CLASSES FIRST. A module that declares Phase subclasses is
    # the authored form; PHASES/phases as a plain list stays supported
    # for a short static sequence, and for anything generated.
    from workspace.bt.phase import collect as _collect_phases
    spec_val = _collect_phases(mod) or None
    if spec_val is None:
        spec_val = getattr(mod, "PHASES", None)
    if spec_val is None:
        spec_val = getattr(mod, "phases", None)
    if callable(spec_val):
        try:
            spec_val = spec_val(**(kwargs or {}))
        except TypeError:
            spec_val = spec_val()
    if spec_val is None:
        log.warning("Launcher: %r defines no PHASES — running without phases.", ref)
    return spec_val


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
    phases: Any = None,
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
    # PHASES MAY COME FROM launch.yaml OR FROM setup(). The launch.yaml
    # form matches how ``actions:`` and ``checks:`` are already named —
    # a path to a project file — which keeps a long generated phase list
    # out of actions.py:
    #
    #     launch.yaml:   phases: phases.py
    #     phases.py:     PHASES = [...]        # or def phases(**kwargs)
    #
    # setup() returning "phases" still works and wins if both are given,
    # because setup() can see the kwargs and a static file cannot.
    # Passing nothing keeps the old behaviour exactly.
    phases_spec = spec.get("phases") or _load_phases(phases, kwargs)
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
    # Resolve ``slice_dim`` — the ``objects`` key the planner windows
    # along. When unset (the default), auto-infer it: a single-dimension
    # protocol (one ``objects`` key, which is ~every lab protocol) slices
    # along that key, so windowed planning "just works" regardless of
    # what the key is named. Only multi-dimension protocols need to name
    # the dim explicitly (via ``launch.yaml: slice_dim``). Previously
    # this defaulted to the literal ``"tube"``, so any project whose key
    # wasn't named "tube" silently lost windowing and planned the whole
    # batch at once.
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

    # Two views of the slicing dim:
    #   * ``all_items``  — full operator-supplied set (stays constant)
    #   * ``ctx.meta["objects"][slice_dim]`` — current window the
    #     planner thinks about. Re-assigned each rebuild.
    all_items: list = list(objects.get(slice_dim, [])) if slice_dim else []

    # ── Phases ────────────────────────────────────────────────────────
    #
    # An PHASE is a goal the whole batch reaches before any item moves
    # past it: "every tube barcoded", then "every tube weighed", and so
    # on. It exists because BOTH halves of the pipeline grow with how
    # much work is in flight at once, and item-windowing only bounds one
    # of them. Measured on bd: the full protocol at 12 items is a
    # 230-action plan and CP-SAT returns UNKNOWN on it, while one stage
    # over the same 12 items is ~60 actions and schedules fine. Cutting
    # the HORIZON is what item windows cannot do — a window of 4 still
    # carries each of those 4 all the way to the end.
    #
    # AUTHORED AS A GOAL, NOT AS A PREDICATE NAME. A bare name is sugar
    # for "this fact holds for every item in scope"; a callable
    # ``(state, items) -> bool`` says anything. That is deliberate: the
    # nested case (every tube in rack r, then the next rack) is the same
    # list with a different callable, not a new mechanism.
    #
    # ONE OBJECT DIM. A phase always windows the project's ``slice_dim``;
    # a rack-scoped phase still windows TUBES, just a subset, which is
    # what ``scope`` is for.
    #
    # ORDER IS DERIVED FROM ``pre``, not from list position. The launcher
    # runs the first phase that is ready and not yet reached, so a list
    # written out of dependency order still runs correctly and a phase
    # whose ``pre`` is unmet is skipped rather than selected. Validation below
    # is the guard that the declared order is achievable at all.
    def _always_ready(_st, _items):
        """A phase with no ``pre`` is ready as soon as its turn comes."""
        return True

    def _normalise_phases(spec_val):
        """Normalise to ``[(name, scope, pre, reached, plan_window)]``.

        SCOPE AND GOAL ARE SEPARATE, and that separation is what makes
        hierarchy expressible. Scope answers "which items does this
        phase concern"; goal answers "have they reached it". Fusing them
        into one ``(state, items) -> bool`` looks tidier and breaks the
        moment a phase covers a SUBSET: the window picker has to ask
        "is THIS item past the phase", and a rack-scoped predicate given
        one tube ignores the tube and answers about the whole rack, so
        every item is filtered or none is.

        With them split, a rack/tube hierarchy needs no nesting
        machinery at all — the project emits a FLAT list of scoped
        phases and the launcher walks it:

            "phases": [
                {"name": f"rack{r}_{stage}",
                 "scope": (lambda st, _r=r: tubes_in(_r)),
                 "goal":  stage}          # or a Phase subclass
                for r in racks for stage in ("returned", "ejected")
            ]

        Levels are just more entries. A third level is more entries
        again. Nothing in the launcher counts levels.

        THERE IS NO PER-PHASE OBJECT DIM. A phase always windows the
        project's one ``slice_dim``; a rack-scoped phase still windows
        TUBES, just a subset of them, which is what ``scope`` is for. A
        second dim would only be needed by a project whose actions span
        two object kinds — LoadRack(rack) alongside Dispense(tube) — and
        none does. A ``{dim: [...]}`` form used to be accepted here and
        silently dropped every phase keyed to any other dim, which
        promised multi-dim support that did not exist.
        """
        if not spec_val:
            return []
        if isinstance(spec_val, dict):
            spec_val = [spec_val]           # a single dict entry
        from workspace.bt.phase import Phase as _Phase
        out = []
        for entry in spec_val:
            if isinstance(entry, _Phase):
                # An authored Phase: scope / pre / eff, same shape as an
                # Action one level up. ``pre`` folds into the goal —
                # a phase that may not open yet is simply not reached.
                out.append((
                    entry.name,
                    (lambda st, _e=entry: _e.scope(st, all_items)),
                    (lambda st, items, _e=entry: bool(_e.pre(st, items))),
                    (lambda st, items, _e=entry: _e.reached(st, items)),
                    getattr(entry, "plan_window", None),
                ))
                continue
            if isinstance(entry, dict):
                name = str(entry.get("name") or entry.get("goal") or "phase")
                scope = entry.get("scope")
                goal = entry.get("goal")
            elif callable(entry):
                name, scope, goal = getattr(entry, "__name__", "phase"), None, entry
            else:
                name, scope, goal = str(entry), None, str(entry)
            # scope: None means "every item still live"
            scope_fn = scope if callable(scope) else (lambda st, _s=scope: _s)
            if scope is None:
                scope_fn = None
            # goal: a name is sugar for "that fact holds for every item
            # in scope"; a callable gets (state, items).
            if callable(goal):
                goal_fn = goal
            else:
                goal_fn = (lambda st, items, _n=str(goal):
                           all((_n, it) in st for it in items))
            out.append((name, scope_fn, _always_ready, goal_fn, None))
        return out

    # The monotonic set is invariant for a given action registry, but it
    # costs a probe of every action against every binding (~1.2 ms at 28
    # items). Three sites want it and two of those only want its LENGTH
    # for a log line, so compute it lazily and at most once — paying it
    # three times to print a number is the wrong trade.
    _mono_memo = []
    def _mono():
        if not _mono_memo:
            _mono_memo.append(registry.monotonic_predicates(ctx))
        return _mono_memo[0]

    phases = _normalise_phases(phases_spec)

    if phases:
        # A NON-MONOTONIC PHASE CANNOT BE A PHASE BOUNDARY. If some
        # action removes the fact again, "everyone has reached it" is
        # not a line the batch crosses once — it can un-cross, the
        # phase never closes, and the run stalls with no error. This is
        # the classic Sussman trap (achieving goal B undoes goal A);
        # catching it at launch beats discovering it mid-batch.
        mono = _mono()
        for nm, _scope, _pre, _fn, _w in phases:
            if nm not in mono and nm != "phase":
                log.warning(
                    "Launcher: phase %r is not monotonic — some action "
                    "removes it, so the phase may never close. Phases "
                    "must be facts that only ever get added.", nm,
                )
        log.info("Launcher: %d phase(s): %s",
                 len(phases), " -> ".join(nm for nm, _s, _p, _g, _w in phases))

    def _current_phase(state):
        """First unmet phase as ``(name, items_in_scope, goal_fn)``.

        Scope is resolved here, once, so both the planning goal and the
        window picker see the same item set.
        """
        live = [it for it in all_items if not item_done(state, it)] \
            if item_done is not None else list(all_items)
        blocked = []
        for nm, scope_fn, pre_fn, reached_fn, _win in phases:
            items = list(scope_fn(state)) if scope_fn is not None else (live or all_items)
            if not items:
                continue                     # scope empty — phase vacuous
            if reached_fn(state, items):
                continue                     # already crossed
            if not pre_fn(state, items):
                blocked.append(nm)
                continue                     # not ready — try the next
            return nm, items, reached_fn, _win
        if blocked:
            # Every outstanding phase is waiting on something no other
            # phase will provide. Say so loudly: silently returning None
            # here would fall through to the global goal and plan the
            # whole protocol, which is the failure this exists to avoid.
            log.warning("Launcher: phases %s are all blocked by their pre() "
                        "— check their order and conditions.", blocked)
        return None

    # Phases make windowing worthwhile even when the window covers the
    # whole batch, so the "batch bigger than the window" test no longer
    # decides it on its own.
    slicing_active = (
        item_done is not None
        and (len(all_items) > int(plan_window) or bool(phases))
    )

    def _pick_window(state) -> list:
        """Next ``plan_window`` items still outstanding, in order.

        "Outstanding" means not finished; while an phase is open it
        also means not yet past THAT phase, so a tube that already
        cleared the current phase drops out of the window and the
        planner stops reasoning about it.
        """
        cur = _current_phase(state) if phases else None
        scope = set(cur[1]) if cur is not None else None
        width = int(cur[3]) if (cur is not None and cur[3]) else int(plan_window)
        out: list = []
        for it in all_items:
            if item_done(state, it):
                continue
            if scope is not None:
                if it not in scope:
                    continue        # this phase does not concern it
                if cur[2](state, [it]):
                    continue        # already past this phase
            out.append(it)
            if len(out) >= width:
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
        # PHASES TAKE PRECEDENCE OVER THE WINDOW TEST. While an phase
        # is open the planner's target is that phase, not "these items
        # are finished" — that is the whole point: it stops the plan at
        # the phase boundary instead of carrying every item to the end.
        # Only when the last phase has closed do we fall through to the
        # window/tail logic below.
        if phases:
            cur = _current_phase(state)
            if cur is not None:
                # NOT ``goal_fn`` — binding that name here would make
                # it local to this whole function and turn every other
                # ``goal_fn(state)`` read below into an UnboundLocalError.
                _nm, items, phase_reached, _w = cur
                window = ctx.meta["objects"].get(slice_dim, [])
                # Target the phase over the items actually in the
                # window, so a window smaller than the scope still
                # terminates.
                scoped = [it for it in (window or all_items) if it in items]
                return phase_reached(state, scoped or items)
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
            # Stamp the phase this window belongs to, so the schedule
            # event can carry it and the Gantt can band consecutive
            # slices under their phase name. Resolved here rather than
            # in build_tree because _observe already did the work.
            _cur = _current_phase(state) if phases else None
            c.meta["current_phase"] = _cur[0] if _cur else None
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
                len(_mono()),
            )
        else:
            goal_facts = registry.derive_goal_facts(ctx)
            log.info(
                "Launcher: auto-derived %d goal_facts across %d monotonic predicates",
                len(goal_facts), len(_mono()),
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

    def _capacity(plan):
        facts = ctx.state.get("facts", frozenset())
        initial = facts if isinstance(facts, frozenset) else frozenset(facts)
        return derive_capacity_spans(plan, registry, initial_state=initial, ctx=ctx)

    use_cpsat = (str(scheduler).lower() == "cpsat")
    build_schedule = make_schedule_builder(
        meta, use_cpsat=use_cpsat, precedence_fn=_precedence, capacity_fn=_capacity,
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
                    # Which phase this slice belongs to, or None when the
                    # project declares no phases. The Gantt groups
                    # consecutive slices sharing a phase under one band.
                    "phase": ctx.meta.get("current_phase"),
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
            # max_attempts=1 = NO implicit retry — a failed action goes
            # straight to replan. Retry only where a project explicitly
            # opts in with its own with_retry wrapper.
            return with_retry(leaf_factory(action_name, item_index), max_attempts=1)
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
    return status
