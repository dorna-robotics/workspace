"""ph_meter example — vision/sensor checks. Empty in this minimal example."""


class Checks:

    def __init__(self, rcp, rt, **kwargs):
        self.rcp = rcp
        self.rt  = rt

    def register(self, runner) -> None:
        pass
