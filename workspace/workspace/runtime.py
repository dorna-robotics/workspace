# workspace/runtime.py
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")


class RTState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    PARKING = "PARKING"
    ERROR = "ERROR"
    KILLED = "KILLED"


class KillRequested(SystemExit):
    """Raised to terminate the gate/worker thread immediately (cooperative thread-exit)."""

class ParkRequested(Exception):
    """Raised to gracefully park the workflow — finish current action, run trigger:park handler, then exit."""


@dataclass
class RTStatus:
    state: RTState = RTState.IDLE
    last_error: Optional[str] = None
    job_runs: int = 0
    job_pauses: int = 0
    job_resumes: int = 0
    kills: int = 0


class Runtime:
    """
    Scalable, thread-safe runtime gate.

    - Dynamic robot API forwarding via __getattr__ (no wrapper maintenance).
    - pause/resume gates via checkpoint().
    - kill() terminates the gate/worker thread (exits loops).
    - Can run a workflow in a dedicated internal thread.
    """

    def __init__(self, robot_api: Any = None, *, sleep_fn: Callable[[float], None] = time.sleep):
        self.robot_api = robot_api
        self._sleep = sleep_fn

        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)

        self._status = RTStatus()

        # start-token handshake
        self._start_token = 0
        self._seen_start_token = 0

        # kill flag (kills thread loops)
        self._killed = False

        # stop flag (graceful stop — finish current action then stop)
        self._parking = False

        # cleanup flag — suppresses ParkRequested in checkpoint() during trigger:park / release
        self._in_cleanup = False

        # prevent concurrent worker() runs
        self._in_worker = False

        # internal workflow thread
        self._workflow_thread: Optional[threading.Thread] = None

        # thread-local operator marker — see operator_call() / _is_workflow_thread()
        self._op_tl = threading.local()

        self.on_state_change: Optional[Callable[[RTState, RTState], None]] = None
        # Fired on every state transition with the complete RTStatus
        # snapshot (state, last_error, job_runs, …). Wired by
        # ``RuntimeServer`` to broadcast on ``/ws/status`` so the UI
        # reacts to state changes within milliseconds rather than
        # waiting on the slower HTTP /status polling. Safe to call
        # under the runtime lock — the wired listener schedules the
        # broadcast on the IO loop and returns immediately.
        self.on_status: Optional[Callable[[dict], None]] = None
        # Callback signature: (steps_snapshot: list, progress: int) → None.
        # ``progress`` is 0-100 or -1 when not set. Fired on every
        # workflow step (including progress-only updates) so the UI's
        # WS push never lags the polled HTTP /status — important for
        # the final 100% emission landing reliably in the operator UI.
        self.on_step: Optional[Callable[[list, int], None]] = None

        # workflow step tracking
        self._steps: list = []  # list of step labels (timeline)
        self._progress: int = -1  # -1 = no progress, 0-100 = percentage

        # Per-RUN timing (Unix seconds). ``run_started_at`` is set the
        # moment the runtime first enters RUNNING for a given run, kept
        # across pause/resume, reset on the next cold start. ``run_
        # finished_at`` is set when the run terminates (RUNNING/PAUSED/
        # PARKING → IDLE/ERROR/KILLED). Lets ``/status`` report a stable
        # "Up" value the orchestrator can pass through unchanged — no
        # race with the orchestrator's polling loop.
        self.run_started_at: Optional[float] = None
        self.run_finished_at: Optional[float] = None

    # ---------------------------------------------------------------------
    # Status helpers
    # ---------------------------------------------------------------------

    @property
    def status(self) -> RTStatus:
        with self._lock:
            return RTStatus(**self._status.__dict__)

    @property
    def state(self) -> RTState:
        with self._lock:
            return self._status.state

    def _set_state(self, new_state: RTState, *, err: Optional[str] = None) -> None:
        old = self._status.state
        if old == new_state and err is None:
            return
        self._status.state = new_state
        if err is not None:
            self._status.last_error = err

        # Per-run timing — single source of truth. Set when the run
        # actually begins, frozen when it ends. Pause/resume preserve
        # ``run_started_at`` so the operator's wall-clock view of a
        # paused-then-resumed run matches their expectation. ``time``
        # imported at the top of the module.
        if new_state in (RTState.RUNNING, RTState.PARKING) and old not in (
            RTState.RUNNING,
            RTState.PARKING,
        ):
            if old == RTState.PAUSED:
                self.run_finished_at = None  # resume — keep started
            else:
                self.run_started_at = time.time()
                self.run_finished_at = None
        elif (
            new_state in (RTState.IDLE, RTState.ERROR, RTState.KILLED)
            and old in (RTState.RUNNING, RTState.PAUSED, RTState.PARKING)
        ):
            if self.run_started_at and not self.run_finished_at:
                self.run_finished_at = time.time()

        self._cv.notify_all()
        # Push the new status snapshot to any wired listener (e.g. the
        # runtime_server's /ws/status broadcaster). The listener is
        # expected to schedule its work asynchronously, so calling it
        # under the runtime lock is safe.
        cb = self.on_status
        if cb is not None:
            try:
                cb({
                    "state": str(new_state),
                    "last_error": self._status.last_error,
                    "job_runs": self._status.job_runs,
                    "job_pauses": self._status.job_pauses,
                    "job_resumes": self._status.job_resumes,
                    "kills": self._status.kills,
                })
            except Exception:
                pass

    def _set_state_with_callback(self, new_state: RTState, *, err: Optional[str] = None) -> None:
        cb = None
        old = None
        with self._lock:
            old = self._status.state
            self._set_state(new_state, err=err)
            cb = self.on_state_change
        if cb is not None:
            try:
                cb(old, new_state)
            except Exception:
                pass

    # ---------------------------------------------------------------------
    # Workflow step tracking
    # ---------------------------------------------------------------------

    _STEP_LEVELS = ("info", "success", "warning", "error", "progress")

    def step(self, label, level: str = "info") -> None:
        """Mark a workflow step. Accumulates as a timeline in the dashboard.

        **Not pause-aware.** ``step`` is pure observability — it records a
        timeline entry and returns. It deliberately does NOT call
        ``checkpoint()`` so logging a step never blocks. If the runtime
        is paused, the pause is observed by the next pause-aware call
        (``rt.sleep`` / ``rt.delay`` / ``rt.<robot_method>`` /
        ``rt.checkpoint``). See docs/project-guide.md §9 for the
        full pause-aware/not-pause-aware map.

        level: 'info' (default), 'success', 'warning', 'error', or 'progress'.
        For progress: label is a number 0-100 (percentage).
        """
        if level not in self._STEP_LEVELS:
            level = "info"

        if level == "progress":
            # Progress: label is a number 0-100, stored separately, not in timeline
            val = max(0, min(100, int(label)))
            print(f"[STEP][progress] {val}%")
            with self._lock:
                self._progress = val
                steps_snapshot = list(self._steps)
                progress_snapshot = self._progress
            cb = self.on_step
            if cb is not None:
                try:
                    cb(steps_snapshot, progress_snapshot)
                except Exception:
                    pass
            return

        print(f"[STEP][{level}] {label}")
        entry = {"label": str(label), "level": level}
        with self._lock:
            self._steps.append(entry)
            steps_snapshot = list(self._steps)
            progress_snapshot = self._progress
        cb = self.on_step
        if cb is not None:
            try:
                cb(steps_snapshot, progress_snapshot)
            except Exception:
                pass

    @property
    def step_info(self) -> Optional[dict]:
        with self._lock:
            if not self._steps and self._progress < 0:
                return None
            d = {"steps": list(self._steps)}
            if self._progress >= 0:
                d["progress"] = self._progress
            return d

    def _clear_steps(self) -> None:
        self._steps.clear()
        self._progress = -1

    # ---------------------------------------------------------------------
    # Control API
    # ---------------------------------------------------------------------

    def start(self) -> None:
        """Issue a start token, or resume if paused."""
        with self._lock:
            if self._killed:
                return  # dead runtime

            st = self._status.state
            if st == RTState.PAUSED:
                self._status.job_resumes += 1
                # Park is a sticky flag: if the user parked, then paused
                # mid-park, then resumed, we go back to PARKING (not
                # RUNNING) so the park-cleanup pipeline keeps draining.
                self._set_state(RTState.PARKING if self._parking else RTState.RUNNING)
                # No token bump here — the state transition itself wakes
                # everyone via cv.notify_all. ``wait_for_start`` accepts
                # state==RUNNING as an exit condition (so a thread that
                # hasn't yet consumed a start token still wakes), and
                # ``checkpoint`` exits on state!=PAUSED. Bumping the
                # token would queue a phantom restart that fires after
                # the current workflow completes — replaying the full
                # protocol unintendedly.
                self._cv.notify_all()
                return

            if st in (RTState.RUNNING, RTState.PARKING):
                return

            self._start_token += 1
            self._status.last_error = None
            self._clear_steps()
            self._set_state(RTState.IDLE)
            self._cv.notify_all()

    def pause(self) -> None:
        with self._lock:
            if self._killed:
                return
            # PARKING is also pausable — Park is a flag, the workflow is
            # still executing recipes until the next checkpoint catches
            # ParkRequested, and the operator must be able to halt it
            # mid-stride (e.g. to clear a robot alarm before cleanup).
            if self._status.state in (RTState.RUNNING, RTState.IDLE, RTState.PARKING):
                self._status.job_pauses += 1
                self._set_state(RTState.PAUSED)
                self._cv.notify_all()

    def resume(self) -> None:
        with self._lock:
            if self._killed:
                return
            if self._status.state == RTState.PAUSED:
                self._status.job_resumes += 1
                # If the park flag is set, resume to PARKING (not RUNNING)
                # so the park-cleanup pipeline keeps draining.
                self._set_state(RTState.PARKING if self._parking else RTState.RUNNING)
                self._cv.notify_all()

    def park(self) -> None:
        """Request graceful park — current action finishes, then
        ParkRequested is raised at next checkpoint, followed by every
        ``trigger="park"`` Action class running once in sequence."""
        with self._lock:
            if self._killed or self._parking:
                return
            self._parking = True
            self._set_state(RTState.PARKING)
            # If paused, resume so the checkpoint can see the stop flag
            self._cv.notify_all()

    @property
    def parking(self) -> bool:
        return self._parking

    def kill(self) -> None:
        """Kill runtime and join workflow thread."""
        with self._lock:
            if self._killed:
                return
            self._killed = True
            self._status.kills += 1
            self._set_state(RTState.KILLED)
            self._start_token += 1
            self._cv.notify_all()

        # join internal workflow thread outside lock
        if self._workflow_thread:
            self._workflow_thread.join()
            self._workflow_thread = None

    def reset(self) -> None:
        """Reset runtime after kill() or stop()."""
        with self._lock:
            self._killed = False
            self._parking = False
            self._in_cleanup = False
            self._status.last_error = None
            self._status.state = RTState.IDLE
            self._cv.notify_all()

    # ---------------------------------------------------------------------
    # Gate helpers for your thread model
    # ---------------------------------------------------------------------

    def mark_running(self) -> None:
        if self._killed:
            raise KillRequested()
        if self._parking:
            raise ParkRequested()
        self._set_state_with_callback(RTState.RUNNING)

    def mark_idle(self) -> None:
        if self._killed:
            raise KillRequested()
        # Clear any pending park / cleanup flags from the run we're
        # leaving. Without this, after a Park the runtime transitions
        # to IDLE but ``_parking`` stays True — the next ``mark_running``
        # would immediately raise ParkRequested, killing the gate loop
        # before the new run can start.
        with self._lock:
            self._parking = False
            self._in_cleanup = False
        self._set_state_with_callback(RTState.IDLE)

    def mark_error(self, ex: Exception) -> None:
        self._set_state_with_callback(RTState.ERROR, err=f"{type(ex).__name__}: {ex}")

    def wait_for_start(self) -> None:
        """Block until start token; exits if killed or ended.

        Also exits when state transitions to RUNNING — covers the case
        where the operator paused before any workflow thread existed
        (e.g. a critical-device auto-pause between Launch and Start);
        the user clicks Start to resume, state goes RUNNING without
        bumping the token (because bumping would phantom-restart a
        live workflow), and a freshly-spawned gate-loop still wakes.
        """
        with self._lock:
            while True:
                if self._killed:
                    raise KillRequested()
                if self._parking:
                    raise ParkRequested()
                if self._seen_start_token != self._start_token:
                    self._seen_start_token = self._start_token
                    return
                if self._status.state == RTState.RUNNING:
                    self._seen_start_token = self._start_token
                    return
                self._cv.wait()

    # ---------------------------------------------------------------------
    # Optional worker loop (also exits on kill)
    # ---------------------------------------------------------------------

    def worker(self, job_fn: Callable[..., T], /, **kwargs) -> Optional[T]:
        with self._lock:
            if self._in_worker:
                raise RuntimeError("Runtime.worker() is already running in another thread.")
            self._in_worker = True

        result: Optional[T] = None
        try:
            while True:
                self.wait_for_start()
                self._set_state_with_callback(RTState.RUNNING)
                with self._lock:
                    self._status.job_runs += 1
                try:
                    result = job_fn(**kwargs)
                except KillRequested:
                    raise
                except Exception as ex:
                    self._set_state_with_callback(RTState.ERROR, err=f"{type(ex).__name__}: {ex}")
                self._set_state_with_callback(RTState.IDLE)
        finally:
            with self._lock:
                self._in_worker = False
        return result

    # ---------------------------------------------------------------------
    # Workflow thread
    # ---------------------------------------------------------------------

    def set_workflow_kwargs(self, kwargs: dict) -> None:
        """Update the kwargs passed to the workflow function on its
        next invocation. The gate-loop reads ``self._pending_kwargs``
        fresh each iteration, so a Start click that changed Parameters
        between runs (same workspace process, multiple back-to-back
        runs) actually picks up the new values — instead of using the
        kwargs frozen at first-Start time.

        Caller (the cmd handler) sets this before ``self.start()`` so
        the gate-loop sees the new values when it wakes up on the
        token bump.
        """
        self._pending_kwargs = dict(kwargs or {})

    def run_workflow_thread(self, workflow_fn: Callable[..., Any], *, workspace: Any, **extra_kwargs):
        """Run a workflow in its own internal thread, managed by this runtime."""
        if self._workflow_thread and self._workflow_thread.is_alive():
            raise RuntimeError("Workflow thread already running!")

        # Seed pending_kwargs with the kwargs from this initial call so
        # the very first run has them. Subsequent Starts can update via
        # ``set_workflow_kwargs``.
        if not hasattr(self, "_pending_kwargs"):
            self._pending_kwargs: dict = {}
        if extra_kwargs:
            self._pending_kwargs = dict(extra_kwargs)

        def _gate_loop():
            while True:
                try:
                    self.wait_for_start()
                    self.mark_running()
                    # Read kwargs fresh on every iteration so Parameters
                    # changes between runs take effect.
                    current_kwargs = dict(self._pending_kwargs)
                    workflow_fn(workspace=workspace, core=workspace.components["core"], **current_kwargs)
                    self.mark_idle()
                except KillRequested:
                    return
                except ParkRequested:
                    # Park was requested but never picked up by the
                    # framework's cleanup path. ``mark_idle`` resets
                    # the parking flag so the next Start works
                    # cleanly. We DON'T return — keep the gate loop
                    # alive for the next run.
                    self.mark_idle()
                except Exception as ex:
                    self.mark_error(ex)
                    import traceback
                    traceback.print_exc()
                    if self.state != RTState.KILLED:
                        self.mark_idle()

        th = threading.Thread(target=_gate_loop, daemon=True)
        th.start()
        self._workflow_thread = th
        return th

    # ---------------------------------------------------------------------
    # Pause gate + utilities (exits on kill)
    # ---------------------------------------------------------------------

    def _is_workflow_thread(self) -> bool:
        """Whether the calling thread is on the workflow side of the pause gate.

        The pause/kill gate holds the WORKFLOW only. But the workflow is a
        FAMILY of threads — the gate loop plus every ``bt-action-*`` worker
        the BT engine spawns — so membership can't be a thread-identity
        check against one thread. Instead the few operator entry points
        (device-panel actions arriving on server executor threads) mark
        themselves with ``operator_call()``; every unmarked thread — gate
        loop, BT workers, recipe helpers, direct/offline runs — counts as
        workflow and blocks while PAUSED. Pausing exists precisely so the
        operator can intervene, so marked threads pass the gate instead of
        hanging until Resume.
        """
        return not getattr(self._op_tl, "operator", False)

    @contextmanager
    def operator_call(self):
        """Context manager marking the current thread as an operator call.

        Inside the context, ``checkpoint()`` passes straight through while
        PAUSED, and a robot alarm inside ``call()`` returns its code to the
        caller (the panel shows it) instead of entering the workflow's
        pause-and-wait dance.
        """
        prev = getattr(self._op_tl, "operator", False)
        self._op_tl.operator = True
        try:
            yield
        finally:
            self._op_tl.operator = prev

    def checkpoint(self) -> None:
        # End is honored only between states (see ORRunner.run), not mid-state,
        # so partially-completed atomic operations (e.g. tool swaps) can finish.
        # Kill and Pause are still observed here — but only for the
        # WORKFLOW side. Operator calls (marked via operator_call()) pass
        # straight through: holding them made every rt.*-touching operator
        # action hang while PAUSED.
        if not self._is_workflow_thread():
            return
        with self._lock:
            while True:
                if self._killed:
                    raise KillRequested()
                st = self._status.state
                if st == RTState.PAUSED:
                    self._cv.wait()
                    continue
                return

    def call(self, fn: Callable[..., T], *a: Any, checkpoint: bool = True, **k: Any) -> T:
        if checkpoint:
            self.checkpoint()
        while True:
            result = fn(*a, **k)
            # Motion commands return int status: >=1 ok, <0 alarm/error
            if not isinstance(result, (int, float)) or result >= 0:
                return result
            # Operator-thread call hit an alarm: surface the failure to the
            # caller (the panel shows it) instead of the workflow's
            # pause-and-wait dance — a non-workflow thread must not spin or
            # block here.
            if not self._is_workflow_thread():
                return result
            # Alarm — pause and wait for user to clear and resume.
            # Logged at ``info`` (timeline only, no banner / no audio /
            # no desktop notification). The Devices panel owns the
            # urgent UX: ``RobotStation._wrap_call`` flips the device
            # state to "down", the MQTT publish reaches the
            # orchestrator, the panel renders a persistent red dot
            # with the alarm code, and one beep + one notification
            # fire on the rising edge of critical-down. Anything
            # stronger here would double-fire for the same event.
            self.step(
                f"Robot alarm (code {int(result)}). Clear the alarm on the robot, then click Resume.",
                level="info",
            )
            self.pause()
            self.checkpoint()  # blocks until user resumes

    def sleep(self, seconds: float = 0.0, *, checkpoint: bool = True, step: float = 0.05, val: Optional[float] = None) -> None:
        if val is not None:
            seconds = float(val)
        if seconds <= 0:
            return
        if not checkpoint:
            self._sleep(seconds)
            return
        end = time.time() + seconds
        while True:
            self.checkpoint()
            now = time.time()
            if now >= end:
                return
            self._sleep(min(step, end - now))

    def delay(self, seconds: float = 0.0, **k: Any) -> None:
        if "val" in k and not seconds:
            seconds = float(k["val"])
        self.sleep(float(seconds), checkpoint=True)

    # ---------------------------------------------------------------------
    # Dynamic forwarding to robot_api (scalable)
    # ---------------------------------------------------------------------

    def _require_robot(self) -> Any:
        rb = self.robot_api
        if rb is None:
            raise RuntimeError("Runtime.robot_api is None.")
        if hasattr(rb, "robot_api"):
            inner = getattr(rb, "robot_api")
            if inner is not None:
                rb = inner
        return rb

    def __getattr__(self, name: str):
        rb = self._require_robot()
        attr = getattr(rb, name)
        if callable(attr):
            def _wrapped(*a, **k):
                return self.call(attr, *a, **k)
            _wrapped.__name__ = name
            return _wrapped
        return attr