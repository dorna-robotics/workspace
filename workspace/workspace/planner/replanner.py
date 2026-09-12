"""Replanning helper — observation → fresh plan from current world state.

The framework's loop is:

  1. Observe world state (vision, weight, gripper, device-bus).
  2. Run PDDL plan from observed state → action sequence.
  3. Run OR scheduler over the sequence → timed schedule.
  4. Build BT from schedule.
  5. Execute BT. If world drifts (drip, technician intervention, device
     down) — observe again, plan again, rebuild tree.

The :class:`Replanner` bundles steps 1–4 into one ``rebuild()`` callable
that the :class:`BTEngine` can invoke whenever a leaf raises
``ReplanRequested``. Projects supply:

* an ``observe(ctx) -> state`` callable (reads sensors / device bus
  into a fresh ``State`` frozenset),
* a domain (templates) and goal,
* the schedule builder + tree builder.

The replanner stitches them together.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence, Tuple

import py_trees

from workspace.planner.pddl import (
    Action,
    ActionTemplate,
    Domain,
    Goal,
    State,
    domain_from_templates,
    plan,
)


log = logging.getLogger(__name__)


# Type aliases used in signatures.
Observe = Callable[[Any], State]            # ctx -> state
ScheduleBuilder = Callable[[List[Action]], Sequence[Tuple[str, int, float]]]
TreeBuilder = Callable[
    [Sequence[Tuple[str, int, float]], Any],
    py_trees.behaviour.Behaviour,
]


@dataclass
class ReplanConfig:
    """Tuning knobs for replanning.

    Attributes:
        max_plan_depth: Hard cap on plan length passed to the PDDL planner.
        max_plan_states: Hard cap on states the PDDL planner expands.
        verbose: If True, log every plan attempt with action count + duration.
    """

    max_plan_depth: int = 500
    max_plan_states: int = 200_000
    verbose: bool = True


class Replanner:
    """Single entry point for "go from now to a fresh BT".

    Typical wiring:

        replanner = Replanner(
            ctx=ctx,
            observe=my_observe_fn,
            templates=[decap_template, dispense_template, …],
            goal=my_goal_fn,
            build_schedule=ORScheduler(...).schedule_from_plan,
            build_tree=my_project.tree.build_tree,
        )
        root = replanner.rebuild()       # initial tree
        engine = BTEngine(root, rebuild=replanner.rebuild, runtime=rt)
        engine.run()

    Why all the callables instead of a god-class: projects vary in
    domain shape, scheduler usage, and tree composition. By taking
    those as injected functions the Replanner stays project-agnostic.

    Failure modes:
        * ``observe`` raises → ``rebuild`` raises (engine aborts).
        * ``plan`` returns None → ``rebuild`` raises ``RuntimeError``;
          engine aborts. Operator must intervene.
        * Empty plan (goal already satisfied) → returns a trivial tree
          that succeeds on first tick.
    """

    def __init__(
        self,
        *,
        ctx: Any,
        observe: Observe,
        templates: Sequence[ActionTemplate],
        goal: Goal,
        build_schedule: ScheduleBuilder,
        build_tree: TreeBuilder,
        config: Optional[ReplanConfig] = None,
        goal_facts: Optional[Any] = None,
    ):
        self.ctx = ctx
        self._observe = observe
        self._templates = list(templates) if templates is not None else None
        self._domain = domain_from_templates(templates)
        self._goal = goal
        # goal_facts may be a static frozenset OR a zero-arg callable
        # returning a frozenset. Callable form lets slicing re-derive
        # the heuristic hint each rebuild as the active window changes.
        # ``None`` falls back to BFS.
        self._goal_facts = goal_facts
        self._build_schedule = build_schedule
        self._build_tree = build_tree
        self._cfg = config or ReplanConfig()
        self.last_state: Optional[State] = None
        self.last_plan: Optional[List[Action]] = None
        self.last_schedule: Optional[Sequence[Tuple[str, int, float]]] = None
        self._calls = 0

    def rebuild(self) -> py_trees.behaviour.Behaviour:
        """Observe → plan → schedule → tree. Returns the new root behaviour."""
        self._calls += 1
        # 1. Observe.
        state = self._observe(self.ctx)
        if not isinstance(state, frozenset):
            state = frozenset(state)
        self.last_state = state

        # 2. Plan. If goal_facts is provided, GBFS uses it as a
        #    heuristic — scales to large state spaces. Otherwise BFS.
        gf = self._goal_facts
        if callable(gf):
            gf = gf()
        def _plan(st, goal=None, facts=None):
            return plan(
                st,
                self._domain,
                goal if goal is not None else self._goal,
                goal_facts=facts if facts is not None else gf,
                max_depth=self._cfg.max_plan_depth,
                max_states=self._cfg.max_plan_states,
            )

        # TEMPLATE EXPANSION FIRST, search as the fallback. When the
        # items are independent the plan is one chain stamped N times
        # and there is nothing to search for; when they are not, the
        # simulation inside expand_template_plan says so and we search.
        # Measured on bd: 0.01s at n=4/8/19 against 1.8s/4.6s/59s for
        # the search, with the same plan and the same makespan. bna
        # falls back, because its phase barriers genuinely couple items.
        actions = None
        objs = (self.ctx.meta or {}).get("objects") or {}
        # The current phase's partition of the window into the sets that
        # move together (launcher._observe, from Phase.group). With a
        # partition declared the plan is one group's chain stamped per
        # group — and NEVER a search over the coupled items: that search
        # grows with the batch (it took the bench Pi down), so a stamp
        # that does not hold is an error to fix, not a reason to search.
        groups = (self.ctx.meta or {}).get("groups")
        dims = [k for k, v in objs.items() if v]
        if self._templates is not None and len(dims) == 1:
            dim = dims[0]
            try:
                actions = expand_template_plan(
                    self._templates, state, self._goal, gf,
                    list(objs[dim]), self.ctx, dim, _plan, groups=groups,
                )
            except Exception:
                if groups:
                    raise
                log.debug("Replanner: template expansion raised; searching.",
                          exc_info=True)
                actions = None
            if actions is not None and self._cfg.verbose:
                log.info("Replanner[#%d]: template-expanded %d item(s)%s",
                         self._calls, len(objs[dim]),
                         f" in {len(groups)} group(s)" if groups else "")
        if actions is None and groups:
            raise RuntimeError(
                "Replanner: the phase declares groups but one group's chain did "
                "not stamp onto the others — the groups are not uniform, or a "
                "group's chain does not close on its own. Fix the phase; a "
                "grouped phase is never searched."
            )
        if actions is None:
            actions = _plan(state)
        if actions is None:
            raise RuntimeError(
                "Replanner: PDDL search found no plan from the current state. "
                "The world is in a state from which the goal is unreachable "
                "under the current action templates. Operator intervention "
                "required."
            )
        self.last_plan = actions
        if self._cfg.verbose:
            log.info("Replanner[#%d]: plan=%d actions", self._calls, len(actions))

        # 3. Schedule.
        try:
            schedule = self._build_schedule(actions)
        except Exception:
            log.exception("Replanner: schedule builder raised")
            raise
        self.last_schedule = schedule

        # 4. Tree.
        try:
            root = self._build_tree(schedule, self.ctx)
        except Exception:
            log.exception("Replanner: tree builder raised")
            raise

        if self._cfg.verbose:
            log.info("Replanner[#%d]: tree rebuilt", self._calls)
        return root


def expand_template_plan(templates, state, goal, goal_facts, items, ctx, dim, plan_fn,
                         groups=None):
    """Plan ONE item (or ONE GROUP), replicate the chain for every other, verify.

    ``groups``: a partition of ``items`` into the sets that move together
    (``Phase.group``). Then the unit that is planned once and stamped is
    the group, not the item — the first group's chain, re-grounded onto
    every other group by position; a group of a different size (the
    batch's last, partial bank) is planned on its own, a search bounded
    by the group size. Everything else is unchanged.

    Searching over N interchangeable items rediscovers the same chain N
    times while wading through their orderings. When the items really
    are independent the plan is not something to search for — it is one
    chain, stamped N times — and contention (a two-slot shaker, one
    vortex) is the scheduler's job, not the plan's.

    NOTHING IS TRUSTED HERE. The replicated plan is SIMULATED against
    the real ``preconditions``/``effects`` before it is returned: every
    action must be applicable when its turn comes and the goal must
    hold at the end. That is the same check ``bt.replay`` performs, and
    it is why this needs no rule about when items "look" independent —
    if replication does not actually work (a barrier couples the items,
    a capacity fact is acquired and never released inside the chain)
    the simulation fails and we fall back to the ordinary search.

    Returns the expanded plan, or ``None`` to mean "fall back".
    """
    if not items or not dim or not goal_facts:
        return None
    units = [list(g) for g in groups if g] if groups else [[it] for it in items]
    if len(units) < 2 and not groups:
        return None
    # THE ONE-UNIT GOAL IS THE ONE-UNIT SLICE OF goal_facts. Planning
    # with objects restricted to one unit but the FULL goal still asking
    # for all N leaves the search unable to finish — it was the first
    # thing this got wrong. Keep the facts that mention the unit's items
    # plus none of the itemless ones.
    first = units[0]
    # PER-ITEM FACTS ONLY. Itemless goal facts (``started``, ``parked``)
    # belong to the whole run, and a project's Park typically gates on
    # ``_ctx_all_objects()`` — every item recapped — which one item can
    # never satisfy. Including them makes the one-item search
    # unsatisfiable, which is exactly how this first failed. The tail is
    # planned separately at step 4, from a state where the chains are
    # already done, so that search is trivial.
    objects = ctx.meta.get("objects") or {}
    saved = list(objects.get(dim, []))

    def _plan_unit(unit, from_state):
        """The chain for ONE unit, planned with objects restricted to it,
        from ``from_state`` — the world as the chains already expanded
        leave it. Mid-phase the units are at different stages and one
        may hold what another needs (the arm carries bank 0's tube while
        bank 1 waits on the shaker): bank 1's chain does not close from
        the observed state, it closes from the state bank 0's chain
        leaves — so each new chain is planned after the ones before it,
        in the order they are expanded."""
        solo = frozenset(f for f in goal_facts if len(f) > 1 and f[1] in unit)
        if not solo:
            return None, None
        objects[dim] = list(unit)
        try:
            one = plan_fn(from_state, (lambda st, _s=solo: _s <= st), solo)
        finally:
            objects[dim] = saved
        if not one:
            return None, None
        return [a for a in one if a.params], [a for a in one if not a.params]

    def _advance(sim, actions):
        """``sim`` after ``actions``, or None if one is not applicable."""
        for a in actions:
            try:
                if not a.preconditions(sim):
                    return None
                sim = a.effects(sim)
            except Exception:
                return None
            if not isinstance(sim, frozenset):
                sim = frozenset(sim)
        return sim

    def _signature(unit):
        """The unit's state, item positions in place of item ids — two
        units with the same signature have the same chain ahead of
        them. A replan mid-phase finds groups at different stages
        (bank 0 extracted, bank 1 loaded, bank 2 untouched): each
        distinct signature is planned once, bounded by the group size,
        and stamped over its class — never a search over the batch."""
        pos = {it: k for k, it in enumerate(unit)}
        return (len(unit), frozenset((f[0], pos[f[1]]) + tuple(f[2:])
                                     for f in state if len(f) > 1 and f[1] in pos))

    # 1. Plan the chain for the first unit that closes from the observed
    #    state (bookends come from it). Mid-phase the first declared unit
    #    may be the one waiting on another — the order of expansion is
    #    the order in which the chains close, declared order among the
    #    candidates, so it is deterministic.
    chain, bookends, first = None, None, None
    for unit in units:
        chain, bookends = _plan_unit(unit, state)
        if chain:
            first = unit
            break
    if not chain:
        return None
    templates_by_sig = {_signature(first): (first, chain)}

    # 2. Re-ground the chain for every item. Templates are indexed by
    #    name so an action can be built for an item the search never
    #    visited.
    by_name = {t.name: t for t in templates}
    if any(a.name not in by_name for a in chain):
        return None

    def _ground(name, params):
        t = by_name[name]
        return Action(
            name=name,
            params=tuple(params),
            preconditions=(lambda s, p=tuple(params), _t=t: _t.preconditions(s, p)),
            effects=(lambda s, p=tuple(params), _t=t: _t.effects(s, p)),
        )

    expanded = list(bookends[:1])
    sim = _advance(state, expanded)
    if sim is None:
        return None
    remaining = list(units)
    while remaining:
        # The next chain: the first remaining unit whose chain closes
        # from the world the expanded chains leave — a stamp of a known
        # signature, or its own plan, bounded by the group size.
        for unit in remaining:
            sig = _signature(unit)
            if sig in templates_by_sig:
                tmpl_unit, tmpl_chain = templates_by_sig[sig]
                # Re-ground by POSITION: the k-th item of the template
                # unit becomes the k-th item of this unit, in every
                # parameter.
                m = dict(zip(tmpl_unit, unit))
                stamped = [_ground(a.name, tuple(m.get(p, p) for p in a.params))
                           for a in tmpl_chain]
            else:
                own, _ = _plan_unit(unit, sim)
                if not own:
                    continue
                templates_by_sig[sig] = (unit, own)
                stamped = list(own)
            after = _advance(sim, stamped)
            if after is None:
                continue
            expanded += stamped
            sim = after
            remaining.remove(unit)
            break
        else:
            return None
    expanded += bookends[1:]

    # 3. Verify by simulation — every action applicable when its turn
    #    comes. Nothing here is assumed; if replication does not work
    #    the simulation says so and we fall back. (Each chain was already
    #    advanced as it was expanded; this is the whole plan, once more,
    #    from the observed state — the same check bt.replay performs.)
    sim = _advance(state, expanded)
    if sim is None:
        return None

    # 4. Tail. The chains are done but run-level goal facts (parked) may
    #    not be; search for the remainder from here. It is a handful of
    #    actions from a nearly-complete state, so this is cheap.
    if not goal(sim):
        tail = plan_fn(sim, goal, goal_facts)
        if not tail:
            return None
        expanded += list(tail)
        for a in tail:
            try:
                sim = a.effects(sim)
            except Exception:
                return None
            if not isinstance(sim, frozenset):
                sim = frozenset(sim)
        if not goal(sim):
            return None
    return expanded
