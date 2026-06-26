"""apc protocol — Start → [per-disc pipeline] ×batch_size → Park.

Each disc goes through a SPLIT chain of small BT actions, threaded by
facts (the BT moves action→action as each eff is asserted). Per disc i:

  1. Create       spawn a disc at the current IN holder (in_1 until empty,
                  then in_2) — on demand, like the runtime example.
  2. Pick         suction-pick it off the IN stack.
  3. Inspect      present to the inspection station + detect() (generic).
  4. PlaceAnode   place the disc on the anode's "place" anchor.
  5. CathodeDown  drive the rotating cylinder down so the cathode contacts
                  the disc (clamped anode ↔ cathode).
  6. Measure      read the multimeter capacitance → record it for the disc.
  7. CathodeUp    retract the cylinder (cathode up).
  8. PickAnode    suction-pick the disc back off the anode.
  9. Sort         drop it into an OUT holder by the measured C:
                  C_MIN ≤ C ≤ C_MAX → good (fill out_good_1, then _2);
                  otherwise → bad (out_bad_1). Ordered fill (see below).

Then Park once every disc is sorted.

Pick is ON DEMAND (create + pick, not ordered). The DROP is ORDERED:
  * good fills out_good_1 completely, then out_good_2; bad fills out_bad_1.
  * within a holder: slots A1 → A7 in order.
  * within a slot: z starts at 0 and steps by Z_STEP per disc, up to
    MAX_PER_SLOT discs.
The next (holder, slot, z) is DERIVED FROM THE SCENE — we count the discs
already placed in each OUT holder — NOT a hidden counter. This is the
modular, replan-safe way: the out holders ARE the state, so a retry /
restart self-corrects (no bookkeeping fact to desync). See project-guide
§8.

BT philosophy: actions are small; pre/eff carry the per-disc state machine
forward. Suction pick/place follow the runtime example (tool_tcp_z_offset
on pick, gravity_offset on place).

NOTE: no tool swapping — the suction gripper is mounted on the robot
(no rack), so NO action sets ``tool`` (leave it unset everywhere).
"""

from __future__ import annotations

from workspace.bt import Action, predicate


# ── Per-disc facts (the action chain) ─────────────────────────────────
started      = predicate("started")
created      = predicate("created")      # disc spawned at an in holder
picked       = predicate("picked")       # disc in the gripper (off the in stack)
inspected    = predicate("inspected")    # presented + detect() ran
on_anode     = predicate("on_anode")     # disc placed on the anode
cathode_down = predicate("cathode_down") # cylinder driven down (cathode contact)
measured     = predicate("measured")     # capacitance read for this disc
cathode_up   = predicate("cathode_up")   # cylinder retracted
off_anode    = predicate("off_anode")    # disc re-gripped off the anode
sorted_      = predicate("sorted")       # disc dropped into an out holder
parked       = predicate("parked")

# ── Single-occupancy resources (capacity-1, no args) ──────────────────
# The gripper holds ONE disc and the anode/cathode station processes ONE
# disc at a time. Without these the planner interleaves discs on the
# SHARED stations — two discs on the anode, picking while the cathode is
# down, etc. Each is consumed (-fact) when the slot fills and restored
# (+fact) when it empties, forcing strictly one-disc-at-a-time through the
# hand and the anode. See project-guide §8 "Single-occupancy resources".
hand_empty  = predicate("hand_empty")    # gripper holds no disc
anode_free  = predicate("anode_free")    # anode/cathode station is idle


# ── Exposed, tweakable parameters ─────────────────────────────────────
IN_HOLDERS  = [1, 2]                            # draw from in_1, then in_2
SLOTS       = [f"A{c}" for c in range(1, 7 + 1)]  # A1 .. A7, in order
Z_STEP      = 0.254                            # per-disc stack lift (mm)
MAX_PER_SLOT = 225                             # discs per slot before next slot

# Good/bad capacitance window (Farads). Defaulted WIDE so everything
# currently lands in "good" — set the real spec later.
C_MIN = 0.0
C_MAX = 1.0e9

# Ordered OUT-holder fill sequences (recipe aliases, in fill order).
GOOD_HOLDERS = ["disc_out_good_1", "disc_out_good_2"]
BAD_HOLDERS  = ["disc_out_bad_1"]

# Suction motion offsets (mirror the runtime example).
PICK_TCP_Z   = -10                             # suction drives deeper to grab
PLACE_GRAV   = -5                              # suction presses on release

_STEPS = 9                                     # per-disc steps for progress


# ── Scene-derived ordered-drop helper ─────────────────────────────────
# A placed disc persists as a scene component named "<holder>__<slot>__<n>"
# (n = 0-based depth in the slot). Counting those components per holder
# tells us the next free (holder, slot, z) — no counter, replan-safe.

def _placed_name(holder: str, slot: str, depth: int) -> str:
    return f"{holder}__{slot}__{depth}"


def _next_drop(ws, holders):
    """Find the next free (holder_alias, slot, z, depth) across an ordered
    list of OUT holder aliases, filling slot A1→A7 and stacking z by Z_STEP
    up to MAX_PER_SLOT, holder by holder. Returns None if all are full."""
    for holder in holders:
        for slot in SLOTS:
            for depth in range(MAX_PER_SLOT):
                if _placed_name(holder, slot, depth) not in ws.components:
                    return holder, slot, round(depth * Z_STEP, 3), depth
    return None


# ── Generic helpers ───────────────────────────────────────────────────

def _disc(disc: int) -> str:
    return f"disc_{disc}"


def _in_holder(disc: int) -> int:
    """Which IN holder this disc comes from — in_1 until 'empty', then
    in_2. Modeled simply: split the batch in half (first half from in_1,
    rest from in_2). On-demand create, so this only decides where to
    spawn it. Adjust to a real magazine-count check when stacks are
    finite."""
    half = (len(IN_HOLDERS) and 1) or 1   # placeholder split point
    # Spawn from in_1 for even-ish first portion; here: alternate by a
    # simple rule that prefers in_1 — kept trivial since create is on
    # demand. Real "in_1 until empty" needs a magazine count; wire that
    # when the in stacks are finite.
    return IN_HOLDERS[0]


def _progress_pct(action):
    discs = action._ctx_all_objects().get("disc", [])
    total = (len(discs) or 1) * _STEPS
    ctx_state = getattr(action.ctx, "state", None) or {}
    facts = ctx_state.get("facts") or set()
    done = 0
    for d in discs:
        for p in (created, picked, inspected, on_anode, cathode_down,
                  measured, cathode_up, off_anode, sorted_):
            if (p.name, d) in facts:
                done += 1
    return int((done + 1) / total * 100)


# ── setup ─────────────────────────────────────────────────────────────

def setup(**kwargs):
    batch_size = int(kwargs.get("batch_size", 100))
    discs = list(range(batch_size))

    def item_done(state, disc):
        return (sorted_.name, disc) in state

    def goal(state):
        return (
            (started.name,) in state
            and all(item_done(state, d) for d in discs)
            and (parked.name,) in state
        )

    goal_facts = frozenset(
        [(sorted_.name, d) for d in discs]
        + [(started.name,), (parked.name,)]
    )

    return {
        "initial_facts": frozenset(),
        "goal":          goal,
        "item_done":     item_done,
        "goal_facts":    goal_facts,
        "objects":       {"disc": discs},
    }


# ── Lifecycle ─────────────────────────────────────────────────────────

class Start(Action):
    params   = []
    duration = 5
    resource = "robot"

    def pre(self):
        return ~started()

    def eff(self):
        # Seed the single-occupancy resources: hand + anode both start free.
        return {"started": (+started(), +hand_empty(), +anode_free())}

    def execute(self):
        rt = self.ctx.runtime
        rt.motor(1)
        return "started"


class Create(Action):
    """Spawn a disc on demand at the current IN holder (z=0)."""
    params   = ["disc"]
    duration = 2
    resource = "robot"

    def pre(self, disc):
        return started() & ~created(disc)

    def eff(self, disc):
        return {"created": (+created(disc),)}

    def execute(self, disc):
        rt, ws = self.ctx.runtime, self.ctx.workspace
        name = _disc(disc)
        # Idempotent retry — clear a leftover from a failed prior attempt.
        if name in ws.components:
            ws.remove_component(name)
        in_h = _in_holder(disc)
        slot = SLOTS[0]   # spawn at A1 of the in stack (on-demand source)
        rt.step(f"disc {disc + 1}: create at in_{in_h}[{slot}]")
        rt.step(_progress_pct(self), level="progress")
        ws.add_component(name, {
            "type": "disc_22mm",
            "attach": {
                "parent_name":   f"stack_holder_disc_in_{in_h}",
                "parent_solid":  "body",
                "parent_anchor": slot,
                "child_solid":   "body",
                "child_anchor":  "center",
                "offset":        [0, 0, 0, 0, 0, 0],
            },
        })
        return "created"


class Pick(Action):
    """Suction-pick the disc off the IN stack."""
    params   = ["disc"]
    duration = 10
    resource = "robot"

    def pre(self, disc):
        # hand_empty gates one-disc-at-a-time in the gripper.
        return created(disc) & hand_empty() & ~picked(disc)

    def eff(self, disc):
        return {"picked": (+picked(disc), -hand_empty())}   # hand now full

    def execute(self, disc):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        in_h = _in_holder(disc)
        rt.step(f"disc {disc + 1}: pick from in_{in_h}[{SLOTS[0]}]")
        rt.step(_progress_pct(self), level="progress")
        rcp[f"disc_in_{in_h}"].pick(SLOTS[0], tool_tcp_z_offset=PICK_TCP_Z)
        return "picked"


class Inspect(Action):
    """Present the held disc to the inspection station and run detect()."""
    params   = ["disc"]
    duration = 8
    resource = "robot"

    def pre(self, disc):
        return picked(disc) & ~inspected(disc)

    def eff(self, disc):
        return {"inspected": (+inspected(disc),)}

    def execute(self, disc):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"disc {disc + 1}: inspect")
        rt.step(_progress_pct(self), level="progress")
        rcp["inspector"].present()
        rcp["inspector"].detect()
        return "inspected"


class PlaceAnode(Action):
    """Place the disc on the anode's "place" anchor."""
    params   = ["disc"]
    duration = 10
    resource = "robot"

    def pre(self, disc):
        # anode_free gates one-disc-at-a-time on the shared anode/cathode.
        return inspected(disc) & anode_free() & ~on_anode(disc)

    def eff(self, disc):
        # Disc leaves the hand onto the anode: hand frees, anode occupied.
        return {"on_anode": (+on_anode(disc), +hand_empty(), -anode_free())}

    def execute(self, disc):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"disc {disc + 1}: place on anode")
        rt.step(_progress_pct(self), level="progress")
        rcp["anode"].place("place", gravity_offset=PLACE_GRAV, soft_approach=True)
        return "on_anode"


class CathodeDown(Action):
    """Drive the rotating cylinder down so the cathode contacts the disc."""
    params   = ["disc"]
    duration = 4
    resource = "robot"

    def pre(self, disc):
        return on_anode(disc) & ~cathode_down(disc)

    def eff(self, disc):
        return {"cathode_down": (+cathode_down(disc),)}

    def execute(self, disc):
        rt, ws = self.ctx.runtime, self.ctx.workspace
        rt.step(f"disc {disc + 1}: cathode down")
        rt.step(_progress_pct(self), level="progress")
        ws.components["rotating_cylinder_mkb1630_1"].enable()
        return "cathode_down"


class Measure(Action):
    """Read the disc's capacitance (clamped anode ↔ cathode)."""
    params   = ["disc"]
    duration = 3
    resource = "robot"

    def pre(self, disc):
        return cathode_down(disc) & ~measured(disc)

    def eff(self, disc):
        return {"measured": (+measured(disc),)}

    def execute(self, disc):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(_progress_pct(self), level="progress")
        m = rcp["meter"].read_capacitance()
        if m is None:
            rt.step(f"disc {disc + 1}: capacitance unavailable — will retry after recover")
            return False
        # Stash the measured value on the ctx so Sort can read it without
        # a planning fact (it's per-disc runtime data, not plan state).
        self.ctx.meta.setdefault("disc_c", {})[disc] = m.primary
        rt.step(f"disc {disc + 1}: C = {m.primary:g} {m.primary_unit}")
        return "measured"


class CathodeUp(Action):
    """Retract the cylinder (cathode up) so the disc can be lifted."""
    params   = ["disc"]
    duration = 4
    resource = "robot"

    def pre(self, disc):
        return measured(disc) & ~cathode_up(disc)

    def eff(self, disc):
        return {"cathode_up": (+cathode_up(disc),)}

    def execute(self, disc):
        rt, ws = self.ctx.runtime, self.ctx.workspace
        rt.step(f"disc {disc + 1}: cathode up")
        rt.step(_progress_pct(self), level="progress")
        ws.components["rotating_cylinder_mkb1630_1"].disable()
        return "cathode_up"


class PickAnode(Action):
    """Suction-pick the disc back off the anode."""
    params   = ["disc"]
    duration = 10
    resource = "robot"

    def pre(self, disc):
        # hand_empty required to re-grip; frees the anode for the next disc.
        return cathode_up(disc) & hand_empty() & ~off_anode(disc)

    def eff(self, disc):
        # Disc back into the hand off the anode: hand fills, anode frees.
        return {"off_anode": (+off_anode(disc), -hand_empty(), +anode_free())}

    def execute(self, disc):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        rt.step(f"disc {disc + 1}: pick off anode")
        rt.step(_progress_pct(self), level="progress")
        rcp["anode"].pick("place", tool_tcp_z_offset=PICK_TCP_Z)
        return "off_anode"


class Sort(Action):
    """Drop the disc into an OUT holder by its measured capacitance, into
    the next free ordered slot (scene-derived)."""
    params   = ["disc"]
    duration = 10
    resource = "robot"

    def pre(self, disc):
        return off_anode(disc) & ~sorted_(disc)

    def eff(self, disc):
        # Disc dropped into the out holder: hand frees.
        return {"sorted": (+sorted_(disc), +hand_empty())}

    def execute(self, disc):
        rt, rcp, ws = self.ctx.runtime, self.ctx.recipes, self.ctx.workspace
        c = self.ctx.meta.get("disc_c", {}).get(disc)
        good = (c is not None) and (C_MIN <= c <= C_MAX)
        holders = GOOD_HOLDERS if good else BAD_HOLDERS

        nxt = _next_drop(ws, holders)
        if nxt is None:
            rt.step(f"disc {disc + 1}: all {'good' if good else 'bad'} holders FULL")
            return False
        holder, slot, z, depth = nxt
        rt.step(f"disc {disc + 1}: {'GOOD' if good else 'BAD'} → {holder}[{slot}] z={z}")
        rt.step(_progress_pct(self), level="progress")

        # Place the held disc into the ordered slot with the stacked z lift.
        rcp[holder].place(slot, offset=[0, 0, z, 0, 0, 0], gravity_offset=PLACE_GRAV)

        # The placed disc persists in the scene as a named marker so the
        # next _next_drop sees this slot/depth as occupied (replan-safe
        # occupancy). The robot's held disc (disc_<i>) is consumed.
        held = _disc(disc)
        if held in ws.components:
            ws.remove_component(held)
        ws.add_component(_placed_name(holder, slot, depth), {
            "type": "disc_22mm",
            "attach": {
                "parent_name":   rcp[holder].component.name,
                "parent_solid":  "body",
                "parent_anchor": "place",
                "child_solid":   "body",
                "child_anchor":  "center",
                "offset":        [0, 0, z, 0, 0, 0],
            },
        })
        return "sorted"


class Park(Action):
    """Final park — after every disc is sorted."""
    params      = []
    duration    = 5
    resource    = "robot"
    PARK_JOINTS = [0, 185, -94, 0, 0, 0, 100]

    def pre(self):
        discs = self._ctx_all_objects().get("disc", [])
        expr = ~parked() & started()
        for d in discs:
            expr = expr & sorted_(d)
        return expr

    def eff(self):
        return {"parked": (+parked(),)}

    def execute(self):
        rt = self.ctx.runtime
        # TODO: move to PARK_JOINTS once a park/motion recipe is wired
        # (no tool recipe — gripper is mounted on the robot).
        rt.motor(0)
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — fires on the Park button, outside the plan."""
    trigger = "park"
