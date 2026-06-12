"""runtime protocol — Start → Cycle(i) ×batch_size → Park.

Each ``Cycle`` is a full disc lifecycle, done the documented way
(docs/component-guide.md §9, docs/bt-framework-guide.md §9):

    1. CREATE   workspace.add_component — a disc at a RANDOM in-holder
                slot, lifted by a random z1 ∈ [0, 57.15] mm.
    2. TRANSFER pick it, place it at a RANDOM out-holder slot, lifted by
                a random z2 ∈ [0, 57.15] mm.
    3. REMOVE   workspace.remove_component — the disc is consumed.

Only ONE disc exists at a time (created then removed inside the same
action), so the disc is a *transient* scene object, not a persistent
planning object — the planner reasons only about the cycle index. The
single planning fact is ``cycled(i)``, declared in ``eff``.

Why this is bulletproof:

  * **Randomness lives in execute (runtime), never in pre/eff.** The
    plan is always ``Cycle(0..n-1)`` regardless of the dice, so the
    planner and the runtime can never disagree.
  * **Balanced scene mutation.** Every ``add_component`` is matched by a
    ``remove_component`` in the same action; the disc never outlives its
    cycle. ``eff`` only ever ADDS ``cycled(i)`` — so a failure can leave
    a stray *scene* object but never an orphan *fact*.
  * **Idempotent retries.** Each cycle defensively removes a leftover
    ``disc_<i>`` before creating, so a retried cycle starts clean (a
    second ``add_component`` of a live name would otherwise raise).

``Start`` / ``Park`` / ``OperatorPark`` stay the canonical shape.
"""

from __future__ import annotations

import random

from workspace.bt import Action, predicate


started = predicate("started")
cycled  = predicate("cycled")    # cycle i has run (create→transfer→remove)
parked  = predicate("parked")


IN_HOLDERS  = [1, 2]              # in_1, in_2
OUT_HOLDERS = [1, 2, 3]          # out_1, out_2, out_3
SLOTS       = [f"A{c}" for c in range(1, 7 + 1)]   # A1..A7
Z_MAX       = 57.15              # max lift (mm) for the spawn / place offset


def _progress_pct(action):
    """Monotonic % from completed cycles in the live fact set.

    Reads ``action.ctx.state["facts"]``; ``action.state`` is None inside
    execute. This cycle's eff hasn't applied yet, so count it as +1.
    """
    cycles = action._ctx_all_objects().get("cycle", [])
    total = len(cycles) or 1
    ctx_state = getattr(action.ctx, "state", None) or {}
    facts = ctx_state.get("facts") or set()
    done = sum(1 for c in cycles if (cycled.name, c) in facts)
    return int((done + 1) / total * 100)


def setup(**kwargs):
    batch_size = int(kwargs.get("batch_size", 5))
    cycles = list(range(batch_size))

    def item_done(state, cycle):
        return (cycled.name, cycle) in state

    def goal(state):
        return (
            (started.name,) in state
            and all(item_done(state, c) for c in cycles)
            and (parked.name,) in state
        )

    goal_facts = frozenset(
        [(cycled.name, c) for c in cycles]
        + [(started.name,), (parked.name,)]
    )

    return {
        "initial_facts": frozenset(),
        "goal":          goal,
        "item_done":     item_done,
        "goal_facts":    goal_facts,
        "objects":       {"cycle": cycles},
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


class Cycle(Action):
    """One disc lifecycle: create (random in + z1) → transfer → remove.

    See the module docstring for why randomness-in-execute +
    balanced-create/remove + defensive-cleanup make this bulletproof.
    """
    params   = ["cycle"]
    duration = 12
    resource = "robot"
    tool     = "gripper"

    def pre(self, cycle):
        return started() & ~cycled(cycle)

    def eff(self, cycle):
        return {"cycled": (+cycled(cycle),)}

    def execute(self, cycle):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes
        ws  = self.ctx.workspace
        name = f"disc_{cycle}"

        # Idempotent retry: clear any leftover from a failed prior
        # attempt so add_component starts from a clean slate.
        if name in ws.components:
            ws.remove_component(name)

        # Roll this cycle's random targets + lifts (runtime only — the
        # plan never depends on these).
        in_h, in_s, z1 = random.choice(IN_HOLDERS),  random.choice(SLOTS), round(random.uniform(0, Z_MAX), 2)
        out_h, out_s, z2 = random.choice(OUT_HOLDERS), random.choice(SLOTS), round(random.uniform(0, Z_MAX), 2)

        total = len(self._ctx_all_objects().get("cycle", [])) or 1
        rt.step(f"cycle {cycle + 1}/{total}: in_{in_h}[{in_s}]+{z1} → out_{out_h}[{out_s}]+{z2}")
        rt.step(_progress_pct(self), level="progress")

        # 1. CREATE — spawn the disc at the random in slot, lifted z1.
        #    cfg is the same dict shape a scene yaml entry parses to.
        ws.add_component(name, {
            "type": "disc_22mm",
            "attach": {
                "parent_name":   f"stack_holder_disc_in_{in_h}",
                "parent_solid":  "body",
                "parent_anchor": in_s,
                "child_solid":   "body",
                "child_anchor":  "center",
                "offset":        [0, 0, z1, 0, 0, 0],
            },
        })

        # 2. TRANSFER — pick it (suction drives deeper), place at the
        #    random out slot lifted z2 (suction presses on release). The
        #    pick finds the disc by walking the tree, so the z1 lift is
        #    honoured automatically.
        rcp[f"disc_in_{in_h}"].pick(in_s, tool_tcp_z_offset=-10)
        rcp[f"disc_out_{out_h}"].place(out_s, offset=[0, 0, z2, 0, 0, 0], gravity_offset=-5)

        # 3. REMOVE — the disc is consumed; drop it from the scene. No
        #    fact to clean: the disc never had a persistent one.
        ws.remove_component(name)

        return "cycled"


class Park(Action):
    """Final park — planned by PDDL after every cycle has run.

    Subclass and set ``trigger = "park"`` to reuse the same motion as an
    operator-initiated cleanup (see ``OperatorPark``).
    """
    params      = []
    duration    = 5
    resource    = "robot"
    tool        = None
    PARK_JOINTS = [0, 185, -94, 0, 0, 0, 100]

    def pre(self):
        cycles = self._ctx_all_objects().get("cycle", [])
        expr = ~parked() & started()
        for c in cycles:
            expr = expr & cycled(c)
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
