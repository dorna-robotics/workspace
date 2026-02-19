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
    ERROR = "ERROR"
    KILLED = "KILLED"


class KillRequested(SystemExit):
    """Raised to terminate the gate/worker thread immediately (cooperative thread-exit)."""


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

        # prevent concurrent worker() runs
        self._in_worker = False

        self.on_state_change: Optional[Callable[[RTState, RTState], None]] = None

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
        self._cv.notify_all()

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
                self._cv.notify_all()
                return

            if st == RTState.RUNNING:
                return

            self._start_token += 1
            self._status.last_error = None
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

    def kill(self) -> None:
        """
        Kill the runtime:
          - unblocks wait_for_start()
          - unblocks checkpoint() (even if paused)
          - makes gate/worker loops EXIT (thread ends)
        """
        with self._lock:
            if self._killed:
                return
            self._killed = True
            self._status.kills += 1
            self._set_state(RTState.KILLED)
            self._start_token += 1  # release wait_for_start
            self._cv.notify_all()


    def reset(self) -> None:
        """
        Reset runtime after kill().
        Allows creating a new gate thread and starting again.
        """
        with self._lock:
            self._killed = False
            self._status.last_error = None
            self._status.state = RTState.IDLE
            self._cv.notify_all()
    # ---------------------------------------------------------------------
    # Gate helpers for your thread model
    # ---------------------------------------------------------------------

    def mark_running(self) -> None:
        if self._killed:
            raise KillRequested()
        self._set_state_with_callback(RTState.RUNNING)

    def mark_idle(self) -> None:
        if self._killed:
            raise KillRequested()
        self._set_state_with_callback(RTState.IDLE)

    def mark_error(self, ex: Exception) -> None:
        self._set_state_with_callback(RTState.ERROR, err=f"{type(ex).__name__}: {ex}")

    def wait_for_start(self) -> None:
        """Block until start token; exits if killed."""
        with self._lock:
            while True:
                if self._killed:
                    raise KillRequested()

                if self._seen_start_token != self._start_token:
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
    # Pause gate + utilities (exits on kill)
    # ---------------------------------------------------------------------

    def checkpoint(self) -> None:
        with self._lock:
            while True:
                if self._killed:
                    raise KillRequested()

                st = self._status.state

                if st == RTState.PAUSED:
                    self._cv.wait()
                    continue

                # RUNNING / IDLE / ERROR -> allow
                return

    def call(self, fn: Callable[..., T], *a: Any, checkpoint: bool = True, **k: Any) -> T:
        if checkpoint:
            self.checkpoint()
        return fn(*a, **k)

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

        # unwrap Core -> core.robot_api if someone passed core by mistake
        if hasattr(rb, "robot_api"):
            inner = getattr(rb, "robot_api")
            if inner is not None:
                rb = inner

        return rb

    def __getattr__(self, name: str):
        rb = self._require_robot()
        attr = getattr(rb, name)  # raises AttributeError if missing

        if callable(attr):
            def _wrapped(*a, **k):
                return self.call(attr, *a, **k)
            _wrapped.__name__ = name
            return _wrapped

        return attr
