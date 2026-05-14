# pace_bt/checks.py
# Pre/post verification checks for actions in actions.py.
#
# Each method signature: (item_i: int) -> bool | (passed: bool, message: str)
#   - passed=True   → check passes, the action runs (or its result is accepted)
#   - passed=False  → for pre_check, the action is SKIPPED (success, BT moves on)
#                     for post_check, the action FAILS (BT may retry/replan)
#
# Returning ``(False, "tube missing")`` is the pace_or convention — the
# framework logs the message at INFO level on failure. Returning a plain
# bool is also fine.
#
# Currently stubs returning True — replace each body with a real camera
# / sensor call when the vision system is ready.


class Checks:
    """Project-specific verification methods.

    The framework instantiates this once, calls ``register(...)`` to
    collect ``name → bound method`` pairs into a dict, and looks up
    that dict whenever an Action declares ``pre_check`` or
    ``post_check`` by name.

    Args:
        rcp: ``{alias: recipe_instance}`` dict from
            ``workspace.bt.load_recipes``. Use ``self.rcp["inspector"]``
            for camera calls.
        rt: Workspace runtime (``ws.rt``). Use for cancellation-aware
            polling if your check loops.
    """

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

    def register(self, runner) -> None:
        """Hand the framework's registrar (or pace_or's runner) the
        name → method bindings."""
        runner.register_check("source_tube_present",  self.source_tube_present)
        runner.register_check("tube_in_working_rack", self.tube_in_working_rack)
