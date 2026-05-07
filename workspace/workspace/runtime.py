# workspace/runtime.py
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")


class RTState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    ENDING = "ENDING"
    ERROR = "ERROR"
    KILLED = "KILLED"


class KillRequested(SystemExit):
    """Raised to terminate the gate/worker thread immediately (cooperative thread-exit)."""

class EndRequested(Exception):
    """Raised to gracefully end the workflow — finish current action, run trigger:end handler, then exit."""


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
        self._ending = False

        # cleanup flag — suppresses EndRequested in checkpoint() during trigger:end / release
        self._in_cleanup = False

        # prevent concurrent worker() runs
        self._in_worker = False

        # internal workflow thread
        self._workflow_thread: Optional[threading.Thread] = None

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
        # ENDING → IDLE/ERROR/KILLED). Lets ``/status`` report a stable
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
        if new_state == RTState.RUNNING and old != RTState.RUNNING:
            if old == RTState.PAUSED:
                self.run_finished_at = None  # resume — keep started
            else:
                self.run_started_at = time.time()
                self.run_finished_at = None
        elif (
            new_state in (RTState.IDLE, RTState.ERROR, RTState.KILLED)
            and old in (RTState.RUNNING, RTState.PAUSED, RTState.ENDING)
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
            return  # no checkpoint for progress updates

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
        self.checkpoint()

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
                self._set_state(RTState.RUNNING)
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

            if st == RTState.RUNNING:
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
            if self._status.state in (RTState.RUNNING, RTState.IDLE):
                self._status.job_pauses += 1
                self._set_state(RTState.PAUSED)
                self._cv.notify_all()

    def resume(self) -> None:
        with self._lock:
            if self._killed:
                return
            if self._status.state == RTState.PAUSED:
                self._status.job_resumes += 1
                self._set_state(RTState.RUNNING)
                self._cv.notify_all()

    def end(self) -> None:
        """Request graceful stop — current action finishes, then EndRequested is raised at next checkpoint."""
        with self._lock:
            if self._killed or self._ending:
                return
            self._ending = True
            self._set_state(RTState.ENDING)
            # If paused, resume so the checkpoint can see the stop flag
            self._cv.notify_all()

    @property
    def ending(self) -> bool:
        return self._ending

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
            self._ending = False
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
        if self._ending:
            raise EndRequested()
        self._set_state_with_callback(RTState.RUNNING)

    def mark_idle(self) -> None:
        if self._killed:
            raise KillRequested()
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
                if self._ending:
                    raise EndRequested()
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
                except EndRequested:
                    self.mark_idle()
                    return
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

    def checkpoint(self) -> None:
        # End is honored only between states (see ORRunner.run), not mid-state,
        # so partially-completed atomic operations (e.g. tool swaps) can finish.
        # Kill and Pause are still observed here.
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