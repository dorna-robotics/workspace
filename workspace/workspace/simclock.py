"""The simulation's clock — the one place a sim run's time passes.

A simulation runs in real time: the robot's motion profile plays out tick
by tick, a pump stroke blocks for the seconds the drive would take, an
incubation waits its minutes. The operator's speed control (0.25x .. 4x,
changeable mid-run) scales ALL of that by one number, so every place a
sim run waits reads it from here instead of the wall clock:

    now()               sim time, epoch seconds — equal to time.time()
                        while the speed has always been 1x
    sleep(s)            wait ``s`` seconds of sim time
    wait(event, s)      the same, cut short by ``event`` (True if set)
    Pacer               a tick loop's deadline (the sim robot's 120 Hz)
    at(wall)            a wall-clock stamp on the sim timeline
    computing           a decorator: while the robot's software computes (path
                        planning, IK, certifying a motion) the clock runs at 1x

Computing takes real time on a real robot too, so it does in the sim's time:
only the waits speed up. A 0.5 s plan stays 0.5 s on the timeline at 16x,
instead of being recorded as 8 s — recordings, and the run times measured
from them, stay true at any speed.

A speed change takes effect from that moment — a wait in progress
speeds up or slows down for what is left of it (waits sleep in slices of
at most ``SLICE_S``). It never moves the robot faster: ``speed_factor``
(the robot's own vel / accel) is a different thing and untouched.

Real hardware always runs at 1x: the runtime refuses ``set_speed`` when
anything in the workspace is real, and the core puts the clock back to
1x when it leaves simulation. One clock per process — one workspace.
"""
import functools
import threading
import time
from collections import deque

SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
SLICE_S = 0.1          # the longest a wait sleeps before re-reading the speed

_lock = threading.Lock()
_speed = 1.0           # the operator's choice
_scale = 1.0           # what the clock runs at now: 1x while computing, else _speed
_computing = 0         # computing sections in progress (any thread)
# speed changes, newest last: (wall, sim, scale) — sim time from that wall
# instant on is sim + (wall' - wall) * scale. Bounded: at() only looks back
# as far as a stamp can be old (events are recorded as they happen).
_marks = deque([(time.time(),) * 2 + (1.0,)], maxlen=4096)


def speed() -> float:
    """The speed the operator chose."""
    return _speed


def rate() -> float:
    """What the clock runs at right now (1x while computing)."""
    return _scale


def _apply():                                # under _lock
    global _scale
    x = 1.0 if _computing else _speed
    if x != _scale:
        wall = time.time()
        _marks.append((wall, _sim_at(wall), x))
        _scale = x


def set_speed(x) -> float:
    """Run sim time at ``x`` times real time from now on (one of SPEEDS)."""
    global _speed
    x = float(x)
    if x not in SPEEDS:
        raise ValueError(f"speed must be one of {', '.join(f'{s:g}' for s in SPEEDS)}")
    with _lock:
        _speed = x
        _apply()
    return _speed


def computing(fn):
    """Run ``fn`` with the clock at 1x (it computes; nothing waits)."""
    @functools.wraps(fn)
    def run(*a, **k):
        global _computing
        with _lock:
            _computing += 1
            _apply()
        try:
            return fn(*a, **k)
        finally:
            with _lock:
                _computing -= 1
                _apply()
    return run


def reset() -> None:
    """Back to 1x (leaving simulation)."""
    set_speed(1.0)


def _sim_at(wall: float) -> float:
    for w, s, k in reversed(_marks):
        if wall >= w:
            return s + (wall - w) * k
    w, s, _k = _marks[0]
    return s + (wall - w)              # older than every mark: 1x before them


def now() -> float:
    return _sim_at(time.time())


def at(wall: float) -> float:
    """A ``time.time()`` stamp taken earlier, on the sim timeline."""
    return _sim_at(float(wall))


def sleep(seconds: float) -> None:
    if seconds <= 0:
        return
    if _scale == 1.0 and seconds <= SLICE_S:
        time.sleep(seconds)
        return
    end = now() + seconds
    while True:
        left = end - now()
        if left <= 0:
            return
        time.sleep(min(left / _scale, SLICE_S))


def wait(event: threading.Event, seconds: float) -> bool:
    """``event.wait(seconds)`` on sim time: True as soon as the event is
    set, False when the seconds ran out."""
    if seconds <= 0:
        return event.is_set()
    end = now() + seconds
    while True:
        left = end - now()
        if left <= 0:
            return event.is_set()
        if event.wait(min(left / _scale, SLICE_S)):
            return True


class Pacer:
    """The wall deadline of a fixed-rate tick loop on sim time: each
    ``next(dt)`` moves it ``dt`` sim seconds on and returns the seconds to
    sleep until it (negative: the loop is late — it runs the next tick at
    once and catches up, as before)."""

    def __init__(self):
        self.t = time.perf_counter()

    def next(self, dt: float) -> float:
        self.t += dt / _scale
        return self.t - time.perf_counter()
