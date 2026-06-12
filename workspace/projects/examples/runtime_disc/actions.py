"""runtime_disc protocol — dynamic components done the documented way.

The teaching point: a component created at runtime must be paired with
the PDDL fact that describes it. Scene topology and planner state are
**separate concerns** — the framework never infers one from the other
(docs/component-guide.md §9, docs/bt-framework-guide.md §9).

Inside a BT action that pairing is:

    execute()  →  scene side   — workspace.add_component(...)
    eff()      →  planner side — the location fact the new object gets

The framework applies ``eff`` after ``execute`` succeeds, so declaring
``at_in(d)`` in ``SpawnDiscs.eff`` *is* the explicit fact mutation — no
separate ``add_fact`` call is needed from inside the action. (Use
``workspace.add_fact`` only when mutating outside an action, e.g. an
operator-recovery hook — same rule, no eff to carry it.) The symmetric
removal is ``workspace.remove_component`` + the eff dropping the fact
(``-at_out(d)``); see the note on ``Transfer`` and the README.

Flow:

    Start          motor on + park (canonical)
    SpawnDiscs     add 14 discs at runtime; eff places at_in(d)  (CREATE)
    Transfer(d)    pick from in slot → place in paired out slot;
                   eff:  -at_in(d) +at_out(d)                    (MOVE)
    Park           motor off (canonical)

    in_1[A_k] → out_1[A_k]  (discs 0..6) ; in_2[A_k] → out_2[A_k] (7..13)
"""

from __future__ import annotations

from workspace.bt import Action, predicate


started = predicate("started")
spawned = predicate("spawned")
at_in   = predicate("at_in")    # disc d sits in its IN holder slot
at_out  = predicate("at_out")   # disc d sits in its OUT holder slot
parked  = predicate("parked")


# 14 discs: 0..6 → holder 1 (in_1 / out_1), 7..13 → holder 2.
DISC_COUNT = 14


def _holder(d):
    """Holder number (1 or 2) a disc belongs to."""
    return 1 if d < 7 else 2


def _slot(d):
    """Slot anchor (A1..A7) a disc occupies in its holder."""
    return f"A{(d % 7) + 1}"


def _disc_name(d):
    """Scene component name for disc index ``d``."""
    return f"disc_{d}"


def _progress_pct(action):
    """Monotonic % from discs already at their OUT slot.

    Reads the live fact set (``action.ctx.state["facts"]``); ``action.state``
    is only populated during planning (pre/eff) and is None in execute.
    This action's eff hasn't applied yet, so count it as +1.
    """
    discs = action._ctx_all_objects().get("disc", [])
    total = len(discs) or 1
    ctx_state = getattr(action.ctx, "state", None) or {}
    facts = ctx_state.get("facts") or set()
    done = sum(1 for d in discs if (at_out.name, d) in facts)
    return int((done + 1) / total * 100)


def setup(**kwargs):
    # The discs are planning objects, but they have NO location facts at
    # plan time — they don't exist in the scene yet. SpawnDiscs creates
    # them and declares at_in(d), which the planner foresees (it's a
    # declared effect), so it can schedule the transfers.
    discs = list(range(DISC_COUNT))

    def item_done(state, disc):
        return (at_out.name, disc) in state

    def goal(state):
        return (
            (started.name,) in state
            and (spawned.name,) in state
            and all(item_done(state, d) for d in discs)
            and (parked.name,) in state
        )

    goal_facts = frozenset(
        [(at_out.name, d) for d in discs]
        + [(started.name,), (spawned.name,), (parked.name,)]
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
    """Create the 14 discs in the scene at runtime — the CREATE demo.

    Scene side (``execute``): ``workspace.add_component`` per disc, each
    attached to its IN holder slot. ``cfg`` is the same dict shape a
    ``scene/*.j2`` entry parses to.

    Planner side (``eff``): ``at_in(d)`` for every disc. This is the
    explicit half of the explicit-mutation rule — the framework applies
    the eff once ``execute`` succeeds, so the planner now knows each disc
    sits in its IN slot and can reason about the transfers. No separate
    ``add_fact`` call: inside an action, the eff *is* the fact mutation.

    No robot — pure scene mutation. ``spawned`` gates it so it runs once
    (a second ``add_component`` of the same name would raise).
    """
    params   = []
    duration = 1
    resource = None
    # tool left unset (default) — not a robot action, no tool swap.

    def pre(self):
        return started() & ~spawned()

    def eff(self):
        discs = self._ctx_all_objects().get("disc", [])
        return {"spawned": tuple([+spawned()] + [+at_in(d) for d in discs])}

    def execute(self):
        ws = self.ctx.workspace
        for d in range(DISC_COUNT):
            ws.add_component(_disc_name(d), {
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
        return "spawned"


class Transfer(Action):
    """Pick one disc from its IN slot, place it in the paired OUT slot.

    The move is purely kinematic — ``pick`` attaches the disc to the
    tool, ``place`` re-attaches it under the OUT holder, so no
    add/remove is needed here; only the location fact changes
    (``-at_in(d) +at_out(d)``). The recipe finds the physical disc by
    walking the kinematic tree from the slot, so the planner object
    (index ``d``) and the scene component (``disc_d``) stay in sync via
    the ``_slot`` / ``_holder`` mapping.

    Suction tool drives deeper on pick (``tool_tcp_z_offset=-10``) and
    presses on release (``gravity_offset=-5``).

    (To instead *consume* a disc — the removal half of the rule — you'd
    ``workspace.remove_component(_disc_name(d))`` in execute and drop the
    fact in eff with ``-at_out(d)``. See the README.)
    """
    params   = ["disc"]
    duration = 10
    resource = "robot"
    tool     = "gripper"

    def pre(self, disc):
        return at_in(disc)

    def eff(self, disc):
        return {"moved": (-at_in(disc), +at_out(disc))}

    def execute(self, disc):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes
        holder, slot = _holder(disc), _slot(disc)

        rt.step(f"disc {disc + 1}/{DISC_COUNT}: in_{holder}[{slot}] → out_{holder}[{slot}]")
        rt.step(_progress_pct(self), level="progress")

        rcp[f"disc_in_{holder}"].pick(slot, tool_tcp_z_offset=-10)
        rcp[f"disc_out_{holder}"].place(slot, gravity_offset=-5)
        return "moved"


class Park(Action):
    """Final park — planned by PDDL after every disc is at its OUT slot.

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
        expr = ~parked() & spawned()
        for d in discs:
            expr = expr & at_out(d)
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
