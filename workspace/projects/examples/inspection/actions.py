"""inspection protocol — Start → [Pick → Present1 → Present2 → Place] ×N → Park.

For each of ``batch_size`` tubes, the robot:
  1. Pick      — pick the capped tube from its rack slot
  2. Present1  — present it to inspection station 1, run detect()
  3. Present2  — present it to inspection station 2, run detect()
  4. Place     — return the tube to its slot

Each is its own BT action, gated by facts so they run in order per tube;
the planner sequences tubes (windowed by plan_window). Tubes are the
single objects dim → slicing auto-engages.

Pattern reference: projects/examples/capping/actions.py (per-item
multi-action) + projects/examples/feeder/actions.py (slots from the
component). Inspector API: workspace/recipes/inspector.py.
"""

from __future__ import annotations

from workspace.bt import Action, predicate


started     = predicate("started")
picked      = predicate("picked")       # tube is in the gripper
presented1  = predicate("presented1")   # shown to station 1
presented2  = predicate("presented2")   # shown to station 2
placed      = predicate("placed")       # tube returned to its slot
parked      = predicate("parked")

# Single-occupancy: the gripper holds ONE tube. Without this the planner
# can batch all the Picks before any Place (impossible — one gripper).
# Consumed on Pick, restored on Place; the tube is held the whole
# Pick→Present1→Present2→Place chain, so the Presents don't touch it.
# See project-guide §8 "Single-occupancy resources".
hand_empty  = predicate("hand_empty")   # gripper holds no tube


RACK = "rack_autosampler_2ml_1"


def _slot(action, tube):
    """Rack slot anchor (A1..F8) for tube index ``tube`` — read from the
    rack component so the order matches the scene, not a hardcoded list."""
    return action.ctx.workspace.components[RACK].slot["body"][tube]


def _progress_pct(action):
    """Monotonic % over all four per-tube steps. Reads the live fact set
    (``action.ctx.state["facts"]``; ``action.state`` is None in execute).
    This action's eff hasn't applied yet, so count it as +1."""
    tubes = action._ctx_all_objects().get("tube", [])
    total = (len(tubes) or 1) * 4
    ctx_state = getattr(action.ctx, "state", None) or {}
    facts = ctx_state.get("facts") or set()
    done = sum(
        ((picked.name, t) in facts) + ((presented1.name, t) in facts)
        + ((presented2.name, t) in facts) + ((placed.name, t) in facts)
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
        return {"started": (+started(), +hand_empty())}   # gripper starts empty

    def execute(self):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes
        rt.motor(1)
        rcp["robot"].park(joint=[0, 45, -90, 0, -45, 0, 100], has_motion_plan=True)
        return "started"


class Pick(Action):
    """Pick the capped tube from its rack slot."""
    params   = ["tube"]
    duration = 10
    resource = "robot"
    tool     = "gripper"

    def pre(self, tube):
        return started() & hand_empty() & ~picked(tube)

    def eff(self, tube):
        return {"picked": (+picked(tube), -hand_empty())}   # hand now full

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        slot = _slot(self, tube)
        rt.step(f"tube {tube + 1}: pick from rack[{slot}]")
        rt.step(_progress_pct(self), level="progress")
        rcp["tube_rack"].pick(slot, soft_approach=True)
        return "picked"


class Present1(Action):
    """Present the held tube to inspection station 1 and inspect."""
    params   = ["tube"]
    duration = 8
    resource = "robot"
    tool     = "gripper"

    def pre(self, tube):
        return picked(tube) & ~presented1(tube)

    def eff(self, tube):
        return {"presented1": (+presented1(tube),)}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"tube {tube + 1}: present to station 1")
        rt.step(_progress_pct(self), level="progress")
        rcp["inspector_1"].present()
        rcp["inspector_1"].detect()
        return "presented1"


class Present2(Action):
    """Present the held tube to inspection station 2 and inspect."""
    params   = ["tube"]
    duration = 8
    resource = "robot"
    tool     = "gripper"

    def pre(self, tube):
        return presented1(tube) & ~presented2(tube)

    def eff(self, tube):
        return {"presented2": (+presented2(tube),)}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"tube {tube + 1}: present to station 2")
        rt.step(_progress_pct(self), level="progress")
        rcp["inspector_2"].present()
        rcp["inspector_2"].detect()
        return "presented2"


class Place(Action):
    """Return the tube to its rack slot."""
    params   = ["tube"]
    duration = 10
    resource = "robot"
    tool     = "gripper"

    def pre(self, tube):
        return presented2(tube) & ~placed(tube)

    def eff(self, tube):
        return {"placed": (+placed(tube), +hand_empty())}   # tube back in rack, hand frees

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
        rcp["robot"].park(joint=self.PARK_JOINTS, has_motion_plan=True)
        rt.motor(0)
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — fires on the Park button, outside the plan."""
    trigger = "park"
