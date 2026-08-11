"""ph_meter protocol — Start → [Immerse → ReadPH → Retract] ×N → Park.

For each of ``batch_size`` cups, the robot (carrying the Atlas pH
probe from the tool rack):
  1. Immerse — dip the probe into the cup's well
  2. ReadPH  — wait for a settled reading (NO motion — a pure device read)
  3. Retract — lift the probe clear of the cup

Each is its own BT action, gated by facts so they run in order per cup;
the planner sequences cups (windowed by plan_window). Cups are the
single objects dim → slicing auto-engages.

WHY ReadPH is split out as its own read-only action — declarative retry
=======================================================================
Same reference pattern as examples/scale's Weigh (project-guide §8
"Device reads + declarative retry"): ``ReadPH`` asserts
``measured(cup)`` ONLY when it gets a valid number. On a failed read it
``return False`` → the leaf FAILS → no effect applies → the engine
replans from the observed world, where ``immersed(cup) &
~measured(cup)`` still holds → the planner re-selects ReadPH. The probe
stays in the liquid (no motion repeated) — only the read retries.
Meanwhile a real, ``critical`` EZO going down pauses the runtime until
it recovers; the retry then runs against the reconnected circuit.

The probe runs in sim by default → ``ph()`` returns the canned value
(injected per-cup via ``sim_return`` so a sim run exercises per-cup
logic). Point ph_meter_atlas_1 at the real circuit (port +
simulation:false in the layout) for live readings.

Pattern reference: examples/scale/actions.py. Probe/site API:
workspace/recipes/ph_meter.py — immerse/retract + ph/read_stable
through the mounted tool.
"""

from __future__ import annotations

from workspace.bt import Action, predicate


started  = predicate("started")
immersed = predicate("immersed")   # probe is in this cup's liquid
measured = predicate("measured")   # a valid pH was read for this cup
done     = predicate("done")       # probe lifted clear of this cup
parked   = predicate("parked")

# ── Single-occupancy resource (capacity-1, no args) ───────────────────
# One probe: it can only be in one cup at a time. Consumed on Immerse,
# restored on Retract — forcing strictly one-cup-at-a-time through the
# dip/read/lift chain. See project-guide §8 "Single-occupancy resources".
probe_free = predicate("probe_free", capacity=True)

# How many per-cup steps the progress bar spans (Immerse, ReadPH, Retract).
_STEPS = 3


def _progress_pct(action):
    """Monotonic % over all per-cup steps. Reads the live fact set
    (``action.ctx.state["facts"]``; ``action.state`` is None in execute).
    This action's eff hasn't applied yet, so count it as +1."""
    cups = action._ctx_all_objects().get("cup", [])
    total = (len(cups) or 1) * _STEPS
    ctx_state = getattr(action.ctx, "state", None) or {}
    facts = ctx_state.get("facts") or set()
    n = sum(
        ((immersed.name, c) in facts) + ((measured.name, c) in facts)
        + ((done.name, c) in facts)
        for c in cups
    )
    return int((n + 1) / total * 100)


def setup(**kwargs):
    cups = list(range(int(kwargs.get("batch_size", 3))))

    def item_done(state, cup):
        return (done.name, cup) in state

    def goal(state):
        return (
            (started.name,) in state
            and all(item_done(state, c) for c in cups)
            and (parked.name,) in state
        )

    goal_facts = frozenset(
        [(done.name, c) for c in cups]
        + [(started.name,), (parked.name,)]
    )

    return {
        "initial_facts": frozenset(),
        "goal":          goal,
        "item_done":     item_done,
        "goal_facts":    goal_facts,
        "objects":       {"cup": cups},
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
    """Dip the probe into the cup's well — deep enough to submerge the
    glass bulb (a dry electrode reads garbage)."""
    params   = ["cup"]
    duration = 10
    resource = "robot"
    tool     = "probe"

    def pre(self, cup):
        # probe_free gates one-at-a-time: the probe is in at most one cup.
        return started() & probe_free() & ~immersed(cup)

    def eff(self, cup):
        return {"immersed": (+immersed(cup), -probe_free())}   # probe now busy

    def execute(self, cup):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"cup {cup + 1}: immerse probe")
        rt.step(_progress_pct(self), level="progress")
        rcp[f"cup_{cup}"].immerse()
        return "immersed"


class ReadPH(Action):
    """Wait for a settled pH — a PURE device read, no robot motion.

    Asserts ``measured(cup)`` only on a valid reading. On a failed read
    (``ph()`` returns None — circuit offline) it returns ``False`` so
    the leaf FAILS, no effect is applied, and the planner re-selects
    this action after the device recovers. The retry is declarative —
    see this module's docstring."""
    params   = ["cup"]
    duration = 10
    # The probe sits in the liquid; the arm is genuinely idle during the
    # settling wait. "ph_meter" is the honest scheduling lock — the
    # scheduler may interleave other motion into the read window (there
    # is none in this minimal example, but the shape is the reference).
    resource = "ph_meter"

    def pre(self, cup):
        return immersed(cup) & ~measured(cup)

    def eff(self, cup):
        return {"measured": (+measured(cup),)}

    def execute(self, cup):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(_progress_pct(self), level="progress")
        # Settled reading through the mounted tool. ``sim_return``
        # (device-guide §17) injects a distinct fake pH per cup so a sim
        # run exercises per-cup logic; the real circuit ignores it.
        value = rcp[f"cup_{cup}"].ph(sim_return=6.5 + cup * 0.5)
        if value is None:
            # Read failed (circuit offline). Do NOT assert measured(cup):
            # FAIL the leaf so the engine replans and re-selects ReadPH
            # once the device is back. The probe stays immersed (immersed
            # holds) so no motion is repeated — only the read retries.
            rt.step(f"cup {cup + 1}: pH unavailable — will retry after recover")
            return False
        rt.step(f"cup {cup + 1}: pH = {value:.3f}")
        return "measured"


class Retract(Action):
    """Lift the probe clear of the cup."""
    params   = ["cup"]
    duration = 10
    resource = "robot"
    tool     = "probe"

    def pre(self, cup):
        return measured(cup) & ~done(cup)

    def eff(self, cup):
        return {"done": (+done(cup), +probe_free())}   # probe free again

    def execute(self, cup):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"cup {cup + 1}: retract probe")
        rt.step(_progress_pct(self), level="progress")
        rcp[f"cup_{cup}"].retract()
        return "done"


class Park(Action):
    """Final park — planned by PDDL after every cup is measured."""
    params      = []
    duration    = 5
    resource    = "robot"
    tool        = None
    PARK_JOINTS = [0, 90, 0, 0, 0, 0, 100]

    def pre(self):
        cups = self._ctx_all_objects().get("cup", [])
        expr = ~parked() & started()
        for c in cups:
            expr = expr & done(c)
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
