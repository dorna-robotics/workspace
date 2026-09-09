"""phased — the phases the batch passes through, in dependency order.

One class per phase, in the same order as the ``actions/phase_N.py``
modules. A phase has no ``execute``: it is a goal the whole batch reaches
before any tube moves past it (bt-framework-guide §13). The bench state
at each boundary is written down in ``doc/phases.md`` — that table is
the contract the phase modules are built against.
"""

from workspace.bt.phase import Phase

from actions.predicates import home


class Weighed1(Phase):
    """Pass 1: every tube weighed once and back in its own slot."""

    name = "weighed_1"
    fact = home[1]


class Weighed2(Phase):
    """Pass 2: the same again — opens only once pass 1 is complete."""

    name = "weighed_2"
    fact = home[2]

    def pre(self, state, items):
        return all(home[1](t).as_tuple() in state for t in items)
