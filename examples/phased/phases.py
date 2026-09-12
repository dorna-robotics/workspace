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
from actions.predicates import home


class Weighed1(Phase):
    """Pass 1: every tube weighed once and back in its own slot."""

    name = "weighed_1"
    fact = home[1]
    route = [Pick1, PlaceOnScale1, Weigh1, PickFromScale1, Return1]


class Weighed2(Phase):
    """Pass 2: shaken in banks of four, then weighed again.

    ONE phase for the shake and the second weighing, so the scheduler
    pipelines the banks: the shake holds only the shaker, the weighing
    the arm — bank 2 shakes while bank 1 is weighed. A barrier between
    them would idle the arm through every shake.
    """

    name = "weighed_2"
    fact = home[2]
    route = [Load, Shake, Unload, Pick2, PlaceOnScale2, Weigh2, PickFromScale2, Return2]
    # The whole rack in one window: an overlap needs both banks in the
    # same schedule. (``schedule_budget`` stays the default: two banks
    # are a small model; bna's seven at 238 actions name one.)
    plan_window = MAX_BATCH

    def pre(self, state, items):
        return all(home[1](t).as_tuple() in state for t in items)


# The run: Start opens it, Park closes it; the phases between are the
# barriers the batch crosses together, in this order.
ROUTE = [Start, Weighed1, Weighed2, Park]
