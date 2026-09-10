"""Phase — one goal the whole batch reaches before any item moves past it.

A phase is authored like an Action, because it IS the same idea one level
up: ``pre`` says when it may open, ``eff`` says what must hold for it to
be done, ``scope`` says which items it concerns. What it does NOT have is
an ``execute`` — a phase performs no motion. Its "execution" is the
planner reaching its goal, which is precisely why it can bound the
planner's horizon: the search stops at the phase boundary instead of
carrying every item to the end of the protocol.

    from workspace.bt.phase import Phase
    from actions import dispensed, unloaded, racked

    class Dispensed(Phase):
        '''Barcode, weigh, decap and dose — every tube, in its own slot.'''
        fact = dispensed

    class Unloaded(Phase):
        '''Shake, four at a time.'''
        fact = unloaded

    class Racked(Phase):
        '''Recap and return home.'''
        fact = racked

Declaration order is the order they run — the same way ``actions.py`` is
read top to bottom. Nothing needs to list them.

WHY THIS AND NOT A PLAIN LIST OF NAMES. A list is enough for three
phases and stops being enough the moment they are generated: a
rack/tube hierarchy is sixty entries whose order is only correct if the
loop that emitted them was. With ``pre`` the dependency is stated on the
phase itself, so a wrong order is a wrong ``pre`` rather than a silent
mid-batch stall. The bare-string form still works and is still the right
choice for a short static list.

MONOTONICITY IS THE ONE HARD RULE. ``eff`` must name facts no action
removes. "The whole batch has crossed this line" has to stay crossed, or
the phase re-opens, the planner re-targets it, and the run stalls with
no error — the Sussman trap. The launcher checks each named fact against
``monotonic_predicates()`` at startup and warns.
"""

from __future__ import annotations

from typing import Any, List, Optional

# Definition order == run order. A counter beats sorting by name and
# beats making every project hand-maintain a list.
_ORDER = {"n": 0}


class Phase:
    """Base class for a protocol phase. Subclass it; don't instantiate it."""

    #: Sugar for the common case: ``eff`` becomes "this fact holds for
    #: every item in scope". Set either this or override ``eff``.
    fact: Any = None

    #: Human name. Defaults to the class name lowercased.
    name: Optional[str] = None

    #: Same knob as launch.yaml's ``plan_window``, scoped to the span
    #: this phase is open. ``None`` inherits the launch.yaml value.
    #: Deliberately the SAME NAME — one concept, one word to grep.
    #:
    #: This is WIDTH, and it is not the same knob as the phase list,
    #: which is DEPTH. Nor is it how you say "shake four at a time" —
    #: that is hardware, and it belongs in capacity facts so that
    #: re-benching changes the number without touching code. Set this
    #: only when a phase is big enough that CP-SAT struggles: bna's
    #: dose phase is 309 actions and takes ~29s to schedule, and
    #: halving the window halves the model.
    plan_window: Optional[int] = None

    _order: int = 0

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        _ORDER["n"] += 1
        cls._order = _ORDER["n"]
        if cls.name is None:
            cls.name = cls.__name__.lower()

    # ── The three hooks ────────────────────────────────────────────────
    def scope(self, state, items) -> List:
        """Which items this phase concerns. Default: all of them.

        Override to make a phase cover a SUBSET — one rack of a hotel,
        or only the samples a rework needs. Returning an empty list
        makes the phase vacuous and the launcher skips it, which is how
        a conditional phase is written.
        """
        return list(items)

    def pre(self, state, items) -> bool:
        """May this phase open? Default: yes.

        Declaration order already sequences phases, so this is only for
        a dependency that order does not express.
        """
        return True

    def eff(self, items):
        """The facts that must hold for this phase to be done.

        RETURNS FACTS, not a boolean — the same shape ``Action.eff``
        returns, and for the same reason: the launcher does not only ask
        "are we there", it feeds these to the planner as ``goal_facts``
        so GBFS has a heuristic for the phase. A boolean would answer
        the first question and leave the search blind.

        Default: ``fact`` asserted for every item in scope.
        """
        if self.fact is None:
            raise NotImplementedError(
                f"{type(self).__name__}: set ``fact`` or override ``eff``."
            )
        return [self.fact(it) for it in items]

    # ── Starting past this phase (workspace.bt.bench) ─────────────────
    def seed(self, items):
        """The facts that describe every item AT REST after this phase
        closed — what a bench that starts PAST this phase seeds.

        Default: ``eff`` — with one outcome the closure fact is the
        outcome fact, so it says everything. A phase with several
        outcomes overrides this to pick the nominal one (``sorted(t)``
        plus ``in_heavy(t)``), because the next phase's actions gate on
        the outcome, not on the closure alone.
        """
        return self.eff(items)

    def layout(self, items):
        """Where the items PHYSICALLY rest after this phase closed, as
        attach clauses relative to the launch-time scene:
        ``[(child_name, {parent_name, parent_solid, parent_anchor,
        child_solid, child_anchor, offset}), ...]`` — the same shape as
        a scene file's ``attach:``.

        Default: nothing moved. Right for a phase that returns every
        item to its own slot; wrong for one that parks caps in a cap
        rack or leaves vials on a station — the boundary table's
        "resting state" column, made machine-readable. The bench
        applies these so the model matches the real bench before a
        later phase runs, and prints them so the operator can set the
        bench the same way.
        """
        return []

    # ── Derived from eff — the launcher calls these ───────────────────
    def eff_tuples(self, items):
        """``eff`` as plain fact tuples, which is what a State holds."""
        out = []
        for f in self.eff(items):
            out.append(f.as_tuple() if hasattr(f, "as_tuple") else tuple(f))
        return out

    def reached(self, state, items) -> bool:
        """Every fact ``eff`` names is true."""
        return all(t in state for t in self.eff_tuples(items))

    def seed_tuples(self, items):
        """``seed`` as plain fact tuples."""
        return [f.as_tuple() if hasattr(f, "as_tuple") else tuple(f)
                for f in self.seed(items)]

    # ── Introspection used by the launcher ────────────────────────────
    def fact_names(self, items) -> List[str]:
        """Predicate names this phase's ``eff`` asserts — what the
        launcher checks for monotonicity at startup."""
        return sorted({t[0] for t in self.eff_tuples(items) if t})


def collect(module) -> List[Phase]:
    """Every Phase subclass defined in ``module``, in declaration order."""
    found = []
    for obj in vars(module).values():
        if isinstance(obj, type) and issubclass(obj, Phase) and obj is not Phase:
            found.append(obj)
    found.sort(key=lambda c: c._order)
    return [c() for c in found]


# ═══ The launcher's phase machinery, as plain functions ═══════════════
# ``run_protocol`` and ``bt.replay`` walk phases with the SAME code — a
# replay that re-implemented this would drift from the live run, which
# is the one thing a preview must never do.

def _always_ready(_st, _items):
    """A phase with no ``pre`` is ready as soon as its turn comes."""
    return True


def normalise_phases(spec_val, all_items):
    """Normalise a phases spec to
    ``[(name, scope, pre, reached, plan_window, goal_facts)]``.

    ``goal_facts(items)`` is the phase's own facts for those items — what
    the planner's GBFS heuristic aims at while the phase is open. Aiming
    at the protocol's FINAL facts instead makes the search wander past
    the phase boundary and drag later actions into the slice (measured
    on bna: the split window planned an internal-standard dose and an
    inspection). ``None`` for a bare callable goal, which has no facts.

    SCOPE AND GOAL ARE SEPARATE, and that separation is what makes
    hierarchy expressible. Scope answers "which items does this phase
    concern"; goal answers "have they reached it". Fused into one
    ``(state, items) -> bool`` it breaks the moment a phase covers a
    SUBSET: the window picker has to ask "is THIS item past the phase".

    Accepted entries: an authored :class:`Phase` (scope / pre / eff), a
    dict ``{"name", "scope", "goal"}``, a callable ``(state, items) ->
    bool``, or a bare predicate name meaning "that fact holds for every
    item in scope". A phase always windows the project's one
    ``slice_dim``; ``scope`` is how a phase concerns a subset of it.
    """
    if not spec_val:
        return []
    if isinstance(spec_val, dict):
        spec_val = [spec_val]           # a single dict entry
    out = []
    for entry in spec_val:
        if isinstance(entry, Phase):
            out.append((
                entry.name,
                (lambda st, _e=entry: _e.scope(st, all_items)),
                (lambda st, items, _e=entry: bool(_e.pre(st, items))),
                (lambda st, items, _e=entry: _e.reached(st, items)),
                getattr(entry, "plan_window", None),
                (lambda items, _e=entry: _e.eff_tuples(items)),
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
        scope_fn = scope if callable(scope) else (lambda st, _s=scope: _s)
        if scope is None:
            scope_fn = None                 # "every item still live"
        if callable(goal):
            goal_fn, facts_fn = goal, None
        else:
            goal_fn = (lambda st, items, _n=str(goal):
                       all((_n, it) in st for it in items))
            facts_fn = (lambda items, _n=str(goal): [(_n, it) for it in items])
        out.append((name, scope_fn, _always_ready, goal_fn, None, facts_fn))
    return out


def current_phase(state, phases, all_items, item_done, log=None):
    """First unmet phase as ``(name, items_in_scope, reached_fn, window,
    goal_facts_fn)``, or None when every phase is reached (or blocked —
    logged).

    Scope is resolved here, once, so the planning goal and the window
    picker see the same item set. Order is derived: the first phase
    that is ready (``pre``) and not yet reached runs, whatever its
    position in the list.
    """
    live = [it for it in all_items if not item_done(state, it)] \
        if item_done is not None else list(all_items)
    blocked = []
    for nm, scope_fn, pre_fn, reached_fn, _win, facts_fn in phases:
        items = list(scope_fn(state)) if scope_fn is not None else (live or all_items)
        if not items:
            continue                     # scope empty — phase vacuous
        if reached_fn(state, items):
            continue                     # already crossed
        if not pre_fn(state, items):
            blocked.append(nm)
            continue                     # not ready — try the next
        return nm, items, reached_fn, _win, facts_fn
    if blocked and log is not None:
        log.warning("Launcher: phases %s are all blocked by their pre() "
                    "— check their order and conditions.", blocked)
    return None


def pick_window(state, phases, all_items, item_done, plan_window, log=None):
    """Next ``plan_window`` items still outstanding, in order.

    "Outstanding" means not finished; while a phase is open it also
    means not yet past THAT phase, so an item that already cleared the
    current phase drops out of the window and the planner stops
    reasoning about it. A phase's own ``plan_window`` overrides the
    project's while it is open.
    """
    cur = current_phase(state, phases, all_items, item_done, log=log) if phases else None
    scope = set(cur[1]) if cur is not None else None
    width = int(cur[3]) if (cur is not None and cur[3]) else int(plan_window)
    out = []
    for it in all_items:
        if item_done is not None and item_done(state, it):
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
