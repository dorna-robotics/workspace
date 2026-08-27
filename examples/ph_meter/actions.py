"""ph_meter protocol — Start → [Immerse → ReadPH → Retract → Rinse] ×N → Park.

For each of ``batch_size`` amber 40 ml vials (open, in the 1x6 rack),
the robot — carrying the Atlas pH probe from the tool rack:
  1. Immerse — dip the probe into the vial (slot anchor A1..A6)
  2. ReadPH  — wait for a settled reading (NO motion — a pure device read)
  3. Retract — lift the probe clear of the vial
  4. Rinse   — dip into the storage-solution cup and lift out, so the
               probe never travels dry between samples

Each is its own BT action, gated by facts so they run in order per
vial; the planner sequences vials (windowed by plan_window). Vials are
the single objects dim → slicing auto-engages.

WHY ReadPH is split out as its own read-only action — declarative retry
=======================================================================
Same reference pattern as examples/scale's Weigh (project-guide §8
"Device reads + declarative retry"): ``ReadPH`` asserts
``measured(vial)`` ONLY when it gets a valid number. On a failed read
it ``return False`` → the leaf FAILS → no effect applies → the engine
replans from the observed world, where ``immersed(vial) &
~measured(vial)`` still holds → the planner re-selects ReadPH. The
probe stays in the liquid (no motion repeated) — only the read
retries.
Meanwhile a real, ``critical`` EZO going down pauses the runtime until
it recovers; the retry then runs against the reconnected circuit.

The probe runs in sim by default → ``ph()`` returns the canned value
(injected per-vial via ``sim_return`` so a sim run exercises per-vial
logic). Point ph_meter_atlas_1 at the real circuit (port +
simulation:false in the layout) for live readings.

Pattern reference: examples/scale/actions.py. Probe/site API:
workspace/recipes/ph_meter.py — immerse/retract + ph/read_stable
through the mounted tool.
"""

from __future__ import annotations

from workspace.bt import Action, predicate


started  = predicate("started")
immersed = predicate("immersed")   # probe is in this vial's liquid
measured = predicate("measured")   # a valid pH was read for this vial
lifted   = predicate("lifted")     # probe clear of this vial
rinsed   = predicate("rinsed")     # probe dipped in storage after this vial

parked   = predicate("parked")

# ── Single-occupancy resource (capacity-1, no args) ───────────────────
# One probe: it is in at most one vessel at a time. Consumed on Immerse,
# restored by Rinse (the storage dip closes each vial's cycle) — forcing
# strictly one-vial-at-a-time through dip/read/lift/rinse. See
# project-guide §8 "Single-occupancy resources".
probe_free = predicate("probe_free", capacity=True)

RACK = "rack_amber_40ml_1x6_1"

# How many per-vial steps the progress bar spans
# (Immerse, ReadPH, Retract, Rinse).
_STEPS = 4


def _slot(action, vial):
    """Rack slot anchor (A1..A6) for vial index ``vial`` — read from the
    rack component so the order matches the scene, not a hardcoded list."""
    return action.ctx.workspace.components[RACK].slot["body"][vial]


def _progress_pct(action):
    """Monotonic % over all per-cup steps. Reads the live fact set
    (``action.ctx.state["facts"]``; ``action.state`` is None in execute).
    This action's eff hasn't applied yet, so count it as +1."""
    vials = action._ctx_all_objects().get("vial", [])
    total = (len(vials) or 1) * _STEPS
    ctx_state = getattr(action.ctx, "state", None) or {}
    facts = ctx_state.get("facts") or set()
    n = sum(
        ((immersed.name, v) in facts) + ((measured.name, v) in facts)
        + ((lifted.name, v) in facts) + ((rinsed.name, v) in facts)
        for v in vials
    )
    return int((n + 1) / total * 100)


def setup(**kwargs):
    vials = list(range(int(kwargs.get("batch_size", 3))))

    def item_done(state, vial):
        return (rinsed.name, vial) in state

    def goal(state):
        return (
            (started.name,) in state
            and all(item_done(state, v) for v in vials)
            and (parked.name,) in state
        )

    goal_facts = frozenset(
        [(rinsed.name, v) for v in vials]
        + [(started.name,), (parked.name,)]
    )

    return {
        "initial_facts": frozenset(),
        "goal":          goal,
        "item_done":     item_done,
        "goal_facts":    goal_facts,
        "objects":       {"vial": vials},
    }


class Start(Action):
    params      = []
    duration    = 5
    resource    = "robot"
    START_JOINTS = [0, 45, -90, 0, -45, 0, 100]

    def pre(self):
        return ~started()

    def eff(self):
        # Seed the single-occupancy resource: the probe starts free.
        return {"started": (+started(), +probe_free())}

    def execute(self):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes
        ws  = self.ctx.workspace
        core = ws.components["core"]
        rt.motor(1)
        # Home the rail before any move that assumes a homed axis. A
        # homing failure is FATAL: the reserved "killed" outcome kills
        # the runtime on the spot — no motion on an unhomed rail.
        if core.has_rail:
            rt.step("homing rail")
            if not rcp["robot"].set_axis_with_stop(core.rail_cfg):
                rt.step("homing failed")
                return "killed"
        rcp["robot"].park(joint=self.START_JOINTS)
        return "started"


class Immerse(Action):
    """Dip the probe into the vial — deep enough to submerge the glass
    bulb (a dry electrode reads garbage)."""
    params   = ["vial"]
    duration = 10
    resource = "robot"
    tool     = "probe"

    def pre(self, vial):
        # probe_free gates one-at-a-time: the probe is in at most one vessel.
        return started() & probe_free() & ~immersed(vial)

    def eff(self, vial):
        return {"immersed": (+immersed(vial), -probe_free())}   # probe now busy

    def execute(self, vial):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        slot = _slot(self, vial)
        rt.step(f"vial {vial + 1}: immerse probe in rack[{slot}]")
        rt.step(_progress_pct(self), level="progress")
        # The vial mouth is the slot anchor; dive far enough to submerge
        # the bulb inside the 40 ml vial.
        rcp["vials"].immerse(anchor=slot, dist=60)
        return "immersed"


class ReadPH(Action):
    """Wait for a settled pH — a PURE device read, no robot motion.

    Asserts ``measured(cup)`` only on a valid reading. On a failed read
    (``ph()`` returns None — circuit offline) it returns ``False`` so
    the leaf FAILS, no effect is applied, and the planner re-selects
    this action after the device recovers. The retry is declarative —
    see this module's docstring."""
    params   = ["vial"]
    duration = 10
    # The probe sits in the liquid; the arm is genuinely idle during the
    # settling wait. "ph_meter" is the honest scheduling lock — the
    # scheduler may interleave other motion into the read window (there
    # is none in this minimal example, but the shape is the reference).
    resource = "ph_meter"

    def pre(self, vial):
        return immersed(vial) & ~measured(vial)

    def eff(self, vial):
        return {"measured": (+measured(vial),)}

    def execute(self, vial):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(_progress_pct(self), level="progress")
        # Settled reading through the mounted tool. ``sim_return``
        # (device-guide §17) injects a distinct fake pH per vial so a
        # sim run exercises per-vial logic; the real circuit ignores it.
        value = rcp["vials"].ph(sim_return=6.5 + vial * 0.3)
        if value is None:
            # Read failed (circuit offline). Do NOT assert measured(vial):
            # FAIL the leaf so the engine replans and re-selects ReadPH
            # once the device is back. The probe stays immersed (immersed
            # holds) so no motion is repeated — only the read retries.
            rt.step(f"vial {vial + 1}: pH unavailable — will retry after recover")
            return False
        rt.step(f"vial {vial + 1}: pH = {value:.3f}")
        return "measured"


class Retract(Action):
    """Lift the probe clear of the vial."""
    params   = ["vial"]
    duration = 10
    resource = "robot"
    tool     = "probe"

    def pre(self, vial):
        return measured(vial) & ~lifted(vial)

    def eff(self, vial):
        return {"lifted": (+lifted(vial),)}   # probe still "busy" until rinsed

    def execute(self, vial):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        slot = _slot(self, vial)
        rt.step(f"vial {vial + 1}: retract probe")
        rt.step(_progress_pct(self), level="progress")
        rcp["vials"].retract(anchor=slot)
        return "lifted"


class Rinse(Action):
    """Dip the probe into the storage-solution cup and lift out — the
    probe never travels dry between samples, and each vial's cycle
    closes by restoring ``probe_free``."""
    params   = ["vial"]
    duration = 10
    resource = "robot"
    tool     = "probe"

    def pre(self, vial):
        return lifted(vial) & ~rinsed(vial)

    def eff(self, vial):
        return {"rinsed": (+rinsed(vial), +probe_free())}   # probe free again

    def execute(self, vial):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"vial {vial + 1}: rinse in storage cup")
        rt.step(_progress_pct(self), level="progress")
        rcp["storage"].immerse()
        rcp["storage"].retract()
        return "rinsed"


class Park(Action):
    """Final park — planned by PDDL after every cup is measured."""
    params      = []
    duration    = 5
    resource    = "robot"
    tool        = None
    PARK_JOINTS = [0, 90, 0, 0, 0, 0, 100]

    def pre(self):
        vials = self._ctx_all_objects().get("vial", [])
        expr = ~parked() & started()
        for v in vials:
            expr = expr & rinsed(v)
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
