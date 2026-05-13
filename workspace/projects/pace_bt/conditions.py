"""BT leaf conditions for pace_bt.

Conditions are dirt-cheap reads of ``ctx.state`` (the world-state dict
the framework maintains). Tests should never touch hardware or fire
recipe calls — those are actions.

A condition's only output is SUCCESS / FAILURE based on a boolean
predicate over current state. The framework's :class:`PredicateCondition`
base handles all the BT plumbing.
"""

from __future__ import annotations

from workspace.bt import PredicateCondition, WorkspaceContext


# Predicate names — must match domain.py / actions.py.
P_HAS_CAP, P_WEIGHED, P_WEIGHT_HEAVY, P_DOSED = (
    "has_cap", "weighed", "weight_heavy", "dosed",
)


def _facts(ctx: WorkspaceContext) -> set:
    """Return the live set of facts (creates it lazily)."""
    return ctx.state.setdefault("facts", set())


class IsCapped(PredicateCondition):
    def __init__(self, ctx, tube):
        super().__init__(name=f"is_capped(t{tube})", ctx=ctx)
        self.tube = tube

    def check(self) -> bool:
        return (P_HAS_CAP, self.tube) in _facts(self.ctx)


class IsWeighed(PredicateCondition):
    def __init__(self, ctx, tube):
        super().__init__(name=f"is_weighed(t{tube})", ctx=ctx)
        self.tube = tube

    def check(self) -> bool:
        return (P_WEIGHED, self.tube) in _facts(self.ctx)


class IsHeavy(PredicateCondition):
    def __init__(self, ctx, tube):
        super().__init__(name=f"is_heavy(t{tube})", ctx=ctx)
        self.tube = tube

    def check(self) -> bool:
        return (P_WEIGHT_HEAVY, self.tube) in _facts(self.ctx)


class IsDosed(PredicateCondition):
    def __init__(self, ctx, tube):
        super().__init__(name=f"is_dosed(t{tube})", ctx=ctx)
        self.tube = tube

    def check(self) -> bool:
        return (P_DOSED, self.tube) in _facts(self.ctx)
