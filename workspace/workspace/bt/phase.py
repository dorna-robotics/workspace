"""Phase — one goal the whole batch reaches before any item moves past it.

A phase is authored like an Action, because it IS the same idea one level
up: ``pre`` says when it may open, ``eff`` says what must hold for it to
be done, ``scope`` says which items it concerns, and ``route`` says the
steps its items take while it is open. What it does NOT have is an
``execute`` — a phase performs no motion. Its "execution" is the planner
reaching its goal, which is precisely why it bounds the horizon: the
route stops at the phase boundary instead of carrying every item to the
end of the protocol.

    from workspace.bt.phase import Phase
    from actions import Start, Park, dispensed, unloaded, racked
    from actions.phase_1 import Barcode, Weigh, Decap, Dose
    ...

    class Dispensed(Phase):
        '''Barcode, weigh, decap and dose — every tube, in its own slot.'''
        fact = dispensed
        route = [Barcode, Weigh, Decap, Dose]

    class Unloaded(Phase):
        '''Shake, four at a time.'''
        fact = unloaded
        route = [Load, Shake, Unload]

    class Racked(Phase):
        '''Recap and return home.'''
        fact = racked
        route = [Recap, Return]

    ROUTE = [Start, Dispensed, Unloaded, Racked, Park]

THE ORDER IS THE ROUTE'S. ``ROUTE`` lists the phases in the order the
batch crosses them; ``pre`` is a guard on that order, not its source —
a phase that opens while its ``pre`` is false is an error at once, not
a phase silently skipped.

MONOTONICITY IS THE ONE HARD RULE. ``eff`` must name facts no action
removes. "The whole batch has crossed this line" has to stay crossed, or
the phase re-opens and the run stalls — the Sussman trap. The launcher
checks each named fact against the route's monotonic predicates at
startup and warns.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple


class Phase:
    """Base class for a protocol phase. Subclass it; don't instantiate it."""

    #: Sugar for the common case: ``eff`` becomes "this fact holds for
    #: every item in scope". Set either this or override ``eff``.
    fact: Any = None

    #: Human name. Defaults to the class name lowercased.
    name: Optional[str] = None

    #: THE STEPS, in the order an item meets them while this phase is
    #: open — Action classes. This is the phase: what it lists is what
    #: runs; an action not listed here (or at run level in ``ROUTE``)
    #: is never planned. A step whose ``pre`` spans several items (a
    #: shake that needs its bank seated) is listed once, like any other.
    route: List[Any] = []

    #: Same knob as launch.yaml's ``plan_window``, scoped to the span
    #: this phase is open. ``None`` inherits the launch.yaml value.
    #: Deliberately the SAME NAME — one concept, one word to grep.
    #:
    #: This is WIDTH — how many items the scheduler holds at once — and
    #: it is not the same knob as the phase list, which is DEPTH. Nor is
    #: it how you say "shake four at a time" — that is hardware, and it
    #: belongs in capacity facts. Set it when a phase's overlap needs
    #: the whole batch in one schedule (a shaker pipeline, ``MAX_BATCH``)
    #: or when a phase is so big that CP-SAT struggles and halving the
    #: window halves the model.
    plan_window: Optional[int] = None

    #: The scheduler's DETERMINISTIC search budget while this phase is
    #: open (``schedule_cpsat(deterministic_limit=...)``); ``None``
    #: inherits the scheduler's default. A whole-batch window is a
    #: bigger model by design: bna's shake-rest-extract pass (238
    #: actions) is FEASIBLE at 6460 s under the default and proven
    #: OPTIMAL at 5070 s with 4.0 — about 200 s of scheduling on the
    #: Pi, reproducible because the budget, not the wall, stops it.
    schedule_budget: Optional[float] = None

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if cls.name is None:
            cls.name = cls.__name__.lower()

    # ── The hooks ─────────────────────────────────────────────────────
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

        ``ROUTE`` already sequences the phases; this is the guard that
        the batch really is where the order says — a false ``pre`` on
        the phase whose turn it is stops the run with the phase named.
        """
        return True

    def eff(self, items):
        """The facts that must hold for this phase to be done.

        RETURNS FACTS, not a boolean — the same shape ``Action.eff``
        returns: the launcher asks "are we there" with them, and the
        bench seeds them when a run starts past this phase.

        Default: ``fact`` asserted for every item in scope.
        """
        if self.fact is None:
            raise NotImplementedError(
                f"{type(self).__name__}: set ``fact`` or override ``eff``."
            )
        return [self.fact(it) for it in items]

    # ── Starting past this phase (workspace.bt.bench) ─────────────────
    def layout(self, items):
        """Where the items PHYSICALLY rest after this phase closed, as
        attach clauses relative to the launch-time scene:
        ``[(child_name, {parent_name, parent_solid, parent_anchor,
        child_solid, child_anchor, offset}), ...]``.

        The default — nothing — means "where the launch scene puts
        them": a tube dosed in place, a rack that never moved. Override
        for a phase whose items END somewhere else (caps parked in a
        cap rack, tubes moved to a working rack), so a bench can start
        the NEXT phase with the model — and the operator — set up the
        way this phase left the bench. Facts are not declared here:
        they are what the earlier phases' actions assert, fact-replayed
        (bt.replay.state_before).
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

    # ── Introspection used by the launcher ────────────────────────────
    def fact_names(self, items) -> List[str]:
        """Predicate names this phase's ``eff`` asserts — what the
        launcher checks for monotonicity at startup."""
        return sorted({t[0] for t in self.eff_tuples(items) if t})

    def __repr__(self) -> str:
        return f"<Phase {self.name}>"


class PhaseNotReady(RuntimeError):
    """The phase whose turn it is (ROUTE order) has a false ``pre``:
    the batch is not where the route says. Named, never skipped."""


# ═══ The launcher's phase machinery, as plain functions ═══════════════
# ``run_protocol`` and ``bt.replay`` walk phases with the SAME code — a
# replay that re-implemented this would drift from the live run, which
# is the one thing a preview must never do.

def current_phase(state, phases, all_items, item_done) -> Optional[Tuple[Phase, list]]:
    """The open phase as ``(phase, items in scope)``, or ``None`` when
    every phase is reached.

    ROUTE order: the first phase with items in scope that is not yet
    reached. Scope is resolved here, once, so the planning goal and the
    window picker see the same item set. A phase whose turn it is with
    a false ``pre`` raises :class:`PhaseNotReady`.
    """
    live = [it for it in all_items if not item_done(state, it)] \
        if item_done is not None else list(all_items)
    for ph in phases:
        items = list(ph.scope(state, live or all_items))
        if not items:
            continue                     # scope empty — phase vacuous
        if ph.reached(state, items):
            continue                     # already crossed
        if not ph.pre(state, items):
            raise PhaseNotReady(
                f"phase {ph.name!r} is next in ROUTE but its pre() is false for "
                f"items {items} — the batch is not where the route says"
            )
        return ph, items
    return None


def pick_window(state, phases, all_items, item_done, plan_window) -> list:
    """Next ``plan_window`` items still outstanding, in order.

    "Outstanding" means not finished; while a phase is open it also
    means not yet past THAT phase, so an item that already cleared the
    current phase drops out of the window and the planner stops
    reasoning about it. A phase's own ``plan_window`` overrides the
    project's while it is open.
    """
    cur = current_phase(state, phases, all_items, item_done) if phases else None
    scope = set(cur[1]) if cur is not None else None
    width = int(cur[0].plan_window) if (cur is not None and cur[0].plan_window) else int(plan_window)
    out = []
    for it in all_items:
        if item_done is not None and item_done(state, it):
            continue
        if scope is not None:
            if it not in scope:
                continue        # this phase does not concern it
            if cur[0].reached(state, [it]):
                continue        # already past this phase
        out.append(it)
        if len(out) >= width:
            break
    return out
