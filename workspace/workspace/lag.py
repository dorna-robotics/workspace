"""Who is busy right now — the one line a lag report needs.

The Display's frame loop and the simulated robot's tick loop run in the
same interpreter as the protocol. When one of them is late, the useful
question is which thread had the interpreter. ``busy_threads()`` samples
every thread's current frame and names, for each thread that is not
parked in a sleep or a wait, its innermost frame in workspace code — so a
lag line reads "BT: recipes/recipe.py:2990 smove_certify" instead of
"something was slow". A sample costs well under a millisecond and is
taken only when a lag has already happened: observability never blocks.
"""
import gc
import linecache
import os
import re
import sys
import threading
import time

_PKG = os.path.dirname(os.path.abspath(__file__))

# ── the switch ───────────────────────────────────────────────────────
# Off: nothing below prints and nothing is sampled — the detectors cost
# a few comparisons per frame and per sim tick, no more. On: every late
# frame, slow ack, late sim tick and long collection prints one line
# naming the busy thread. A developer's tool, flipped here in the code
# (and LAG_LINES in gui/orchestrator/web/index.html for the browser
# side); project-guide §10.5.
LAG_LINES = False


def say(channel, msg):
    """Print ``[channel] msg`` when the switch is on. ``msg`` may be a
    callable making the text, evaluated only when it prints."""
    if not LAG_LINES:
        return
    if callable(msg):
        msg = msg()
    print(f"[{channel}] {msg}", flush=True)


def busy_threads(exclude_ident=None, limit=3):
    """``"<thread>: <file>:<line> <function>; …"`` for up to ``limit``
    threads busy in workspace code right now, the calling thread excluded
    when its ident is given."""
    names = {t.ident: t.name for t in threading.enumerate()}
    out = []
    for ident, frame in sys._current_frames().items():
        if ident == exclude_ident:
            continue
        try:
            line = linecache.getline(frame.f_code.co_filename, frame.f_lineno)
        except Exception:
            line = ""
        if _PARKED.match(line):
            continue
        f = frame
        while f is not None and not f.f_code.co_filename.startswith(_PKG):
            f = f.f_back
        if f is None:
            continue                      # a library's own thread, not ours
        rel = os.path.relpath(f.f_code.co_filename, _PKG)
        out.append(f"{names.get(ident, str(ident))}: {rel}:{f.f_lineno} {f.f_code.co_name}")
        if len(out) >= limit:
            break
    return "; ".join(out) if out else "no workspace thread was busy — the CPU was taken outside this process"


# ── the garbage collector ────────────────────────────────────────────
# A full (generation 2) collection walks every live object while holding
# the interpreter, so on a bench of hundreds of solids plus the planner's
# structures it is a freeze no thread can be blamed for: busy_threads()
# sees nothing, the sim robot's ticks run late, the viewer's ack waits.
# ``watch_gc`` prints one line per collection longer than GC_MS.
GC_MS = 50
_gc_t0 = {}


def _gc_cb(phase, info):
    gen = info.get("generation")
    if phase == "start":
        _gc_t0[gen] = time.perf_counter()
        return
    t0 = _gc_t0.pop(gen, None)
    if t0 is None:
        return
    ms = (time.perf_counter() - t0) * 1000.0
    if ms > GC_MS:
        say("gc", f"generation {gen} collection took {ms:.0f} ms "
                  f"({info.get('collected', 0)} freed, {info.get('uncollectable', 0)} uncollectable) — "
                  f"a freeze of the whole process")


def watch_gc():
    """Install the collector watcher once per process (switch on only)."""
    if LAG_LINES and _gc_cb not in gc.callbacks:
        gc.callbacks.append(_gc_cb)


def freeze_heap(what):
    """Collect, then move every object alive now out of the collector's
    reach (``gc.freeze``): the scene tree, the planner, the caches are
    long-lived, and a full collection that walks them is a 60-120 ms
    freeze of the whole process on a 250-solid bench — for nothing, they
    are never garbage. Objects created afterwards are collected as usual.
    Called when the scene is built and when a run starts; says what it did."""
    gc.collect()
    gc.freeze()
    say("gc", f"{what}: {gc.get_freeze_count()} long-lived objects frozen — full collections skip them")
