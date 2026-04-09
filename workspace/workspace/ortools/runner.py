# workspace/ortools/runner.py
# ORRunner — executes schedules produced by ORScheduler.
#
# Features:
# - Pre/post checks: runs verification functions before/after each action.
#   Checks are defined in 5_verify/checks.py and registered via register_check().
# - on_fail: "pause" stops the run and waits for user to clear the issue.
# - Rolling horizon: replan every `horizon` tasks so the system adapts
#   after failures or mid-run changes.
# - Background task timing: robot executes other tasks while a background
#   state (shaker, incubator) runs; runner waits before dependent tasks.
# - Background cleanup: optional per-state cleanup handler called after
#   the background timer expires (e.g. stop_shaking).

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import yaml

from workspace.ortools.scheduler import ORScheduler


class ORRunner:
    """
    Executes a lab protocol using OR-Tools for scheduling.

    Args:
        rt:               Runtime object (provides rt.step, rt.delay, rt.pause).
        protocol_path:    Path to protocol/protocol.yaml.
        batch_size:          Number of items — set dynamically at run time.
        cfg:              Params namespace (for real background durations).
        horizon:          Rolling window size (None = plan all tasks at once).
    """

    def __init__(
        self,
        rt,
        protocol_path: Path,
        batch_size: int,
        horizon: int | None = None,
    ):
        self.rt       = rt
        self._horizon = horizon

        self._scheduler  = ORScheduler(protocol_path)
        self._handlers:   dict[str, Callable] = {}
        self._checks:     dict[str, Callable] = {}

        with open(protocol_path) as f:
            data = yaml.safe_load(f)

        raw = data["states"]
        self._states = [{"name": k, **v} for k, v in raw.items()] if isinstance(raw, dict) else raw
        self._goal   = set(data.get("goal", []))
        # Separate normal states from trigger states
        self._trigger_states = {s["name"]: s for s in self._states if s.get("trigger")}
        self._states = [s for s in self._states if not s.get("trigger")]
        self._snames = [s["name"] for s in self._states]
        self._smap   = {s["name"]: s for s in self._states}

        # Real-time durations for background states
        self._bg_dur: dict[str, float] = {}
        for s in self._states:
            if not s.get("background"):
                continue
            sn = s["name"]
            d  = s.get("duration")
            self._bg_dur[sn] = float(d) if isinstance(d, (int, float)) else 1.0

    # ── Registration ─────────────────────────────────────────────────────────

    def register_state(self, state_name: str, handler: Callable):
        """Register the execution handler for a state."""
        self._handlers[state_name] = handler

    def register_check(self, name: str, fn: Callable):
        """
        Register a verification check function.

        fn signature: (item_i: int) -> bool
          - True = passed, False = failed
          - Use rt.step() inside the check for custom messages

        Check names are referenced in protocol.yaml under pre/post fields.
        """
        self._checks[name] = fn

    # ── Main execution loop ───────────────────────────────────────────────────

    def has_trigger(self, trigger_name: str) -> bool:
        """Check if a trigger state is defined in the protocol."""
        return any(s.get("trigger") == trigger_name for s in self._trigger_states.values())

    def run_trigger(self, trigger_name: str):
        """Execute a trigger state (e.g. 'stop'). Called outside normal scheduling."""
        for sname, s in self._trigger_states.items():
            if s.get("trigger") == trigger_name:
                handler = self._handlers.get(sname)
                if handler:
                    self.rt.step(f"→ {sname}")
                    handler(0)
                return

    def run(self, batch_size: int):
        """
        Execute the full protocol for batch_size.

        Rolling horizon replanning happens automatically every `horizon` tasks.
        After a fault, update completed externally and call run() again to resume.
        """
        from workspace.runtime import EndRequested

        completed: dict[str, set[int]] = {n: set() for n in self._snames}
        bg_active: dict[str, tuple[float, float]] = {}
        total_tasks = len(self._snames) * batch_size
        done_count = 0

        def all_done() -> bool:
            return all(len(completed.get(g, set())) >= batch_size for g in self._goal)

        self.rt.step(0, level="progress")
        try:
            while not all_done():
                schedule = self._scheduler.schedule(
                    batch_size, completed,
                    horizon_tasks=self._horizon,
                )
                if not schedule:
                    break

                for (state_name, item_i) in schedule:
                    s       = self._smap[state_name]
                    is_bg   = s.get("background", False)

                    # Wait for any still-running background prereqs
                    for req in s.get("requires", []):
                        if req in bg_active:
                            self._wait_bg(req, bg_active)

                    # ── Pre-checks ────────────────────────────────────────────
                    pre = s.get("pre_check")
                    if pre and not self._run_checks(pre, item_i):
                        continue

                    handler = self._handlers.get(state_name)

                    if is_bg:
                        real_dur = self._bg_dur.get(state_name, 120.0)
                        self.rt.step(f"OR → {state_name} [background, {real_dur:.0f}s]")
                        if handler:
                            handler(0)
                        bg_active[state_name] = (time.time(), real_dur)
                        for t in range(batch_size):
                            completed[state_name].add(t)
                        done_count += batch_size
                    else:
                        self.rt.step(f"OR → {state_name} [{item_i + 1}/{batch_size}]")
                        if handler:
                            handler(item_i)
                        completed[state_name].add(item_i)
                        done_count += 1

                    self.rt.step(int(done_count / total_tasks * 100), level="progress")

                    # ── Post-checks ───────────────────────────────────────────
                    post = s.get("post_check")
                    if post and not is_bg:
                        self._run_checks(post, item_i)

                if not self._horizon:
                    break

            # Drain any remaining background tasks before finishing
            for bg_name in list(bg_active.keys()):
                self._wait_bg(bg_name, bg_active)

            missing = [g for g in self._goal if len(completed.get(g, set())) < batch_size]
            if missing:
                raise RuntimeError(f"OR runner did not reach goal states: {missing}")

            self.rt.step(100, level="progress")
            self.rt.step("Protocol complete — all goal states reached", level="success")

        except EndRequested:
            # Graceful stop — re-raise for workflow to handle
            raise

    # ── Check execution ───────────────────────────────────────────────────────

    def _run_checks(self, checks, item_i: int) -> bool:
        """
        Run one or more named checks. Returns True if all pass, False on first failure.
        The check itself handles messaging and pausing via rt.step() / rt.pause().
        """
        names = [checks] if isinstance(checks, str) else checks
        for name in names:
            fn = self._checks.get(name)
            if fn is None:
                continue
            result = fn(item_i)
            passed = result[0] if isinstance(result, tuple) else bool(result)
            if not passed:
                return False
        return True

    # ── Background wait helper ────────────────────────────────────────────────

    def _wait_bg(
        self,
        bg_name: str,
        bg_active: dict[str, tuple[float, float]],
    ):
        """Wait for a background task to finish, then call its cleanup handler."""
        if bg_name not in bg_active:
            return
        start, dur = bg_active.pop(bg_name)
        remaining = dur - (time.time() - start)
        if remaining > 0:
            self.rt.step(f"Waiting {remaining:.0f}s for {bg_name} to finish")
            self.rt.delay(remaining)
        post = self._smap.get(bg_name, {}).get("post_check")
        if post:
            for name in ([post] if isinstance(post, str) else post):
                cleanup = self._handlers.get(name)
                if cleanup:
                    cleanup()
