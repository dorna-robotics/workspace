# pace_or/checks.py
# Pre/post verification checks for each protocol state.
#
# Each method signature: (item_i: int) -> (passed: bool, message: str)
# - passed=True  → check passes, execution continues
# - passed=False → check fails, runner pauses and prompts user
#
# Currently stubs returning True — replace the body of each method
# with a real camera call when the vision system is ready.


class Checks:

    def __init__(self, rcp, rt, **kwargs):
        self.rcp = rcp
        self.rt  = rt

    def source_tube_present(self, item_i) -> tuple[bool, str]:
        """Tube is present at the source rack position before picking."""
        # TODO: camera.detect("tube", SOURCE[item_i])
        return True, "source tube present"

    def tube_in_working_rack(self, item_i) -> tuple[bool, str]:
        """Tube is in the working rack (after pick-and-place or after dosing)."""
        # TODO: camera.detect("tube", WORKING[item_i])
        return True, "tube in working rack"

    def shaker_slot_empty(self, item_i) -> tuple[bool, str]:
        """Shaker slot is empty before loading a tube."""
        # TODO: camera.is_empty(SHAKER_SLOTS[item_i])
        return True, "shaker slot empty"

    def tube_on_shaker(self, item_i) -> tuple[bool, str]:
        """Tube is confirmed on the shaker (after loading, or before retrieval)."""
        # TODO: camera.detect("tube", SHAKER_SLOTS[item_i])
        return True, "tube on shaker"

    def cap_holder_empty(self, item_i) -> tuple[bool, str]:
        """Cap holder slot is empty before feeding a new cap."""
        # TODO: camera.is_empty(CAP_FEEDER[item_i])
        return True, "cap holder empty"

    def cap_in_holder(self, item_i) -> tuple[bool, str]:
        """Cap is in the holder and ready for capping the 2ml vial."""
        # TODO: camera.detect("cap", CAP_FEEDER[item_i])
        return True, "cap in holder"

    def tube_in_2ml_rack(self, item_i) -> tuple[bool, str]:
        """2ml vial is placed in the final rack after capping."""
        # TODO: camera.detect("tube", RACK_2ML_END[item_i])
        return True, "tube in 2ml rack"

    def make(self):
        return {
            "source_tube_present":  self.source_tube_present,
            "tube_in_working_rack": self.tube_in_working_rack,
            "shaker_slot_empty":    self.shaker_slot_empty,
            "tube_on_shaker":       self.tube_on_shaker,
            "cap_holder_empty":     self.cap_holder_empty,
            "cap_in_holder":        self.cap_in_holder,
            "tube_in_2ml_rack":     self.tube_in_2ml_rack,
        }
