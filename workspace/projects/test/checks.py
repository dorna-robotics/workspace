"""Checks — pre/post-state assertions registered alongside States.

Empty for this project: a single rotation has no preconditions worth
verifying. Mirrors the canonical project template so the structure
stays consistent — fill in here when a state needs an assertion
before it can run, and bind each check inside ``register(runner)``.
"""


class Checks:
    def __init__(self, rcp, rt, **kwargs):
        self.rcp = rcp
        self.rt = rt

    def register(self, runner):
        """Bind each check to its name. Called once by BaseWorkflow at
        workflow startup; framework-reserved."""
        # No checks in this project — fill in once a state needs one:
        #   runner.register_check("tube_in_rack", self.tube_in_rack)
        return
