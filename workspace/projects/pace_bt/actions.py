"""BT leaf actions for pace_bt.

One ``RecipeAction`` subclass per atomic action declared in
``domain.py``. Each class is small: an ``execute()`` that calls the
recipe layer to do the physical work, and an ``apply_effects()`` that
mirrors the PDDL effects so the post-success world state propagates
to subsequent ticks / replans.

The framework supplies the threading, status reporting, and
cancellation — subclasses focus on lab logic.

In sim mode (``ctx.core.simulation``) every action just sleeps for its
nominal duration and returns success. That's enough to validate the
plan-schedule-tree-execute loop end-to-end on a Pi with no real robot.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Tuple

import py_trees

from workspace.bt import RecipeAction, WorkspaceContext


# ── Shared utilities ───────────────────────────────────────────────────────


def _sim_sleep(seconds: float) -> None:
    """Sleep, broken up so a runtime stop can interrupt us quickly."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(min(0.05, deadline - time.monotonic()))


# Predicate names mirror domain.py — kept in sync by hand. Tiny enough.
P_IN_SOURCE, P_IN_WORKING, P_IN_DONE = "in_source", "in_working", "in_done"
P_HAS_CAP, P_WEIGHED, P_WEIGHT_HEAVY, P_DOSED = (
    "has_cap", "weighed", "weight_heavy", "dosed",
)


# ── Action classes ─────────────────────────────────────────────────────────


class _ItemAction(RecipeAction):
    """Helper base — every action in pace_bt takes a single ``tube`` index."""

    def __init__(self, ctx: WorkspaceContext, tube: int, *, label: str):
        super().__init__(name=f"{label}(t{tube})", ctx=ctx)
        self.tube = tube
        self._label = label

    # Sim helper used by every leaf.
    def _sim_or_real(self, duration: float, real_fn=None) -> bool:
        if getattr(self.ctx.core, "_simulation_mode", True):
            _sim_sleep(duration)
            return True
        if real_fn is None:
            self.log.warning(
                "%s: no real-mode implementation — falling back to sim", self._label,
            )
            _sim_sleep(duration)
            return True
        try:
            return bool(real_fn())
        except Exception as ex:
            self.log.warning("%s real-mode raised: %s", self._label, ex)
            return False


class Inspect(_ItemAction):
    def __init__(self, ctx, tube):
        super().__init__(ctx, tube, label="inspect")

    def execute(self) -> bool:
        # Real-mode would: pick from source → place on scale → weight → return
        # Sim mode just sleeps and pretends.
        return self._sim_or_real(10.0)

    def apply_effects(self, state: Dict[str, Any]) -> None:
        # Mirror domain.inspect_eff: weight is now known.
        state.setdefault("facts", set()).add((P_WEIGHED, self.tube))


class Decap(_ItemAction):
    def __init__(self, ctx, tube):
        super().__init__(ctx, tube, label="decap")

    def execute(self) -> bool:
        return self._sim_or_real(10.0)

    def apply_effects(self, state):
        f = state.setdefault("facts", set())
        f.discard((P_HAS_CAP, self.tube))
        f.discard((P_IN_SOURCE, self.tube))
        f.add((P_IN_WORKING, self.tube))


class DispenseLight(_ItemAction):
    def __init__(self, ctx, tube):
        super().__init__(ctx, tube, label="dispense_light")

    def execute(self) -> bool:
        return self._sim_or_real(10.0)

    def apply_effects(self, state):
        state.setdefault("facts", set()).add((P_DOSED, self.tube))


class DispenseHeavy(_ItemAction):
    def __init__(self, ctx, tube):
        super().__init__(ctx, tube, label="dispense_heavy")

    def execute(self) -> bool:
        return self._sim_or_real(15.0)

    def apply_effects(self, state):
        state.setdefault("facts", set()).add((P_DOSED, self.tube))


class Recap(_ItemAction):
    def __init__(self, ctx, tube):
        super().__init__(ctx, tube, label="recap")

    def execute(self) -> bool:
        return self._sim_or_real(10.0)

    def apply_effects(self, state):
        state.setdefault("facts", set()).add((P_HAS_CAP, self.tube))


class Shelve(_ItemAction):
    def __init__(self, ctx, tube):
        super().__init__(ctx, tube, label="shelve")

    def execute(self) -> bool:
        return self._sim_or_real(5.0)

    def apply_effects(self, state):
        f = state.setdefault("facts", set())
        f.discard((P_IN_WORKING, self.tube))
        f.add((P_IN_DONE, self.tube))


# ── Leaf factory (used by tree.py via from_schedule) ───────────────────────


# Keyed by action name (matches domain.py templates).
_LEAVES = {
    "inspect":        Inspect,
    "decap":          Decap,
    "dispense_light": DispenseLight,
    "dispense_heavy": DispenseHeavy,
    "recap":          Recap,
    "shelve":         Shelve,
}


def make_leaf(ctx: WorkspaceContext):
    """Return a leaf-factory closed over ``ctx`` for ``from_schedule``."""
    def _factory(action_name: str, item_index: int) -> py_trees.behaviour.Behaviour:
        cls = _LEAVES.get(action_name)
        if cls is None:
            raise KeyError(action_name)
        return cls(ctx, item_index)
    return _factory
