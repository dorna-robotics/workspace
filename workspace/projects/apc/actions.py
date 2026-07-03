"""apc protocol — Start → [per-disc pipeline] ×(inventory total) → Park.

IN inventory comes from launch.yaml: two lists of 7 (``in_1``, ``in_2``),
where index i = number of discs stacked at anchor A<i+1> of that holder.
Discs are consumed TOP-of-stack first, A1→A7, in_1 until empty, then
in_2. Each disc appears in the scene the moment it's about to be picked
(create-on-demand, one at a time via feed_free) at its stack position
(z = depth × Z_STEP) — the racks hold at most one transient disc while
the counts still come from the configured inventory.

Each disc goes through a SPLIT chain of small BT actions, threaded by
facts (the BT moves action→action as each eff is asserted). Per disc i:

  1. Create       spawn the disc at its inventory position (top of the
                  remaining stack at its in-holder anchor).
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

The DROP is ORDERED by a fill counter (ctx.meta["filled"]):
  * good fills out_good_1 completely, then out_good_2; bad fills out_bad_1.
  * within a holder: slots A1 → A7 in order.
  * within a slot: z starts at 0 and steps by Z_STEP per disc, up to
    MAX_PER_SLOT discs.
Every sorted disc is DELETED right after place() — sorted discs are
terminal, so nothing accumulates in the scene. Start additionally sweeps
any disc_* component left over from a previous run that was killed or
stopped mid-cycle, so the out racks can never show stale discs.

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
# Three shared slots, each holding ONE disc at a time. Without these the
# planner runs actions in parallel across discs — creating several discs
# up front (they pile on the feed), or two discs on the anode, or picking
# while the cathode is down. Each fact is consumed (-fact) when its slot
# fills and restored (+fact) when it empties, forcing strictly
# one-disc-at-a-time:
#   feed_free  — only one un-picked disc may exist (create → pick → create
#                → pick…, never a batch of Creates ahead of the picks).
#   hand_empty — the gripper holds one disc.
#   anode_free — the anode/cathode station processes one disc.
# See project-guide §8 "Single-occupancy resources".
feed_free   = predicate("feed_free")     # in-feed has no un-picked disc
hand_empty  = predicate("hand_empty")    # gripper holds no disc
anode_free  = predicate("anode_free")    # anode/cathode station is idle


# ── Exposed, tweakable parameters ─────────────────────────────────────
SLOTS       = [f"A{c}" for c in range(1, 7 + 1)]  # A1 .. A7, in order
Z_STEP      = 0.254                            # per-disc stack lift (mm), in + out
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


# ── Ordered-drop position — a simple counter ──────────────────────────
# Where the next disc goes is tracked by a per-holder fill COUNT in
# ctx.meta["filled"] = {holder_alias: n_dropped}. From the count we derive
# (slot, z) deterministically: slot = SLOTS[count // MAX_PER_SLOT], z =
# (count % MAX_PER_SLOT) * Z_STEP; roll to the next holder when the current
# is full. This is runtime state (lives in execute, never in planner
# facts), so it's BT-legal. It does NOT survive a restart mid-batch (the
# count resets); fine here because a batch is run start-to-finish and
# sorted discs are terminal — every one is DELETED right after place().

def _next_drop(filled, holders):
    """Next (holder_alias, slot, z, count) from the per-holder fill counts.
    Fills slot A1→A7, stacking z by Z_STEP up to MAX_PER_SLOT, holder by
    holder. Returns None when every holder is full."""
    cap = len(SLOTS) * MAX_PER_SLOT
    for holder in holders:
        count = filled.get(holder, 0)
        if count < cap:
            slot = SLOTS[count // MAX_PER_SLOT]
            z = round((count % MAX_PER_SLOT) * Z_STEP, 3)
            return holder, slot, z, count
    return None


# ── Generic helpers ───────────────────────────────────────────────────

def _disc(disc: int) -> str:
    return f"disc_{disc}"


# ── IN inventory ──────────────────────────────────────────────────────
# Filled by setup() from the launch.yaml in_1 / in_2 lists (each 7 ints,
# index i = discs stacked at anchor A<i+1>). INVENTORY[disc] = (holder,
# slot, z): stacks are consumed TOP-first (depth n-1 → 0, z = depth ×
# Z_STEP), slots A1→A7, in_1 until empty, then in_2. Module-level so the
# per-disc actions (Create / Pick) can read their position; rebuilt on
# every setup() call, so a replan stays consistent with the same kwargs.
INVENTORY: list = []   # disc index → (in_holder, slot, z)


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
    def _counts(key, default):
        """Parse an inventory spec into exactly len(SLOTS) ints — lenient
        by design, since the GUI params form may deliver the list as a
        string like "1,1,1,1,1,1,1" or "[2, 1]":
          * list/tuple of numbers → used as-is
          * string → brackets/spaces stripped, split on commas
          * scalar → treated as [scalar]
        A shorter list fills the leading anchors (rest 0); a longer one is
        truncated to A1..A7. Values clamp to 0..MAX_PER_SLOT."""
        raw = kwargs.get(key, default)
        if isinstance(raw, str):
            raw = [p for p in raw.strip().strip("[]").replace(" ", "").split(",") if p]
        elif isinstance(raw, (int, float)):
            raw = [raw]
        counts = []
        for n in list(raw)[:len(SLOTS)]:
            try:
                v = int(float(n))
            except (TypeError, ValueError):
                v = 0
            counts.append(max(0, min(MAX_PER_SLOT, v)))
        counts += [0] * (len(SLOTS) - len(counts))
        return counts

    in_1 = _counts("in_1", [1] * len(SLOTS))
    in_2 = _counts("in_2", [0] * len(SLOTS))

    INVENTORY.clear()
    for holder, counts in ((1, in_1), (2, in_2)):
        for s, n in enumerate(counts):
            for depth in range(n - 1, -1, -1):        # top of the stack first
                INVENTORY.append((holder, SLOTS[s], round(depth * Z_STEP, 3)))

    discs = list(range(len(INVENTORY)))

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
    START_JOINTS = [0, 45, -90, 0, -45, 0, 100]

    def pre(self):
        return ~started()

    def eff(self):
        # Seed the single-occupancy resources: feed, hand, anode all free.
        return {"started": (+started(), +feed_free(), +hand_empty(), +anode_free())}

    def execute(self):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes
        ws  = self.ctx.workspace
        # Clean slate: sweep any disc_* component left over from a previous
        # run that was killed / stopped mid-cycle. Such a disc (stranded in
        # an out rack, on the anode, or on the gripper) stays in the scene
        # forever otherwise — this is how stale discs pile up in the out
        # racks across runs.
        for name in [c for c in list(ws.components) if c.startswith("disc_")]:
            ws.remove_component(name)
        rt.motor(1)
        # Move to a known ready pose (Recipe.park is a base move-to-joint
        # on the generic component-less "robot" recipe).
        rcp["robot"].park(joint=self.START_JOINTS, has_motion_plan=True)
        return "started"


class Create(Action):
    """Spawn the disc at its configured inventory position — the top of
    the remaining stack at its in-holder anchor (z = depth × Z_STEP)."""
    params   = ["disc"]
    duration = 2
    resource = "robot"

    def pre(self, disc):
        # feed_free gates one un-picked disc at a time (no batch of Creates).
        return started() & feed_free() & ~created(disc)

    def eff(self, disc):
        return {"created": (+created(disc), -feed_free())}   # feed now occupied

    def execute(self, disc):
        rt, ws = self.ctx.runtime, self.ctx.workspace
        name = _disc(disc)
        # Idempotent retry — clear a leftover from a failed prior attempt.
        if name in ws.components:
            ws.remove_component(name)
        in_h, slot, z = INVENTORY[disc]   # configured stack position
        rt.step(f"disc {disc + 1}: create at in_{in_h}[{slot}] z={z}")
        rt.step(_progress_pct(self), level="progress")
        ws.add_component(name, {
            "type": "disc_22mm",
            "attach": {
                "parent_name":   f"stack_holder_disc_in_{in_h}",
                "parent_solid":  "body",
                "parent_anchor": slot,
                "child_solid":   "body",
                "child_anchor":  "center",
                "offset":        [0, 0, z, 0, 0, 0],
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
        # Disc leaves the feed into the hand: feed frees, hand fills.
        return {"picked": (+picked(disc), +feed_free(), -hand_empty())}

    def execute(self, disc):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        in_h, slot, _z = INVENTORY[disc]   # same position the disc was created at
        rt.step(f"disc {disc + 1}: pick from in_{in_h}[{slot}]")
        rt.step(_progress_pct(self), level="progress")
        rcp[f"disc_in_{in_h}"].pick(slot, tool_tcp_z_offset=PICK_TCP_Z, soft_approach=True)
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
        rcp["anode"].pick("place", tool_tcp_z_offset=PICK_TCP_Z, soft_approach=True)
        return "off_anode"


class Sort(Action):
    """Drop the disc into an OUT holder by its measured capacitance, into
    the next ordered slot (fill counter), then delete it — sorted discs
    are terminal and never linger in the scene."""
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

        # Self-heal: a disc whose sorted-fact is TRUE must not exist in the
        # scene (it was deleted the moment it was placed). One can survive
        # when a kill / error / operator-skip lands between place() and the
        # delete — it then floats at its old stack height while newer discs
        # stack through it. Sweep any such stragglers now.
        facts = (getattr(self.ctx, "state", None) or {}).get("facts") or set()
        for comp in [c for c in list(ws.components) if c.startswith("disc_")]:
            try:
                j = int(comp.split("_", 1)[1])
            except ValueError:
                ws.remove_component(comp)          # stray non-pipeline junk
                continue
            if j != disc and (sorted_.name, j) in facts:
                ws.remove_component(comp)

        c = self.ctx.meta.get("disc_c", {}).get(disc)
        good = (c is not None) and (C_MIN <= c <= C_MAX)
        holders = GOOD_HOLDERS if good else BAD_HOLDERS

        filled = self.ctx.meta.setdefault("filled", {})   # holder → n dropped
        nxt = _next_drop(filled, holders)
        if nxt is None:
            rt.step(f"disc {disc + 1}: all {'good' if good else 'bad'} holders FULL")
            return False
        holder, slot, z, count = nxt
        rt.step(f"disc {disc + 1}: {'GOOD' if good else 'BAD'} → {holder}[{slot}] z={z}")
        rt.step(_progress_pct(self), level="progress")

        # Place the held disc into the ordered slot, then DELETE it. Sorted
        # discs are terminal — we don't keep any in the scene, so nothing
        # accumulates (no meshes/pickables piling up over ~3500 discs). The
        # fill counter, not the scene, tracks where the next disc goes.
        # place() re-attaches the held disc_<i> into the slot; remove it.
        rcp[holder].place(slot, offset=[0, 0, z, 0, 0, 0], gravity_offset=PLACE_GRAV)
        name = _disc(disc)
        if name in ws.components:
            ws.remove_component(name)

        filled[holder] = count + 1
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
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes
        # Move to the park pose, then cut motor. Recipe.park is a base
        # move-to-joint (collision-aware + a checkpoint so Pause/Resume stays
        # live); apc has no gripper/tool recipe, so we borrow "inspector".
        rcp["robot"].park(joint=self.PARK_JOINTS, has_motion_plan=True)
        rt.motor(0)
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — fires on the Park button, outside the plan."""
    trigger = "park"
