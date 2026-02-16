# workspace/runtime.py
import time
import threading
import functools
from enum import Enum, auto


class RunState(Enum):
    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()
    FAULT = auto()


class StopRequested(RuntimeError):
    """Raised inside running code when STOP/FAULT/IDLE is requested."""
    pass


class Runtime:
    """
    Runtime controller for start/pause/resume/stop.

    - Thread-safe state machine (Condition + RLock)
    - Pause gate via checkpoint()
    - STOP propagates by raising StopRequested from checkpoint()
    - Single-writer guarantee for robot_api via _robot_lock
    - Dynamic proxy to core.robot_api: ws.rt.<any_robot_method>(...) works
      without you adding wrappers over time.
    - Use rt.delay(sec) instead of robot_api.sleep(sec) to respect pause/stop.
    """

    def __init__(self, core):
        self.core = core
        self.state = RunState.IDLE

        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)

        # Single-writer lock for robot_api I/O (prevents interleaving from threads)
        self._robot_lock = threading.RLock()

    # ---------------- commands (call from UI/IO thread) ----------------
    def start(self) -> bool:
        with self._cv:
            if self.state == RunState.FAULT:
                return False
            self.state = RunState.RUNNING
            self._cv.notify_all()
            return True

    def pause(self) -> None:
        with self._cv:
            if self.state == RunState.RUNNING:
                self.state = RunState.PAUSED
            self._cv.notify_all()

        # Optional: if your robot supports real pause, do it best-effort
        ra = getattr(self.core, "robot_api", None)
        if ra is not None and hasattr(ra, "pause"):
            with self._robot_lock:
                try:
                    ra.pause()
                except Exception:
                    pass

    def resume(self) -> None:
        with self._cv:
            if self.state == RunState.PAUSED:
                self.state = RunState.RUNNING
            self._cv.notify_all()

        # Optional: if your robot supports real resume, do it best-effort
        ra = getattr(self.core, "robot_api", None)
        if ra is not None and hasattr(ra, "resume"):
            with self._robot_lock:
                try:
                    ra.resume()
                except Exception:
                    pass

    def stop(self) -> None:
        # Set state and wake paused threads
        with self._cv:
            if self.state not in (RunState.IDLE, RunState.STOPPING):
                self.state = RunState.STOPPING
            self._cv.notify_all()

        # Best-effort immediate stop (protected by robot lock)
        ra = getattr(self.core, "robot_api", None)
        if ra is None:
            return
        with self._robot_lock:
            for fn in ("stop", "halt", "estop"):
                if hasattr(ra, fn):
                    try:
                        getattr(ra, fn)()
                        break
                    except Exception:
                        pass

    def set_fault(self) -> None:
        with self._cv:
            self.state = RunState.FAULT
            self._cv.notify_all()

    def to_idle(self) -> None:
        """Return to IDLE after a job ends (will not clear FAULT)."""
        with self._cv:
            if self.state != RunState.FAULT:
                self.state = RunState.IDLE
            self._cv.notify_all()

    # ---------------- enforcement (called inside recipes) ----------------
    def checkpoint(self) -> None:
        """Blocks if paused. Raises StopRequested if stopping/fault/idle."""
        with self._cv:
            while self.state == RunState.PAUSED:
                self._cv.wait(timeout=0.2)

            if self.state in (RunState.STOPPING, RunState.FAULT, RunState.IDLE):
                raise StopRequested()

    def delay(self, sec: float, step: float = 0.05) -> None:
        """Pause/stop-aware sleep. Use this instead of robot_api.sleep()."""
        t0 = time.time()
        while True:
            self.checkpoint()
            dt = time.time() - t0
            if dt >= sec:
                return
            time.sleep(min(step, sec - dt))

    # ---------------- dynamic proxy to robot_api ----------------
    def __getattr__(self, name):
        """
        Proxy unknown attributes/methods to core.robot_api.

        - If it's callable: return a wrapped callable that does checkpoint() + robot lock
        - If it's a value/property: return as-is

        Note: __getattr__ is only called if 'name' is not found on Runtime itself,
        so Runtime.delay/checkpoint/etc. will not be shadowed by robot_api members.
        """
        ra = getattr(self.core, "robot_api", None)
        if ra is None:
            raise AttributeError(
                f"Runtime has no core.robot_api; cannot access '{name}'"
            )

        attr = getattr(ra, name)  # raises AttributeError if missing (good)

        if callable(attr):
            @functools.wraps(attr)
            def _wrapped(*args, **kwargs):
                self.checkpoint()
                with self._robot_lock:
                    return attr(*args, **kwargs)
            return _wrapped

        return attr
