"""pace_bt vision/sensor checks. Referenced by name from actions.py.

Framework reference: ../../../docs/bt-framework-guide.md §13
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

    def register(self, runner) -> None:
        runner.register_check("source_tube_present",  self.source_tube_present)
        runner.register_check("tube_in_working_rack", self.tube_in_working_rack)
