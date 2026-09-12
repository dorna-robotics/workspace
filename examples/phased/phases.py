"""phased — the ROUTE: the phases the batch passes through, in order,
each with the steps its tubes take.

One class per phase, in the order the batch crosses them (``ROUTE``);
each names its ``route`` — the ``actions/phase_N.py`` module read top
to bottom. A phase has no ``execute``: it is a goal the whole batch
reaches before any tube moves past it (bt-framework-guide §13). The
bench state at each boundary is written down in ``doc/phases.md`` —
that table is the contract the phase modules are built against.
"""

from workspace.bt.phase import Phase

from actions import Park, Start
from actions.base import MAX_BATCH
from actions.phase_1 import Pick1, PickFromScale1, PlaceOnScale1, Return1, Weigh1
from actions.phase_2 import (Load, Pick2, PickFromScale2, PlaceOnScale2, Return2, Shake,
                             Unload, Weigh2)
from actions.predicates import N_SEATS, home


def bank(tube):
    """The cycle's round: a shaker bank — the same static rule the
    shake's ``pre`` spans (``actions.base.bank_of``)."""
    return tube // N_SEATS


class Weighed1(Phase):
    """Pass 1: every tube weighed once and back in its own slot."""

    name = "weighed_1"
    fact = home[1]
    route = [Pick1, PlaceOnScale1, Weigh1, PickFromScale1, Return1]


class Weighed2(Phase):
    """Pass 2: shaken in banks of four, then weighed again.

    ONE phase for the shake and the second weighing, and the order
    across banks is DECLARED: while a bank shakes, the previous bank
    is weighed. The shake holds only the shaker, the weighing the arm,
    so the cycle below overlaps them. A barrier between the two would
    idle the arm through every shake.
    """

    name = "weighed_2"
    fact = home[2]
    route = [Load, Shake, Unload, Pick2, PlaceOnScale2, Weigh2, PickFromScale2, Return2]
    group = bank
    # One round per bank; each stage is walked tube by tube, and
    # ``[-1]`` tags a stage with the PREVIOUS bank: the last bank comes
    # off the shaker, this bank goes on (the shake fires with its last
    # tube), and the last bank is weighed while it shakes. Round 0 has
    # no previous bank, and the rounds keep going after the last bank
    # until the ``[-1]`` stages run dry — the list never says so.
    cycle = [[Unload[-1]],
             [Load, Shake],
             [Pick2[-1], PlaceOnScale2[-1], Weigh2[-1], PickFromScale2[-1], Return2[-1]]]
    # The whole rack in one window: a cycle spans banks.
    plan_window = MAX_BATCH

    def pre(self, state, items):
        return all(home[1](t).as_tuple() in state for t in items)


# The run: Start opens it, Park closes it; the phases between are the
# barriers the batch crosses together, in this order.
ROUTE = [Start, Weighed1, Weighed2, Park]
