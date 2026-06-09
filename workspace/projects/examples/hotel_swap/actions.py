"""hotel_swap example — Start → Swap(level) × N → Park.

Swaps plates pairwise between two hotels via two SBS plate holders.
Per level:
  1. pick plate from hotel_a[level]   → place on holder_a
  2. pick plate from hotel_b[level]   → place on holder_b
  3. pick plate from holder_a         → place on hotel_b[level]
  4. pick plate from holder_b         → place on hotel_a[level]

End state: every level pair has been swapped between the two hotels.
"""

from __future__ import annotations

from workspace.bt import Action, predicate


started   = predicate("started")
parked    = predicate("parked")
swapped   = predicate("swapped")


def setup(**kwargs):
    level_count = int(kwargs.get("level_count", 1))
    levels = list(range(level_count))

    facts: set = set()

    def item_done(state, level):
        return (swapped.name, level) in state

    def goal(state):
        return (
            (started.name,) in state
            and all(item_done(state, lvl) for lvl in levels)
            and (parked.name,) in state
        )

    goal_facts = frozenset(
        [(swapped.name, lvl) for lvl in levels]
        + [(started.name,), (parked.name,)]
    )

    return {
        "initial_facts": frozenset(facts),
        "goal":          goal,
        "item_done":     item_done,
        "goal_facts":    goal_facts,
        "objects":       {"level": levels},
    }


def _progress_pct(action) -> int:
    """Monotonic progress: count tubes where swapped(level) is in state."""
    levels = action._ctx_all_objects().get("level", [])
    total = len(levels) or 1
    ctx_state = getattr(action.ctx, "state", None) or {}
    facts = ctx_state.get("facts") or set()
    done = sum(1 for lvl in levels if (swapped.name, lvl) in facts)
    # This action's eff hasn't applied yet; count +1 for it.
    done += 1
    return int(done / total * 100)


class Start(Action):
    params   = []
    duration = 5
    resource = "robot"

    def pre(self):
        return ~started()

    def eff(self):
        return {"started": (+started(),)}

    def execute(self):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes
        rt.motor(1)
        rcp["gripper"].park(joint=[0, 45, -90, 0, -45, 0, 100], has_motion_plan=True)
        return "started"


class Swap(Action):
    """Swap the plate pair at ``level`` between hotel_a and hotel_b.

    Uses both holders as temporary stash slots. 8 motions total.
    Same pattern as projects_old/syringe/main.ipynb's plate handling.
    """

    params    = ["level"]
    duration  = 40
    resource  = "robot"
    tool      = "gripper"

    def pre(self, level):
        return started() & ~swapped(level)

    def eff(self, level):
        return {"swapped": (+swapped(level),)}

    def execute(self, level):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes

        rt.step(f"swap level {level}: hotel_a ↔ hotel_b")
        rt.step(_progress_pct(self), level="progress")

        # 1. Move hotel_a[level] plate to holder_a.
        rcp["hotel_a"].pick(level=level)
        rcp["holder_a"].place()

        # 2. Move hotel_b[level] plate to holder_b.
        rcp["hotel_b"].pick(level=level)
        rcp["holder_b"].place()

        # 3. Move holder_a's plate (came from hotel_a) to hotel_b[level].
        rcp["holder_a"].pick()
        rcp["hotel_b"].place(level=level)

        # 4. Move holder_b's plate (came from hotel_b) to hotel_a[level].
        rcp["holder_b"].pick()
        rcp["hotel_a"].place(level=level)

        return "swapped"


class Park(Action):
    """Final park — planned by PDDL after every level is swapped.

    Subclass and set ``trigger = "park"`` to reuse the same motion
    as an operator-initiated cleanup (see ``OperatorPark``).
    """
    params      = []
    duration    = 5
    resource    = "robot"
    tool        = None
    PARK_JOINTS = [0, 185, -94, 0, 0, 0, 100]

    def pre(self):
        levels = self._ctx_all_objects().get("level", [])
        if not levels:
            return ~parked()
        expr = ~parked()
        for lvl in levels:
            expr = expr & swapped(lvl)
        return expr

    def eff(self):
        return {"parked": (+parked(),)}

    def execute(self):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes
        rcp["gripper"].park(joint=self.PARK_JOINTS, has_motion_plan=True)
        rt.motor(0)
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — fires on the Park button, outside the plan."""
    trigger = "park"
