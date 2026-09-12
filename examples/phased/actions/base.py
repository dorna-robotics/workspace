"""Shared action bodies — the motion, written once.

Every station visit here is an ABSTRACT base parameterised by
``PASS``. The phase modules subclass it in one line (``PASS``), so pass
2 is the same code as pass 1 under pass-indexed facts. A base is never
planned: what runs is what a phase's ``route`` lists (phases.py), and
no base is listed. A base is only ever created on its SECOND use: one
concrete action in one phase stays a plain class in that phase's
module — the shaker's three (phase_2.py) are exactly that.

Helpers that several actions share (the rack slot lookup, the shaker
seat and bank, the progress figure) live here too, as plain functions.
"""

from workspace.bt import Action

from actions.predicates import (N_SEATS, PASSES, hand_empty, home, off_scale,
                                on_scale, on_shaker, pan_empty, picked, shaken,
                                started, unloaded, weighed)

RACK   = "rack_amber_40ml_2x4_1"
SHAKER = "shaker_4slot_1"

# The rack: 2 × 4 slots (launch.yaml batch_size.max). Two banks of
# N_SEATS — the pass-2 window holds both so the scheduler can overlap
# them (phases.py, Weighed2).
MAX_BATCH = 8

# Per-tube steps: 5 per weigh pass, 3 through the shaker — what the
# progress bar spans.
_STEPS_PER_PASS = 5
_STEPS_SHAKER = 3


def slot_of(action, tube):
    """Rack slot anchor (A1..B4) for tube index ``tube`` — read from the
    rack component so the order matches the scene, not a hardcoded list."""
    return action.ctx.workspace.components[RACK].slot["body"][tube]


def seat_of(action, tube):
    """Shaker seat anchor (A1..A4) for ``tube`` — static, ``tube % N_SEATS``,
    read from the shaker's ``rotating`` solid like the rack slots."""
    return action.ctx.workspace.components[SHAKER].slot["rotating"][tube % N_SEATS]


def bank_of(action, tube):
    """The tubes that share a shaker load with ``tube`` — those of the
    window in its bank (``tube // N_SEATS``). The same static rule as
    ``phases.banks``, so planner, replay and bench agree whatever the
    order the window came in."""
    k = tube // N_SEATS
    window = action._ctx_objects().get("tube", [])
    return [t for t in window if t // N_SEATS == k] or [tube]


def item_id(tube):
    """The identity a row is recorded under (project-guide §3 rt.record).
    A real project uses the sample's own id — an L-number from the
    manifest — never its position."""
    return f"tube {tube + 1}"


def progress_pct(action):
    """Monotonic % over every per-tube step of every pass and of the
    shake. Reads the live fact set; this action's eff has not applied
    yet, so count it +1."""
    tubes = action._ctx_all_objects().get("tube", [])
    total = (len(tubes) or 1) * (_STEPS_PER_PASS * len(PASSES) + _STEPS_SHAKER)
    facts = (getattr(action.ctx, "state", None) or {}).get("facts") or set()
    done = sum(
        ((pred[p].name, t) in facts)
        for t in tubes for p in PASSES
        for pred in (picked, on_scale, weighed, off_scale, home)
    ) + sum(
        ((pred.name, t) in facts)
        for t in tubes for pred in (on_shaker, shaken, unloaded)
    )
    return int((done + 1) / total * 100)


class PassAction(Action):
    """Base of every per-tube weigh action: one param, pass-indexed facts.

    ``gate`` is what lets the pass start on this tube — pass 1 needs the
    run started, pass 2 needs the tube back from the shaker. That single
    hook is the whole difference between the passes.
    """
    PASS: int = 0
    params = ["tube"]
    resource = "robot"
    tool = "gripper"

    def gate(self, tube):
        return started() if self.PASS == 1 else unloaded(tube)

    def tag(self, tube):
        return f"tube {tube + 1} pass {self.PASS}: "


class PickBase(PassAction):
    """Lift the tube out of its rack slot."""
    duration = 10

    def pre(self, tube):
        # hand_empty gates one-at-a-time: no pick while holding a tube.
        return self.gate(tube) & hand_empty() & ~picked[self.PASS](tube)

    def eff(self, tube):
        return {"picked": (+picked[self.PASS](tube), -hand_empty())}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        slot = slot_of(self, tube)
        rt.step(f"{self.tag(tube)}pick from rack[{slot}]")
        rt.step(progress_pct(self), level="progress")
        rcp["tube_rack"].pick(slot, soft_approach=True)
        return "picked"


class PlaceOnScaleBase(PassAction):
    """Stand the held tube on the balance top and let go."""
    duration = 10

    def pre(self, tube):
        return picked[self.PASS](tube) & pan_empty() & ~on_scale[self.PASS](tube)

    def eff(self, tube):
        return {"on_scale": (+on_scale[self.PASS](tube), +hand_empty(), -pan_empty())}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"{self.tag(tube)}place on scale")
        rt.step(progress_pct(self), level="progress")
        rcp["scale_holder"].place("place", gravity_offset=4, soft_approach=True)
        return "on_scale"


class WeighBase(PassAction):
    """Read the settled mass — a pure device read, its own action so a
    failed reading is retried without redoing an arm move (examples/scale
    is the reference for that pattern). Writes the tube's audit row
    where the value is produced."""
    duration = 3
    resource = "scale"    # the top is busy, the arm is free
    tool = None

    def pre(self, tube):
        return on_scale[self.PASS](tube) & ~weighed[self.PASS](tube)

    def eff(self, tube):
        return {"weighed": (+weighed[self.PASS](tube),)}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(progress_pct(self), level="progress")
        grams = rcp["scale"].weight(sim_return=10.0 + tube + 0.1 * self.PASS)
        if grams is None:
            rt.step(f"{self.tag(tube)}weight unavailable — will retry after recover")
            return False
        rt.step(f"{self.tag(tube)}weight = {grams} g")
        rt.record(item_id(tube), **{f"weight_{self.PASS}_g": grams})
        return "weighed"


class PickFromScaleBase(PassAction):
    """Re-grip the weighed tube and lift it off the top."""
    duration = 10

    def pre(self, tube):
        return weighed[self.PASS](tube) & hand_empty() & ~off_scale[self.PASS](tube)

    def eff(self, tube):
        return {"off_scale": (+off_scale[self.PASS](tube), -hand_empty(), +pan_empty())}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"{self.tag(tube)}pick off scale")
        rt.step(progress_pct(self), level="progress")
        rcp["scale_holder"].pick("place", soft_approach=True)
        return "off_scale"


class ReturnBase(PassAction):
    """Back into its own slot — the phase fact for this pass."""
    duration = 10

    def pre(self, tube):
        return off_scale[self.PASS](tube) & ~home[self.PASS](tube)

    def eff(self, tube):
        return {"home": (+home[self.PASS](tube), +hand_empty())}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        slot = slot_of(self, tube)
        rt.step(f"{self.tag(tube)}place back to rack[{slot}]")
        rt.step(progress_pct(self), level="progress")
        rcp["tube_rack"].place(slot, gravity_offset=4, soft_approach=True)
        return "home"
