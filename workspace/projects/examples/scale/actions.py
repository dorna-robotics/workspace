"""scale protocol — Start → [Pick → Weigh → Place] ×N → Park.

For each of ``batch_size`` tubes, the robot:
  1. Pick   — pick the 2 ml tube from its rack slot (SBS rack on plate 4)
  2. Weigh  — place it on the balance pan, read the settled weight, and
              pick it back off the pan (tube ends back in the gripper)
  3. Place  — return the tube to its rack slot

Each is its own BT action, gated by facts so they run in order per tube;
the planner sequences tubes (windowed by plan_window). Tubes are the
single objects dim → slicing auto-engages.

The scale runs in sim by default, so ``weight()`` returns the canned
reading (~12.345 g). Point scale_spx222_1 at a real balance (ip +
simulation:false in the layout) for live readings.

Pattern reference: projects/examples/inspection/actions.py (per-item
multi-action). Scale API: workspace/recipes/scale.py — pick/place onto
the pan ("place" anchor) + weight(stable=True).
"""

from __future__ import annotations

from workspace.bt import Action, predicate


started  = predicate("started")
picked   = predicate("picked")    # tube is in the gripper
weighed  = predicate("weighed")   # tube has been weighed (and lifted off the pan)
placed   = predicate("placed")    # tube returned to its slot
parked   = predicate("parked")


RACK = "rack_autosampler_2ml_1"


def _slot(action, tube):
    """Rack slot anchor (A1..F8) for tube index ``tube`` — read from the
    rack component so the order matches the scene, not a hardcoded list."""
    return action.ctx.workspace.components[RACK].slot["body"][tube]


def _progress_pct(action):
    """Monotonic % over all three per-tube steps. Reads the live fact set
    (``action.ctx.state["facts"]``; ``action.state`` is None in execute).
    This action's eff hasn't applied yet, so count it as +1."""
    tubes = action._ctx_all_objects().get("tube", [])
    total = (len(tubes) or 1) * 3
    ctx_state = getattr(action.ctx, "state", None) or {}
    facts = ctx_state.get("facts") or set()
    done = sum(
        ((picked.name, t) in facts) + ((weighed.name, t) in facts)
        + ((placed.name, t) in facts)
        for t in tubes
    )
    return int((done + 1) / total * 100)


def setup(**kwargs):
    tubes = list(range(int(kwargs.get("batch_size", 4))))

    def item_done(state, tube):
        return (placed.name, tube) in state

    def goal(state):
        return (
            (started.name,) in state
            and all(item_done(state, t) for t in tubes)
            and (parked.name,) in state
        )

    goal_facts = frozenset(
        [(placed.name, t) for t in tubes]
        + [(started.name,), (parked.name,)]
    )

    return {
        "initial_facts": frozenset(),
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


class Pick(Action):
    """Pick the 2 ml tube from its rack slot."""
    params   = ["tube"]
    duration = 10
    resource = "robot"
    tool     = "gripper"

    def pre(self, tube):
        return started() & ~picked(tube)

    def eff(self, tube):
        return {"picked": (+picked(tube),)}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        slot = _slot(self, tube)
        rt.step(f"tube {tube + 1}: pick from rack[{slot}]")
        rt.step(_progress_pct(self), level="progress")
        rcp["tube_rack"].pick(slot, soft_approach=True)
        return "picked"


class Weigh(Action):
    """Place the held tube on the balance pan, read the settled weight,
    then pick it back off the pan (tube ends back in the gripper)."""
    params   = ["tube"]
    duration = 12
    resource = "robot"
    tool     = "gripper"

    def pre(self, tube):
        return picked(tube) & ~weighed(tube)

    def eff(self, tube):
        return {"weighed": (+weighed(tube),)}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"tube {tube + 1}: place on scale")
        rt.step(_progress_pct(self), level="progress")
        # Release the tube on the pan ("place" anchor) so the balance
        # measures it.
        rcp["scale"].place("place", gravity_offset=4, soft_approach=True)
        # Settled reading. ``sim_return`` (device-guide §17) injects the
        # sim weight explicitly — here a distinct fake gram value per tube
        # so a sim run exercises per-tube logic. On the real balance the
        # argument is ignored and the actual weight is read.
        grams = rcp["scale"].weight(stable=True, sim_return=10.0 + tube)
        rt.step(f"tube {tube + 1}: weight = {grams} g" if grams is not None
                else f"tube {tube + 1}: weight unavailable (scale offline)")
        # Re-grip the tube and lift it back off the pan.
        rcp["scale"].pick("place", soft_approach=True)
        return "weighed"


class Place(Action):
    """Return the tube to its rack slot."""
    params   = ["tube"]
    duration = 10
    resource = "robot"
    tool     = "gripper"

    def pre(self, tube):
        return weighed(tube) & ~placed(tube)

    def eff(self, tube):
        return {"placed": (+placed(tube),)}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        slot = _slot(self, tube)
        rt.step(f"tube {tube + 1}: place back to rack[{slot}]")
        rt.step(_progress_pct(self), level="progress")
        rcp["tube_rack"].place(slot, gravity_offset=4, soft_approach=True)
        return "placed"


class Park(Action):
    """Final park — planned by PDDL after every tube is back in its slot."""
    params      = []
    duration    = 5
    resource    = "robot"
    tool        = None
    PARK_JOINTS = [0, 185, -94, 0, 0, 0, 100]

    def pre(self):
        tubes = self._ctx_all_objects().get("tube", [])
        expr = ~parked() & started()
        for t in tubes:
            expr = expr & placed(t)
        return expr

    def eff(self):
        return {"parked": (+parked(),)}

    def execute(self):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rcp["gripper"].park(joint=self.PARK_JOINTS, has_motion_plan=True)
        rt.motor(0)
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — fires on the Park button, outside the plan."""
    trigger = "park"
