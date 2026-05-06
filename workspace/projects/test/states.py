"""States — one per protocol.yaml state.

Each handler takes an index ``i`` (the batch position) and runs the
physical work for that state at that index. ``register(runner)`` binds
each handler to its protocol-state name; BaseWorkflow calls it once at
workflow startup.

This project has exactly one state: ``rotated``. Each execution
rotates j5 by ``rotation_deg`` (configured via launch.yaml kwarg).
"""


class States:
    def __init__(self, rcp, rt, **kwargs):
        self.rcp = rcp
        self.rt = rt
        self.rotation_deg = int(kwargs.get("rotation_deg", 10))

    def rotated(self, i: int):
        """Single physical step: rotate j5 by ±``rotation_deg``.

        Direction alternates per iteration (even = +, odd = -) so the
        joint oscillates around its starting position rather than
        drifting in one direction across a long run.

        ``rt.step`` surfaces a timeline entry visible in the project
        page; the implicit checkpoint after each ``step`` honors any
        operator-initiated pause from the UI.
        """
        rt = self.rt
        delta = self.rotation_deg if (i % 2 == 0) else -self.rotation_deg
        rt.step(f"[#{i}] Rotating j5 by {delta:+d}°", level="info")
        self.rcp["inspector"].rotate(rotation=delta)
        rt.step(f"[#{i}] Rotation complete", level="success")

    def register(self, runner):
        """Bind each state handler to its protocol-state name. Called
        once by BaseWorkflow at workflow startup; framework-reserved."""
        runner.register_state("rotated", self.rotated)
