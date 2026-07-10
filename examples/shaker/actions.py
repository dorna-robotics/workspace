"""shaker example — Start → [Load(t) ×2 → Shake → Unload(t) ×2] → Park.

Pick 40 ml amber tubes from the rack, load them onto the 2-slot
shaker, shake, and return them — split into separate BT actions so
the planner's resource management is visible on the Gantt:

  Load(t):   rack slot → shaker slot            (resource: robot)
  Shake:     one mechanical cycle — shakes BOTH  (resource: shaker —
             loaded tubes at once                 NOT the robot; the
                                                  robot lane is free
                                                  while the shaker runs)
  Unload(t): shaker slot → back to rack slot    (resource: robot)

``batch_size`` operator kwarg sets how many tubes to run. The shaker
holds two tubes, so ``plan_window: 2`` slices the batch into pairs:
each slice loads both slots, shakes once, unloads both. Tube ``t``
uses shaker slot A1/A2 by ``t % 2``, so consecutive slices reuse the
slots without ever colliding in time (same transient-slot idea as
bna's ``_ReusableSlots``, small enough here to be a plain list).
"""

from __future__ import annotations

from workspace.bt import Action, predicate


started   = predicate("started")
parked    = predicate("parked")
in_rack   = predicate("in_rack")     # tube waiting in the amber rack
in_shaker = predicate("in_shaker")   # tube sitting in a shaker slot
shaken    = predicate("shaken")      # tube has been shaken
done      = predicate("done")        # tube back in its rack slot


TUBE_RACK = "rack_amber_40ml_2x4_1"

# Transient shaker slots — tube t passes through slot t % 2.
SHAKER_SLOTS = ["A1", "A2"]

SHAKE_DURATION = 10   # seconds per mechanical shake cycle


def _progress_pct(action) -> int:
    """Monotonic progress: 2 steps per tube (shaken, done)."""
    tubes = action._ctx_all_objects().get("tube", [])
    total = (len(tubes) or 1) * 2
    ctx_state = getattr(action.ctx, "state", None) or {}
    facts = ctx_state.get("facts") or set()
    n = sum(1 for t in tubes for p in (shaken, done) if (p.name, t) in facts)
    return int((n + 1) / total * 100)


def setup(**kwargs):
    """kwargs: batch_size (1..8 — the amber rack has 2×4 slots)."""
    batch_size = int(kwargs.get("batch_size", 1))
    tubes = list(range(batch_size))

    # Initial state is *empty* — ``Start.eff`` seeds ``in_rack(t)`` for
    # every tube, which gates the whole plan behind Start without
    # adding ``& started`` to every per-tube pre.
    facts: set = set()

    def item_done(state, tube):
        return (done.name, tube) in state

    def goal(state):
        return (
            (started.name,) in state
            and all(item_done(state, t) for t in tubes)
            and (parked.name,) in state
        )

    # ``shaken(t)`` is added by Shake's state-aware eff, so
    # auto-derivation can't see it — list the per-tube progress
    # markers explicitly (same note as bna's setup).
    goal_facts = frozenset(
        [(p.name, t) for p in (shaken, done) for t in tubes]
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
        # Seed the FULL tube list, not the current slice — later
        # slices need their ``in_rack`` facts too (``_ctx_all_objects``
        # reads the launcher's un-sliced snapshot for exactly this).
        tubes = self._ctx_all_objects().get("tube", [])
        seeds = [+started()]
        for t in tubes:
            seeds.append(+in_rack(t))
        return {"started": tuple(seeds)}

    def execute(self):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes
        rt.motor(1)
        rcp["robot"].park(joint=[0, 45, -90, 0, -45, 0, 100], has_motion_plan=True)
        return "started"


class Load(Action):
    """Pick tube t from the rack and set it into shaker slot t % 2."""

    params    = ["tube"]
    duration  = 20
    resource  = "robot"
    tool      = "gripper"

    def pre(self, tube):
        return in_rack(tube) & ~in_shaker(tube)

    def eff(self, tube):
        return {"loaded": (-in_rack(tube), +in_shaker(tube))}

    def execute(self, tube):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes

        slot = self.ctx.workspace.components[TUBE_RACK].slot["body"][tube]
        shaker_slot = SHAKER_SLOTS[tube % 2]

        rt.step(f"load {tube + 1}: rack {slot} → shaker {shaker_slot}")

        rcp["tube_rack"].pick(slot, soft_approach=True)
        rcp["shaker"].place(shaker_slot, gravity_offset=4)

        return "loaded"


class Shake(Action):
    """One mechanical shake — shakes every tube loaded on the shaker.

    Batched by design: the device shakes both slots in one cycle, so
    this action has no ``tube`` param. ``pre`` requires every tube of
    the current slice to be loaded and unshaken; ``eff`` marks all of
    them shaken (state-aware — only tubes actually ``in_shaker``).

    ``resource = "shaker"`` (not the robot!) is the point of this
    example: on the Gantt the shake occupies the shaker lane while the
    robot lane is free for the scheduler.
    """

    params   = []
    duration = SHAKE_DURATION
    resource = "shaker"

    def _destined_tubes(self):
        # Current slice only — with plan_window 2 that is exactly the
        # pair occupying the two shaker slots.
        return list(self._ctx_objects().get("tube", []))

    def pre(self):
        destined = self._destined_tubes()
        if not destined:
            return False
        if all((shaken.name, t) in self.state for t in destined):
            return False
        expr = in_shaker(destined[0]) & ~shaken(destined[0])
        for t in destined[1:]:
            expr = expr & in_shaker(t) & ~shaken(t)
        return expr

    def eff(self):
        return {"shaken": tuple(
            +shaken(t) for t in self._destined_tubes()
            if (in_shaker.name, t) in self.state
        )}

    def execute(self):
        rt = self.ctx.runtime
        rt.step(f"shake: {SHAKE_DURATION} s cycle")
        rt.step(_progress_pct(self), level="progress")
        self.ctx.recipes["shaker"].shake(duration=SHAKE_DURATION)
        return "shaken"


class Unload(Action):
    """Pick the shaken tube off the shaker and return it to its rack slot."""

    params    = ["tube"]
    duration  = 20
    resource  = "robot"
    tool      = "gripper"

    def pre(self, tube):
        return in_shaker(tube) & shaken(tube)

    def eff(self, tube):
        return {"unloaded": (-in_shaker(tube), +done(tube))}

    def execute(self, tube):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes

        slot = self.ctx.workspace.components[TUBE_RACK].slot["body"][tube]
        shaker_slot = SHAKER_SLOTS[tube % 2]

        rt.step(f"unload {tube + 1}: shaker {shaker_slot} → rack {slot}")
        rt.step(_progress_pct(self), level="progress")

        rcp["shaker"].pick(shaker_slot)
        rcp["tube_rack"].place(slot, gravity_offset=4, soft_approach=True)

        return "unloaded"


class Park(Action):
    """Final park — planned by PDDL after every tube is back in the rack.

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
            expr = expr & done(t)
        return expr

    def eff(self):
        return {"parked": (+parked(),)}

    def execute(self):
        rcp = self.ctx.recipes
        rcp["robot"].park(joint=self.PARK_JOINTS, has_motion_plan=True)
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — fires on the Park button, outside the plan."""
    trigger = "park"
