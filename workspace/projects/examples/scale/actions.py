"""scale protocol — Start → [Pick → PlaceOnScale → Weigh → PickFromScale → Place] ×N → Park.

For each of ``batch_size`` tubes, the robot:
  1. Pick          — pick the 2 ml tube from its rack slot (SBS rack, plate 4)
  2. PlaceOnScale  — release the tube on the balance pan
  3. Weigh         — read the settled weight (NO motion — a pure device read)
  4. PickFromScale — re-grip the tube and lift it off the pan
  5. Place         — return the tube to its rack slot

Each is its own BT action, gated by facts so they run in order per tube;
the planner sequences tubes (windowed by plan_window). Tubes are the
single objects dim → slicing auto-engages.

WHY Weigh is split out as its own read-only action — declarative retry
================================================================
The weigh is separated from the place/pick motions ON PURPOSE, so a
failed reading can be retried without redoing the arm moves. This is the
reference pattern for "a device read that must succeed before the flow
continues" — see project-guide §"Device reads + declarative retry".

The mechanism is the planner, not a retry loop:

  * ``Weigh`` asserts ``weighed(tube)`` ONLY when it actually gets a
    number. On a failed read it ``return False`` → the leaf FAILS → no
    effect is applied → ``weighed(tube)`` stays false.
  * A failed leaf raises a replan (the launcher wraps the body in
    ``replan_on_failure``). The engine rebuilds from the OBSERVED world,
    where ``on_scale(tube) & ~weighed(tube)`` is still true — so the
    planner re-selects ``Weigh`` for that tube. The retry IS the plan.
  * Meanwhile, if the scale is ``critical`` + real, its bus ``down`` state
    pauses the runtime (the orchestrator calls ``runtime.pause()``); when
    the device recovers and the operator resumes, the re-selected
    ``Weigh`` runs against the now-reconnected balance.

So recovery falls out of honest pre/eff + replan — no per-device retry
code, no hardcoded loop. The same shape works for ANY device read
(multimeter, vision, …): keep the read its own action, assert the
success fact only on success, return False otherwise.

The scale runs in sim by default → ``weight()`` returns the canned
reading. Point scale_spx222_1 at a real balance (ip + simulation:false in
the layout) for live readings and the real pause/recover/retry path.

Pattern reference: projects/examples/inspection/actions.py (per-item
multi-action). Scale API: workspace/recipes/scale.py — pick/place onto
the pan ("place" anchor) + weight(stable=True).
"""

from __future__ import annotations

from workspace.bt import Action, predicate


started   = predicate("started")
picked    = predicate("picked")     # tube is in the gripper
on_scale  = predicate("on_scale")   # tube released on the balance pan
weighed   = predicate("weighed")    # a valid weight was read for this tube
off_scale = predicate("off_scale")  # tube re-gripped and lifted off the pan
placed    = predicate("placed")     # tube returned to its slot
parked    = predicate("parked")


RACK = "rack_autosampler_2ml_1"

# How many per-tube steps the progress bar spans (Pick, PlaceOnScale,
# Weigh, PickFromScale, Place).
_STEPS = 5


def _slot(action, tube):
    """Rack slot anchor (A1..F8) for tube index ``tube`` — read from the
    rack component so the order matches the scene, not a hardcoded list."""
    return action.ctx.workspace.components[RACK].slot["body"][tube]


def _progress_pct(action):
    """Monotonic % over all per-tube steps. Reads the live fact set
    (``action.ctx.state["facts"]``; ``action.state`` is None in execute).
    This action's eff hasn't applied yet, so count it as +1."""
    tubes = action._ctx_all_objects().get("tube", [])
    total = (len(tubes) or 1) * _STEPS
    ctx_state = getattr(action.ctx, "state", None) or {}
    facts = ctx_state.get("facts") or set()
    done = sum(
        ((picked.name, t) in facts) + ((on_scale.name, t) in facts)
        + ((weighed.name, t) in facts) + ((off_scale.name, t) in facts)
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


class PlaceOnScale(Action):
    """Release the held tube on the balance pan ("place" anchor) so the
    balance can measure it."""
    params   = ["tube"]
    duration = 10
    resource = "robot"
    tool     = "gripper"

    def pre(self, tube):
        return picked(tube) & ~on_scale(tube)

    def eff(self, tube):
        return {"on_scale": (+on_scale(tube),)}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"tube {tube + 1}: place on scale")
        rt.step(_progress_pct(self), level="progress")
        rcp["scale"].place("place", gravity_offset=4, soft_approach=True)
        return "on_scale"


class Weigh(Action):
    """Read the settled weight — a PURE device read, no robot motion.

    Asserts ``weighed(tube)`` only on a valid reading. On a failed read
    (``weight()`` returns None — scale offline) it returns ``False`` so
    the leaf FAILS, no effect is applied, and the planner re-selects this
    action after the device recovers. The retry is declarative — see this
    module's docstring."""
    params   = ["tube"]
    duration = 3
    resource = "robot"        # held inline with the arm; the read is ~instant

    def pre(self, tube):
        return on_scale(tube) & ~weighed(tube)

    def eff(self, tube):
        return {"weighed": (+weighed(tube),)}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(_progress_pct(self), level="progress")
        # Settled reading. ``sim_return`` (device-guide §17) injects the
        # sim weight explicitly — a distinct fake gram value per tube so a
        # sim run exercises per-tube logic. On the real balance the
        # argument is ignored and the actual weight is read.
        grams = rcp["scale"].weight(sim_return=10.0 + tube)
        if grams is None:
            # Read failed (scale offline). Do NOT assert weighed(tube):
            # FAIL the leaf so the engine replans and re-selects Weigh once
            # the device is back. The tube stays on the pan (on_scale holds)
            # so no motion is repeated — only the read is retried.
            rt.step(f"tube {tube + 1}: weight unavailable — will retry after recover")
            return False
        rt.step(f"tube {tube + 1}: weight = {grams} g")
        return "weighed"


class PickFromScale(Action):
    """Re-grip the weighed tube and lift it off the balance pan."""
    params   = ["tube"]
    duration = 10
    resource = "robot"
    tool     = "gripper"

    def pre(self, tube):
        return weighed(tube) & ~off_scale(tube)

    def eff(self, tube):
        return {"off_scale": (+off_scale(tube),)}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"tube {tube + 1}: pick off scale")
        rt.step(_progress_pct(self), level="progress")
        rcp["scale"].pick("place", soft_approach=True)
        return "off_scale"


class Place(Action):
    """Return the tube to its rack slot."""
    params   = ["tube"]
    duration = 10
    resource = "robot"
    tool     = "gripper"

    def pre(self, tube):
        return off_scale(tube) & ~placed(tube)

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
