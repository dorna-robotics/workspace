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


# Pipetting parameters
SOURCE_ANCHOR  = "A1"
DEST_ANCHORS   = ["A2", "A3", "A4", "A5"]   # one per transfer index
TIP_ANCHORS    = ["A1", "A2", "A3", "A4"]   # fresh tip per transfer
IMMERSE_DEPTH  = 20    # mm below the tube top
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


class Transfer(Action):
    """One pipette transfer: pick tip → aspirate from source → dispense in dest → eject tip.

    Same pattern as the pipetting block in
    ``projects_old/printer/workflow.py``.
    """

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

        tip_slot  = TIP_ANCHORS[transfer]
        dest_slot = DEST_ANCHORS[transfer]

        rt.step(f"transfer {transfer + 1}: tip {tip_slot}, {SOURCE_ANCHOR} → {dest_slot}")
        rt.step(_progress_pct(self), level="progress")

        # 1. Pick a fresh tip onto the pipettor.
        rcp["tip_rack"].pick_tip(tip_slot)

        # 2. Aspirate from the source tube.
        rcp["falcon_pipette"].immerse(anchor=SOURCE_ANCHOR, depth=IMMERSE_DEPTH)
        rcp["falcon_pipette"].aspirate(vol=VOL_UL)
        rcp["falcon_pipette"].retract(anchor=SOURCE_ANCHOR)

        # 3. Dispense into the destination tube.
        rcp["falcon_pipette"].immerse(anchor=dest_slot, depth=IMMERSE_DEPTH)
        rcp["falcon_pipette"].dispense(vol=VOL_UL)
        rcp["falcon_pipette"].retract(anchor=dest_slot)

        # 4. Drop the used tip into the waste bin.
        rcp["waste_bin"].eject_tip()

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
    PARK_JOINTS = [0, 185, -94, 0, 0, 0, 100]

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
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes
        rcp["gripper"].park(joint=self.PARK_JOINTS, has_motion_plan=True)
        rt.motor(0)
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — fires on the Park button, outside the plan."""
    trigger = "park"
