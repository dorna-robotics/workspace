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
import threading
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


def _hashable(x):
    """Items may arrive as JSON lists; compare them as tuples."""
    return tuple(_hashable(v) for v in x) if isinstance(x, list) else x


@dataclass
class EngineConfig:
    """Knobs for the tick loop.

    Attributes:
        tick_hz: The FALLBACK tick rate. A finished worker wakes the
            loop at once (RecipeAction signals ``runtime._bt_wake``),
            so the hand-over from one action to the next costs
            milliseconds, not a tick: at 10 Hz the bench measured 90 ms
            of standing still at every action boundary (apc, 13 h,
            2026-09-23). The period still bounds how long a pause,
            park or kill goes unnoticed while nothing finishes.
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
        replan_items: Optional[Callable[[], list]] = None,
        replan_prepare: Optional[Callable[[dict], dict]] = None,
        replan_commit: Optional[Callable[[dict], str]] = None,
    ):
        self._root = root
        # Operator Replan (Runtime.replan, bt-framework-guide §8.6): the
        # launcher's callbacks (bt/remove.py) — the items to offer; a
        # choice CHECKED (raises ValueError, nothing changed); a checked
        # choice APPLIED (facts + 3D scene, returns a summary).
        self._replan_items = replan_items
        self._replan_prepare = replan_prepare
        self._replan_commit = replan_commit
        # Applied while actions not tied to the removed items were
        # paused mid-way: they finish first, then the plan is rebuilt.
        self._rebuild_pending = False
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
        # Workers wake the loop through the runtime (RecipeAction._target).
        self._wake = threading.Event()
        if self._runtime is not None:
            try:
                self._runtime._bt_wake = self._wake
            except Exception:
                pass
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
                    # KEEP TICKING under a park. The tree keeps being
                    # ticked so in-flight leaves can finish and report;
                    # leaves that have not started refuse to start while
                    # parking (RecipeAction._park_hold) unless the hand
                    # is full and they use the robot — the item must be
                    # put down. Cleanup starts the moment no ROBOT
                    # worker is alive and the hand is empty. Non-robot
                    # workers (a shake, a rest) never block the park:
                    # their device op finishes on its own thread.
                    #
                    # Why the whole tree, and why robot workers only:
                    # from_schedule's Parallel may put the shaker or rest
                    # branch BEFORE the robot branch, so
                    # "the first RUNNING leaf" was the 300 s Shake, and
                    # the engine sat here without ticking while the robot
                    # stood still with a vial in the gripper (bna bench,
                    # 2026-09-21).
                    if not self._robot_workers_alive() and not self._tool_holds_load():
                        if not self._enter_cleanup():
                            # nothing to clean (no trigger="park" actions)
                            return py_trees.common.Status.SUCCESS
                    # else: a robot leaf is mid-motion, or the hand is
                    # full — tick on; re-check at the next tick

                # Replan applied while untied actions were paused mid-way
                # (see _replan_paused): they have finished — rebuild.
                # Leaves of the old tree did not start meanwhile
                # (runtime.replan_hold). Park takes over from a pending
                # rebuild: its cleanup replaces the tree anyway.
                if self._rebuild_pending and not self._runtime_paused():
                    if self._runtime_parking() or self._in_cleanup:
                        self._rebuild_pending = False
                        self._runtime_call("set_replan_hold", False)
                    elif not self._alive_workers():
                        self._rebuild_pending = False
                        self._runtime_call("set_replan_hold", False)
                        if not self._handle_replan(ReplanRequested("operator replan — rebuild")):
                            return self._abort()
                        continue

                if self._runtime_paused():
                    # Replan lives inside Pause (bt-framework-guide §8.6).
                    if self._runtime_replanning() and not self._in_cleanup:
                        if not self._replan_paused():
                            return self._abort()
                    # Don't tick during pause. Sleep a short period and
                    # re-check. The tree's currently-active leaf will be
                    # ticked again as soon as we resume — its state is
                    # preserved.
                    time.sleep(min(period, 0.1))
                    next_tick = time.monotonic()  # reset cadence
                    continue

                # Tick once. Replan signals come out as ReplanRequested
                # propagating up from a leaf via the tree's update().
                # Cleared BEFORE the tick: a worker that finishes during
                # it sets the event and the next wait returns at once.
                self._wake.clear()
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

                # Pace the loop: the period, or sooner when a worker
                # finishes.
                next_tick += period
                sleep_for = next_tick - time.monotonic()
                if sleep_for > 0:
                    if self._wake.wait(sleep_for):
                        next_tick = time.monotonic()
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

    def _replan_paused(self) -> bool:
        """One step of an operator Replan, run from the paused loop
        (never ticks). Returns False only when the engine must abort
        (a rebuild failed).

        1. ``opening`` -> offer the items (launcher callback).
        2. A choice arrived -> it is applied only when EVERY worker in
           flight stands at a checkpoint (Runtime.paused_workers): no
           robot command of any of them is on the wire. Until then the
           choice waits and the dialog says what for.
        3. CHECK everything first (launcher ``replan_prepare`` — the
           items, their dependents, the 3D models): a refusal changes
           nothing and goes back to the dialog.
        4. APPLY: the actions tied to a removed item — bound to it, or
           naming it in their precondition (a bank's shake) — are
           dropped without halting the robot (RecipeAction._replan_drop);
           the items leave the plan and the 3D scene (``replan_commit``).
        5. Rebuild the plan now if nothing else is in flight; otherwise
           hold every new leaf until the untied actions that were paused
           mid-way have finished on Resume, then rebuild.
        """
        rt = self._runtime
        info = self._runtime_call("replan_info") if callable(getattr(rt, "replan_info", None)) \
            else getattr(rt, "replan_info", None)
        if not info:
            return True
        if info.get("phase") == "opening":
            items = []
            if self._replan_items is not None:
                try:
                    items = list(self._replan_items())
                except Exception:
                    log.exception("BTEngine: replan_items raised — offering none")
            self._runtime_call("replan_offer", items)
            return True
        choice = self._runtime_call("replan_take")
        if choice is None:
            return True
        alive = self._alive_workers()
        parked = self._runtime_call("paused_workers") or set()
        moving = [l.name for l in alive
                  if getattr(getattr(l, "_worker", None), "ident", None) not in parked]
        if moving:
            self._runtime_call("replan_put_back", choice,
                               f"waiting for {', '.join(sorted(moving))} to reach a stop")
            return True
        try:
            if self._replan_prepare is None or self._replan_commit is None:
                raise ValueError("this run cannot remove items (no item model)")
            prep = self._replan_prepare(choice)
            gone = set(map(_hashable, prep.get("items") or []))
            tied = [l for l in alive if self._leaf_tied(l, gone)]
            summary = self._replan_commit(prep)
        except ValueError as ex:
            self._runtime_call("replan_failed", str(ex))
            return True
        except Exception as ex:
            log.exception("BTEngine: replan apply raised")
            self._runtime_call("replan_failed", f"internal error: {type(ex).__name__}: {ex}")
            return True
        for leaf in tied:
            drop = getattr(leaf, "_replan_drop", None)
            if callable(drop):
                drop()
        self._runtime_call("wake_workers")
        rest = [l for l in alive if l not in tied]
        names = ", ".join(l.name for l in tied)
        log.warning("BTEngine: operator replan — %s%s", summary,
                    f"; dropped {names}" if names else "")
        step = getattr(rt, "step", None)
        if callable(step):
            try:
                step(f"Replan: {summary}" + (f"; stopped {names}" if names else "")
                     + (f"; {len(rest)} paused action(s) finish on Resume, then the new plan"
                        if rest else "; the new plan runs on Resume"), level="info")
            except Exception:
                pass
        self._runtime_call("replan_done")
        if rest:
            self._runtime_call("set_replan_hold", True)
            self._rebuild_pending = True
            return True
        return self._handle_replan(ReplanRequested(f"operator replan — {summary}"))

    @staticmethod
    def _leaf_tied(leaf, gone: set) -> bool:
        """Is this in-flight leaf's action tied to a removed item? It
        is when it is BOUND to one (its item parameter), or its
        precondition NAMES one (a shake whose pre spans its bank)."""
        cls = getattr(leaf, "_cls", None)
        if cls is None:
            return False
        item = getattr(leaf, "_item", None)
        if getattr(cls, "params", None) and _hashable(item) in gone:
            return True
        try:
            from workspace.bt.dsl import _extract_pre_facts, state_to_frozen
            inst = leaf._instance
            inst.state = state_to_frozen(leaf.ctx.state)
            pos, neg = _extract_pre_facts(inst.pre(*leaf._params()))
            return any(_hashable(a) in gone for f in (pos | neg) for a in f[1:])
        except Exception:
            log.exception("BTEngine: could not read %s's precondition — treating it as tied", leaf.name)
            return True

    def _runtime_replanning(self) -> bool:
        if self._runtime is None:
            return False
        r = getattr(self._runtime, "replan_requested", False)
        return bool(r() if callable(r) else r)

    def _runtime_call(self, name: str, *args):
        fn = getattr(self._runtime, name, None)
        return fn(*args) if callable(fn) else None

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

    def _alive_workers(self):
        """Every leaf in the tree with a worker IN FLIGHT — the whole
        tree, every branch of every parallel. In flight means the leaf
        still holds its worker: started and not yet reported. A thread
        that has just finished counts until the leaf's next tick turns
        it into SUCCESS and applies the effects (RecipeAction.terminate
        drops the reference then) — tearing the tree down in between
        loses those effects (base: Park right after Start ended, the
        cleanup's own pre saw no ``started``)."""
        out = []
        def walk(node):
            children = getattr(node, "children", []) or []
            if not children:
                if getattr(node, "_worker", None) is not None:
                    out.append(node)
                return
            for c in children:
                walk(c)
        walk(self._root)
        return out

    def _robot_workers_alive(self) -> bool:
        """Is any leaf that USES THE ROBOT mid-motion? A leaf without
        the ``uses_robot`` hook counts as a robot leaf (safe default)."""
        for leaf in self._alive_workers():
            fn = getattr(leaf, "uses_robot", None)
            if not callable(fn) or fn():
                return True
        return False

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
        # The cleanup tree's own leaves must start while parking — they
        # ARE the park. Mark them exempt from the leaf-level park hold.
        def _exempt(node):
            if not (getattr(node, "children", []) or []):
                node._park_exempt = True
            for c in getattr(node, "children", []) or []:
                _exempt(c)
        _exempt(cleanup)
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
