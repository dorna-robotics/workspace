"""Schedule replay — step 4's logic gate, as a command.

    sudo python3 -m workspace.bt.replay <project_dir> [--batch 1 2 4] [--kw k=v ...]

Runs the project's protocol through the REAL pipeline — the route
lookup → precedence → capacity spans → CP-SAT schedule — then replays
the steps in SCHEDULED order against the real ``pre()``/``eff()``:
every precondition must hold at its scheduled moment and the goal must
be reached. Pure logic: no workspace, no robot, no motion, seconds.

This is what proves pre/eff truthfulness (and therefore schedule
correctness — schedules are derived, never authored). Run it after any
actions.py change, at batch 1 AND a multi-item batch: single-item
catches wrongly-seeded facts, multi-item catches capacity/interleaving
mistakes.

``--batch N...`` sets the launch.yaml's first int kwarg (tube_count /
batch_size / disc_count — whatever the project calls it); other kwargs
take their launch.yaml defaults; ``--kw name=value`` overrides any.
Exit code 0 only if every batch replays clean.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time

import yaml


def resolve_kwargs(launch, batch=None, overrides=(), project_dir=None):
    """launch.yaml kwargs schema → concrete kwargs dict. ``batch``
    lands on the first int-typed kwarg, else on the first kwarg whose
    default is a collection (sliced to N entries); ``overrides`` are
    k=v strings.

    The schema key is ``default:`` (canonical). The old ``kwargs:``
    key still loads but warns — same rule as the orchestrator's
    ``load_kwargs_schema``. Either may be inline OR a file path."""
    launch = dict(launch or {})
    schema = launch.get("default")
    if schema is None and launch.get("kwargs") is not None:
        print("[default] launch.yaml: `kwargs:` was renamed to "
              "`default:` — it still loads, but rename it")
        schema = launch.get("kwargs")
    if isinstance(schema, str) and project_dir:
        from pathlib import Path
        try:
            from jinja2 import Template
            text = (Path(project_dir) / schema).read_text()
            if schema.endswith(".j2") or "{%" in text or "{{" in text:
                text = Template(text).render()
            data = yaml.safe_load(text) or {}
            if isinstance(data, dict) and isinstance(data.get("kwargs"), dict):
                data = data["kwargs"]
            schema = data
        except Exception:
            schema = {}
    if not isinstance(schema, dict):
        schema = {}
    out = {}
    first_int = None
    first_coll = None
    for name, spec in schema.items():
        # Keys starting with "_" are presentation hints for the GUI
        # (``_layout``), never run parameters.
        if name.startswith("_"):
            continue
        # Bare entry vs spec — same rule as the orchestrator's
        # ``normalise_kwargs_schema``: a dict containing "default" is a
        # spec; anything else IS the default (``print_label: false``,
        # ``tubes: {"A1": 0.4}``).
        if not (isinstance(spec, dict) and "default" in spec):
            spec = {"default": spec}
        out[name] = spec.get("default")
        if first_int is None and spec.get("type") == "int":
            first_int = name
        if first_coll is None and isinstance(spec.get("default"), (list, dict)):
            first_coll = name
    if batch is not None and first_int is not None:
        out[first_int] = int(batch)
    elif batch is not None and first_coll is not None:
        # No int to batch on, so batch the first COLLECTION kwarg by
        # slicing its declared default to N entries — "run N items"
        # keeps meaning for a project whose run is a set of positions.
        # The default is the project's own list of what it can run; the
        # platform reads its length and nothing about what the entries
        # mean.
        d = out[first_coll]
        if isinstance(d, dict):
            out[first_coll] = {k: d[k] for k in list(d)[:int(batch)]}
        else:
            out[first_coll] = d[:int(batch)]
    for kv in overrides:
        k, _, v = kv.partition("=")
        try:
            v = yaml.safe_load(v)
        except Exception:
            pass
        out[k] = v
    return out


def _walk(res, out, protocol, ctx, meta, state, t_off, show, tool_now, phase=None):
    """Replay one scheduled window against the real pre()/eff(): every
    precondition at its scheduled moment, effects applied to ``state``.
    Returns (failures, lines, tool_now). Times are offset by ``t_off``."""
    from workspace.bt.dsl import _normalise_eff, Fact, _default_branch
    order = sorted(range(len(res)), key=lambda i: (out[i][2], i))
    failures, lines = [], []
    for i in order:
        a = res[i]
        cls = protocol.get(a.name)
        inst = cls(); inst.ctx = ctx; inst.state = frozenset(state)
        expr = inst.pre(*a.params)
        ok = expr if isinstance(expr, bool) else (
            expr.as_tuple() in state if isinstance(expr, Fact)
            else expr.evaluate(frozenset(state)))
        t = out[i][2] + t_off
        if not ok:
            # Name what is missing at that moment — the fact, not the guess.
            from workspace.bt.dsl import _extract_pre_facts
            pos, neg = _extract_pre_facts(expr)
            miss = [f"{f[0]}{f[1:]}" for f in sorted(pos, key=repr) if f not in state]
            miss += [f"not {f[0]}{f[1:]}" for f in sorted(neg, key=repr) if f in state]
            failures.append(f"{a.name}{a.params} @t={t:.0f}" + (f" [{phase}]" if phase else "")
                            + (f" needs {', '.join(miss)}" if miss else ""))
        m = meta[a.name]
        # The tool circuit, as the scheduler sees it: only an action
        # that DECLARED a tool takes part; a change is a swap the
        # runtime inserts before it (dsl auto-swap). Tracked whether or
        # not we print, because the next window starts from it.
        swapped = m.tool_required and m.tool != tool_now
        if show and swapped:
            lines.append(f"      {'':>6}  swap {tool_now or '-'} -> {m.tool or '-'}")
        if swapped:
            tool_now = m.tool
        if show:
            prm = ", ".join(str(x) for x in a.params)
            res_ = m.resource if isinstance(m.resource, str) else ",".join(m.resource or ())
            held = f" / {m.tool}" if (m.tool_required and m.tool) else ""
            lines.append(f"  t={t:6.0f}  {a.name}({prm})"
                         f"{'':<{max(1, 28 - len(a.name) - len(prm))}}"
                         f"{'PRE FALSE  ' if not ok else ''}"
                         f"[{res_}{held}]  {m.duration}s")
        inst.state = frozenset(state)  # state-aware effs see the live state
        eff = _normalise_eff(inst.eff(*a.params), a.name)
        for f in eff[_default_branch(eff)]:
            if isinstance(f, Fact):
                state.add(f.as_tuple()) if f.polarity else state.discard(f.as_tuple())
    return failures, lines, tool_now


def replay(project_dir, kwargs, show=False, event=False, launch=None):
    """One replay. Returns (plan_len, failures, goal_ok, makespan).

    The replay WALKS THE RUN exactly as the launcher does — the same
    ``current_phase`` / ``pick_window`` code in workspace/bt/phase.py
    and the same route lookup: plan and schedule one window of one
    phase toward that phase's goal, apply the effects, advance, and
    finally the tail toward the run's goal. The per-window schedules
    are laid end to end, each offset by the makespans before it, into
    ONE sequence. A project without phases is windowed the same way
    over its one route.

    ``show=True`` also returns the scheduled sequence as text lines —
    start time, action, params, tool, and every tool swap — the staged
    order an operator can read without a bench. ``event=True`` returns
    the schedule as the ``schedule`` event the launcher publishes to the
    GUI, so the Schedule tab can draw a plan that never ran."""
    sys.path.insert(0, project_dir)
    # A package (``actions/``) registers its classes when its submodules
    # import; those stay cached under ``actions.<phase>`` and would skip
    # registration on the next import, leaving the registry empty.
    for _m in [m for m in sys.modules
               if m in ("actions", "phases") or m.startswith("actions.")]:
        sys.modules.pop(_m, None)
    import workspace.bt.dsl as dsl
    dsl.ActionRegistry._stack = []
    dsl._CAPACITY_PREDICATE_NAMES.clear()
    import actions as A
    try:
        n, failures, goal_ok, mk, _state, extra = _replay_loaded(
            A, kwargs, show=show, event=event, launch=launch,
            project_name=os.path.basename(project_dir))
        return (n, failures, goal_ok, mk, *extra)
    finally:
        sys.path.remove(project_dir)


def state_before(actions_module, launch, kwargs, phase):
    """The fact state a phased project is in when ``phase`` opens — the
    earlier phases fact-replayed (planned and their effects applied, no
    motion) from ``setup()``'s initial state, with the actions module
    ALREADY imported (a notebook's, a bench's). Returns
    ``(state, failures)``; a failure means an earlier phase does not
    replay clean, which is the project's problem to fix first."""
    _n, failures, _ok, _mk, state, _extra = _replay_loaded(
        actions_module, kwargs, launch=launch,
        project_name=str(launch.get("project_name") or ""), until_phase=phase)
    return state, failures


def _replay_loaded(A, kwargs, *, show=False, event=False, launch=None, project_name="",
                   until_phase=None):
    """The walk itself, on an imported actions module — see ``replay``.
    ``until_phase`` stops before planning that phase. Returns
    ``(plan_len, failures, goal_ok, makespan, state, extra)``."""
    from workspace.bt.dsl import build_precedence, derive_capacity_spans, WorkspaceContext
    from workspace.bt.launcher import _load_route
    from workspace.bt.phase import PhaseNotReady, current_phase, pick_window
    from workspace.planner.cpsat_scheduler import schedule_cpsat
    from workspace.planner.plan_scheduler import schedule_greedy
    from workspace.planner.route import RouteError, plan_cycle, plan_route

    spec = A.setup(**kwargs)
    initial = frozenset(spec["initial_facts"])
    objects = dict(spec.get("objects") or {})
    ctx = WorkspaceContext(
        workspace=None, core=None, runtime=None, state={"facts": initial},
        recipes={}, meta={"project": project_name, "kwargs": kwargs,
                          "objects": objects,
                          "all_objects": {k: list(v) for k, v in objects.items()},
                          "checks": {}, "current_tool": None, "event_publisher": None})
    launch = launch or {}
    protocol = _load_route(launch.get("route"), A, project=project_name)
    meta = protocol.meta()
    phases = list(protocol.phases)
    item_done = spec.get("item_done")
    slice_dim = launch.get("slice_dim") or (next(iter(objects)) if len(objects) == 1 else None)
    if slice_dim is not None and slice_dim not in objects:
        raise ValueError(
            f"launch.yaml: slice_dim={slice_dim!r} is not an objects key of setup(): "
            f"{list(objects)} — name one of them, or drop the key for a "
            f"single-dimension protocol")
    all_items = list(objects.get(slice_dim, [])) if slice_dim else []
    plan_window = int(launch.get("plan_window", 4))
    windowed = item_done is not None and slice_dim is not None

    timing = []            # one row per window: where the seconds go
    state = set(initial)
    t_off = 0.0
    tool_now = None
    failures, lines = [], []
    res_all, out_all, swaps_all, window_events = [], [], [], []
    rid = 0
    while True:
        fstate = frozenset(state)
        if spec["goal"](fstate):
            break
        cur = None
        if phases:
            try:
                cur = current_phase(fstate, phases, all_items, item_done)
            except PhaseNotReady as ex:
                failures.append(str(ex))
                break
        if until_phase is not None and cur is not None and cur[0].name == until_phase:
            break                          # the state this phase starts from
        if cur is None:
            # No phase open: the run's own goal — every item done, then
            # the tail (Park). Windowed like the launcher; the last
            # window carries the run's goal.
            if windowed:
                window = pick_window(fstate, [], all_items, item_done, plan_window)
                outside = [it for it in all_items if it not in window and not item_done(fstate, it)]
                goal = (lambda st, _w=window, _o=outside:
                        all(item_done(st, it) for it in _w) and (bool(_o) or spec["goal"](st)))
            else:
                window = list(all_items)
                goal = spec["goal"]
            name, cycle, rounds = None, [], []
        else:
            ph, items = cur
            window = pick_window(fstate, phases, all_items, item_done, plan_window)
            if not window:
                failures.append(f"phase {ph.name}: no window to plan")
                break
            scoped = [it for it in window if it in items] or list(items)
            goal = (lambda st, _p=ph, _s=scoped: _p.reached(st, _s))
            name, cycle = ph.name, protocol.cycle(ph.name)
            rounds = ph.rounds(list(window)) if cycle else []
        if slice_dim:
            ctx.meta["objects"][slice_dim] = list(window)
        ctx.meta["current_phase"] = name
        rid += 1
        t1 = time.perf_counter()
        templates = protocol.templates(ctx, protocol.candidates(name))
        try:
            if cycle:
                res = plan_cycle(templates, fstate, goal, rounds, cycle, explain=protocol.explainer(ctx))
            else:
                res = plan_route(templates, fstate, goal, list(window), explain=protocol.explainer(ctx))
        except RouteError as ex:
            failures.append(f"{name or 'tail'} window {list(window)}: {ex}")
            break
        t2 = time.perf_counter()
        if not res:
            failures.append(f"{name or 'tail'} window {list(window)}: the goal holds but "
                            f"the phase is not reached — its fact is not what the route asserts")
            break
        preds = build_precedence(res, protocol, initial_state=fstate, ctx=ctx)
        caps = derive_capacity_spans(res, protocol, initial_state=fstate, ctx=ctx)
        if cycle:
            # The cycle's order is the schedule: timed as declared, never reordered.
            out, swaps = schedule_greedy(res, meta, predecessors=preds, initial_tool=tool_now)
        else:
            out, swaps = schedule_cpsat(res, meta, predecessors=preds, capacity_spans=caps or None,
                                        initial_tool=tool_now)
        t3 = time.perf_counter()
        timing.append({"phase": name or "tail", "window": len(window), "actions": len(res),
                       "plan_s": round(t2 - t1, 3), "sched_s": round(t3 - t2, 3)})
        if os.environ.get("REPLAY_TRACE"):
            print(f"[replay] {name or 'tail':<16} window {list(window)} actions {len(res):3d} "
                  f"plan {t2 - t1:6.3f}s sched {t3 - t2:5.2f}s",
                  file=sys.__stderr__, flush=True)
        if show:
            lines.append(f"── {name or 'tail'} · window {list(window)} · t0={t_off:.0f} ──")
        f_, l_, tool_now = _walk(res, out, protocol, ctx, meta, state, t_off, show, tool_now, phase=name)
        failures += f_
        lines += l_
        mk = max(out[i][2] + meta[res[i].name].duration for i in range(len(res)))
        out_off = [(n, i, s_ + t_off) for n, i, s_ in out]
        swaps_off = [(s_ + t_off, ft, tt, d) for s_, ft, tt, d in (swaps or [])]
        if event:
            ev = _schedule_event(res, out_off, swaps_off, meta, protocol, t_off + mk, kwargs)
            ev["replan_id"] = rid
            ev["phase"] = name
            ev["window"] = list(window)
            for act in ev["actions"]:
                act["phase"] = name
            window_events.append(ev)
        res_all += list(res)
        out_all += out_off
        swaps_all += swaps_off
        t_off += mk
        if not goal(frozenset(state)):
            failures.append(f"{name or 'tail'} window {list(window)}: the plan did not reach "
                            f"its goal when replayed in scheduled order")
            break
        if rid > 5000:
            failures.append("replay: more than 5000 windows — aborting")
            break
    goal_ok = spec["goal"](frozenset(state)) if until_phase is None else not failures
    if show:
        # Where the seconds went, per phase (bt-framework-guide §13).
        agg = {}
        for row in timing:
            a = agg.setdefault(row["phase"], {"windows": 0, "actions": 0, "plan_s": 0.0, "sched_s": 0.0})
            a["windows"] += 1; a["actions"] += row["actions"]
            for k in ("plan_s", "sched_s"):
                a[k] += row[k]
        lines.append("")
        lines.append(f"  {'phase':<16}{'windows':>8}{'actions':>9}{'plan s':>9}{'sched s':>9}")
        tot = {"plan_s": 0.0, "sched_s": 0.0}
        for nm, a in agg.items():
            lines.append(f"  {nm:<16}{a['windows']:>8}{a['actions']:>9}{a['plan_s']:>9.3f}{a['sched_s']:>9.2f}")
            for k in tot:
                tot[k] += a[k]
        lines.append(f"  {'TOTAL':<16}{'':>8}{len(res_all):>9}{tot['plan_s']:>9.3f}{tot['sched_s']:>9.2f}")
    extra = []
    if show:
        extra.append(lines)
    if event:
        ev = _schedule_event(res_all, out_all, swaps_all, meta, protocol, t_off, kwargs)
        ev["timing"] = timing
        for act, sl in zip(ev["actions"], [a for e in window_events for a in e["actions"]]):
            act["phase"] = sl.get("phase")
        ev["phases"] = [ph.name for ph in phases]
        ev["slices"] = window_events
        extra.append(ev)
    return (len(res_all), failures, goal_ok, t_off, frozenset(state), extra)


def _schedule_event(res, out, swaps, meta, protocol, makespan, kwargs):
    """The launcher's ``schedule`` event shape (launcher.run_protocol),
    from a replay: one window, ``replan_id`` 0, ``preview`` true."""
    from workspace.planner.plan_scheduler import _resources as _r
    acts = []
    for i, a in enumerate(res):
        n = a.name
        cls = protocol.get(n)
        item = a.params[0] if a.params else None
        m = meta.get(n)
        acts.append({
            "leaf_name": f"{n}(t{item})",
            "name": n,
            "class_name": cls.__name__ if cls is not None else n,
            "item": item,
            "parametrized": bool(cls.params) if cls is not None else True,
            "start_t": float(out[i][2]),
            "duration": float(m.duration) if m else 1.0,
            "resources": list(_r(m.resource)) if m else [],
            "tool": m.tool if m else None,
        })
    return {
        "type": "schedule",
        "replan_id": 0,
        "preview": True,
        "batch": kwargs.get("batch_size"),
        "tool_resource": "robot",
        "phase": None,
        "actions": acts,
        "swaps": [{"leaf_name": f"swap({ft or '∅'}→{tt or '∅'})", "from": ft, "to": tt,
                   "start_t": float(st), "duration": float(d)} for st, ft, tt, d in (swaps or [])],
        "makespan": float(makespan),
    }


def main():
    ap = argparse.ArgumentParser(description="Replay the schedule against real pre()/eff() — pure logic, no motion.")
    ap.add_argument("project", help="project directory (holds launch.yaml + actions.py)")
    ap.add_argument("--batch", type=int, nargs="*", default=[1, 4], help="batch sizes (default: 1 4)")
    ap.add_argument("--kw", action="append", default=[], help="kwarg override name=value (repeatable)")
    ap.add_argument("--show", action="store_true",
                    help="print the scheduled sequence: start time, action, tool, swaps")
    ap.add_argument("--json", action="store_true",
                    help="print ONLY the schedule as JSON (the GUI's schedule event) — one batch")
    ap.add_argument("--kwargs-json", default=None,
                    help="a JSON object of kwargs applied over the defaults (before --kw)")
    args = ap.parse_args()

    project = os.path.abspath(args.project)
    from workspace.recipes.solve import load_launch
    launch = load_launch(project)

    extra_kw = json.loads(args.kwargs_json) if args.kwargs_json else {}

    if args.json:
        n = args.batch[0]
        kwargs = resolve_kwargs(launch, batch=n, overrides=args.kw, project_dir=project)
        kwargs.update(extra_kw)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            plan_len, fails, goal_ok, mk, ev = replay(project, kwargs, event=True,
                                                      launch=launch)
        ev["fails"] = list(fails)
        ev["goal_ok"] = bool(goal_ok)
        print(json.dumps(ev))
        sys.exit(1 if (fails or not goal_ok) else 0)

    bad = False
    for n in args.batch:
        kwargs = resolve_kwargs(launch, batch=n, overrides=args.kw, project_dir=project)
        kwargs.update(extra_kw)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            r = replay(project, kwargs, show=args.show, launch=launch)
        plan_len, fails, goal_ok, mk = r[:4]
        status = "OK" if (not fails and goal_ok) else "*** BROKEN ***"
        bad = bad or bool(fails) or not goal_ok
        if args.show:
            print(f"── batch {n} — scheduled order (t = start, s) ──")
            print("\n".join(r[4]))
        print(f"batch={n:<3d} plan={plan_len:3d} actions  fails={len(fails)}  "
              f"goal={goal_ok}  makespan={mk:5.0f}  {status}")
        for f in fails[:4]:
            print(f"    {f}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
