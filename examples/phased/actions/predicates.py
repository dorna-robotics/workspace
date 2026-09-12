"""Every fact this protocol uses, grouped by phase, one line of meaning
each. The single place to look up what a fact means.

Per-pass facts are PASS-INDEXED (``picked_1``, ``picked_2``), never a
``pass`` parameter: an action takes one param, and a phase boundary must
be a fact nothing removes — ``home_1`` stays true forever, where a
shared ``home`` would be cleared by pass 2's pick and re-open phase 1.
"""

from workspace.bt import predicate

PASSES = (1, 2)


def per_pass(name):
    """``{1: predicate("name_1"), 2: predicate("name_2")}``."""
    return {p: predicate(f"{name}_{p}") for p in PASSES}


# ── Bookends ───────────────────────────────────────────────────────
started = predicate("started")
parked  = predicate("parked")

# ── Single-occupancy resources (capacity facts) ────────────────────
# One gripper, one balance top, four shaker seats. capacity=True makes
# the scheduler derive an exclusive span between the action that clears
# the fact and the one that restores it (project-guide §8).
hand_empty = predicate("hand_empty", capacity=True)   # gripper holds no tube
pan_empty  = predicate("pan_empty", capacity=True)    # balance top is free

# The shaker head holds N_SEATS tubes at once. A seat is a resource like
# the pan; a tube's seat is static (``tube % N_SEATS``, actions/base.py)
# so planner, replay and bench agree whatever the order. Four tubes
# sharing one head is what couples them into a BANK (phases.py).
N_SEATS   = 4
seat_free = {i: predicate(f"seat_free_{i}", capacity=True) for i in range(N_SEATS)}

# ── Per pass: phase_1.py (pass 1), phase_2.py (pass 2) ─────────────
picked    = per_pass("picked")      # tube in the gripper, off its slot
on_scale  = per_pass("on_scale")    # tube released on the balance top
weighed   = per_pass("weighed")     # a valid mass was read
off_scale = per_pass("off_scale")   # tube re-gripped off the top
home      = per_pass("home")        # back in its own slot — THE PHASE FACT

# ── The shake, once, between the passes: phase_2.py ────────────────
on_shaker = predicate("on_shaker")  # tube seated on the shaker head
shaken    = predicate("shaken")     # its bank's shake has run
unloaded  = predicate("unloaded")   # back in its own slot, shaken — pass 2's gate
