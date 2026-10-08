"""Dispatch scheduler — choose the best allowed step each time something frees up.

The CP-SAT scheduler times a whole window up front: every step of every
item in the window, all the way to the phase goal, in one model. That
model grows with window width x protocol depth, which is why a big
protocol is cut into phases and hand-written cycles.

This scheduler never builds that model. It runs the window as an
event-driven simulation and, at every event (the robot or a device frees
up), asks the ROUTE what each item may do next and picks one:

    candidates   for every unit (the itemless run steps + each item) the
                 route's next step (``route._next_step``) whose ``pre``
                 holds — in the projected world (everything started so far
                 applied, so a seat taken is taken) AND in the settled
                 world (only what has finished, so a step never starts
                 before the step that enables it is done) — and whose
                 resources are free now. One step at a time per item.
    devices      steps that do not use the tool-holding resource (a shake,
                 a rest, a vortex run) start the moment they are allowed:
                 they compete for nothing the robot needs.
    the robot    among its candidates, the RANKING picks — a key over a
                 Candidate (below), lowest first (``time``):
                   1. works at the bottleneck — the step holds a station
                      besides the robot (resource ("scale", "robot")), and
                      that station has at least as much work left as the
                      robot (so it mustn't sit idle)
                   2. feeds the bottleneck — the item's next route step is
                      a device step, and that device has at least as much
                      work left as the robot
                   3. setup — the tool swap seconds it needs first (0: none)
                   4. feeds a device — the item's next route step is a
                      device step (the load before a shake)
                   5. furthest along the route
                   6. item order, then route order (deterministic)
                 "Work left" on a resource is the summed durations of the
                 route steps every unit still has ahead on it. Feeding any
                 device ahead of a tool swap (the ranking up to 2026-10)
                 lost most where devices are few and short: a device only
                 jumps the swap when it is the bottleneck. Measured against
                 the exact optimum (CP-SAT) on 425 random instances: a
                 station step first ALWAYS (not only at the bottleneck) lost
                 3% on average, and so did making the robot wait for one;
                 a liquid-change flag, "serves a device", "same step as last"
                 and "shorter first" changed nothing or lost.

The scheduler and its ranking are one built-in thing, the platform's like
motion planning: no project replaces either (launch.yaml names no
``optimizer:`` and no ``scheduler: <file>.py`` — ``resolve()`` refuses them).

Loops and retries are the protocol's, not the scheduler's: a step whose
effect makes an earlier step applicable again (adjust → measure again) is
simply offered again; one step at a time per item, and a step runs again
only once its previous run has finished.

Same state in, same schedule out. The result has the scheduler's usual
shape — ``(plan, schedule, swaps)`` — so ``bt.replay`` re-checks it
against the real pre()/eff() exactly like a CP-SAT window.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from workspace.planner.plan_scheduler import ActionMeta, _resources
from workspace.planner.route import RouteError, Step, Template, _next_step

TOOL_RESOURCE = "robot"


class DispatchStall(RouteError):
    """Nothing can start and nothing is running, yet the goal is not reached."""


@dataclass(frozen=True)
class Candidate:
    """One robot step the ranking may pick now."""
    step: Step                 # the grounded step (name, params)
    item: Any                  # its item (None: a run-level step)
    item_pos: int              # the item's place in the batch
    index: int                 # its place in the route (higher = further along)
    duration: float            # seconds, the action's estimate
    swap_s: float              # tool swap seconds it needs first (0: none)
    finish: float              # when it would end if started now
    feeds_device: bool         # the item's next step is a device's (load before a shake)
    feeds_bottleneck: bool = False   # feeds a device with at least the robot's work left
    at_bottleneck: bool = False      # holds a station (besides the robot) with at least the robot's work left


def time(c: Candidate):
    """The ranking: keep the bottleneck busy (work at it, then load it), then
    the least setup, keep devices fed, finish items."""
    return (not c.at_bottleneck, not c.feeds_bottleneck, c.swap_s, not c.feeds_device, -c.index,
            c.item_pos, c.index)


def resolve(launch: Dict[str, Any], project_dir=None):
    """The scheduler for a project: always the built-in ``dispatch`` with its
    ``time`` ranking. A launch.yaml that asks for a project optimizer or a
    scheduler file is refused — the scheduler is the platform's.
    Returns (dispatch_fn, key_fn)."""
    sched = str((launch or {}).get("scheduler") or "dispatch")
    opt = (launch or {}).get("optimizer")
    if sched.endswith(".py") or (opt and str(opt) != "time"):
        raise ValueError("the scheduler is built in: a project can't bring its own scheduler or optimizer "
                         "(remove 'optimizer:' and use 'scheduler: dispatch' in launch.yaml)")
    return dispatch, time


def dispatch(
    templates: Sequence[Template],
    state,
    goal,
    items: Sequence[Any],
    meta: Dict[str, ActionMeta],
    *,
    initial_tool: Optional[str] = None,
    capacity_names: Sequence[str] = (),
    tool_resource: str = TOOL_RESOURCE,
    optimizer: Optional[Callable[[Candidate], Any]] = None,
) -> Tuple[List[Step], List[Tuple[str, int, float]], List[Tuple[float, Optional[str], Optional[str], int]], Optional[str]]:
    """Run the window to ``goal``. Returns (plan, schedule, swaps, tool at the end).

    ``schedule`` rows are ``(name, item, start seconds from 0)`` in plan
    order — the item as the other schedulers give it (the step's item
    parameter, 0 for a run-level step), which is what the tree names its
    leaves by (``prime(t0)``) and matches its precedence edges to;
    ``swaps`` rows ``(start, from tool, to tool, seconds)`` — the same
    shapes ``schedule_cpsat`` returns.
    """
    settled = frozenset(state)                 # what has finished
    proj = settled                             # plus everything started
    units: List[Any] = [None] + list(items)
    index = {t.name: i for i, t in enumerate(templates)}
    device_steps = {t.name for t in templates
                    if meta.get(t.name) and _resources(meta[t.name].resource)
                    and tool_resource not in _resources(meta[t.name].resource)}
    caps = set(capacity_names)

    # Each template's bindings per unit, worked out once: the window's
    # objects don't change while it runs, and re-enumerating them for
    # every unit at every event was nearly all of the time (route's
    # _bindings_for walks the whole Cartesian product each call).
    binds: Dict[str, Dict[Any, List[Tuple[Any, ...]]]] = {}
    for t in templates:
        per: Dict[Any, List[Tuple[Any, ...]]] = {}
        for params in t.param_iter(settled):
            params = tuple(params)
            per.setdefault(params[0] if params else None, []).append(params)
        binds[t.name] = per

    key_of = optimizer or time

    # Work left per resource, for "is that device the bottleneck?": each
    # unit's route from its next position on (the steps that bind to it),
    # summed per device and for the robot. Suffix sums, worked out once.
    def _res_of(name):
        rs = [r for r in _resources(meta[name].resource) if r != tool_resource] if meta.get(name) else []
        return tuple(rs) if name in device_steps else (tool_resource,)
    suffix: Dict[Any, List[Dict[str, float]]] = {}
    for unit in units:
        acc: Dict[str, float] = {}
        rows: List[Dict[str, float]] = [dict()] * (len(templates) + 1)
        for j in range(len(templates) - 1, -1, -1):
            t = templates[j]
            if meta.get(t.name) and binds[t.name].get(unit) and (unit is not None or not binds[t.name][unit][0]):
                acc = dict(acc)
                for r in _res_of(t.name):
                    acc[r] = acc.get(r, 0.0) + float(meta[t.name].duration)
            rows[j] = acc
        suffix[unit] = rows
    unit_pos: Dict[Any, int] = {}          # each unit's route position after its last started step

    def work_left():
        tot: Dict[str, float] = {}
        for unit in units:
            for r, v in suffix[unit][min(unit_pos.get(unit, 0), len(templates))].items():
                tot[r] = tot.get(r, 0.0) + v
        return tot

    def next_step(st_, unit):
        """route._next_step with the bindings cached: the first route step
        that applies to ``unit`` in ``st_`` — scanned from the top, so a
        loop (an effect that re-opens an earlier step) is followed."""
        for t in templates:
            for params in binds[t.name].get(unit, ()):
                if unit is None and params:
                    continue
                try:
                    ok = t.pre(st_, params)
                except Exception as ex:
                    raise RouteError(f"{t.name}{params}: pre() raised {type(ex).__name__}: {ex}") from ex
                if ok:
                    return t.ground(params)
        return None

    plan: List[Step] = []
    sched: List[Tuple[str, int, float]] = []
    swaps: List[Tuple[float, Optional[str], Optional[str], int]] = []
    res_free: Dict[str, float] = {}
    unit_free: Dict[Any, float] = {}
    inflight: List[Tuple[float, int, Step]] = []   # heap of (end, seq, step)
    taken: set = set()
    tool = initial_tool
    now = 0.0
    seq = 0

    def settle_until(t):
        nonlocal settled
        while inflight and inflight[0][0] <= t + 1e-9:
            _, _, st = heapq.heappop(inflight)
            taken.discard((st.name, st.params))     # finished: may be offered again (a loop)
            nxt = st.eff(settled)
            settled = nxt if isinstance(nxt, frozenset) else frozenset(nxt)

    def ready(unit):
        if unit_free.get(unit, 0.0) > now + 1e-9:
            return None
        step = next_step(proj, unit)
        if step is None or (step.name, step.params) in taken:
            return None
        try:
            if not step.pre(settled):          # its enabling step hasn't finished yet
                return None
        except Exception as ex:
            raise RouteError(f"{step!r}: pre() raised {type(ex).__name__}: {ex}") from ex
        m = meta.get(step.name)
        if m is None:
            return None
        if any(res_free.get(r, 0.0) > now + 1e-9 for r in _resources(m.resource)):
            return None
        return step

    def start(step, unit):
        nonlocal proj, tool, seq
        m = meta[step.name]
        t = now
        rs = _resources(m.resource)
        if m.tool_required and m.tool != tool:
            swaps.append((t, tool, m.tool, m.tool_swap_duration))
            t += m.tool_swap_duration
            tool = m.tool
        end = t + max(1, int(m.duration))
        for r in rs:
            res_free[r] = end
        if tool_resource in rs or m.tool_required:
            res_free[tool_resource] = max(res_free.get(tool_resource, 0.0), end)
        unit_free[unit] = end
        unit_pos[unit] = index.get(step.name, 0) + 1
        nxt = step.eff(proj)
        proj = nxt if isinstance(nxt, frozenset) else frozenset(nxt)
        taken.add((step.name, step.params))
        try:
            raw = step.params[m.item_arg_index]
            item = raw if isinstance(raw, int) else int(raw)
        except (IndexError, ValueError, TypeError):
            item = 0
        sched.append((step.name, item, t))
        plan.append(step)
        heapq.heappush(inflight, (end, seq, step))
        seq += 1

    caps_seen = {f for f in settled if f[0] in caps}

    def safe(step, unit):
        """Never hold one capacity while waiting on another (hold-and-wait
        is the deadlock): if the step takes a capacity fact (the hand, a
        seat), walk this item forward alone on the projected world; if it
        gets stuck before releasing it and the only thing missing is a
        capacity (relax them all and it could move), the step is unsafe.
        Stuck on anything else (a bank still filling) is fine."""
        if unit is None or not caps:
            return True
        try:
            after = step.eff(proj)
        except Exception:
            return True
        after = after if isinstance(after, frozenset) else frozenset(after)
        caps_seen.update(f for f in proj if f[0] in caps)
        held = [f for f in proj if f[0] in caps and f not in after]
        if not held:
            return True
        sim = after
        relax = frozenset(caps_seen)
        for _ in range(40):
            if all(f in sim for f in held):
                return True
            nxt = next_step(sim, unit)
            if nxt is None:
                return next_step(sim | relax, unit) is None
            try:
                s2 = nxt.eff(sim)
            except Exception:
                return True
            sim = s2 if isinstance(s2, frozenset) else frozenset(s2)
        return True

    def candidate(item_pos, unit, step, left):
        i = index.get(step.name, 0)
        m = meta[step.name]
        nxt = templates[i + 1].name if i + 1 < len(templates) else None
        feeds = nxt in device_steps
        robot_left = left.get(tool_resource, 0.0)
        swap_s = float(m.tool_swap_duration) if (m.tool_required and m.tool != tool) else 0.0
        return Candidate(
            step=step, item=unit, item_pos=item_pos, index=i, duration=float(m.duration), swap_s=swap_s,
            finish=now + swap_s + max(1, int(m.duration)),
            feeds_device=feeds,
            feeds_bottleneck=feeds and max((left.get(r, 0.0) for r in _res_of(nxt)), default=0.0) >= robot_left,
            at_bottleneck=max((left.get(r, 0.0) for r in _resources(m.resource) if r != tool_resource),
                              default=-1.0) >= robot_left)

    guard = 0
    while True:
        guard += 1
        if guard > 200000:
            raise DispatchStall("dispatch: too many events — aborting")
        settle_until(now)
        done_starting = goal(proj)
        started = False
        if not done_starting:
            # one pass over the units: devices start at once (they take
            # nothing the robot needs); the robot's candidates are ranked
            robot_free = res_free.get(tool_resource, 0.0) <= now + 1e-9
            cands = []
            for pos, unit in enumerate(units):
                st = ready(unit)
                if st is None:
                    continue
                if st.name in device_steps:
                    start(st, unit)
                    started = True
                elif robot_free:
                    cands.append((pos, unit, st))
            if robot_free and cands:
                ranked = []
                left = work_left()
                for pos, unit, st in cands:
                    # a device start above may have changed the world: re-check
                    try:
                        if not (st.pre(proj) and st.pre(settled)):
                            continue
                    except Exception:
                        continue
                    if unit_free.get(unit, 0.0) > now + 1e-9 or not safe(st, unit):
                        continue
                    # …and its stations: a device step started above may have just taken
                    # the station this robot step works at (both would hold it at once)
                    if any(res_free.get(r, 0.0) > now + 1e-9 for r in _resources(meta[st.name].resource)
                           if r != tool_resource):
                        continue
                    ranked.append(candidate(pos, unit, st, left))
                if ranked:
                    best = min(ranked, key=key_of)
                    unit, st = best.item, best.step
                    start(st, unit)
                    started = True
        if started:
            continue
        if done_starting and not inflight:
            break
        # advance to the next event
        nxt_times = [e for e, _, _ in inflight]
        nxt_times += [t for t in res_free.values() if t > now + 1e-9]
        nxt_times += [t for t in unit_free.values() if t > now + 1e-9]
        future = [t for t in nxt_times if t > now + 1e-9]
        if not future:
            if goal(proj):
                break
            lines = []
            for unit in units:
                st = next_step(proj, unit)
                who = "run" if unit is None else f"item {unit!r}"
                lines.append(f"{who}: next {st!r}" if st is not None else f"{who}: nothing allowed")
            recent = ", ".join(repr(s_) for s_ in plan[-12:])
            raise DispatchStall(
                "dispatch: nothing can start and nothing is running — the goal is not reached.\n  "
                + "\n  ".join(lines) + f"\n  last started: {recent}")
        now = min(future)
    return plan, sched, swaps, tool
