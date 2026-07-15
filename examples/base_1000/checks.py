"""base example — vision/sensor checks. Empty in the seed project."""


class Checks:

    def __init__(self, rcp, rt, **kwargs):
        self.rcp = rcp
        self.rt  = rt

    def register(self, runner) -> None:
        pass
