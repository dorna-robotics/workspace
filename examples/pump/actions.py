"""pump protocol — Start → Prime → [NeedleDose] ×N → Flush → Park.

The point of this example is the FLUID PATH, not the choreography: one
pump device feeds two nozzles, and each nozzle is nothing more than a
tube on a valve port declared in the scene.

  * NeedleDose (per vial) — the robot dips the CARRIED needle into the
    vial and doses through valve port 3. Motion is the stock
    ``DosingSite`` recipe; no pumping-specific recipe family exists,
    because the needle's fluid API deliberately uses the pipettor's
    method names.
  * Flush (once, at the end) — the FIXED nozzle pushes the barrel out
    through valve port 2 into its own cup. No robot motion: a
    stationary nozzle doses into whatever sits beneath it, which is why
    the scene mounts it over a cup.

Both calls land on the same pump component — the sole owner of the
device id — so the bus shows ONE row, one sim pill, one recovery path.

Declarative retry (project-guide §8): each dose asserts its fact only
when the pump reports success. A pump that goes down mid-run fails the
leaf, the effect is never applied, and the planner re-selects that dose
after recovery — the arm never repeats motion it already did.

The pump is sim by default: volume bookkeeping runs in memory, so a sim
run exercises the same barrel accounting the real drive does.
"""

from __future__ import annotations

from workspace.bt import Action, predicate


started      = predicate("started")
primed       = predicate("primed")        # air flushed out of the path
needle_dosed = predicate("needle_dosed")  # carried nozzle dosed this vial
flushed      = predicate("flushed")       # fixed nozzle emptied the barrel
parked       = predicate("parked")

# One needle, one barrel: the carried nozzle is in at most one vial at a
# time. Consumed when it goes in, restored when it comes out.
needle_free = predicate("needle_free", capacity=True)

RACK = "rack_amber_40ml_1x6_1"
_STEPS = 1                                # per-vial steps for the bar


def _slot(action, vial):
    """Rack slot anchor (A1..A6) for vial index ``vial`` — read from the
    rack so the order matches the scene, not a hardcoded list."""
    return action.ctx.workspace.components[RACK].slot["body"][vial]


def _dose_ul(action) -> float:
    """The run's dose, from the Start form. Operator kwargs live in
    ``ctx.meta["kwargs"]`` — the launcher puts them there; there is no
    ``ctx.kwargs``."""
    return float((action.ctx.meta.get("kwargs") or {}).get("dose_ul", 150.0))


def _progress_pct(action):
    vials = action._ctx_all_objects().get("vial", [])
    total = (len(vials) or 1) * _STEPS
    facts = (getattr(action.ctx, "state", None) or {}).get("facts") or set()
    n = sum((needle_dosed.name, v) in facts for v in vials)
    return int((n + 1) / total * 100)


def setup(**kwargs):
    vials = list(range(int(kwargs.get("batch_size", 3))))

    def item_done(state, vial):
        return (needle_dosed.name, vial) in state

    def goal(state):
        return ((started.name,) in state
                and all(item_done(state, v) for v in vials)
                and (flushed.name,) in state
                and (parked.name,) in state)

    goal_facts = frozenset([(needle_dosed.name, v) for v in vials]
                           + [(started.name,), (flushed.name,), (parked.name,)])

    return {
        "initial_facts": frozenset(),
        "goal":          goal,
        "item_done":     item_done,
        "goal_facts":    goal_facts,
        "objects":       {"vial": vials},
    }


class Start(Action):
    params       = []
    duration     = 5
    resource     = "robot"
    START_JOINTS = [0, 45, -90, 0, -45, 0, 100]

    def pre(self):
        return ~started()

    def eff(self):
        return {"started": (+started(), +needle_free())}

    def execute(self):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        core = self.ctx.workspace.components["core"]
        rt.motor(1)
        if core.has_rail:
            rt.step("homing rail")
            if not rcp["robot"].set_axis_with_stop(core.rail_cfg):
                rt.step("homing failed")
                return "killed"          # fatal: never move on an unhomed rail
        rcp["robot"].park(joint=self.START_JOINTS)
        return "started"


class Prime(Action):
    """Flush air out of the fluid path once, before any dosing.

    Pure device work — no motion — so the honest scheduling lock is the
    pump, not the arm.
    """
    params   = []
    duration = 8
    resource = "pump"

    def pre(self):
        return started() & ~primed()

    def eff(self):
        return {"primed": (+primed(),)}

    def execute(self):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step("priming the fluid path")
        # From the single source (reservoir) out through the flush
        # line into its cup — the needle line stays dry because the
        # needle is parked in the tool rack. Cycle count comes from
        # the tube volumes declared in the scene.
        if rcp["pump"].prime(to_port="flush") is False:
            rt.step("prime failed — will retry after recover")
            return False
        return "primed"


class NeedleDose(Action):
    """Dip the CARRIED needle into the vial and dose through its port."""
    params   = ["vial"]
    duration = 14
    resource = "robot"
    tool     = "needle"

    def pre(self, vial):
        return primed() & needle_free() & ~needle_dosed(vial)

    def eff(self, vial):
        # The needle goes in and comes back out within this action, so
        # the occupancy fact is taken and returned here.
        return {"needle_dosed": (+needle_dosed(vial),)}

    def execute(self, vial):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        slot, ul = _slot(self, vial), _dose_ul(self)
        rt.step(f"vial {vial + 1}: needle dose {ul:g} uL into rack[{slot}]")
        rt.step(_progress_pct(self), level="progress")
        # Draw from the reservoir (a named source in the scene's
        # valve_ports), then push out through the needle's own port —
        # the link binds port 3, so the call site never repeats it.
        if rcp["pump"].aspirate(ul, port="reservoir") is False:
            rt.step(f"vial {vial + 1}: aspirate failed — retry after recover")
            return False
        # Straight dive with clearance, like the pH probe into the same
        # vials: an approach corridor around a 157 mm needle inside a
        # 6-vial rack has nowhere to go.
        rcp["vials"].immerse(anchor=slot, depth=60, approach=False, padding=70)
        ok = rcp["vials"].dispense(ul)
        rcp["vials"].retract(anchor=slot, padding=70)
        if ok is False:
            # Pump refused / offline: no fact, so the planner re-selects
            # this dose once the device is back (declarative retry).
            rt.step(f"vial {vial + 1}: needle dose failed — retry after recover")
            return False
        return "needle_dosed"


class Flush(Action):
    """Empty the barrel through the FIXED nozzle into its cup.

    Runs once, after every vial is dosed — the second fluid path on the
    same pump. No robot motion: the arm is bench furniture, so the
    honest scheduling lock is the pump and the robot stays free.
    """
    params   = []
    duration = 8
    resource = "pump"

    def pre(self):
        expr = started() & ~flushed()
        for v in self._ctx_all_objects().get("vial", []):
            expr = expr & needle_dosed(v)
        return expr

    def eff(self):
        return {"flushed": (+flushed(),)}

    def execute(self):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step("flushing the barrel through the fixed nozzle")
        rcp["arm"].down()
        ok = rcp["arm"].dispense(volume_ul=rcp["pump"].volume())
        rcp["arm"].up()
        if ok is False:
            rt.step("flush failed — will retry after recover")
            return False
        rt.step(f"barrel now holds {rcp['pump'].volume():g} uL")
        return "flushed"


class Park(Action):
    params      = []
    duration    = 5
    resource    = "robot"
    tool        = None
    PARK_JOINTS = [0, 90, 0, 0, 0, 0, 100]

    def pre(self):
        return ~parked() & started() & flushed()

    def eff(self):
        return {"parked": (+parked(),)}

    def execute(self):
        self.ctx.recipes["robot"].park(joint=self.PARK_JOINTS)
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — fires on the Park button, outside the plan."""
    trigger = "park"
