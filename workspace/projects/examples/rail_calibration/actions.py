"""rail_calibration protocol — Start → Park (scaffold only).

This is the bare scaffold: just the canonical Start / Park /
OperatorPark trio with no per-item action. The workflow runs Start
(motor on + move to home) → Park (park pose + motor off) and ends.
The point of the project is the SCENE — a probe_rail_calibration
stand on fixture_plate_2/G1 to develop probe/rail calibration
against. Add per-item actions between Start and Park as the
protocol grows; use any of the other examples as templates.
"""

from __future__ import annotations

from workspace.bt import Action, predicate


started = predicate("started")
parked  = predicate("parked")


def setup(**kwargs):
    def goal(state):
        return (started.name,) in state and (parked.name,) in state

    goal_facts = frozenset([(started.name,), (parked.name,)])

    return {
        "initial_facts": frozenset(),
        "goal":          goal,
        "goal_facts":    goal_facts,
        "objects":       {},
    }


class Start(Action):
    params      = []
    duration    = 5
    resource    = "robot"
    HOME_JOINTS = [0, 45, -90, 0, -45, 0, 100]

    def pre(self):
        return ~started()

    def eff(self):
        return {"started": (+started(),)}

    def execute(self):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes
        rt.motor(1)
        rcp["robot"].park(joint=self.HOME_JOINTS, has_motion_plan=True)
        return "started"


class Park(Action):
    """Final park — planned once the (empty) goal is otherwise met.

    Subclass with ``trigger = "park"`` to reuse the same motion as the
    operator-initiated Park button (see ``OperatorPark``).
    """
    params      = []
    duration    = 5
    resource    = "robot"
    PARK_JOINTS = [0, 185, -94, 0, 0, 0, 100]

    def pre(self):
        return started() & ~parked()

    def eff(self):
        return {"parked": (+parked(),)}

    def execute(self):
        rcp = self.ctx.recipes
        rcp["robot"].park(joint=self.PARK_JOINTS, has_motion_plan=True)
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — fires on the Park button, outside the plan."""
    trigger = "park"
