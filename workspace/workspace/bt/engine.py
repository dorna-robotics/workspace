"""BT engine — the run loop that ticks a tree at a fixed rate.

Responsibilities:

* Tick the root behaviour at the configured rate (default 10 Hz).
* Honour ``workspace.Runtime`` lifecycle: pause stops ticking; resume
  resumes; kill aborts the tree and returns; **park** lets the current
  action finish, then swaps in a cleanup tree of all ``trigger="park"``
  actions, runs that to completion, and exits.
* Catch a ``ReplanRequested`` signal raised by any leaf (or by an
  observer) and rebuild the tree mid-run before continuing.

The engine is reusable across every project — projects supply the
root behaviour, a rebuild callback (used by replan), and a
build-park-tree callback (used when the operator clicks Park).

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
        max_replans: Runaway backstop — abort after this many
            *consecutive replans with zero forward progress* (fact state
            unchanged between replans, observed via ``progress_probe``).
            Any action succeeding resets the count, so operator-recovered
            failures and window-completion replans never accumulate
            toward an abort over a long run; only a loop that is failing
            and going nowhere hits the cap. Without a probe wired the
            count is total replans per run. Default 50.
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
        build_park_tree: A zero-arg callable that returns a fresh root
            behaviour wrapping every ``trigger="park"`` action in a
            sequence. Invoked when the runtime transitions to
            ``parking`` (operator clicked Park). ``None`` means "no
            cleanup tree — just exit cleanly when Park is seen". The
            engine completes the current action before switching.
        runtime: Workspace ``Runtime`` instance. The engine consults its
            ``paused`` / ``stopped`` / ``parking`` flags between ticks
            and pauses / exits / runs cleanup accordingly. Optional —
            for tests, pass ``None``.
        progress_probe: Zero-arg callable returning a hashable snapshot
            of the world fact state (the launcher passes the live facts
            as a frozenset). Compared between consecutive replans: a
            change means some action's effects applied — forward
            progress — and the replan counter resets. ``None`` disables
            the reset (counter counts total replans per run).
        config: Tick rate, replan cap, etc.

    Lifecycle returned values from ``run()``:
        Status.SUCCESS — root reached SUCCESS (or Park cleanup finished).
        Status.FAILURE — root reached FAILURE.
        Status.INVALID — kill / abort during run (or hit replan cap).
    """

    def __init__(
        self,
        root: py_trees.behaviour.Behaviour,
        rebuild: Optional[Callable[[], py_trees.behaviour.Behaviour]] = None,
        build_park_tree: Optional[
            Callable[[], Optional[py_trees.behaviour.Behaviour]]
        ] = None,
        runtime: Optional[object] = None,
        progress_probe: Optional[Callable[[], object]] = None,
        config: Optional[EngineConfig] = None,
    ):
        self._root = root
        self._rebuild = rebuild
        self._build_park_tree = build_park_tree
        self._runtime = runtime
        self._progress_probe = progress_probe
        self._cfg = config or EngineConfig()
        self._replans = 0
        self._last_replan_facts: Optional[object] = None
        # Set once we've handed control over to the trigger="park" subtree
        # so we don't loop back into rebuild/replan logic.
        self._in_cleanup = False

    # ── Public API ─────────────────────────────────────────────────────

    def snapshot(self) -> str:
        """Return a human-readable ASCII snapshot of the current tree
        status. Cheap — operators can call this at any time (from a
        debugger, a panel, a Slack bot) to see what the BT is doing.

        Same output as ``workspace.bt.visualizer.ascii_status(root)``,
        re-exposed on the engine so callers don't need to know about
        the visualizer module.
        """
        from workspace.bt.visualizer import ascii_status
        return ascii_status(self._root)

    def active_path(self) -> list:
        """Walk from root to the currently-active leaf, returning a list
        of node names. Useful for one-line "what's running right now"
        output — e.g. ``["pace_bt/root", "pace_bt/body", "decap(t3)"]``.
        """
        path = []

        def _walk(node):
            path.append(node.name)
            for child in getattr(node, "children", []) or []:
                if child.status == py_trees.common.Status.RUNNING:
                    _walk(child)
                    return
            # Leaf — done.

        _walk(self._root)
        return path

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

                # Park: lets the current action finish, then runs the
                # trigger="park" cleanup subtree.
                #
                # Critical: WAIT for the active leaf's worker thread to
                # exit before swapping trees. Recipes are blocking — they
                # don't observe the parking flag — so if we tear down the
                # tree mid-motion, the worker keeps issuing robot commands
                # while the cleanup tree queues a tool-swap. Two streams
                # of motion commands → robot alarm. Polling the worker
                # here is the only safe handoff point.
                if self._runtime_parking() and not self._in_cleanup:
                    leaf = self._active_recipe_leaf()
                    if leaf is not None:
                        worker = getattr(leaf, "_worker", None)
                        if worker is not None and worker.is_alive():
                            time.sleep(min(period, 0.1))
                            next_tick = time.monotonic()
                            continue
                    # Defer Park while the robot is holding a picked item:
                    # keep ticking the plan until the current item is placed
                    # (the hand is empty), so a graceful Park never strands a
                    # load mid-air (drop / collision risk). Only with an empty
                    # hand do we swap in the trigger="park" cleanup tree.
                    if not self._tool_holds_load():
                        if not self._enter_cleanup():
                            # nothing to clean (no trigger="park" actions)
                            return py_trees.common.Status.SUCCESS
                    # else: hand not empty — fall through and advance the
                    # plan one more action, then re-check at the next boundary

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
                    if self._in_cleanup:
                        # Cleanup subtree should not request a replan;
                        # log it and treat as a leaf failure so we exit.
                        log.warning(
                            "BTEngine: ReplanRequested during cleanup — ignoring (%s)",
                            ex.reason,
                        )
                        return py_trees.common.Status.FAILURE
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
        # Killed runtime = hard abort: exit before the next tick instead
        # of burning replans on actions that die at their first checkpoint.
        if getattr(self._runtime, "killed", False):
            return True
        s = getattr(self._runtime, "stopped", None)
        return bool(s() if callable(s) else s)

    def _runtime_parking(self) -> bool:
        if self._runtime is None:
            return False
        e = getattr(self._runtime, "parking", None)
        return bool(e() if callable(e) else e)

    def _tool_holds_load(self) -> bool:
        """True if the robot's mounted tool is holding a picked item.

        Used to defer Park until the hand is empty. Reads ``core``
        (Runtime.robot_api). Fail-safe: any error returns False, so an
        undetectable state never blocks Park (preserves old behaviour)."""
        core = getattr(self._runtime, "robot_api", None)
        fn = getattr(core, "tool_holds_load", None)
        try:
            return bool(fn()) if callable(fn) else False
        except Exception:
            return False

    def _active_recipe_leaf(self):
        """Find the currently-running RecipeAction leaf, if any.

        Returns the leaf whose ``_worker`` thread is in flight, so the
        Park path can wait for it to finish before tearing down the
        tree. ``None`` when no leaf is mid-motion (tree quiescent).
        """
        def walk(node):
            children = getattr(node, "children", []) or []
            if not children:
                return node if node.status == py_trees.common.Status.RUNNING else None
            for c in children:
                if c.status == py_trees.common.Status.RUNNING:
                    leaf = walk(c)
                    if leaf is not None:
                        return leaf
            return None
        return walk(self._root)

    def _enter_cleanup(self) -> bool:
        """Swap the live tree for the trigger="park" cleanup subtree.

        Returns True if a cleanup tree is now in place (engine should
        keep ticking), False if there's no cleanup work to do (engine
        should exit immediately, SUCCESS).
        """
        self._in_cleanup = True
        log.info("BTEngine: Park requested — switching to cleanup subtree")
        # Terminate the live plan's active branch cleanly so any
        # currently-running leaf gets a stop() callback.
        self._safe_terminate(self._root)
        if self._build_park_tree is None:
            log.info("BTEngine: no build_park_tree provided — exiting")
            return False
        try:
            cleanup = self._build_park_tree()
        except Exception:
            log.exception("BTEngine: build_park_tree raised — exiting")
            return False
        if cleanup is None:
            log.info("BTEngine: no trigger='park' actions in project — exiting")
            return False
        self._root = cleanup
        return True

    def _handle_replan(self, ex: ReplanRequested) -> bool:
        """Returns True if the replan succeeded (engine should continue),
        False to abort (rebuild not provided, or replan cap hit)."""
        if self._rebuild is None:
            log.warning("BTEngine: ReplanRequested but no rebuild fn — aborting")
            return False
        # Forward progress resets the cap. The cap is a runaway detector
        # ("N consecutive replans going nowhere"), not a per-run quota:
        # an operator-recovered device failure or a completed plan window
        # must never accumulate toward an abort hours later. Facts change
        # only when an action's effects apply, so a fact-state change
        # between replans == at least one action succeeded in between.
        if self._progress_probe is not None:
            try:
                snap = self._progress_probe()
            except Exception:
                log.exception("BTEngine: progress_probe raised — skipping reset check")
                snap = None
            if snap is not None:
                if self._last_replan_facts is not None and snap != self._last_replan_facts:
                    self._replans = 0
                self._last_replan_facts = snap
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
