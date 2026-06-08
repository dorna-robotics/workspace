"""feeder example — Start → FeedCap(c) × cap_count → Park.

``cap_count`` operator kwarg controls how many caps the run
transfers. Slots A1..A10 are used as destinations; extend
``CAP_HOLDER_SLOTS`` for larger runs.
"""

from __future__ import annotations

from workspace.bt import Action, predicate


started   = predicate("started")
parked    = predicate("parked")
cap_fed   = predicate("cap_fed")


CAP_HOLDER = "capholder_autosampler_2ml_5x10_1"


def setup(**kwargs):
    cap_count = int(kwargs.get("cap_count", 1))
    caps = list(range(cap_count))

    facts: set = set()

    def item_done(state, cap):
        return (cap_fed.name, cap) in state

    def goal(state):
        return (
            (started.name,) in state
            and all(item_done(state, c) for c in caps)
            and (parked.name,) in state
        )

    goal_facts = frozenset(
        [(cap_fed.name, c) for c in caps]
        + [(started.name,), (parked.name,)]
    )

    return {
        "initial_facts": frozenset(facts),
        "goal":          goal,
        "item_done":     item_done,
        "goal_facts":    goal_facts,
        "objects":       {"cap": caps},
    }


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
        rcp["cap_tool"].park(joint=[0, 45, -90, 0, -45, 0, 100], has_motion_plan=True)
        return "started"


class FeedCap(Action):
    """Transfer one cap from the feeder to the cap holder.

    Same pattern as ``sample_prep/actions.py:CapFed`` and
    ``projects_old/syringe/main.ipynb``:
    above → present_cap → pick → place.
    """

    params    = ["cap"]
    duration  = 10
    resource  = "robot"
    tool      = "cap_tool"

    def pre(self, cap):
        return started() & ~cap_fed(cap)

    def eff(self, cap):
        return {"placed": (+cap_fed(cap),)}

    def execute(self, cap):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes

        holder = self.ctx.workspace.components[CAP_HOLDER]
        slot = holder.slot["body"][cap]
        rt.step(f"cap {cap + 1}: feeder → cap_holder[{slot}]")

        rcp["feeder"].above(anchor="plate_center")
        rcp["feeder"].present_cap(rcp["inspector"])
        rcp["feeder"].pick(approach=False)
        rcp["cap_holder"].place(slot, gravity_offset=-15)

        return "placed"


class Park(Action):
    """Final park — planned by PDDL after every cap is fed.

    Subclass and set ``trigger = "park"`` to reuse the same motion
    as an operator-initiated cleanup (see ``OperatorPark``).
    """
    params      = []
    duration    = 5
    resource    = "robot"
    tool        = None
    PARK_JOINTS = [0, 185, -94, 0, 0, 0, 100]

    def pre(self):
        caps = self._ctx_all_objects().get("cap", [])
        if not caps:
            return ~parked()
        expr = ~parked()
        for c in caps:
            expr = expr & cap_fed(c)
        return expr

    def eff(self):
        return {"parked": (+parked(),)}

    def execute(self):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes
        rcp["cap_tool"].park(joint=self.PARK_JOINTS, has_motion_plan=True)
        rt.motor(0)
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — fires on the Park button, outside the plan."""
    trigger = "park"
