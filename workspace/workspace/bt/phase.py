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


def collect(module) -> List[Phase]:
    """Every Phase subclass defined in ``module``, in declaration order."""
    found = []
    for obj in vars(module).values():
        if isinstance(obj, type) and issubclass(obj, Phase) and obj is not Phase:
            found.append(obj)
    found.sort(key=lambda c: c._order)
    return [c() for c in found]
