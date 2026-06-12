"""inspection protocol — Start → Park (scaffold only).

Scene-first scaffold. The inspect workflow (pick a tube → present it to
the vision station → rotate/detect → return) gets added between Start
and Park. Read projects/examples/runtime/actions.py and
projects/examples/capping/actions.py before extending.
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


class Park(Action):
    params      = []
    duration    = 5
    resource    = "robot"
    tool        = None
    PARK_JOINTS = [0, 185, -94, 0, 0, 0, 100]

    def pre(self):
        return started() & ~parked()

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
