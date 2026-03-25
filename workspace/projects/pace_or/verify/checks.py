# pace_or/5_verify/checks.py
# Pre/post verification checks for each protocol state.
#
# Each function returns (passed: bool, message: str).
# - passed=True  → check passes, execution continues
# - passed=False → check fails, runner pauses and prompts user
#
# Currently stubs returning True — replace the body of each function
# with a real camera call when the vision system is ready:
#
#   from workspace.vision.camera import Camera
#   cam = Camera()
#
#   def source_tube_present(item_i, cfg):
#       return cam.detect("tube", cfg.source[item_i]), "No tube at source position"


def source_tube_present(item_i, cfg) -> tuple[bool, str]:
    """Tube is present at the source rack position before picking."""
    # TODO: camera.detect("tube", cfg.source[item_i])
    return True, "source tube present"


def tube_in_working_rack(item_i, cfg) -> tuple[bool, str]:
    """Tube is in the working rack (after pick-and-place or after dosing)."""
    # TODO: camera.detect("tube", cfg.working[item_i])
    return True, "tube in working rack"


def shaker_slot_empty(item_i, cfg) -> tuple[bool, str]:
    """Shaker slot is empty before loading a tube."""
    # TODO: camera.is_empty(cfg.shaker_slots[item_i])
    return True, "shaker slot empty"


def tube_on_shaker(item_i, cfg) -> tuple[bool, str]:
    """Tube is confirmed on the shaker (after loading, or before retrieval)."""
    # TODO: camera.detect("tube", cfg.shaker_slots[item_i])
    return True, "tube on shaker"


def cap_holder_empty(item_i, cfg) -> tuple[bool, str]:
    """Cap holder slot is empty before feeding a new cap."""
    # TODO: camera.is_empty(cfg.cap_feeder[item_i])
    return True, "cap holder empty"


def cap_in_holder(item_i, cfg) -> tuple[bool, str]:
    """Cap is in the holder and ready for capping the 2ml vial."""
    # TODO: camera.detect("cap", cfg.cap_feeder[item_i])
    return True, "cap in holder"


def tube_in_2ml_rack(item_i, cfg) -> tuple[bool, str]:
    """2ml vial is placed in the final rack after capping."""
    # TODO: camera.detect("tube", cfg.rack_2ml_end[item_i])
    return True, "tube in 2ml rack"
