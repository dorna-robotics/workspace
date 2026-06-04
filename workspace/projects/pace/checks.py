"""pace_bt vision/sensor checks. Referenced by name from actions.py.

Framework reference: ../../../docs/bt-framework-guide.md §13
Mirrors pace_or/checks.py verbatim (same scene, same vision needs).
"""


class Checks:

    def __init__(self, rcp, rt, **kwargs):
        self.rcp = rcp
        self.rt  = rt

    def source_tube_present(self, item_i) -> tuple[bool, str]:
        # TODO: camera.detect("tube", SOURCE[item_i])
        return True, "source tube present"

    def tube_in_working_rack(self, item_i) -> tuple[bool, str]:
        # TODO: camera.detect("tube", WORKING[item_i])
        return True, "tube in working rack"

    def shaker_slot_empty(self, item_i) -> tuple[bool, str]:
        # TODO: camera.is_empty(SHAKER_SLOTS[item_i])
        return True, "shaker slot empty"

    def tube_on_shaker(self, item_i) -> tuple[bool, str]:
        # TODO: camera.detect("tube", SHAKER_SLOTS[item_i])
        return True, "tube on shaker"

    def cap_holder_empty(self, item_i) -> tuple[bool, str]:
        # TODO: camera.is_empty(CAP_FEEDER[item_i])
        return True, "cap holder empty"

    def cap_in_holder(self, item_i) -> tuple[bool, str]:
        # TODO: camera.detect("cap", CAP_FEEDER[item_i])
        return True, "cap in holder"

    def tube_in_2ml_rack(self, item_i) -> tuple[bool, str]:
        # TODO: camera.detect("tube", RACK_2ML_END[item_i])
        return True, "tube in 2ml rack"

    def stop_shaken(self, item_i) -> tuple[bool, str]:
        # post-check for the Shaken background action — stop the shaker
        # and report success. Stub: just return True.
        return True, "shaker stopped"

    def register(self, runner) -> None:
        runner.register_check("source_tube_present",  self.source_tube_present)
        runner.register_check("tube_in_working_rack", self.tube_in_working_rack)
        runner.register_check("shaker_slot_empty",    self.shaker_slot_empty)
        runner.register_check("tube_on_shaker",       self.tube_on_shaker)
        runner.register_check("cap_holder_empty",     self.cap_holder_empty)
        runner.register_check("cap_in_holder",        self.cap_in_holder)
        runner.register_check("tube_in_2ml_rack",     self.tube_in_2ml_rack)
        runner.register_check("stop_shaken",          self.stop_shaken)
