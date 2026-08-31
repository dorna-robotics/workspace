"""pipetting example — Start → Transfer(t) × N → Park.

Each transfer t:
  1. pick fresh tip from tip_rack[A{t+1}]
  2. immerse pipette into source tube (A1)        + aspirate
  3. retract
  4. immerse pipette into destination tube (A{t+2}) + dispense
  5. retract
  6. eject tip into waste bin

Source is fixed (A1). Destinations walk A2, A3, A4, A5.
"""

from __future__ import annotations

from workspace.bt import Action, predicate


started     = predicate("started")
parked      = predicate("parked")
transferred = predicate("transferred")


# Component names — actions look up slot lists on these at runtime
# instead of hardcoding anchor lists. Each rack class populates
# ``slot["body"]`` with its row × col grid in iteration order.
FALCON_RACK    = "rack_falcon"
TIP_RACK       = "rack_tip"

# Pipetting parameters
IMMERSE_DIST  = 20    # mm below the tube top
VOL_UL         = 400   # microliters


def setup(**kwargs):
    transfer_count = int(kwargs.get("transfer_count", 1))
    transfers = list(range(transfer_count))

    facts: set = set()

    def item_done(state, t):
        return (transferred.name, t) in state

    def goal(state):
        return (
            (started.name,) in state
            and all(item_done(state, t) for t in transfers)
            and (parked.name,) in state
        )

    goal_facts = frozenset(
        [(transferred.name, t) for t in transfers]
        + [(started.name,), (parked.name,)]
    )

    return {
        "initial_facts": frozenset(facts),
        "goal":          goal,
        "item_done":     item_done,
        "goal_facts":    goal_facts,
        "objects":       {"transfer": transfers},
    }


def _progress_pct(action) -> int:
    """Monotonic progress: count completed transfers + 1 for current."""
    transfers = action._ctx_all_objects().get("transfer", [])
    total = len(transfers) or 1
    ctx_state = getattr(action.ctx, "state", None) or {}
    facts = ctx_state.get("facts") or set()
    done = sum(1 for t in transfers if (transferred.name, t) in facts) + 1
    return int(done / total * 100)


class Start(Action):
    params   = []
    duration = 5
    resource = "robot"
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


class Transfer(Action):
    """One pipette transfer: pick tip → aspirate from source → dispense in dest → eject tip."""

    params    = ["transfer"]
    duration  = 25
    resource  = "robot"
    tool      = "gripper"

    def pre(self, transfer):
        return started() & ~transferred(transfer)

    def eff(self, transfer):
        return {"transferred": (+transferred(transfer),)}

    def execute(self, transfer):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes

        # Read slot lists from the components — source is the
        # falcon rack's first slot (A1); destination walks the rest.
        # Tip per transfer is the next free slot on the tip rack.
        falcon_slots = self.ctx.workspace.components[FALCON_RACK].slot["body"]
        tip_slots    = self.ctx.workspace.components[TIP_RACK].slot["body"]
        source_anchor = falcon_slots[0]
        dest_anchor   = falcon_slots[transfer + 1]
        tip_anchor    = tip_slots[transfer]

        rt.step(f"transfer {transfer + 1}: tip {tip_anchor}, {source_anchor} → {dest_anchor}")
        rt.step(_progress_pct(self), level="progress")

        # Pump ops follow declarative retry (project-guide §8): each
        # returns False when the pump refused or is unreachable — we
        # return False, no effects apply, and the planner re-selects
        # this transfer. The pipettor device's bus state (critical)
        # pauses the run for the operator meanwhile. The needle ALWAYS
        # retracts before a failure returns — never leave it immersed.

        # 1. Pick a fresh tip onto the pipettor (verified by the tip sensor).
        if not rcp["tip_rack"].pick_tip(tip_anchor):
            rt.step(f"transfer {transfer + 1}: no tip detected — check the pipettor, then Resume")
            return False

        # 2. Aspirate from the source tube.
        rcp["falcon_pipette"].above(anchor=source_anchor)
        rcp["falcon_pipette"].immerse(dist=IMMERSE_DIST, anchor=source_anchor)
        ok = rcp["falcon_pipette"].aspirate(vol=VOL_UL)
        rcp["falcon_pipette"].retract(dist=50, anchor=source_anchor)
        if not ok:
            rt.step(f"transfer {transfer + 1}: pump unavailable — reconnect it, then Resume")
            return False

        # 3. Dispense into the destination tube.
        rcp["falcon_pipette"].above(anchor=dest_anchor)
        rcp["falcon_pipette"].immerse(dist=IMMERSE_DIST, anchor=dest_anchor)
        ok = rcp["falcon_pipette"].dispense(vol=VOL_UL)
        rcp["falcon_pipette"].retract(dist=50, anchor=dest_anchor)
        if not ok:
            rt.step(f"transfer {transfer + 1}: pump unavailable — reconnect it, then Resume")
            return False

        # 4. Drop the used tip into the waste bin (verified tip-gone).
        if not rcp["waste_bin"].eject_tip():
            rt.step(f"transfer {transfer + 1}: tip still on — check the pipettor, then Resume")
            return False

        return "transferred"


class Park(Action):
    """Final park — planned by PDDL after every transfer is done.

    Subclass and set ``trigger = "park"`` to reuse the same motion
    as an operator-initiated cleanup (see ``OperatorPark``).
    """
    params      = []
    duration    = 5
    resource    = "robot"
    tool        = None
    PARK_JOINTS = [0, 90, 0, 0, 0, 0, 100]

    def pre(self):
        transfers = self._ctx_all_objects().get("transfer", [])
        if not transfers:
            return ~parked()
        expr = ~parked()
        for t in transfers:
            expr = expr & transferred(t)
        return expr

    def eff(self):
        return {"parked": (+parked(),)}

    def execute(self):
        rcp = self.ctx.recipes
        rcp["robot"].park(joint=self.PARK_JOINTS)
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — fires on the Park button, outside the plan."""
    trigger = "park"
