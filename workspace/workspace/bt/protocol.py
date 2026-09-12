"""Protocol — a project's declared ROUTE, resolved and validated.

A project declares the order its items meet the actions:

    # actions.py — a flat protocol
    ROUTE = [Start, Load, Shake, Unload, Park]

    # phases.py — a phased protocol; each phase carries its own steps
    class Weighed1(Phase):
        fact = home[1]
        route = [Pick1, PlaceOnScale1, Weigh1, PickFromScale1, Return1]

    ROUTE = [Start, Weighed1, Weighed2, Park]

An entry is an :class:`~workspace.bt.dsl.Action` subclass — a RUN-LEVEL
step, a candidate in every window (``Start`` opens the run, ``Park``
closes it; their ``pre`` says when) — or a :class:`~workspace.bt.phase.
Phase`, whose ``route`` lists the steps its items take while it is
open, in order. A phase is a barrier: the batch reaches it before any
item moves past it, and only that phase's steps are candidates while
it is open (bt-framework-guide §13).

Being in the ROUTE is what makes an action part of the run. An
``Action`` subclass that is not in it — an abstract base, a helper —
is simply never planned; ``trigger = "park"`` classes are the one
exception, collected for the operator's Park button.

The Protocol is what the launcher, ``bt.replay`` and the bench all
build from, so the three never disagree about what the run is.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Type

import py_trees

from workspace.bt.behaviours import WorkspaceContext
from workspace.bt.dsl import (
    Action,
    ActionRegistry,
    Expr,
    Fact,
    _DSLActionLeaf,
    _default_branch,
    _extract_pre_facts,
    _is_park_trigger,
    _normalise_eff,
    _to_snake,
)
from workspace.bt.phase import Phase
from workspace.planner.plan_scheduler import ActionMeta
from workspace.planner.route import State, Template


log = logging.getLogger(__name__)


class RouteDeclarationError(ValueError):
    """The ROUTE as written cannot be a protocol — said at load, with
    the entry named."""


def _is_action_class(obj: Any) -> bool:
    return isinstance(obj, type) and issubclass(obj, Action)


class Protocol:
    """See the module doc."""

    def __init__(self, route: Sequence[Any], *, project: str = ""):
        if not isinstance(route, (list, tuple)) or not route:
            raise RouteDeclarationError(
                f"{project or 'project'}: ROUTE must be a non-empty list of Action "
                f"classes and Phases — got {type(route).__name__}"
            )
        self.project = project
        self.entries: List[Any] = []         # Action classes and Phase instances, in order
        self.phases: List[Phase] = []
        self.run_steps: List[Type[Action]] = []
        self._by_name: Dict[str, Type[Action]] = {}
        self._where: Dict[str, str] = {}     # name -> "run" | phase name

        for i, entry in enumerate(route):
            if isinstance(entry, type) and issubclass(entry, Phase):
                entry = entry()
            if isinstance(entry, Phase):
                steps = getattr(entry, "route", None)
                if not isinstance(steps, (list, tuple)) or not steps:
                    raise RouteDeclarationError(
                        f"{project}: phase {entry.name!r} (ROUTE[{i}]) declares no route — "
                        f"set ``route = [Step, ...]`` to the steps its items take, in order"
                    )
                for s in steps:
                    self._add(s, entry.name, f"phase {entry.name!r}")
                self.phases.append(entry)
                self.entries.append(entry)
                continue
            if _is_action_class(entry):
                self._add(entry, "run", f"ROUTE[{i}]")
                self.run_steps.append(entry)
                self.entries.append(entry)
                continue
            raise RouteDeclarationError(
                f"{project}: ROUTE[{i}] is {entry!r} — an entry is an Action class "
                f"or a Phase"
            )

        # Park-trigger actions live outside the route: the operator's
        # Park button runs them, the plan never does.
        self.park_classes: List[Tuple[str, Type[Action]]] = []
        for name, cls in sorted(ActionRegistry.current()._actions.items()):
            if _is_park_trigger(cls):
                if name in self._by_name:
                    raise RouteDeclarationError(
                        f"{project}: {cls.__name__} has trigger='park' and is in the "
                        f"ROUTE — a park action is never a route step"
                    )
                self.park_classes.append((name, cls))

    def _add(self, cls: Any, where: str, at: str) -> None:
        if not _is_action_class(cls):
            raise RouteDeclarationError(
                f"{self.project}: {at} lists {cls!r} — a route step is an Action class"
            )
        if _is_park_trigger(cls):
            raise RouteDeclarationError(
                f"{self.project}: {at} lists {cls.__name__}, which has trigger='park' — "
                f"a park action is never a route step"
            )
        name = _to_snake(cls.__name__)
        prev = self._by_name.get(name)
        if prev is not None:
            raise RouteDeclarationError(
                f"{self.project}: {cls.__name__} appears twice in the ROUTE "
                f"({self._where[name]} and {at}) — a step belongs to one place"
            )
        self._by_name[name] = cls
        self._where[name] = where

    # ── Lookup ────────────────────────────────────────────────────────
    def get(self, name: str) -> Optional[Type[Action]]:
        """The class behind a step name — a route step or a park action."""
        cls = self._by_name.get(name)
        if cls is None:
            for n, c in self.park_classes:
                if n == name:
                    return c
        return cls

    def names(self) -> List[str]:
        return list(self._by_name)

    @property
    def classes(self) -> List[Type[Action]]:
        """Every route step, in ROUTE order (phases expanded)."""
        return self.candidates(None, all_phases=True)

    def phase(self, name: str) -> Optional[Phase]:
        return next((p for p in self.phases if p.name == name), None)

    def candidates(self, phase_name: Optional[str], *, all_phases: bool = False) -> List[Type[Action]]:
        """The steps that may be planned while ``phase_name`` is open,
        in ROUTE order: every run-level step, plus that phase's steps
        in its place. ``None`` — no phase open (a flat protocol, or the
        tail after the last phase): the run-level steps only."""
        out: List[Type[Action]] = []
        for entry in self.entries:
            if isinstance(entry, Phase):
                if all_phases or (phase_name is not None and entry.name == phase_name):
                    out.extend(entry.route)
            else:
                out.append(entry)
        return out

    # ── What the planner, scheduler and tree need ─────────────────────
    def templates(self, ctx: WorkspaceContext, classes: Iterable[Type[Action]]) -> List[Template]:
        """:class:`Template` per class, in the given order. The instance
        behind each carries ``ctx`` so ``param_iter`` / ``pre`` / ``eff``
        read the window and the world."""
        out: List[Template] = []
        for cls in classes:
            instance = cls()
            instance.ctx = ctx  # type: ignore[attr-defined]
            out.append(_make_template(_to_snake(cls.__name__), instance))
        return out

    def meta(self) -> Dict[str, ActionMeta]:
        """Scheduler metadata for every route step."""
        out: Dict[str, ActionMeta] = {}
        for name, cls in self._by_name.items():
            # ``tool`` carries two facts: whether the author DECLARED a
            # tool opinion (anything but the unset sentinel), and the
            # tool itself (``None`` = drop whatever is held).
            tool_attr = getattr(cls, "tool", Action._TOOL_UNSET)
            tool_required = tool_attr is not Action._TOOL_UNSET
            out[name] = ActionMeta(
                duration=int(cls.duration),
                resource=cls.resource,
                item_arg_index=0,          # convention: the first param is the item
                tool=None if not tool_required else tool_attr,
                tool_required=tool_required,
                tool_swap_duration=int(cls.tool_swap_duration),
            )
        return out

    def leaf_factory(self, ctx: WorkspaceContext) -> Callable[[str, int], py_trees.behaviour.Behaviour]:
        """``(step name, item) -> leaf`` for ``from_schedule``: the
        class's ``execute`` wrapped as a BT leaf that applies the
        declared effects on success."""
        def _factory(action_name: str, item_index: int) -> py_trees.behaviour.Behaviour:
            cls = self.get(action_name)
            if cls is None:
                raise KeyError(action_name)
            return _DSLActionLeaf(ctx=ctx, action_cls=cls, item_index=item_index)
        return _factory

    def monotonic_predicates(self, ctx: WorkspaceContext) -> set:
        """Predicate names no route step ever removes (across all
        branches) — the facts a phase boundary may be made of. Polarity
        is written into the eff (``+f`` / ``-f``), so one probe per
        binding reads it; every binding is probed because WHICH
        predicates an eff names can depend on the item (a seat fact
        per ``tube % N``)."""
        added: set = set()
        removed: set = set()
        unprobed: list = []
        for cls in self._by_name.values():
            instance = cls()
            instance.state = frozenset()
            instance.ctx = ctx  # type: ignore[attr-defined]
            probed = []
            try:
                for params in instance.param_iter(frozenset()):
                    try:
                        probed.append(_normalise_eff(instance.eff(*params), cls.__name__))
                    except Exception:
                        continue
            except Exception:
                pass
            if not probed:
                unprobed.append(cls.__name__)
                continue
            for effs in probed:
                for branch_facts in effs.values():
                    for f in branch_facts:
                        if isinstance(f, Fact):
                            (added if f.polarity else removed).add(f.pred)
        if unprobed:
            log.warning(
                "Protocol: could not probe eff() for %s — their predicates are "
                "missing from the monotonic set, so the phase check is incomplete.",
                ", ".join(sorted(unprobed)),
            )
        return added - removed

    def explainer(self, ctx: WorkspaceContext) -> Callable[[Template, State, Tuple[Any, ...]], str]:
        """For a stall message: ``(template, state, params) -> "x(1), not
        y(1)"`` — the facts of the step's pre that do not hold. The
        instance carries ``ctx`` like a planned one, so a pre that reads
        the window (``_ctx_all_objects``) names the same facts."""
        def explain(t: Template, state: State, params: Tuple[Any, ...]) -> str:
            cls = self._by_name.get(t.name)
            if cls is None:
                return ""
            instance = cls()
            instance.ctx = ctx  # type: ignore[attr-defined]
            instance.state = state
            try:
                expr = instance.pre(*params)
            except Exception:
                return ""
            pos, neg = _extract_pre_facts(expr)
            missing = [_fmt(f) for f in sorted(pos, key=repr) if f not in state]
            missing += ["not " + _fmt(f) for f in sorted(neg, key=repr) if f in state]
            return ", ".join(missing) if missing else "(a condition the pre computes)"
        return explain


def _fmt(f: Tuple[Any, ...]) -> str:
    return f"{f[0]}({', '.join(repr(a) for a in f[1:])})" if len(f) > 1 else f"{f[0]}()"


def _make_template(name: str, instance: Action) -> Template:
    def param_iter_fn(state: State) -> Iterable[Tuple[Any, ...]]:
        yield from instance.param_iter(state)

    def pre_fn(state: State, params: Tuple[Any, ...]) -> bool:
        # The world is exposed as ``self.state`` BEFORE the call, so a
        # state-aware pre sees the right snapshot.
        instance.state = state
        expr = instance.pre(*params)
        if isinstance(expr, bool):
            return expr
        if isinstance(expr, Fact):
            return expr.as_tuple() in state
        if isinstance(expr, Expr):
            return expr.evaluate(state)
        raise TypeError(
            f"{instance.__class__.__name__}.pre() must return Fact, Expr, or bool "
            f"— got {type(expr).__name__}"
        )

    def eff_fn(state: State, params: Tuple[Any, ...]) -> State:
        instance.state = state
        effs = _normalise_eff(instance.eff(*params), name)
        # The plan projects the FIRST branch — the author lists the
        # nominal outcome first. A different outcome at run time is a
        # replan from the observed state.
        facts = effs[_default_branch(effs)]
        s = set(state)
        for f in facts:
            if not isinstance(f, Fact):
                raise TypeError(
                    f"{instance.__class__.__name__}.eff() must return Facts — "
                    f"got {type(f).__name__}"
                )
            if f.polarity:
                s.add(f.as_tuple())
            else:
                s.discard(f.as_tuple())
        return frozenset(s)

    return Template(name=name, param_iter=param_iter_fn, pre=pre_fn, eff=eff_fn)


def load_route(module: Any, *, project: str = "") -> Protocol:
    """The module's ``ROUTE`` as a :class:`Protocol` — the one place a
    run's steps come from."""
    route = getattr(module, "ROUTE", None)
    if route is None:
        raise RouteDeclarationError(
            f"{project or module.__name__}: no ROUTE — declare the order the items "
            f"meet the actions: ``ROUTE = [Start, ..., Park]`` (phases in phases.py "
            f"carry their own ``route``)"
        )
    return Protocol(route, project=project or module.__name__)
