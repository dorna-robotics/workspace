"""phased — the phases the batch passes through, in dependency order.

One class per phase, in the same order as the ``actions/phase_N.py``
modules. A phase has no ``execute``: it is a goal the whole batch reaches
before any tube moves past it (bt-framework-guide §13). The bench state
at each boundary is written down in ``doc/phases.md`` — that table is
the contract the phase modules are built against.
"""

from workspace.bt.phase import Phase

from actions.base import MAX_BATCH
from actions.predicates import N_SEATS, home


def banks(items):
    """The shaker banks — the sets of tubes that move together through
    the shake (``Phase.group``): static, ``tube // N_SEATS``, the same
    rule the shake itself uses (``actions.base.bank_of``), so planner,
    replay and bench agree whatever the order."""
    keys = sorted({t // N_SEATS for t in items})
    return [[t for t in items if t // N_SEATS == k] for k in keys]


class Weighed1(Phase):
    """Pass 1: every tube weighed once and back in its own slot."""

    name = "weighed_1"
    fact = home[1]


class Weighed2(Phase):
    """Pass 2: shaken in banks of four, then weighed again.

    ONE phase for the shake and the second weighing, so the scheduler
    pipelines the banks: the shake holds only the shaker, the weighing
    the arm — bank 2 shakes while bank 1 is weighed. A barrier between
    them would idle the arm through every shake.
    """

    name = "weighed_2"
    fact = home[2]
    # The whole rack in one window: the planner plans ONE bank's chain
    # and stamps it per bank (``group``), so the size never reaches a
    # search, and the scheduler overlaps the banks — an overlap needs
    # both banks in the model. (``schedule_budget`` stays the default:
    # two banks are a small model; bna's seven at 238 actions name one.)
    plan_window = MAX_BATCH

    def group(self, items):
        return banks(items)

    def pre(self, state, items):
        return all(home[1](t).as_tuple() in state for t in items)
