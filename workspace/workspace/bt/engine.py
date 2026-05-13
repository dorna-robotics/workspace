"""BT engine — the run loop that ticks a tree at a fixed rate.

Responsibilities:

* Tick the root behaviour at the configured rate (default 10 Hz).
* Honour ``workspace.Runtime`` lifecycle: pause stops ticking; resume
  resumes; kill aborts the tree and returns.
* Catch a ``ReplanRequested`` signal raised by any leaf (or by an
  observer) and rebuild the tree mid-run before continuing.
* Surface tree progress as workspace steps (one step per tick where the
  active leaf changed), so the existing runtime steps UI keeps working.

The engine is reusable across every project — projects only supply the
root behaviour and a rebuild callback (used by replan).

Threading: the engine runs on the *calling thread* (typically the
runtime's worker thread). It's not its own thread. This keeps lifecycle
crystal-clear — when ``run()`` returns, the BT is done.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

import py_trees


log = logging.getLogger(__name__)


# ── Replan signal ──────────────────────────────────────────────────────────


class ReplanRequested(Exception):
    """Raised by a leaf (or an observer) to request a re-plan + tree rebuild.

    Carries an optional ``reason`` string for logs / steps UI. The engine
    catches this, calls the rebuild callback, swaps in the new tree, and
    continues ticking — without exiting ``run()``.

    Typical raisers:
      * Leaves whose preconditions failed unexpectedly (world drifted).
      * Observers (separate threads) watching device-bus events that
        invalidate the current plan.
    """

    def __init__(self, reason: str = ""):
        super().__init__(reason or "replan requested")
        self.reason = reason


# ── Engine ─────────────────────────────────────────────────────────────────


@dataclass
class EngineConfig:
    """Knobs for the tick loop.

    Attributes:
        tick_hz: How often to tick the tree. 10 Hz is the py_trees
            convention and is plenty for lab work where actions take
            seconds-to-minutes; the planning algorithms are 100-1000x
            faster than any hardware action.
        max_replans: Safety cap — refuse to rebuild more than this many
            times in one run. Without a cap, a faulty leaf could trigger
            an infinite replan loop. Default 50; lab batches rarely need
            more than a handful.
        idle_quiescence_s: If the tree stays SUCCESS/FAILURE for this
            many ticks, exit. Default 0 (exit immediately on SUCCESS or
            top-level FAILURE).
    """

    tick_hz: float = 10.0
    max_replans: int = 50
    idle_quiescence_s: float = 0.0


class BTEngine:
    """Drives a tree to completion. One-shot — instantiate, ``run()``, done.

    Args:
        root: The root behaviour of the tree.
        rebuild: A zero-arg callable that returns a fresh root behaviour.
            Called when a ``ReplanRequested`` propagates up. Pass the
            project's tree-builder closed over its current context, so
            the rebuilt tree reflects whatever observed state changes
            triggered the replan. ``None`` disables replanning — the
            tree halts on ``ReplanRequested``.
        runtime: Workspace ``Runtime`` instance. The engine consults its
            ``paused`` / ``stopped`` flags between ticks and pauses /
            exits accordingly. Optional — for tests, pass ``None``.
        config: Tick rate, replan cap, etc.

    Lifecycle returned values from ``run()``:
        Status.SUCCESS — root reached SUCCESS.
        Status.FAILURE — root reached FAILURE.
        Status.INVALID — kill / abort during run (or hit replan cap).
    """

    def __init__(
        self,
        root: py_trees.behaviour.Behaviour,
        rebuild: Optional[Callable[[], py_trees.behaviour.Behaviour]] = None,
        runtime: Optional[object] = None,
        config: Optional[EngineConfig] = None,
    ):
        self._root = root
        self._rebuild = rebuild
        self._runtime = runtime
        self._cfg = config or EngineConfig()
        self._replans = 0

    # ── Public API ─────────────────────────────────────────────────────

    def run(self) -> py_trees.common.Status:
        """Tick until the tree completes or the runtime requests exit.

        Returns the final root status. Always idempotent on shutdown
        (terminate is called on the active subtree on the way out).
        """
        period = 1.0 / max(0.1, self._cfg.tick_hz)
        log.info(
            "BTEngine: starting tick loop @ %.1f Hz (period=%.0f ms)",
            self._cfg.tick_hz, period * 1000,
        )
        next_tick = time.monotonic()

        try:
            while True:
                # Runtime lifecycle check before ticking.
                if self._runtime_stopped():
                    log.info("BTEngine: runtime stopped — exiting")
                    return self._abort()

                if self._runtime_paused():
                    # Don't tick during pause. Sleep a short period and
                    # re-check. The tree's currently-active leaf will be
                    # ticked again as soon as we resume — its state is
                    # preserved.
                    time.sleep(min(period, 0.1))
                    next_tick = time.monotonic()  # reset cadence
                    continue

                # Tick once. Replan signals come out as ReplanRequested
                # propagating up from a leaf via the tree's update().
                try:
                    self._root.tick_once()
                except ReplanRequested as ex:
                    if not self._handle_replan(ex):
                        return self._abort()
                    continue
                except Exception:
                    log.exception("BTEngine: unhandled exception during tick")
                    return self._abort()

                status = self._root.status
                if status in (
                    py_trees.common.Status.SUCCESS,
                    py_trees.common.Status.FAILURE,
                ):
                    log.info("BTEngine: root reached %s — exiting", status.name)
                    return status

                # Pace the loop.
                next_tick += period
                sleep_for = next_tick - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)
                else:
                    # Behind schedule. Skip the catch-up; we'd rather miss
                    # ticks than burn CPU running back-to-back.
                    next_tick = time.monotonic()
        finally:
            self._safe_terminate(self._root)

    # ── Internals ──────────────────────────────────────────────────────

    def _runtime_paused(self) -> bool:
        if self._runtime is None:
            return False
        # Workspace Runtime exposes ``paused`` as an attribute or method.
        p = getattr(self._runtime, "paused", None)
        return bool(p() if callable(p) else p)

    def _runtime_stopped(self) -> bool:
        if self._runtime is None:
            return False
        s = getattr(self._runtime, "stopped", None)
        return bool(s() if callable(s) else s)

    def _handle_replan(self, ex: ReplanRequested) -> bool:
        """Returns True if the replan succeeded (engine should continue),
        False to abort (rebuild not provided, or replan cap hit)."""
        if self._rebuild is None:
            log.warning("BTEngine: ReplanRequested but no rebuild fn — aborting")
            return False
        if self._replans >= self._cfg.max_replans:
            log.error(
                "BTEngine: replan cap (%d) hit — aborting. Last reason: %s",
                self._cfg.max_replans, ex.reason,
            )
            return False
        self._replans += 1
        log.info(
            "BTEngine: replanning (#%d) — reason: %s",
            self._replans, ex.reason or "<unspecified>",
        )
        # Terminate the dying tree's active branch cleanly before swap.
        self._safe_terminate(self._root)
        try:
            self._root = self._rebuild()
        except Exception:
            log.exception("BTEngine: rebuild() raised — aborting")
            return False
        return True

    def _safe_terminate(self, root: py_trees.behaviour.Behaviour) -> None:
        try:
            root.stop(py_trees.common.Status.INVALID)
        except Exception:
            log.exception("BTEngine: stop(INVALID) raised on shutdown")

    def _abort(self) -> py_trees.common.Status:
        self._safe_terminate(self._root)
        return py_trees.common.Status.INVALID
