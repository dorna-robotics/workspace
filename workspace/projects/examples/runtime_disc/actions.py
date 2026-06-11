"""runtime_disc protocol — Start → SpawnDiscs → Transfer(d) ×14 → Park.

Demonstrates RUNTIME scene mutation. ``SpawnDiscs`` creates 14 discs in
the scene programmatically (``workspace.add_component``) — 7 in each of
the two *in* holders, slots A1..A7 — instead of declaring them in the
scene yaml. Each ``Transfer(d)`` then has the robot pick a disc from its
*in* slot and place it in the matching slot of the paired *out* holder:

    in_1[A_k]  →  out_1[A_k]      (discs 0..6)
    in_2[A_k]  →  out_2[A_k]      (discs 7..13)

14 picks total. The suction tool drives further down on pick
(``tool_tcp_z_offset=-10``) and presses on release (``gravity_offset=-5``).

``Start`` / ``Park`` / ``OperatorPark`` stay the canonical shape — all the
project-specific work lives in ``SpawnDiscs`` and ``Transfer``.
"""

from __future__ import annotations

from workspace.bt import Action, predicate


started     = predicate("started")
loaded      = predicate("loaded")
transferred = predicate("transferred")
parked      = predicate("parked")


# 14 discs: 0..6 → holder 1 (in_1 / out_1), 7..13 → holder 2.
DISC_COUNT = 14


def _holder(d):
    """Holder number (1 or 2) a disc belongs to."""
    return 1 if d < 7 else 2


def _slot(d):
    """Slot anchor (A1..A7) a disc occupies in its holder."""
    return f"A{(d % 7) + 1}"


def _progress_pct(action):
    """Monotonic % from completed transfers in the live fact set.

    ``action.ctx.state["facts"]`` is the runtime fact set; ``action.state``
    is only populated during planning (pre/eff) and is None in execute.
    This action's eff hasn't applied yet, so count it as +1.
    """
    discs = action._ctx_all_objects().get("disc", [])
    total = len(discs) or 1
    ctx_state = getattr(action.ctx, "state", None) or {}
    facts = ctx_state.get("facts") or set()
    done = sum(1 for d in discs if (transferred.name, d) in facts)
    return int((done + 1) / total * 100)


def setup(**kwargs):
    discs = list(range(DISC_COUNT))

    def item_done(state, disc):
        return (transferred.name, disc) in state

    def goal(state):
        return (
            (started.name,) in state
            and (loaded.name,) in state
            and all(item_done(state, d) for d in discs)
            and (parked.name,) in state
        )

    goal_facts = frozenset(
        [(transferred.name, d) for d in discs]
        + [(started.name,), (loaded.name,), (parked.name,)]
    )

    return {
        "initial_facts": frozenset(),
        "goal":          goal,
        "item_done":     item_done,
        "goal_facts":    goal_facts,
        "objects":       {"disc": discs},
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


class SpawnDiscs(Action):
    """Create the 14 discs in the scene at runtime.

    No robot — pure scene mutation. Each disc is attached to its *in*
    holder's slot (``workspace.add_component`` with an ``attach`` block,
    the same shape a scene yaml entry would have). The transfers then
    move them; nothing is declared in the yaml.
    """
    params   = []
    duration = 1
    resource = None
    # tool left unset (default) — not a robot action, no tool swap.

    def pre(self):
        return started() & ~loaded()

    def eff(self):
        return {"loaded": (+loaded(),)}

    def execute(self):
        ws = self.ctx.workspace
        for d in range(DISC_COUNT):
            ws.add_component(f"disc_{d}", {
                "type": "disc_22mm",
                "attach": {
                    "parent_name":   f"stack_holder_disc_in_{_holder(d)}",
                    "parent_solid":  "body",
                    "parent_anchor": _slot(d),
                    "child_solid":   "body",
                    "child_anchor":  "center",
                    "offset":        [0, 0, 0, 0, 0, 0],
                },
            })
        return "loaded"


class Transfer(Action):
    """Pick one disc from its *in* slot, place it in the paired *out* slot.

    Suction tool drives deeper on pick (``tool_tcp_z_offset=-10``) and
    presses on release (``gravity_offset=-5``).
    """
    params   = ["disc"]
    duration = 10
    resource = "robot"
    tool     = "gripper"

    def pre(self, disc):
        return loaded() & ~transferred(disc)

    def eff(self, disc):
        return {"transferred": (+transferred(disc),)}

    def execute(self, disc):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes
        holder, slot = _holder(disc), _slot(disc)

        rt.step(f"disc {disc + 1}/{DISC_COUNT}: in_{holder}[{slot}] → out_{holder}[{slot}]")
        rt.step(_progress_pct(self), level="progress")

        rcp[f"disc_in_{holder}"].pick(slot, tool_tcp_z_offset=-10)
        rcp[f"disc_out_{holder}"].place(slot, gravity_offset=-5)
        return "transferred"


class Park(Action):
    """Final park — planned by PDDL after every disc is transferred.

    Subclass and set ``trigger = "park"`` to reuse the same motion as an
    operator-initiated cleanup (see ``OperatorPark``).
    """
    params      = []
    duration    = 5
    resource    = "robot"
    tool        = None
    PARK_JOINTS = [0, 185, -94, 0, 0, 0, 100]

    def pre(self):
        discs = self._ctx_all_objects().get("disc", [])
        expr = ~parked() & loaded()
        for d in discs:
            expr = expr & transferred(d)
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
