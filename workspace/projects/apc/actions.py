"""apc protocol — Start → Park (scaffold only).

This is the bare scaffold: just the canonical Start / Park /
OperatorPark trio with no per-item action yet. Workflow runs
Start (motor + park) → Park (motor off) and ends.

Add per-item actions (with their predicates + per-tube execution)
between Start and Park as the protocol grows. Use any of the
``workspace/projects/examples/*`` projects as templates.
"""

from __future__ import annotations

from workspace.bt import Action, predicate


started = predicate("started")
parked  = predicate("parked")


def setup(**kwargs):
    facts: set = set()

    def goal(state):
        return (started.name,) in state and (parked.name,) in state

    goal_facts = frozenset([(started.name,), (parked.name,)])

    return {
        "initial_facts": frozenset(facts),
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
    """Final park — planned by PDDL after every per-item action is done.

    With no per-item actions yet, Park's pre is just ``started() &
    ~parked()``. As you add per-item actions, extend the pre to wait
    for them (see other examples for the ``& done(t)`` for-loop
    pattern).

    Subclass and set ``trigger = "park"`` to reuse the same motion
    as an operator-initiated cleanup (see ``OperatorPark``).
    """
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
