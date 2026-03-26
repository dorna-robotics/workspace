# workspace/or/scheduler.py
# CP-SAT scheduler for lab automation protocols.
#
# Reads protocol.yaml, builds a constraint satisfaction model, and returns an
# optimal (minimum makespan) execution order for all remaining tasks.
#
# No numpy dependency — safe with numpy 1.x environments.

from __future__ import annotations

from pathlib import Path

import yaml
from ortools.sat.python import cp_model


class ORScheduler:
    """
    Converts a protocol.yaml into a CP-SAT scheduling problem and solves it.

    Concepts:
    - task        = (state_name, item_index)
    - robot task  = requires exclusive robot access, no two can overlap
    - bg task     = background (shaker, incubator…), robot is free while it runs
                    represented as a single task for item_index=0 covering all items
    - dependency  = task A must finish before task B can start (per item,
                    unless A is a background state — then it's global)

    Usage:
        sched = ORScheduler(protocol_path)
        order = sched.schedule(n_items=4, completed={...})
        # order is [(state_name, item_index), ...] in execution order
    """

    def __init__(self, protocol_path: Path):
        with open(protocol_path) as f:
            proto = yaml.safe_load(f)

        self._states = proto["states"]
        self._goal   = set(proto.get("goal", []))
        self._smap   = {s["name"]: s for s in self._states}
        self._snames = [s["name"] for s in self._states]

    # ── Duration helpers ─────────────────────────────────────────────────────

    def _duration(self, state_name: str) -> int:
        """Midpoint of [min, max] duration, in integer seconds."""
        d = self._smap[state_name].get("duration", [10, 10])
        if isinstance(d, list):
            return max(1, int((d[0] + d[1]) / 2))
        return max(1, int(d))

    # ── Public interface ─────────────────────────────────────────────────────

    def schedule(
        self,
        n_items: int,
        completed: dict[str, set[int]] | None = None,
        horizon_tasks: int | None = None,
    ) -> list[tuple[str, int]]:
        """
        Compute optimal execution order for all remaining tasks.

        Args:
            n_items:       Number of items (tubes) to process.
            completed:     {state_name: {item_indices already done}}.
                           Pass None to schedule everything from scratch.
            horizon_tasks: If set, return only the first N tasks (rolling window).

        Returns:
            List of (state_name, item_index) in execution order.
            Background states always appear as item_index=0.
        """
        if completed is None:
            completed = {n: set() for n in self._snames}

        tasks = self._pending_tasks(n_items, completed)
        if not tasks:
            return []

        result = self._solve_cpsat(tasks, n_items, completed)
        if result is None:
            result = self._topological_sort(tasks, n_items, completed)

        return result[:horizon_tasks] if horizon_tasks else result

    # ── Task enumeration ─────────────────────────────────────────────────────

    def _pending_tasks(
        self,
        n_items: int,
        completed: dict[str, set[int]],
    ) -> list[tuple[str, int]]:
        tasks = []
        for s in self._states:
            sn = s["name"]
            if s.get("background"):
                # One background task covers all items; represented as item 0
                if 0 not in completed.get(sn, set()):
                    tasks.append((sn, 0))
            else:
                for i in range(n_items):
                    if i not in completed.get(sn, set()):
                        tasks.append((sn, i))
        return tasks

    # ── CP-SAT solver ────────────────────────────────────────────────────────

    def _solve_cpsat(
        self,
        tasks: list[tuple[str, int]],
        n_items: int,
        completed: dict[str, set[int]],
    ) -> list[tuple[str, int]] | None:

        model   = cp_model.CpModel()
        horizon = sum(self._duration(sn) for sn, _ in tasks) + 1

        starts    = {}
        ends      = {}
        intervals = {}

        for (sn, i) in tasks:
            dur      = self._duration(sn)
            start    = model.NewIntVar(0, horizon, f"s_{sn}_{i}")
            end      = model.NewIntVar(0, horizon, f"e_{sn}_{i}")
            interval = model.NewIntervalVar(start, dur, end, f"iv_{sn}_{i}")
            starts[(sn, i)]    = start
            ends[(sn, i)]      = end
            intervals[(sn, i)] = interval

        # Robot no-overlap: only foreground tasks compete for the robot
        robot_ivs = [
            intervals[(sn, i)]
            for (sn, i) in tasks
            if not self._smap[sn].get("background")
        ]
        if robot_ivs:
            model.AddNoOverlap(robot_ivs)

        # Dependency constraints
        for (sn, i) in tasks:
            s      = self._smap[sn]
            is_bg  = s.get("background", False)

            for req in s.get("requires", []):
                if req not in self._smap:
                    continue

                req_is_bg = self._smap[req].get("background", False)

                if req_is_bg:
                    # Prereq is a background state — all items share item-0
                    req_key = (req, 0)
                    if req_key in ends:
                        model.Add(starts[(sn, i)] >= ends[req_key])
                elif is_bg:
                    # Background task must wait for ALL items of the prereq
                    # (e.g. shaken must start only after ALL items are loaded)
                    for j in range(n_items):
                        req_key = (req, j)
                        if req_key in ends:
                            model.Add(starts[(sn, i)] >= ends[req_key])
                else:
                    # Regular task depends on same-item prereq
                    req_key = (req, i)
                    if req_key in ends:
                        model.Add(starts[(sn, i)] >= ends[req_key])

        # Minimize makespan
        makespan = model.NewIntVar(0, horizon, "makespan")
        model.AddMaxEquality(makespan, list(ends.values()))
        model.Minimize(makespan)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 5.0
        status = solver.Solve(model)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None

        return sorted(tasks, key=lambda t: solver.Value(starts[t]))

    # ── Fallback: topological sort ───────────────────────────────────────────

    def _topological_sort(
        self,
        tasks: list[tuple[str, int]],
        n_items: int,
        completed: dict[str, set[int]],
    ) -> list[tuple[str, int]]:
        task_set  = set(tasks)
        done: set = set()

        # Pre-populate with already-completed tasks
        for sn, items in completed.items():
            for i in items:
                done.add((sn, i))

        result    = []
        remaining = list(tasks)

        while remaining:
            progress = False
            for t in list(remaining):
                sn, i = t
                s     = self._smap[sn]
                is_bg = s.get("background", False)
                ready = True

                for req in s.get("requires", []):
                    if req not in self._smap:
                        continue
                    req_is_bg = self._smap[req].get("background", False)
                    if req_is_bg:
                        # Prereq is background (global, item 0)
                        keys = [(req, 0)]
                    elif is_bg:
                        # This is a background task — must wait for ALL items
                        keys = [(req, j) for j in range(n_items)]
                    else:
                        keys = [(req, i)]

                    for key in keys:
                        if key in task_set and key not in done:
                            ready = False
                            break
                    if not ready:
                        break

                if ready:
                    result.append(t)
                    done.add(t)
                    remaining.remove(t)
                    progress = True

            if not progress:
                # Cycle or unresolvable — append first remaining to make progress
                result.append(remaining.pop(0))

        return result
