"""Declarative DSL for BT projects — one block per action, everything in one place.

The pre-DSL pace_bt had four scattered declarations per action:

  domain.py    — ActionTemplate (preconditions + effects for PDDL)
  actions.py   — RecipeAction subclass with execute() + apply_effects()
  schedule.py  — ActionMeta (duration + resource)
  conditions.py — PredicateCondition subclass per testable fact

The DSL collapses them. One :class:`Action` subclass per action declares
preconditions, effects, duration, resource, and the recipe call —
auto-registers itself for PDDL planning, scheduling, BT leaf creation,
and apply_effects derivation. Conditions for any predicate are
auto-generated when you write ``has_cap.condition(tube)``.

Authoring style:

    from workspace.bt.dsl import Action, predicate

    # Declare predicates once. Each gives you fact construction +
    # auto-generated BT conditions.
    in_source  = predicate("in_source")
    has_cap    = predicate("has_cap")
    in_working = predicate("in_working")

    class Decap(Action):
        '''Remove cap, move tube into the working rack.'''
        params   = ["tube"]
        duration = 10
        resource = "robot"

        def pre(self, tube):
            return in_source(tube) & has_cap(tube)

        def eff(self, tube):
            return -has_cap(tube), -in_source(tube), +in_working(tube)

        def execute(self, tube):
            return self.ctx.recipes["decapper"].decap(tube)

That's it — no domain.py, no separate condition class, no schedule
META dict, no _LEAVES dict. The framework reads the class and wires
everything up.

The DSL is a thin layer over the existing PDDL + scheduler + BT
machinery; nothing here is *required* — projects with unusual needs
can drop down to the raw building blocks (``ActionTemplate``,
``ActionMeta``, ``RecipeAction``) any time. Use the DSL for the 90%
case; escape hatch for the 10%.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
    Type,
)

import py_trees

from workspace.bt.behaviours import (
    PredicateCondition,
    RecipeAction,
    WorkspaceContext,
)
from workspace.planner.pddl import ActionTemplate, State
from workspace.planner.plan_scheduler import ActionMeta


log = logging.getLogger(__name__)


# ── Internal: facts and predicates ─────────────────────────────────────────


class Fact:
    """A polarity-tagged fact tuple — the unit of state and effects.

    ``Fact("has_cap", (3,), polarity=True)`` represents "tube 3 has a cap"
    (positive) or its absence (negative). Polarity matters in effects
    (``-has_cap(tube)`` removes the fact; ``+has_cap(tube)`` adds it) but
    not in state membership tests.

    Authors never construct :class:`Fact` directly — they call
    predicates: ``has_cap(tube_3)`` returns ``Fact("has_cap", (tube_3,))``.
    """

    __slots__ = ("pred", "args", "polarity")

    def __init__(self, pred: str, args: Tuple[Any, ...], polarity: bool = True):
        self.pred = pred
        self.args = args
        self.polarity = polarity

    # Polarity operators. The unary ``+`` and ``-`` are sugar for "add"
    # and "remove" when used inside an ``eff()`` return value.
    def __pos__(self) -> "Fact":
        return Fact(self.pred, self.args, polarity=True)

    def __neg__(self) -> "Fact":
        return Fact(self.pred, self.args, polarity=False)

    # Logical combinators for use in ``pre()`` expressions. Promoting a
    # Fact to an Expr via these ops lets authors write
    # ``a & b & ~c`` naturally without thinking about wrapping.
    def __invert__(self) -> "Expr":
        return Expr.not_(_FactExpr(self))

    def __and__(self, other) -> "Expr":
        return Expr.and_(_FactExpr(self), _ensure_expr(other))

    def __rand__(self, other) -> "Expr":
        return Expr.and_(_ensure_expr(other), _FactExpr(self))

    def __or__(self, other) -> "Expr":
        return Expr.or_(_FactExpr(self), _ensure_expr(other))

    def __ror__(self, other) -> "Expr":
        return Expr.or_(_ensure_expr(other), _FactExpr(self))

    def as_tuple(self) -> Tuple[Any, ...]:
        """The (predicate_name, *args) tuple used in PDDL state sets."""
        return (self.pred,) + self.args

    def __repr__(self) -> str:
        sign = "" if self.polarity else "¬"
        return f"{sign}{self.pred}{self.args!r}"


# ── Expressions for preconditions ──────────────────────────────────────────


class Expr:
    """Boolean expression over facts. Built via ``&``, ``|``, ``~`` on facts.

    Authors return :class:`Expr` (or a bare :class:`Fact`) from
    :meth:`Action.pre`. The framework evaluates the expression against
    the current state to decide if the action is applicable.

    Construct via the class-method factories so the constructor stays
    private — users compose expressions through fact arithmetic, never
    by hand.
    """

    __slots__ = ("op", "args")

    def __init__(self, op: str, args: Sequence["Expr"]):
        self.op = op  # "and" | "or" | "not" | "fact" | "true" | "false"
        self.args = tuple(args)

    # ── Factories ───────────────────────────────────────────────────────
    @classmethod
    def and_(cls, a: "Expr", b: "Expr") -> "Expr":
        return cls("and", (a, b))

    @classmethod
    def or_(cls, a: "Expr", b: "Expr") -> "Expr":
        return cls("or", (a, b))

    @classmethod
    def not_(cls, a: "Expr") -> "Expr":
        return cls("not", (a,))

    @classmethod
    def true(cls) -> "Expr":
        return cls("true", ())

    @classmethod
    def false(cls) -> "Expr":
        return cls("false", ())

    # ── Combinators on Expr instances ───────────────────────────────────
    def __and__(self, other) -> "Expr":
        return Expr.and_(self, _ensure_expr(other))

    def __or__(self, other) -> "Expr":
        return Expr.or_(self, _ensure_expr(other))

    def __invert__(self) -> "Expr":
        return Expr.not_(self)

    # ── Evaluation against a frozenset of fact tuples ───────────────────
    def evaluate(self, state: FrozenSet[Tuple[Any, ...]]) -> bool:
        if self.op == "and":
            return all(a.evaluate(state) for a in self.args)
        if self.op == "or":
            return any(a.evaluate(state) for a in self.args)
        if self.op == "not":
            return not self.args[0].evaluate(state)
        if self.op == "true":
            return True
        if self.op == "false":
            return False
        # "fact" — handled by _FactExpr subclass.
        raise AssertionError(f"unknown expr op: {self.op!r}")


class _FactExpr(Expr):
    """Expr wrapping a single Fact — evaluates to fact-in-state."""

    __slots__ = ("fact",)

    def __init__(self, fact: Fact):
        super().__init__("fact", ())
        self.fact = fact

    def evaluate(self, state: FrozenSet[Tuple[Any, ...]]) -> bool:
        return self.fact.as_tuple() in state


def _ensure_expr(x: Any) -> Expr:
    """Coerce a Fact or Expr (or True/False) into an Expr."""
    if isinstance(x, Expr):
        return x
    if isinstance(x, Fact):
        return _FactExpr(x)
    if isinstance(x, bool):
        return Expr.true() if x else Expr.false()
    raise TypeError(
        f"expected Fact or Expr in pre(), got {type(x).__name__}: {x!r}"
    )


# ── Predicate — factory for facts and conditions ───────────────────────────


class Predicate:
    """A named relation. Apply to args to get a :class:`Fact`.

    Also serves as the canonical home for the predicate's auto-generated
    BT condition leaves: ``has_cap.condition(tube_3)`` returns a
    :class:`PredicateCondition` that checks ``has_cap(tube_3)`` in the
    workspace context.

    Predicate identity is by name. Two ``predicate("has_cap")`` calls
    return distinct objects but the facts they produce are
    interchangeable in state sets (it's the tuple that matters).
    """

    __slots__ = ("name",)

    def __init__(self, name: str):
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError(f"predicate name must be a Python identifier, got {name!r}")
        self.name = name

    def __call__(self, *args: Any) -> Fact:
        return Fact(self.name, tuple(args), polarity=True)

    def condition(self, *args: Any) -> py_trees.behaviour.Behaviour:
        """Build a BT ``PredicateCondition`` checking this predicate.

        The returned behaviour is *not* bound to a context yet — you
        wrap it with ``bind(ctx)`` at tree-construction time, or pass it
        through a builder helper that handles ctx threading. (For
        simplicity, projects usually pass ctx at construction; see
        :func:`make_predicate_condition`.)
        """
        return make_predicate_condition(self, args)

    def __repr__(self) -> str:
        return f"predicate({self.name!r})"


def predicate(name: str) -> Predicate:
    """Public helper — same as ``Predicate(name)`` but reads nicer in
    project authoring files."""
    return Predicate(name)


# ── Condition leaves auto-generated from predicates ────────────────────────


class _AutoCondition(PredicateCondition):
    """A :class:`PredicateCondition` derived from a predicate + bound args.

    Created via :meth:`Predicate.condition` or :func:`make_predicate_condition`.
    Reads the workspace state and tests fact-in-set.
    """

    def __init__(
        self,
        pred: Predicate,
        args: Tuple[Any, ...],
        ctx: Optional[WorkspaceContext] = None,
    ):
        name = f"is_{pred.name}({', '.join(map(repr, args))})"
        # ctx may be None when the leaf is constructed before context
        # is bound (e.g. when assembled into a tree by from_schedule).
        # The framework binds ctx at tick time via ``set_ctx``.
        super().__init__(name=name, ctx=ctx)  # type: ignore[arg-type]
        self.pred = pred
        self.args = args

    def check(self) -> bool:
        if self.ctx is None:
            return False
        facts = _facts_from_state(self.ctx.state)
        return (self.pred.name,) + self.args in facts


def make_predicate_condition(
    pred: Predicate,
    args: Sequence[Any],
    ctx: Optional[WorkspaceContext] = None,
) -> _AutoCondition:
    return _AutoCondition(pred=pred, args=tuple(args), ctx=ctx)


# ── State helpers ──────────────────────────────────────────────────────────


def _facts_from_state(state: Dict[str, Any]) -> set:
    """The framework keeps the live world-state facts in
    ``ctx.state["facts"]``. This helper lazy-creates the set on first
    read so callers can be agnostic about init order."""
    facts = state.get("facts")
    if facts is None:
        facts = set()
        state["facts"] = facts
    return facts


def state_to_frozen(state: Dict[str, Any]) -> FrozenSet[Tuple[Any, ...]]:
    """Take the mutable ctx.state and snapshot the facts as a frozenset.

    The PDDL planner only consumes frozensets of fact tuples — the
    framework calls this at the start of each plan request to convert
    the live, mutable state into a planner-friendly snapshot.
    """
    return frozenset(_facts_from_state(state))


# ── Framework conventions ──────────────────────────────────────────────────


# The resource name that holds tools. Actions whose resource doesn't
# include this string are considered "doesn't use the robot" — so
# their _ensure_tool is a no-op and the scheduler doesn't gate their
# start on a tool-swap completing. Hardcoded for now; could become
# a launcher knob if projects need to override.
_TOOL_RESOURCE = "robot"


# ── Shared lock on ctx.meta — for thread safety in Parallel composites ────


def _ctx_lock(meta: Dict[str, Any]) -> threading.RLock:
    """Return (lazily creating) the ctx-wide lock stored in meta.

    Used by ``_DSLActionLeaf._ensure_tool`` so concurrent leaves under
    a Parallel composite don't race on the ``current_tool`` field.
    Reentrant so nested helpers can also take it safely.
    """
    lock = meta.get("_lock")
    if lock is None:
        lock = threading.RLock()
        meta["_lock"] = lock
    return lock


# ── Precedence — for parallel scheduling ───────────────────────────────────


def _extract_pre_facts(
    expr: Any,
) -> Tuple[set, set]:
    """Walk a pre() Expr and split into (positives, negatives).

    Supports conjunction (& / and) and negation (~ / not) — the shape
    every Action in the codebase uses. OR / true / false are ignored
    (treated as no dependency) — preserves correctness conservatively.
    """
    pos: set = set()
    neg: set = set()

    def walk(e: Any) -> None:
        if isinstance(e, Fact):
            pos.add(e.as_tuple())
            return
        if isinstance(e, bool):
            return
        # _FactExpr stores a Fact
        if isinstance(e, _FactExpr):
            pos.add(e.fact.as_tuple())
            return
        if not isinstance(e, Expr):
            return
        if e.op == "and":
            for a in e.args:
                walk(a)
        elif e.op == "not":
            child = e.args[0]
            if isinstance(child, _FactExpr):
                neg.add(child.fact.as_tuple())
            elif isinstance(child, Fact):
                neg.add(child.as_tuple())
            # nested NOT-AND etc. are uncommon in lab protocols; ignore.

    walk(expr)
    return pos, neg


def _extract_eff_facts(branch: Any) -> Tuple[set, set]:
    """From an eff() branch's facts, split into (added, removed)."""
    added: set = set()
    removed: set = set()
    if branch is None:
        return added, removed
    if isinstance(branch, Fact):
        branch = (branch,)
    for f in branch:
        if not isinstance(f, Fact):
            continue
        if f.polarity:
            added.add(f.as_tuple())
        else:
            removed.add(f.as_tuple())
    return added, removed


def build_precedence(
    plan: Sequence[Any],          # list of workspace.planner.pddl.Action
    registry: "ActionRegistry",
) -> List[set]:
    """Compute per-action causal predecessor sets from the plan.

    For each action at index ``i``, returns a set of earlier indices
    ``{j: j < i and action[i].pre intersects action[j].eff (default
    branch)}``. The scheduler uses this to allow actions with no
    causal dependency to overlap on different resources.

    Positive pre-facts (the action needs X to be true): predecessor =
    the most recent earlier action that ADDED X (or none, if X was
    true at t=0).

    Negative pre-facts (the action needs X to be false): predecessor =
    the most recent earlier action that REMOVED X.
    """
    # Pre-compute each action's positive/negative pre facts and added/
    # removed eff facts (using the default eff branch — first dict key).
    metas: List[Dict[str, set]] = []
    for action in plan:
        cls = registry.get(action.name)
        if cls is None:
            metas.append({"pre_pos": set(), "pre_neg": set(),
                          "added": set(), "removed": set()})
            continue
        instance = cls()
        params = tuple(action.params)
        # pre
        pre_expr = instance.pre(*params)
        pre_pos, pre_neg = _extract_pre_facts(pre_expr)
        # eff — default branch (first dict key)
        eff = _normalise_eff(instance.eff(*params), cls.__name__)
        first_branch = eff[next(iter(eff))]
        added, removed = _extract_eff_facts(first_branch)
        metas.append({"pre_pos": pre_pos, "pre_neg": pre_neg,
                      "added": added, "removed": removed})

    predecessors: List[set] = []
    for i, m in enumerate(metas):
        preds: set = set()
        # Positive pre — last earlier action that added the fact.
        # Stop at first remover (the fact was just removed; we're a bug).
        for f in m["pre_pos"]:
            for j in range(i - 1, -1, -1):
                if f in metas[j]["added"]:
                    preds.add(j); break
                if f in metas[j]["removed"]:
                    break
        # Negative pre — last earlier action that removed the fact.
        for f in m["pre_neg"]:
            for j in range(i - 1, -1, -1):
                if f in metas[j]["removed"]:
                    preds.add(j); break
                if f in metas[j]["added"]:
                    break
        predecessors.append(preds)
    return predecessors


def _normalise_eff(effs: Any, action_name: str) -> Dict[str, Tuple[Fact, ...]]:
    """Validate and normalise an ``Action.eff()`` return.

    Every ``eff()`` must return a **dict** mapping branch names to the
    facts that branch produces. A single-branch deterministic action
    looks like ``{"decapped": (-has_cap(t), +in_working(t))}``; a
    sensing action looks like ``{"light": ..., "heavy": ...}``.

    Branch values may be:
      * a single :class:`Fact`        → wrapped as ``(fact,)``
      * a tuple / list of Facts       → coerced to tuple
      * ``None`` (no facts in branch) → ``()``

    Returns a dict of ``{branch_name: tuple_of_facts}``. Raises
    ``TypeError`` if the outer shape isn't a dict — the framework
    rejects the legacy single-fact / tuple shortcut to keep one way
    of writing ``eff()``.
    """
    if not isinstance(effs, dict):
        raise TypeError(
            f"{action_name}.eff() must return a dict of "
            f"{{branch_name: facts}} — got {type(effs).__name__}. "
            f"For a deterministic action, use a single-key dict like "
            f'{{"done": (+fact_a, -fact_b)}}.'
        )
    if not effs:
        raise ValueError(
            f"{action_name}.eff() returned an empty dict — needs at "
            f"least one branch (use {{\"none\": None}} for a no-op effect)."
        )
    out: Dict[str, Tuple[Fact, ...]] = {}
    for name, branch in effs.items():
        if branch is None:
            out[name] = ()
        elif isinstance(branch, Fact):
            out[name] = (branch,)
        else:
            out[name] = tuple(branch)
    return out


def _default_branch(effs: Dict[str, Any]) -> str:
    """The first key of the eff dict — the planner's default projection.

    Python 3.7+ guarantees dict insertion order, so "first" is whatever
    the author wrote first.
    """
    return next(iter(effs))


# ── Action — the unified declaration ───────────────────────────────────────


# Snake-case the class name (Decap → "decap", DispenseHeavy → "dispense_heavy").
_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _to_snake(name: str) -> str:
    return _CAMEL_RE.sub("_", name).lower()


class Action:
    """Base class for declarative actions.

    Subclass once per action. The framework auto-registers the subclass
    into :class:`ActionRegistry`, from which it derives:

      * an :class:`ActionTemplate` for the PDDL planner (preconditions
        + effects),
      * an :class:`ActionMeta` for the scheduler (duration + resource),
      * a BT leaf factory (``execute`` body wrapped in
        :class:`RecipeAction` so threading + cancellation come for free),
      * tool-swap, pre/post-check, and trigger semantics matching the
        project-guide vocabulary.

    Class attributes (the **pace_or-style vocabulary**, see project-guide.md):

        params:             Parameter names — list of strings. Most lab
                            actions have one param: ``["tube"]``.
        duration:           Estimated seconds. Used by the scheduler.
                            Defaults to 1.
        tool:               Tool the robot holds during this action.
                            Strings like ``"gripper"`` auto-swap before
                            the action runs. ``None`` explicitly means
                            "release current tool"; **unset** (sentinel)
                            means "keep whatever's currently held".
                            One tool per action is enforced — keep
                            actions atomic.
        tool_swap_duration: Seconds inserted before this action when the
                            previous same-resource action held a
                            different ``tool``. Per-action so swap
                            times can differ (gripper vs. needle racks
                            are at different distances). Default 10.
        resource:           Hardware lock(s) this action claims for
                            scheduling. Three shapes:
                              * ``None``               — claims nothing,
                                                         unlimited parallel.
                              * ``"robot"`` (string)   — single lock.
                              * ``["robot","scale"]``  — multiple locks
                                claimed simultaneously (e.g. arm holds
                                a tube on the scale while it reads).
                            Two actions sharing ANY lock cannot overlap.
                            An autonomous peripheral (shaker running on
                            its own) gets its own lock (``"shaker_1"``)
                            and the robot is free.
        pre_check:          Name of a check method (in ``Checks`` class)
                            to run **before** the tool swap. On False
                            the action is skipped entirely (no tool
                            swap happens). Can be a list of names.
        post_check:         Name of a check method to run after the
                            action completes. Same shape as pre_check.
        trigger:            ``"end"`` marks this action as the cleanup
                            invoked when the operator clicks End. Not
                            scheduled into the normal plan. The action's
                            ``tool:`` field is the authoritative final
                            tool state. ``duration`` is ignored on
                            triggers.

    Methods to override:
        pre(self, *params)     -> Expr or Fact or bool
        eff(self, *params)     -> tuple of Facts (use +/- for add/remove)
        execute(self, *params) -> bool | None  (raise to signal failure)

    Sim vs. real mode is a **framework-level** decision driven by
    ``core._simulation_mode``. Action subclasses don't have to think
    about it — write ``execute`` as the real-hardware logic; in sim
    mode the framework's leaf wrapper sleeps for ``duration`` and
    skips ``execute``.
    """

    # Sentinel that distinguishes "unset" from "set to None" for tool.
    _TOOL_UNSET = object()

    # ── Required class attributes (override in subclass) ────────────────
    params:              List[str] = []
    duration:            int = 1
    resource:            Any = None    # None | "name" | ["a", "b"]
    tool:                Any = _TOOL_UNSET   # unset | None | "name"
    tool_swap_duration:  int = 10      # gap before this action when tool changes
    pre_check:           Any = None    # str | list[str] | None
    post_check:          Any = None
    trigger:             Optional[str] = None  # "end" or None

    # ── Auto-registration machinery ─────────────────────────────────────
    # Subclasses register themselves into the active ActionRegistry on
    # class creation. The "active" registry is process-global by default
    # but projects can use isolated registries (see ActionRegistry.use()).
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Don't register intermediate bases (names starting with _).
        if cls.__name__.startswith("_"):
            return
        name = _to_snake(cls.__name__)
        ActionRegistry.current().register(name, cls)

    # ── Authors override these ──────────────────────────────────────────
    def pre(self, *args: Any) -> Any:  # pragma: no cover - abstract-ish
        """Precondition expression. Default = always applicable.

        Returning a bare :class:`Fact` is shorthand for "this fact is
        true in state"; otherwise return an :class:`Expr`.
        """
        return True

    def eff(self, *args: Any) -> Dict[str, Any]:
        """Effects of the action — always a dict of named branches.

        Every action returns a ``dict`` keyed by **branch name**. The
        value is the facts that branch produces:

          * single fact:                ``{"decapped": +in_working(t)}``
          * multiple facts (tuple):     ``{"decapped": (-has_cap(t), +in_working(t))}``
          * no change in this branch:   ``{"observed": None}``

        Deterministic actions have **one** key (name it after the
        outcome state — past tense reads naturally):

            def eff(self, tube):
                return {"decapped": (-has_cap(tube), +in_working(tube))}

        Non-deterministic / sensing actions have **multiple** keys.
        ``execute()`` returns the chosen branch name:

            def eff(self, tube):
                return {
                    "light": +weighed(tube),
                    "heavy": (+weighed(tube), +weight_heavy(tube)),
                }
            def execute(self, tube):
                w = self.ctx.recipes["scale"].weight()
                return "heavy" if w > HEAVY_THRESHOLD else "light"

        The **first key** is the planner's default projection. When
        ``execute()`` returns a non-default key at runtime, the
        framework applies that branch and raises ``ReplanRequested``
        so downstream actions re-evaluate against observed state.

        Use ``+predicate(args)`` to ADD a fact and ``-predicate(args)``
        to REMOVE one.

        This mirrors PDDL's ``oneof`` non-deterministic effects
        (PPDDL, ~2002), with named branches instead of anonymous ones
        for readability.
        """
        return {"none": None}

    def execute(self, *args: Any) -> str:  # pragma: no cover - abstract
        """Run the recipe. **Must return the branch name** — a string
        that matches one of the keys in ``eff()``.

        For a deterministic action with a single-branch eff, just
        return that single key:

            def eff(self, tube):
                return {"decapped": (-has_cap(tube), +in_working(tube))}
            def execute(self, tube):
                ... do the work ...
                return "decapped"

        For a sensing action, return whichever key reflects what the
        hardware observed:

            def execute(self, tube):
                w = self.ctx.recipes["scale"].weight()
                return "heavy" if w > HEAVY_THRESHOLD else "light"

        Return ``False`` to signal failure (no effects applied; the BT
        may retry / replan). Any other return type is rejected with a
        warning and treated as failure — there's exactly one way to
        pick a branch, matching ``eff()``'s "always a dict" rule.

        Simulation vs real hardware is the **workspace SDK's** job —
        ``core.robot_api`` is ``SimulationAPI`` in sim mode and
        ``Dorna()`` in real mode. Recipes called from ``execute`` go
        through that abstraction transparently; the framework doesn't
        intervene.
        """
        # Default — the base class's default eff() returns
        # {"none": None}; matching that for the no-op case.
        return "none"

    def param_iter(self, state: State) -> Iterable[Tuple[Any, ...]]:
        """Yield candidate parameter tuples for PDDL instantiation.

        Default: read ``ctx.meta["objects"][param_name]`` for each
        declared param and yield the Cartesian product. Most projects
        just set ``ctx.meta["objects"]["tube"] = range(batch_size)``
        and never override this. Override for filtering or per-state
        dynamic enumeration.
        """
        objects = self._ctx_objects()
        if not self.params:
            yield ()
            return
        pools = [list(objects.get(p, [])) for p in self.params]
        # Cartesian product.
        from itertools import product
        for combo in product(*pools):
            yield combo

    # ── Internal — accessed by ActionRegistry / leaf factory ────────────
    def _ctx_objects(self) -> Dict[str, Iterable[Any]]:
        # In a tree-build-time call, ctx is set on the instance. In a
        # planner enumeration call, the registry threads ctx through.
        ctx = getattr(self, "ctx", None)
        if ctx is None:
            return {}
        return (ctx.meta or {}).get("objects", {})


# ── Action registry — the source of truth for one project's actions ────────


class ActionRegistry:
    """Tracks every :class:`Action` subclass declared in a project.

    Usage:

        # In project authoring code:
        in_source = predicate("in_source")
        class Decap(Action):
            ...

        # Then in workflow.py:
        registry = ActionRegistry.current()
        templates = registry.to_templates(ctx)
        meta      = registry.to_meta()
        leaf      = registry.leaf_factory(ctx)

    By default a single global registry collects all declarations.
    Tests or projects that want isolation can push a fresh one onto the
    stack via :meth:`use` (context manager).
    """

    _stack: List["ActionRegistry"] = []

    def __init__(self):
        self._actions: Dict[str, Type[Action]] = {}

    # ── Class-method API ───────────────────────────────────────────────
    @classmethod
    def current(cls) -> "ActionRegistry":
        if not cls._stack:
            cls._stack.append(ActionRegistry())
        return cls._stack[-1]

    def use(self) -> "_RegistryCtx":
        """Push this registry as ``current`` for the duration of a
        with-block. Useful for isolating tests."""
        return _RegistryCtx(self)

    # ── Registration & lookup ──────────────────────────────────────────
    def register(self, name: str, cls: Type[Action]) -> None:
        if name in self._actions and self._actions[name] is not cls:
            log.warning(
                "ActionRegistry: re-registering %r (was %s, now %s)",
                name, self._actions[name].__name__, cls.__name__,
            )
        self._actions[name] = cls

    def get(self, name: str) -> Optional[Type[Action]]:
        return self._actions.get(name)

    def names(self) -> List[str]:
        return list(self._actions.keys())

    # ── Output: PDDL templates ─────────────────────────────────────────
    def to_templates(self, ctx: WorkspaceContext) -> List[ActionTemplate]:
        """Build a list of :class:`ActionTemplate` for the PDDL planner.

        Each registered action class becomes one template **unless** it
        carries ``trigger="end"`` (project-guide §9 — those are scene
        cleanup, not part of the goal-directed plan; the engine runs
        them when the operator clicks End). The instance used during
        planning carries ``ctx`` so its ``param_iter`` / ``pre`` /
        ``eff`` can consult ``ctx.meta``.
        """
        templates: List[ActionTemplate] = []
        for name, cls in self._actions.items():
            if getattr(cls, "trigger", None) == "end":
                continue
            instance = cls()
            instance.ctx = ctx  # type: ignore[attr-defined]
            templates.append(self._make_template(name, instance))
        return templates

    @staticmethod
    def _make_template(name: str, instance: Action) -> ActionTemplate:
        def param_iter_fn(state: State) -> Iterable[Tuple[Any, ...]]:
            yield from instance.param_iter(state)

        def pre_fn(state: State, params: Tuple[Any, ...]) -> bool:
            expr = instance.pre(*params)
            if isinstance(expr, bool):
                return expr
            if isinstance(expr, Fact):
                return expr.as_tuple() in state
            if isinstance(expr, Expr):
                return expr.evaluate(state)
            raise TypeError(
                f"{instance.__class__.__name__}.pre() must return Fact, "
                f"Expr, or bool — got {type(expr).__name__}"
            )

        def eff_fn(state: State, params: Tuple[Any, ...]) -> State:
            effs = _normalise_eff(instance.eff(*params), name)
            # Planner projects forward with the FIRST branch — author
            # convention is to list the optimistic / default outcome
            # first. Runtime divergence triggers a replan.
            facts = effs[_default_branch(effs)]
            s = set(state)
            for f in facts:
                if not isinstance(f, Fact):
                    raise TypeError(
                        f"{instance.__class__.__name__}.eff() must return Facts "
                        f"— got {type(f).__name__}"
                    )
                if f.polarity:
                    s.add(f.as_tuple())
                else:
                    s.discard(f.as_tuple())
            return frozenset(s)

        return ActionTemplate(
            name=name,
            param_iter=param_iter_fn,
            preconditions=pre_fn,
            effects=eff_fn,
        )

    # ── Output: scheduler meta ─────────────────────────────────────────
    def to_meta(self) -> Dict[str, ActionMeta]:
        out: Dict[str, ActionMeta] = {}
        for name, cls in self._actions.items():
            # trigger="end" actions are cleanup, not scheduled work —
            # the engine runs them outside the schedule when End is
            # requested. Keep them out of scheduler meta too.
            if getattr(cls, "trigger", None) == "end":
                continue
            # ``tool`` is a sentinel for "keep current tool" — for the
            # scheduler that means "no opinion on the held tool" which
            # we encode as None.
            tool_attr = getattr(cls, "tool", Action._TOOL_UNSET)
            tool_val = None if tool_attr is Action._TOOL_UNSET else tool_attr
            out[name] = ActionMeta(
                duration=int(cls.duration),
                resource=cls.resource,
                item_arg_index=0,  # convention: first param is the item
                tool=tool_val,
                tool_swap_duration=int(cls.tool_swap_duration),
            )
        return out

    # ── Output: leaf factory for from_schedule ─────────────────────────
    def leaf_factory(
        self,
        ctx: WorkspaceContext,
    ) -> Callable[[str, int], py_trees.behaviour.Behaviour]:
        """Return a callable suitable for ``workspace.bt.from_schedule``.

        Each scheduled task becomes a :class:`_DSLActionLeaf` that runs
        the Action subclass's ``execute(...)`` (or sleeps in sim) and
        applies the declared effects on success.
        """
        registry = self

        def _factory(action_name: str, item_index: int) -> py_trees.behaviour.Behaviour:
            cls = registry.get(action_name)
            if cls is None:
                raise KeyError(action_name)
            return _DSLActionLeaf(ctx=ctx, action_cls=cls, item_index=item_index)

        return _factory


class _RegistryCtx:
    """Push/pop ActionRegistry on the class-level stack."""

    def __init__(self, registry: ActionRegistry):
        self._registry = registry

    def __enter__(self) -> ActionRegistry:
        ActionRegistry._stack.append(self._registry)
        return self._registry

    def __exit__(self, *exc) -> None:
        ActionRegistry._stack.pop()


# ── BT leaf wrapping a DSL Action ──────────────────────────────────────────


class _DSLActionLeaf(RecipeAction):
    """RecipeAction created on-the-fly from a registered :class:`Action`.

    Honors the full pace_or-style vocabulary (project-guide.md §5):

      * ``pre_check`` runs before tool-swap and execute; on False, the
        whole action is skipped (success status — moving on).
      * Tool-swap (if ``Action.tool`` differs from currently-held tool)
        is performed by the framework; recipes ``rcp["<tool>"].pick()`` /
        ``rcp["<old_tool>"].place()`` from the recipes dict. Skipped
        when the tool's recipe isn't loaded (sim with empty recipes).
      * ``execute()`` runs the Action body (or sleeps for ``duration``
        in sim mode).
      * ``post_check`` runs after execute; on False the leaf reports
        FAILURE so the BT can retry/recover.
      * ``apply_effects()`` mirrors the declared ``eff()`` facts onto
        ``ctx.state["facts"]``.
    """

    def __init__(
        self,
        ctx: WorkspaceContext,
        action_cls: Type[Action],
        item_index: int,
    ):
        name = _to_snake(action_cls.__name__) + f"(t{item_index})"
        super().__init__(name=name, ctx=ctx)
        self._cls = action_cls
        self._item = item_index
        # Instantiate once; reused across run() and apply_effects().
        self._instance = action_cls()
        self._instance.ctx = ctx  # type: ignore[attr-defined]
        # For non-deterministic eff (dict): execute() returns a branch
        # name; apply_effects looks it up here. ``None`` = use default.
        self._branch_choice: Optional[str] = None

    def _params(self) -> Tuple[Any, ...]:
        # Convention: single-param actions use the item index directly.
        # Multi-param actions need a project-specific override; not
        # supported by the auto-factory today (single-item is 100% of
        # lab use we've seen).
        return (self._item,)

    # ── pre/post check helpers ─────────────────────────────────────────

    def _run_checks(self, names) -> bool:
        """Run one or more named checks from the project's Checks
        instance (``ctx.meta['checks']``).

        A check callable may return either:
          * ``bool`` — pass/fail directly.
          * ``(bool, message)`` — pace_or convention. The message is
            logged on failure and otherwise ignored.

        Returns True iff every named check passes.
        """
        if not names:
            return True
        if isinstance(names, str):
            names = [names]
        checks = (self.ctx.meta or {}).get("checks") or {}
        for cname in names:
            fn = checks.get(cname)
            if fn is None:
                self.log.warning("check %r is not registered — treating as pass", cname)
                continue
            try:
                rv = fn(self._item)
            except Exception as ex:
                self.log.warning("check %r raised: %s — treating as fail", cname, ex)
                return False
            # Accept bool or (bool, message) — pace_or returns tuples.
            if isinstance(rv, tuple) and len(rv) >= 1:
                ok = bool(rv[0])
                msg = rv[1] if len(rv) > 1 else ""
            else:
                ok = bool(rv)
                msg = ""
            if not ok:
                self.log.info("check %r failed: %s", cname, msg)
                return False
        return True

    # ── tool-swap helper ───────────────────────────────────────────────

    def _ensure_tool(self) -> None:
        """Inline tool-swap fallback for projects that don't drive
        swaps via :class:`SwapLeaf`. If the scheduler emits SwapLeaf
        nodes, ``current_tool`` is already correct by the time the
        action ticks and this method is a no-op.

        Skipped entirely for actions that don't use the robot
        resource — the tool mount is irrelevant to actions running on
        other resources (e.g. shaker_1), so they shouldn't block on
        an unrelated tool change.

        Thread-safe via the ctx-wide lock so concurrent leaves don't
        race on ``ctx.meta["current_tool"]``.
        """
        wanted = self._cls.tool
        # Sentinel: "keep whatever's currently held" — do nothing.
        if wanted is Action._TOOL_UNSET:
            return
        # Only intervene if this action actually uses the robot. The
        # tool resource is the physical thing that HOLDS tools; for
        # actions on other resources (shaker, scale, …) the current
        # tool is a future-prep concern, not a precondition.
        cls_resource = self._cls.resource
        if cls_resource is None:
            action_resources: Tuple[str, ...] = ()
        elif isinstance(cls_resource, str):
            action_resources = (cls_resource,)
        else:
            action_resources = tuple(cls_resource)
        if _TOOL_RESOURCE not in action_resources:
            return
        meta = self.ctx.meta if isinstance(self.ctx.meta, dict) else {}
        lock = _ctx_lock(meta)
        with lock:
            current = meta.get("current_tool")
            if current == wanted:
                return
            rcp = self.ctx.recipes or {}
            # Drop the old tool first (if held).
            if current is not None:
                old = rcp.get(current)
                if old is not None:
                    try:
                        old.place()
                    except Exception as ex:
                        self.log.warning("tool place(%r) raised: %s", current, ex)
            # Pick up the new tool (if any).
            if wanted is not None:
                new = rcp.get(wanted)
                if new is not None:
                    try:
                        new.pick()
                    except Exception as ex:
                        self.log.warning("tool pick(%r) raised: %s", wanted, ex)
            # Track current tool whether or not the recipe existed —
            # subsequent actions need to know what's nominally held.
            meta["current_tool"] = wanted

    # ── BT lifecycle ────────────────────────────────────────────────────

    def execute(self) -> bool:
        # Reset branch selection — leaves can be re-ticked across
        # replans, so per-tick state needs fresh init.
        self._branch_choice = None
        self._skipped = False

        log.info("BT leaf START: %s", self.name)

        # 1. pre_check — runs BEFORE tool swap. False = skip the whole
        #    action (treated as success so the BT moves on without
        #    failing the parent sequence; matches pace_or semantics).
        if not self._run_checks(self._cls.pre_check):
            log.info("BT leaf SKIP : %s (pre_check failed)", self.name)
            self._skipped = True
            return True

        # 2. Tool swap (if needed).
        self._ensure_tool()

        # 3. Run the action's execute(). The workspace SDK's core
        #    handles sim vs. real internally (SimulationAPI vs Dorna)
        #    — the framework doesn't need to second-guess.
        #    Recipes are expected to BLOCK until the hardware finishes;
        #    long async ops (shake, incubate, …) implement that wait
        #    themselves. The framework runs execute() in a worker
        #    thread (see RecipeAction.initialise) so a blocking
        #    execute() doesn't freeze the engine tick loop.
        try:
            rv = self._instance.execute(*self._params())
        except Exception as ex:
            log.warning("BT leaf RAISE: %s — %s: %s",
                        self.name, type(ex).__name__, ex)
            return False

        # execute() must return:
        #   * str          — name of the chosen eff branch (the
        #                    common case, including deterministic
        #                    actions which return their only key)
        #   * False        — action failed (no effects applied)
        #
        # Anything else (None, True, ints, ...) is rejected as a
        # programmer error — keeps the contract consistent with
        # eff()'s "always a dict" rule: one way to pick a branch.
        if rv is False:
            log.warning("BT leaf FAIL : %s (execute returned False)", self.name)
            return False
        if not isinstance(rv, str):
            effs = self._instance.eff(*self._params())
            keys = list(effs.keys()) if isinstance(effs, dict) else []
            log.warning(
                "BT leaf FAIL : %s.execute returned %r — expected one of %r",
                self._cls.__name__, rv, keys,
            )
            return False
        self._branch_choice = rv

        # 4. post_check — runs AFTER execute. False = action FAILED.
        if not self._run_checks(self._cls.post_check):
            log.warning("BT leaf FAIL : %s (post_check failed)", self.name)
            return False

        log.info("BT leaf DONE : %s (branch=%s)", self.name, rv)
        return True

    def apply_effects(self, state: Dict[str, Any]) -> None:
        # If pre_check skipped us, effects are NOT applied (the world
        # state remains as the planner left it; replanner can re-derive
        # the path from there). Matches pace_or's "skip the state" intent.
        if getattr(self, "_skipped", False):
            return

        effs = _normalise_eff(
            self._instance.eff(*self._params()), self._cls.__name__,
        )
        default = _default_branch(effs)
        chosen = self._branch_choice or default
        if chosen not in effs:
            self.log.warning(
                "%s.execute returned unknown branch %r — using default %r",
                self._cls.__name__, chosen, default,
            )
            chosen = default

        facts = _facts_from_state(state)
        for f in effs[chosen]:
            if f.polarity:
                facts.add(f.as_tuple())
            else:
                facts.discard(f.as_tuple())

        # Effects are now in state — if a non-default branch fired,
        # tell the engine to rebuild the tree so downstream actions
        # re-evaluate against the observed state.
        if chosen != default:
            # Local import to avoid circular dependency at module load.
            from workspace.bt.engine import ReplanRequested
            raise ReplanRequested(
                f"{self._cls.__name__}({self._item}): observed branch "
                f"{chosen!r} differs from planner's default {default!r}"
            )


# ── Convenience: bind ctx to auto-conditions when used in a tree ───────────
#
# Project tree code does e.g. ``has_cap.condition(tube_3)`` to get a BT
# condition. The returned _AutoCondition has ctx=None until wired into
# a tree. The framework's tree-construction path that uses these will
# call ``bind_conditions(root, ctx)`` to inject ctx where needed.


def bind_conditions(
    root: py_trees.behaviour.Behaviour,
    ctx: WorkspaceContext,
) -> None:
    """Walk a tree and inject ``ctx`` into every _AutoCondition leaf."""

    def _walk(node: py_trees.behaviour.Behaviour) -> None:
        if isinstance(node, _AutoCondition):
            node.ctx = ctx
        for child in getattr(node, "children", []) or []:
            _walk(child)

    _walk(root)


__all__ = [
    "Action",
    "ActionRegistry",
    "Expr",
    "Fact",
    "Predicate",
    "bind_conditions",
    "build_precedence",
    "make_predicate_condition",
    "predicate",
    "state_to_frozen",
]
