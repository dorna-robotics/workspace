"""runtime_disc protocol — Start → Park (scaffold only).

The point of this example is RUNTIME scene mutation: spawn a disc into
the scene programmatically and remove it later, instead of declaring it
in the scene yaml. The hooks for that live on ``self.ctx.workspace``:

    ws = self.ctx.workspace

    # Spawn — cfg is the same shape as a scene yaml entry (type + attach):
    ws.add_component("disc_1", {
        "type": "disc_22mm",
        "attach": {
            "parent_name": "...", "parent_solid": "body",
            "parent_anchor": "...", "child_solid": "body",
            "child_anchor": "center", "offset": [0, 0, 0, 0, 0, 0],
        },
    })
    # add_component returns the instance; the caller adds any PDDL facts
    # the change implies via ws.add_fact(...).

    # Kill — detaches the component's solids and drops it from the scene:
    ws.remove_component("disc_1")          # ws.remove_fact(...) to match

Right now this is just the canonical Start / Park / OperatorPark trio
with no per-item action. The spawn-disc and kill-disc actions get added
between Start and Park.
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
    ~parked()``. As you add spawn/kill-disc actions, extend the pre to
    wait for them (see other examples for the ``& done(t)`` for-loop
    pattern).

    Subclass and set ``trigger = "park"`` to reuse the same motion as an
    operator-initiated cleanup (see ``OperatorPark``).
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
