# runner.py
# ProtocolRunner — reads protocol/protocol.yaml and executes it.
#
# How it works:
#   1. Loads states + dependency graph from protocol.yaml
#   2. Resolves execution order using topological sort (respects requires)
#   3. For each tube, walks the states in order
#   4. Delegates execution of each state to a handler registered by the workflow
#
# The workflow registers handlers like:
#   runner.register("inspected", self.phase1_inspect_decap)
#
# This keeps protocol.yaml as the authority on ordering,
# and workflow.py as the authority on how each state is reached.

from pathlib import Path
from typing import Callable

import yaml


class ProtocolRunner:

    def __init__(self, rt, protocol_path: Path, params: object):
        self.rt = rt
        self.params = params
        self._handlers: dict[str, Callable[[int], None]] = {}

        with open(protocol_path) as f:
            data = yaml.safe_load(f)

        raw = data["states"]
        self._states: list[dict] = [{"name": k, **v} for k, v in raw.items()] if isinstance(raw, dict) else raw
        self._goal: list[str] = data["goal"]
        self._order = self._topological_sort()

    # ── Registration ─────────────────────────────────────────────────────────

    def register(self, state_name: str, handler: Callable[[int], None]):
        """Register a handler for a state. Handler receives tube index i."""
        self._handlers[state_name] = handler

    # ── Execution ─────────────────────────────────────────────────────────────

    def run(self, batch_size: int):
        """Execute the full protocol for batch_size tubes."""
        completed: set[str] = set()

        for state in self._order:
            name = state["name"]
            handler = self._handlers.get(name)

            if state.get("background"):
                # background states (e.g. shaker) — handler runs once, not per tube
                self.rt.step(f"State: {name} [background]")
                if handler:
                    handler()
                completed.add(name)
                continue

            if state.get("optional") and name not in self._handlers:
                self.rt.step(f"State: {name} [skipped — no handler]", level="warning")
                completed.add(name)
                continue

            if not handler:
                raise RuntimeError(f"No handler registered for state '{name}'")

            self.rt.step(f"State: {name}")
            for i in range(batch_size):
                handler(i)

            completed.add(name)

        missing = [g for g in self._goal if g not in completed]
        if missing:
            raise RuntimeError(f"Protocol incomplete — goal states not reached: {missing}")

        self.rt.step("Protocol complete — all goal states reached", level="success")

    # ── Topological sort ──────────────────────────────────────────────────────

    def _topological_sort(self) -> list[dict]:
        """Return states in an order that respects all requires dependencies."""
        by_name = {s["name"]: s for s in self._states}
        visited: set[str] = set()
        order: list[dict] = []

        def visit(name: str):
            if name in visited:
                return
            for dep in by_name[name].get("requires") or []:
                visit(dep)
            visited.add(name)
            order.append(by_name[name])

        for state in self._states:
            visit(state["name"])

        return order
