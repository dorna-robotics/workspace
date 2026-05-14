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
import time
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
        :class:`RecipeAction` so threading + cancellation come for free).

    Class attributes:
        params:   List of parameter names. The framework iterates the
                  Cartesian product (well, per-template ``param_iter``)
                  across all candidate values to instantiate templates.
                  For lab work, params are typically a single item
                  index like ``["tube"]``.
        duration: Wall-clock duration in seconds. Used by the
                  scheduler. Must be a positive integer.
        resource: Resource name the action exclusively claims, or
                  ``None`` for resource-free actions (rare).

    Sim vs. real mode is a **framework-level** decision driven by
    ``core._simulation_mode``. In sim mode the framework sleeps for
    ``duration`` and returns success without calling ``execute``. In
    real mode the framework calls ``execute(*params)``. Action
    subclasses don't have to think about sim — write ``execute`` as
    the real-hardware logic, the framework handles the rest.

    Methods to override:
        pre(self, *params)  -> Expr or Fact or bool
        eff(self, *params)  -> tuple of Facts (use +/- for add/remove)
        execute(self, *params) -> bool   (run the recipe; True = success)

    Methods you usually don't touch:
        param_iter(self, state) -> iterable of param tuples to try.
                  Default enumerates each param from ctx.meta["objects"]
                  (e.g. ``{"tube": range(batch_size)}``). Override for
                  domain-specific filtering.
    """

    # ── Required class attributes (override in subclass) ────────────────
    params: List[str] = []
    duration: int = 1
    resource: Optional[str] = None

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

    def eff(self, *args: Any) -> Sequence[Fact]:
        """Effects of the action.

        Return a tuple of facts. Use ``+predicate(args)`` to add and
        ``-predicate(args)`` to remove. Default = no effects.
        """
        return ()

    def execute(self, *args: Any) -> bool:  # pragma: no cover - abstract
        """Run the recipe. Return ``True`` on success.

        Don't sleep here unless sim_passthrough is False — in sim mode
        the framework's default leaf sleeps for ``duration`` seconds.
        """
        return True

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

        Each registered action class becomes one template. The instance
        used during planning carries ``ctx`` so its ``param_iter`` /
        ``pre`` / ``eff`` can consult ``ctx.meta``.
        """
        templates: List[ActionTemplate] = []
        for name, cls in self._actions.items():
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
            effs = instance.eff(*params) or ()
            s = set(state)
            for f in effs:
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
            out[name] = ActionMeta(
                duration=int(cls.duration),
                resource=cls.resource,
                item_arg_index=0,  # convention: first param is the item
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

    Glue between the declarative ``Action`` class and the BT runtime:

      * ``execute()`` calls the Action class's ``execute`` (or sleeps
        in sim mode if ``sim_passthrough`` is True).
      * ``apply_effects()`` walks the declared ``eff()`` facts and
        mutates ``ctx.state["facts"]``. No bug-prone hand-mirroring.
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

    def _params(self) -> Tuple[Any, ...]:
        # Convention: single-param actions use the item index directly.
        # Multi-param actions need a project-specific override; not
        # supported by the auto-factory today (single-item is 100% of
        # lab use we've seen).
        return (self._item,)

    def execute(self) -> bool:
        # The sim/real decision lives ONLY here. Action subclasses
        # don't carry a sim flag — they just define ``execute`` as the
        # real-hardware logic. In sim mode we sleep for the declared
        # duration and skip ``execute`` entirely.
        if getattr(self.ctx.core, "_simulation_mode", True):
            # Sleep in small slices so a runtime stop() interrupts
            # promptly (not after a 10-second sleep block).
            deadline = time.monotonic() + float(self._cls.duration)
            while time.monotonic() < deadline:
                stop = getattr(self.ctx.runtime, "stopped", None)
                if stop and (stop() if callable(stop) else stop):
                    return False
                time.sleep(min(0.05, deadline - time.monotonic()))
            return True
        # Real mode: call the action's execute() directly.
        try:
            return bool(self._instance.execute(*self._params()))
        except Exception as ex:
            self.log.warning("execute raised: %s", ex)
            return False

    def apply_effects(self, state: Dict[str, Any]) -> None:
        facts = _facts_from_state(state)
        for f in self._instance.eff(*self._params()) or ():
            if not isinstance(f, Fact):
                self.log.warning(
                    "%s.eff returned non-Fact: %r — skipping",
                    self._cls.__name__, f,
                )
                continue
            if f.polarity:
                facts.add(f.as_tuple())
            else:
                facts.discard(f.as_tuple())


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
    "make_predicate_condition",
    "predicate",
    "state_to_frozen",
]
