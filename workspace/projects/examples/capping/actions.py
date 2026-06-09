"""capping example — Start → Cap(t) × N → Decap(t) × N → Park.

Demonstrates the full cap + decap roundtrip with the decapper
component and the 4-finger gripper. Same pattern as
``projects_old/printer/workflow.py`` and
``projects_old/syringe/main.ipynb``, split into two BT actions so
the planner schedules them independently:

  Cap(t):   tube_rack → decapper, pick cap, screw on, return to rack
  Decap(t): tube_rack → decapper, unscrew, return cap, return tube

``tube_count`` operator kwarg controls how many tubes the run
processes. Slots A1..E10 are available; the first ``tube_count``
slots in iteration order are used.
"""

from __future__ import annotations

from workspace.bt import Action, predicate


started   = predicate("started")
parked    = predicate("parked")
capped    = predicate("capped")
decapped  = predicate("decapped")


TUBE_RACK = "rack_autosampler_2ml_1"


def _progress_pct(action, *, kind: str) -> int:
    """Monotonic progress percentage from live runtime facts.

    Each tube contributes two transitions: cap-step + decap-step.
    A tube has completed the cap-step if ``capped(t)`` is currently
    set OR ``decapped(t)`` is (Decap removes capped but adds
    decapped — the "OR" preserves the count across that swap).
    Decap-step count is simpler: ``decapped(t)`` in state.

    Each transition adds exactly +1 to the combined count, so the
    bar advances monotonically regardless of plan order.

    Reads facts from ``action.ctx.state["facts"]`` — the live runtime
    fact set. ``action.state`` is only populated by the framework
    during ``pre()`` / ``eff()`` (planning); inside ``execute()`` it
    is None.
    """
    tubes = action._ctx_all_objects().get("tube", [])
    total = len(tubes) or 1
    ctx_state = getattr(action.ctx, "state", None) or {}
    facts = ctx_state.get("facts") or set()
    cap_step   = sum(1 for t in tubes if (capped.name, t) in facts or (decapped.name, t) in facts)
    decap_step = sum(1 for t in tubes if (decapped.name, t) in facts)
    # this action's eff hasn't applied yet — count it as +1
    if kind == "cap":
        cap_step += 1
    elif kind == "decap":
        decap_step += 1
    return int((cap_step + decap_step) / (2 * total) * 100)


def setup(**kwargs):
    tube_count = int(kwargs.get("tube_count", 1))
    tubes = list(range(tube_count))

    facts: set = set()

    def item_done(state, tube):
        return (decapped.name, tube) in state

    def goal(state):
        return (
            (started.name,) in state
            and all(item_done(state, t) for t in tubes)
            and (parked.name,) in state
        )

    goal_facts = frozenset(
        [(decapped.name, t) for t in tubes]
        + [(started.name,), (parked.name,)]
    )

    return {
        "initial_facts": frozenset(facts),
        "goal":          goal,
        "item_done":     item_done,
        "goal_facts":    goal_facts,
        "objects":       {"tube": tubes},
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


class Cap(Action):
    """Cap one tube: pick from rack → decapper → pick cap → screw → return tube."""

    params    = ["tube"]
    duration  = 30
    resource  = "robot"
    tool      = "gripper"

    def pre(self, tube):
        return started() & ~capped(tube)

    def eff(self, tube):
        return {"capped": (+capped(tube),)}

    def execute(self, tube):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes

        tube_rack = self.ctx.workspace.components[TUBE_RACK]
        slot = tube_rack.slot["body"][tube]

        rt.step(f"cap {tube + 1}: tube {slot} → decapper")
        rt.step(_progress_pct(self, kind="cap"), level="progress")

        rcp["tube_rack"].pick(slot)
        rcp["decapper"].place()
        rcp["cap_holder"].pick(slot)
        rcp["decapper"].cap(exit=False)
        rcp["decapper"].pick(approach=False, tool_tcp_z_offset=-2)
        rcp["tube_rack"].place(slot, gravity_offset=4, soft_approach=True)

        return "capped"


class Decap(Action):
    """Decap one tube: pick from rack → decapper → unscrew → return cap + tube."""

    params    = ["tube"]
    duration  = 30
    resource  = "robot"
    tool      = "gripper"

    def pre(self, tube):
        return capped(tube) & ~decapped(tube)

    def eff(self, tube):
        return {"decapped": (+decapped(tube), -capped(tube))}

    def execute(self, tube):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes

        tube_rack = self.ctx.workspace.components[TUBE_RACK]
        slot = tube_rack.slot["body"][tube]

        rt.step(f"decap {tube + 1}: tube {slot} → decapper → unscrew")
        rt.step(_progress_pct(self, kind="decap"), level="progress")

        rcp["tube_rack"].pick(slot)
        rcp["decapper"].place(exit=False)
        rcp["decapper"].decap(approach=False)
        rcp["cap_holder"].place(slot, soft_approach=True, gravity_offset=4)
        rcp["decapper"].pick()
        rcp["tube_rack"].place(slot, gravity_offset=4, soft_approach=True)

        return "decapped"


class Park(Action):
    """Final park — planned by PDDL after every tube is decapped.

    Subclass and set ``trigger = "park"`` to reuse the same motion
    as an operator-initiated cleanup (see ``OperatorPark``).
    """
    params      = []
    duration    = 5
    resource    = "robot"
    tool        = None
    PARK_JOINTS = [0, 185, -94, 0, 0, 0, 100]

    def pre(self):
        tubes = self._ctx_all_objects().get("tube", [])
        if not tubes:
            return ~parked()
        expr = ~parked()
        for t in tubes:
            expr = expr & decapped(t)
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
