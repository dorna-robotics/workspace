"""base_1000 protocol — Start → Park, the canonical bookends.

The base seed on the core_1000 bench: the scene is a populated
sample-prep layout, but the protocol has no per-item actions and uses
no tool. The workflow runs Start (motor on + home the rail — fatal
"killed" outcome if homing fails) and Park (move to park pose), then
ends. Copy this folder to start a project on this bench and add
per-item actions between the bookends.
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
    START_JOINTS = [0, 45, -90, 0, -45, 0, 100]

    def pre(self):
        return ~started()

    def eff(self):
        return {"started": (+started(),)}

    def execute(self):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes
        ws  = self.ctx.workspace
        core = ws.components["core"]
        rt.motor(1)
        # Home the rail before any move that assumes a homed axis:
        # set_axis_with_stop configures the axis + PID and homes against
        # the hard stop — already-homed axes (and sim) short-circuit to
        # True, so calling it every Start is cheap. A homing failure is
        # FATAL: return the reserved "killed" outcome — the runtime is
        # killed on the spot, nothing else runs, no motion ever happens
        # on the unhomed rail. The operator must Reset / re-Launch.
        if core.has_rail:
            rt.step("homing rail")
            if not rcp["robot"].set_axis_with_stop(core.rail_cfg):
                rt.step("homing failed")
                return "killed"
        rcp["robot"].park(joint=self.START_JOINTS)
        return "started"


class Park(Action):
    """Final park — planned once the (empty) goal is otherwise met.

    Subclass with ``trigger = "park"`` to reuse the same motion as the
    operator-initiated Park button (see ``OperatorPark``).
    """
    params      = []
    duration    = 5
    resource    = "robot"
    PARK_JOINTS = [0, 90, 0, 0, 0, 0, 100]

    def pre(self):
        return started() & ~parked()

    def eff(self):
        return {"parked": (+parked(),)}

    def execute(self):
        rcp = self.ctx.recipes
        rcp["robot"].park(joint=self.PARK_JOINTS)
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — fires on the Park button, outside the plan."""
    trigger = "park"
