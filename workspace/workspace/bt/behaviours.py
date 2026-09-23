"""Base behaviour classes for the workspace BT framework.

Every leaf in a project's tree subclasses one of these. They handle the
common concerns (workspace context access, runtime pause/abort hooks,
recipe-call thread management, predicate lookup) so project code stays
small and uniform.

The hierarchy:

    py_trees.behaviour.Behaviour
        WorkspaceBehaviour            ── injects ctx (workspace + core + runtime)
            RecipeAction              ── wraps a (possibly long) recipe call
            PredicateCondition        ── tests a world-state predicate
            DeviceCondition           ── tests device-bus state

Project authors typically subclass ``RecipeAction``, ``PredicateCondition``,
or ``DeviceCondition``. Subclassing ``WorkspaceBehaviour`` directly is fine
for one-off leaves that don't fit the three common shapes.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

import py_trees

# ReplanRequested propagates from apply_effects when an Action with
# non-deterministic eff picks a non-default branch — see dsl._DSLActionLeaf.
from workspace.bt.engine import ReplanRequested


log = logging.getLogger(__name__)


@dataclass
class WorkspaceContext:
    """Bundle of references passed down to every leaf in the tree.

    Keeping this in one object means a leaf's constructor signature stays
    short (only its action parameters) and project tree-builders can be
    re-parameterised by swapping a single ctx.

    Fields:
        workspace: The Workspace SDK root (scene graph + recipes registry).
        core: The Core component (robot api, rail config, sim flag, …).
        runtime: workspace.Runtime — pause/stop/kill aware proxy.
        state: A mutable dict the framework maintains, holding the current
            world-state predicate values. Conditions read from this;
            actions update it via effects after success. The PDDL planner
            and replanner share this same dict so observations propagate.
        recipes: Convenience alias for ``workspace.recipes`` if present.
        meta: Free-form per-tree metadata (batch_id, run_id, …).
    """

    workspace: Any
    core: Any
    runtime: Any
    state: Dict[str, Any] = field(default_factory=dict)
    recipes: Any = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def dump_state(self) -> Dict[str, Any]:
        """Return a JSON-serializable snapshot of the live world state.

        Predicate facts are listed as tuples; project-specific extras
        in ``state`` flow through unchanged (as long as they're
        serializable). Use for diagnose tools, panel UIs, audit logs.
        """
        facts = self.state.get("facts") or set()
        # frozenset/set of tuples → list of lists for JSON friendliness.
        out: Dict[str, Any] = {
            "facts": sorted(list(f) for f in facts),
        }
        for k, v in self.state.items():
            if k == "facts":
                continue
            out[k] = v
        return out


class WorkspaceBehaviour(py_trees.behaviour.Behaviour):
    """Base for every leaf in a workspace BT.

    Provides:
      * ``self.ctx`` — the ``WorkspaceContext`` injected at construction.
      * ``self.log`` — a module logger pre-labelled with the leaf name.
      * Hooks the framework relies on (``setup_done`` flag, etc.).

    Subclasses must override ``update()``. ``initialise()`` and
    ``terminate(new_status)`` are optional but should be tiny.
    """

    def __init__(self, name: str, ctx: WorkspaceContext):
        super().__init__(name=name)
        self.ctx = ctx
        self.log = logging.getLogger(f"bt.{name}")

    # py_trees doesn't enforce an update; we do, to surface mistakes early.
    def update(self) -> py_trees.common.Status:  # pragma: no cover - abstract
        raise NotImplementedError(
            f"{type(self).__name__}.update() must be implemented"
        )


# ── RecipeAction ────────────────────────────────────────────────────────────


class RecipeAction(WorkspaceBehaviour):
    """A leaf that calls one or more recipe methods on a worker thread.

    The recipe layer (``workspace.recipes.*``) is imperative and may take
    seconds to minutes. BT ticks must not block. This base class runs the
    recipe on a daemon thread and reports SUCCESS / FAILURE / RUNNING
    based on the worker's state.

    Subclasses override **one** method:

        def execute(self) -> bool:
            # Run the actual recipe calls. Return True on success,
            # False on failure. Raising is treated as failure.
            self.ctx.recipes["arm"].pick(...)
            self.ctx.recipes["scale"].place(...)
            return True

    Effects on world state after success belong in
    ``apply_effects(state: dict)`` — separated from execute so the framework
    can call it without re-running the recipe (used by replanner).

    Cancellation: if the BT aborts the leaf mid-execution
    (``terminate`` called with status != SUCCESS), the framework calls
    ``ctx.runtime.stop()`` to bring the robot to a safe halt — every
    workspace recipe respects runtime stop already.
    """

    def __init__(self, name: str, ctx: WorkspaceContext):
        super().__init__(name=name, ctx=ctx)
        self._worker: Optional[threading.Thread] = None
        self._result: Optional[bool] = None
        self._exc: Optional[BaseException] = None
        self._started_at: float = 0.0
        self._held: bool = False

    def uses_robot(self) -> bool:
        """Does this leaf move the robot? The engine's Park path waits
        only for robot leaves, and the park hold below lets only robot
        leaves start with a full hand. Default True (safe); the DSL leaf
        derives it from the action's declared ``resource``."""
        return True

    def cancel(self) -> bool:
        """Ask a running worker to stop GRACEFULLY — a device loop's
        own stop signal (the shaker's stop_shaking, the vortex's
        stop_run), so it ends its cycle cleanly. Returns True when such
        a path exists and was taken. Default: none — the worker is then
        cancelled HARD: its thread is marked and unwinds at its next
        rt.checkpoint()/sleep/call (Runtime.ActionCancelled). The DSL
        leaf forwards to the action's ``cancel()`` when it defines one."""
        return False

    def _park_hold(self) -> bool:
        """Should this leaf REFUSE to start right now? Yes while the
        operator has clicked Park — unless the hand is full and this
        leaf uses the robot, because the item must be put down first.
        Lives at the leaf: a Sequence starts its next child inside the
        tick its predecessor finished in, before the engine can look."""
        if getattr(self, "_park_exempt", False):
            return False                    # a leaf of the cleanup tree
        rt = getattr(self.ctx, "runtime", None)
        p = getattr(rt, "parking", None)
        if not (p() if callable(p) else p):
            return False
        core = getattr(rt, "robot_api", None)
        fn = getattr(core, "tool_holds_load", None)
        try:
            hand_full = bool(fn()) if callable(fn) else False
        except Exception:
            hand_full = False
        return not (hand_full and self.uses_robot())

    # ── Override these in subclasses ────────────────────────────────────

    def execute(self) -> bool:
        """Run the recipe call(s). Return True on success."""
        raise NotImplementedError

    def apply_effects(self, state: Dict[str, Any]) -> None:
        """Mutate ``state`` to reflect the effects of this action.

        Called by the framework after SUCCESS so the next BT/planner step
        sees the updated world. Default is no-op — subclasses override
        when an action affects shared state (e.g. ``state["has_cap"][t] = False``
        after a decap). Keeping effects in code keeps domain.py PDDL
        effects and BT effects in lockstep.
        """
        return None

    # ── BT lifecycle ────────────────────────────────────────────────────

    def initialise(self) -> None:
        """Start the worker thread. Called each time we (re-)enter this leaf."""
        self._result = None
        self._exc = None
        self._started_at = time.monotonic()
        self._held = self._park_hold()
        if self._held:
            self.log.info("RecipeAction[%s]: parking — not started", self.name)
            return

        def _target():
            try:
                self._result = bool(self.execute())
            except BaseException as ex:  # noqa: BLE001 — log + carry forward
                self._exc = ex
                self._result = False
            finally:
                # Wake the engine now, not at its next tick (BTEngine.run).
                wake = getattr(getattr(self.ctx, "runtime", None), "_bt_wake", None)
                if wake is not None:
                    wake.set()

        self._worker = threading.Thread(
            target=_target,
            name=f"bt-action-{self.name}",
            daemon=True,
        )
        # The runtime reads this to know whether work on this thread
        # may touch the robot's held motion tail (Runtime.settle).
        self._worker._bt_leaf = self
        self._worker.start()
        self.log.debug("RecipeAction.initialise: worker started")

    def update(self) -> py_trees.common.Status:
        if self._held:
            return py_trees.common.Status.RUNNING   # parking: never started
        # Worker hasn't finished yet.
        if self._worker is not None and self._worker.is_alive():
            return py_trees.common.Status.RUNNING

        if self._exc is not None:
            self.log.warning(
                "RecipeAction[%s] raised: %s: %s",
                self.name, type(self._exc).__name__, self._exc,
            )
        if self._result:
            try:
                self.apply_effects(self.ctx.state)
            except ReplanRequested:
                # Non-deterministic action picked a non-default branch
                # — let the engine catch this and rebuild the tree
                # from the observed state.
                raise
            except Exception:
                self.log.exception("apply_effects raised — state may be stale")
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.FAILURE

    def terminate(self, new_status: py_trees.common.Status) -> None:
        # If we're being aborted while the worker is still in flight, halt
        # the robot. Workspace recipes already poll runtime.stop().
        w = self._worker
        if w is not None and w.is_alive() and new_status != py_trees.common.Status.SUCCESS:
            # A robot worker: halt the robot (every recipe polls
            # runtime.stop()). A NON-robot worker (a shake, a rest) is
            # never stopped that way — it would halt the park motion
            # about to start.
            if self.uses_robot():
                try:
                    stop = getattr(self.ctx.runtime, "stop", None)
                    if callable(stop):
                        stop()
                except Exception:
                    self.log.exception("terminate: runtime.stop() raised")
            # Either way the worker must END, not run its loop to the
            # end on a tree that no longer exists (a replan used to pile
            # up shake workers, each toggling the same shaker). Graceful
            # when the action has a stop of its own, hard otherwise:
            # the thread unwinds at its next checkpoint.
            try:
                graceful = bool(self.cancel())
            except Exception:
                self.log.exception("terminate: cancel() raised")
                graceful = False
            w._bt_cancel = "soft" if graceful else "hard"
            self.log.info("RecipeAction[%s]: terminated mid-flight — %s cancel",
                          self.name, "graceful" if graceful else "hard")
        self._worker = None
        self._held = False


# ── PredicateCondition ──────────────────────────────────────────────────────


class PredicateCondition(WorkspaceBehaviour):
    """A leaf that returns SUCCESS / FAILURE from a world-state predicate.

    The predicate is evaluated synchronously inside ``update()`` — must be
    cheap (no I/O, no recipe calls). For anything that requires a sensor
    read, model it as an action whose effects populate ``ctx.state``, then
    test the result via this class.

    Subclass and override ``check()``:

        class IsCapped(PredicateCondition):
            def __init__(self, ctx, tube):
                super().__init__(name=f"is_capped({tube})", ctx=ctx)
                self.tube = tube
            def check(self) -> bool:
                return self.ctx.state.get("has_cap", {}).get(self.tube, False)
    """

    def check(self) -> bool:
        raise NotImplementedError

    def update(self) -> py_trees.common.Status:
        try:
            ok = bool(self.check())
        except Exception as ex:
            self.log.warning("PredicateCondition[%s] raised: %s", self.name, ex)
            return py_trees.common.Status.FAILURE
        return (
            py_trees.common.Status.SUCCESS
            if ok else py_trees.common.Status.FAILURE
        )


# ── DeviceCondition ─────────────────────────────────────────────────────────


class DeviceCondition(WorkspaceBehaviour):
    """A condition that reads the live device-bus state of one device id.

    The check is "is this device.state == ok right now?". Use it to gate
    a subtree on hardware availability — e.g., refuse to enter the
    weigh-subtree if ``scale:01`` is down.

    Wiring: pass a ``device_id`` and a ``read_state`` callable. ``read_state``
    is typically the orchestrator's ``MQTTOrchestrator.get(id)`` method,
    which returns the cached snapshot for the device. The callable lets
    tests inject a fake without bringing up MQTT.
    """

    def __init__(
        self,
        name: str,
        ctx: WorkspaceContext,
        device_id: str,
        read_state: Callable[[str], Optional[Dict[str, Any]]],
        expected: str = "ok",
    ):
        super().__init__(name=name, ctx=ctx)
        self.device_id = device_id
        self._read_state = read_state
        self._expected = expected

    def update(self) -> py_trees.common.Status:
        try:
            snap = self._read_state(self.device_id) or {}
        except Exception as ex:
            self.log.warning("DeviceCondition read failed: %s", ex)
            return py_trees.common.Status.FAILURE
        cur = str(snap.get("state", "unknown"))
        return (
            py_trees.common.Status.SUCCESS
            if cur == self._expected else py_trees.common.Status.FAILURE
        )
