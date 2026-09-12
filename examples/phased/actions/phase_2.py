"""Phase 2 — ``weighed_2``: shaken in banks of four, then weighed again.

Read top to bottom, this file IS the phase: the concrete actions in the
order a tube meets them — the same order ``phases.Weighed2.route``
declares. First the shaker — three actions used once, so they are
plain classes here, no base (a base exists only on a second user).
Then the same five stations as pass 1 as thin subclasses of the bases
in ``base.py`` — the ``gate`` in ``PassAction`` makes each tube wait
for its own ``unloaded``, and ``phases.Weighed2.pre`` makes the whole
batch wait for everyone's ``home_1``.

The shake couples four tubes — one head, one clamp, one cycle: its
``pre`` spans the bank, so the route lookup simply waits until the
bank is seated. The shake and the second weighing are ONE phase so
the scheduler overlaps the banks: bank 2 shakes while bank 1 is
weighed. Boundary at exit: doc/phases.md row ``weighed_2``.
"""

from workspace.bt import Action

from actions.base import (PickBase, PickFromScaleBase, PlaceOnScaleBase,
                          ReturnBase, WeighBase, bank_of, progress_pct,
                          seat_of, slot_of)
from actions.predicates import (N_SEATS, hand_empty, home, on_shaker, seat_free,
                                shaken, unloaded)

SHAKE_S = 30    # one mechanical cycle — a real protocol shakes minutes


class Load(Action):
    """Rack slot → the tube's shaker seat. One action for the two moves:
    nothing is mid-carry at a boundary, and no boundary falls between
    them. The seat is a capacity fact: a tube of the next bank cannot
    take it before this one is unloaded. ``~unloaded``: a tube that is
    back from the shaker is behind this step for good — the route
    lookup needs every step's pre to say so."""
    params   = ["tube"]
    duration = 20
    resource = "robot"
    tool     = "gripper"

    def pre(self, tube):
        return (home[1](tube) & hand_empty() & seat_free[tube % N_SEATS]()
                & ~on_shaker(tube) & ~unloaded(tube))

    def eff(self, tube):
        return {"loaded": (+on_shaker(tube), -seat_free[tube % N_SEATS]())}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        slot, seat = slot_of(self, tube), seat_of(self, tube)
        rt.step(f"tube {tube + 1}: rack[{slot}] → shaker {seat}")
        rt.step(progress_pct(self), level="progress")
        rcp["tube_rack"].pick(slot, soft_approach=True)
        rcp["shaker"].place(seat, gravity_offset=4)
        return "loaded"


class Shake(Action):
    """One shake for this tube's BANK — every tube of its load.

    Parameterised by a tube (four identical params-free actions cannot
    be told apart by the scheduler), the bank is static (``tube // N_SEATS``)
    so planner and replay agree whatever the order. Holds the shaker,
    NOT the robot: the arm keeps working through the cycle — on the
    Gantt the shake sits on the shaker lane while the robot lane weighs
    the previous bank."""
    params   = ["tube"]
    duration = SHAKE_S
    resource = "shaker"

    def pre(self, tube):
        expr = ~shaken(tube)
        for t in bank_of(self, tube):
            expr = expr & on_shaker(t)
        return expr

    def eff(self, tube):
        return {"shaken": tuple(+shaken(t) for t in bank_of(self, tube))}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"shake {len(bank_of(self, tube))} tube(s) for {SHAKE_S} s")
        rt.step(progress_pct(self), level="progress")
        rcp["shaker"].shake(duration=SHAKE_S)
        return "shaken"


class Unload(Action):
    """Shaker seat → its own rack slot: ``unloaded``, pass 2's gate, and
    the seat is free for the next bank."""
    params   = ["tube"]
    duration = 20
    resource = "robot"
    tool     = "gripper"

    def pre(self, tube):
        return shaken(tube) & hand_empty() & ~unloaded(tube)

    def eff(self, tube):
        return {"unloaded": (+unloaded(tube), -on_shaker(tube), +seat_free[tube % N_SEATS]())}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        slot, seat = slot_of(self, tube), seat_of(self, tube)
        rt.step(f"tube {tube + 1}: shaker {seat} → rack[{slot}]")
        rt.step(progress_pct(self), level="progress")
        rcp["shaker"].pick(seat)
        rcp["tube_rack"].place(slot, gravity_offset=4, soft_approach=True)
        return "unloaded"


class Pick2(PickBase):
    PASS = 2


class PlaceOnScale2(PlaceOnScaleBase):
    PASS = 2


class Weigh2(WeighBase):
    PASS = 2


class PickFromScale2(PickFromScaleBase):
    PASS = 2


class Return2(ReturnBase):
    PASS = 2
