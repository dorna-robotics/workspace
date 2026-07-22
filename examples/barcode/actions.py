"""barcode protocol — Start → [Pick → Present → Scan → Place] ×N → Park.

For each of ``batch_size`` tubes, the robot:
  1. Pick     — pick the 2 ml tube from its rack slot (SBS rack, plate 4)
  2. Present  — position the held tube at the barcode reader's window
  3. Scan     — read the barcode (NO motion — a pure device read)
  4. Place    — return the tube to its rack slot

Each is its own BT action, gated by facts so they run in order per tube;
the planner sequences tubes (windowed by plan_window). Tubes are the
single objects dim → slicing auto-engages.

Two patterns from the platform's playbook are demonstrated here:

* **Declarative retry on the read** (project-guide §8). ``Scan`` is a
  read-only action separated from the present/place motions, so a failed
  read is retried WITHOUT redoing the arm moves. ``Scan`` asserts
  ``scanned(tube)`` only on a valid scan; on a failed read it returns
  ``False`` → the leaf FAILS → no effect → the planner re-selects
  ``Scan`` after the reader recovers (the launcher wraps the body in
  ``replan_on_failure``). If the reader is ``critical`` + real, its bus
  ``down`` also pauses the runtime; on resume the re-selected ``Scan``
  runs against the reconnected scanner. The tube stays presented
  (``presented`` holds), so only the read is retried.

* **Single-occupancy resource** (project-guide §8). ``hand_empty`` (the
  gripper holds one tube) is consumed on Pick and restored on Place, so
  the planner can't batch all the Picks before any Place.

The reader runs in sim by default → ``code()`` returns the canned scan.
Point barcode_reader_1 at a real DS457 (port + simulation:false in the
layout) for live barcodes and the real pause/recover/retry path.

Pattern reference: examples/inspection/actions.py (per-item
multi-action) + examples/scale/actions.py (read-only +
declarative retry). Barcode API: workspace/recipes/barcode_reader.py —
present() + scan()/code().
"""

from __future__ import annotations

from workspace.bt import Action, predicate


started    = predicate("started")
picked     = predicate("picked")      # tube is in the gripper
presented  = predicate("presented")   # tube positioned at the reader window
scanned    = predicate("scanned")     # a valid barcode was read for this tube
placed     = predicate("placed")      # tube returned to its slot
parked     = predicate("parked")

# Single-occupancy: the gripper holds ONE tube. Without this the planner
# can batch all the Picks before any Place (impossible — one gripper).
# Consumed on Pick, restored on Place; the tube is held the whole
# Pick→Present→Scan→Place chain, so Present/Scan don't touch it.
# See project-guide §8 "Single-occupancy resources".
#
# capacity=True: shared mutual-exclusion facts, not causal ones —
# see dsl.py's "Capacity facts" section. Without the flag the
# scheduler ties precedence to whichever item's action the plan's
# own linearization set the fact last, serializing items that
# could otherwise be batched by tool.
hand_empty = predicate("hand_empty", capacity=True)  # gripper holds no tube


RACK = "rack_autosampler_2ml_1"

# Per-tube steps the progress bar spans (Pick, Present, Scan, Place).
_STEPS = 4


def _slot(action, tube):
    """Rack slot anchor (A1..F8) for tube index ``tube`` — read from the
    rack component so the order matches the scene, not a hardcoded list."""
    return action.ctx.workspace.components[RACK].slot["body"][tube]


def _progress_pct(action):
    """Monotonic % over all per-tube steps. Reads the live fact set
    (``action.ctx.state["facts"]``; ``action.state`` is None in execute).
    This action's eff hasn't applied yet, so count it as +1."""
    tubes = action._ctx_all_objects().get("tube", [])
    total = (len(tubes) or 1) * _STEPS
    ctx_state = getattr(action.ctx, "state", None) or {}
    facts = ctx_state.get("facts") or set()
    done = sum(
        ((picked.name, t) in facts) + ((presented.name, t) in facts)
        + ((scanned.name, t) in facts) + ((placed.name, t) in facts)
        for t in tubes
    )
    return int((done + 1) / total * 100)


def setup(**kwargs):
    tubes = list(range(int(kwargs.get("batch_size", 4))))

    def item_done(state, tube):
        return (placed.name, tube) in state

    def goal(state):
        return (
            (started.name,) in state
            and all(item_done(state, t) for t in tubes)
            and (parked.name,) in state
        )

    goal_facts = frozenset(
        [(placed.name, t) for t in tubes]
        + [(started.name,), (parked.name,)]
    )

    return {
        "initial_facts": frozenset(),
        "goal":          goal,
        "item_done":     item_done,
        "goal_facts":    goal_facts,
        "objects":       {"tube": tubes},
    }


class Start(Action):
    params   = []
    duration = 5
    resource = "robot"
    START_JOINTS = [0, 45, -90, 0, -45, 0, 100]

    def pre(self):
        return ~started()

    def eff(self):
        return {"started": (+started(), +hand_empty())}   # gripper starts empty

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


class Pick(Action):
    """Pick the 2 ml tube from its rack slot."""
    params   = ["tube"]
    duration = 10
    resource = "robot"
    tool     = "gripper"

    def pre(self, tube):
        return started() & hand_empty() & ~picked(tube)

    def eff(self, tube):
        return {"picked": (+picked(tube), -hand_empty())}   # hand now full

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        slot = _slot(self, tube)
        rt.step(f"tube {tube + 1}: pick from rack[{slot}]")
        rt.step(_progress_pct(self), level="progress")
        rcp["tube_rack"].pick(slot, soft_approach=True)
        return "picked"


class Present(Action):
    """Position the held tube at the barcode reader's window."""
    params   = ["tube"]
    duration = 8
    resource = "robot"
    tool     = "gripper"

    def pre(self, tube):
        return picked(tube) & ~presented(tube)

    def eff(self, tube):
        return {"presented": (+presented(tube),)}

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"tube {tube + 1}: present to barcode reader")
        rt.step(_progress_pct(self), level="progress")
        rcp["barcode_reader"].present()
        return "presented"


class Scan(Action):
    """Read the barcode — a PURE device read, no robot motion.

    Asserts ``scanned(tube)`` only on a valid read. On a failed read
    (``code()`` returns None — reader offline) it returns ``False`` so the
    leaf FAILS, no effect is applied, and the planner re-selects this
    action after the device recovers. The retry is declarative — see this
    module's docstring."""
    params   = ["tube"]
    duration = 3
    # resource is the SCHEDULING lock, not "which device this touches".
    # The read touches the reader, but the robot is committed to this tube
    # (held at the reader window, must place it next), so "robot" keeps the
    # per-tube present→scan→place sequence serial. See project-guide §8.
    resource = "robot"

    def pre(self, tube):
        return presented(tube) & ~scanned(tube)

    def eff(self, tube):
        return {"scanned": (+scanned(tube),)}

    def execute(self, tube):
        from workspace.components.barcode_reader.ds457_driver import Scan
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(_progress_pct(self), level="progress")
        # Read the barcode via detect() — returns a Scan (status + data +
        # symbology). ``sim_return`` (device-guide §17) injects a full fake
        # Scan per tube so a sim run exercises per-tube logic; on the real
        # reader the argument is ignored and the actual barcode is read.
        # Pass ``allowed=[...]`` to restrict which symbologies count
        # (default: any). e.g. rcp["barcode_reader"].detect(allowed=["code128"]).
        scan = rcp["barcode_reader"].detect(
            sim_return=Scan(status="ok", data=f"TUBE-{tube + 1:04d}", symbology="code128"))
        if scan is None or not scan.ok:
            # Read failed (reader offline / timeout / nak). Do NOT assert
            # scanned(tube): FAIL the leaf so the engine replans and
            # re-selects Scan once the device is back. The tube stays
            # presented so no motion is repeated — only the read is retried.
            rt.step(f"tube {tube + 1}: barcode unavailable — will retry after recover")
            return False
        rt.step(f"tube {tube + 1}: barcode = {scan.data} ({scan.symbology})")
        return "scanned"


class Place(Action):
    """Return the tube to its rack slot."""
    params   = ["tube"]
    duration = 10
    resource = "robot"
    tool     = "gripper"

    def pre(self, tube):
        return scanned(tube) & ~placed(tube)

    def eff(self, tube):
        return {"placed": (+placed(tube), +hand_empty())}   # tube back in rack, hand frees

    def execute(self, tube):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        slot = _slot(self, tube)
        rt.step(f"tube {tube + 1}: place back to rack[{slot}]")
        rt.step(_progress_pct(self), level="progress")
        rcp["tube_rack"].place(slot, gravity_offset=4, soft_approach=True)
        return "placed"


class Park(Action):
    """Final park — planned by PDDL after every tube is back in its slot."""
    params      = []
    duration    = 5
    resource    = "robot"
    tool        = None
    PARK_JOINTS = [0, 90, 0, 0, 0, 0, 100]

    def pre(self):
        tubes = self._ctx_all_objects().get("tube", [])
        expr = ~parked() & started()
        for t in tubes:
            expr = expr & placed(t)
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
