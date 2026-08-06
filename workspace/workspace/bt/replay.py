"""Schedule replay — step 4's logic gate, as a command.

    sudo python3 -m workspace.bt.replay <project_dir> [--batch 1 2 4] [--kw k=v ...]

Runs the project's protocol through the REAL pipeline — PDDL plan →
precedence → capacity spans → CP-SAT schedule — then replays the
actions in SCHEDULED order against the real ``pre()``/``eff()``:
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
import os
import sys

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


def replay(project_dir, kwargs):
    """One replay. Returns (plan_len, failures, goal_ok, makespan)."""
    sys.path.insert(0, project_dir)
    sys.modules.pop("actions", None)
    import workspace.bt.dsl as dsl
    dsl.ActionRegistry._stack = []
    dsl._CAPACITY_PREDICATE_NAMES.clear()
    import actions as A
    from workspace.bt.dsl import (ActionRegistry, build_precedence, derive_capacity_spans,
                                  WorkspaceContext, _normalise_eff, Fact, _default_branch)
    from workspace.planner.pddl import domain_from_templates, plan as pddl_plan
    from workspace.planner.cpsat_scheduler import schedule_cpsat

    try:
        spec = A.setup(**kwargs)
        initial = frozenset(spec["initial_facts"])
        objects = dict(spec.get("objects") or {})
        ctx = WorkspaceContext(
            workspace=None, core=None, runtime=None, state={"facts": initial},
            recipes={}, meta={"project": os.path.basename(project_dir), "kwargs": kwargs,
                              "objects": objects,
                              "all_objects": {k: list(v) for k, v in objects.items()},
                              "checks": {}, "current_tool": None, "event_publisher": None})
        reg = ActionRegistry.current()
        meta = reg.to_meta()
        domain = domain_from_templates(reg.to_templates(ctx))
        gf = spec.get("goal_facts") or reg.derive_goal_facts(ctx)
        res = pddl_plan(initial, domain, spec["goal"], goal_facts=gf)
        preds = build_precedence(res, reg, initial_state=initial, ctx=ctx)
        caps = derive_capacity_spans(res, reg, initial_state=initial, ctx=ctx)
        out, _ = schedule_cpsat(res, meta, predecessors=preds, capacity_spans=caps or None)

        order = sorted(range(len(res)), key=lambda i: (out[i][2], i))
        state = set(initial)
        failures = []
        for i in order:
            a = res[i]
            cls = reg.get(a.name)
            inst = cls(); inst.ctx = ctx; inst.state = frozenset(state)
            expr = inst.pre(*a.params)
            ok = expr if isinstance(expr, bool) else (
                expr.as_tuple() in state if isinstance(expr, Fact)
                else expr.evaluate(frozenset(state)))
            if not ok:
                failures.append(f"{a.name}{a.params} @t={out[i][2]:.0f}")
            inst.state = frozenset(state)  # state-aware effs see the live state
            eff = _normalise_eff(inst.eff(*a.params), a.name)
            for f in eff[_default_branch(eff)]:
                if isinstance(f, Fact):
                    state.add(f.as_tuple()) if f.polarity else state.discard(f.as_tuple())
        goal_ok = spec["goal"](frozenset(state))
        mk = max(out[i][2] + meta[res[i].name].duration for i in range(len(res)))
        return len(res), failures, goal_ok, mk
    finally:
        sys.path.remove(project_dir)


def main():
    ap = argparse.ArgumentParser(description="Replay the schedule against real pre()/eff() — pure logic, no motion.")
    ap.add_argument("project", help="project directory (holds launch.yaml + actions.py)")
    ap.add_argument("--batch", type=int, nargs="*", default=[1, 4], help="batch sizes (default: 1 4)")
    ap.add_argument("--kw", action="append", default=[], help="kwarg override name=value (repeatable)")
    args = ap.parse_args()

    project = os.path.abspath(args.project)
    from workspace.recipes.solve import load_launch
    launch = load_launch(project)

    bad = False
    for n in args.batch:
        kwargs = resolve_kwargs(launch, batch=n, overrides=args.kw, project_dir=project)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            plan_len, fails, goal_ok, mk = replay(project, kwargs)
        status = "OK" if (not fails and goal_ok) else "*** BROKEN ***"
        bad = bad or bool(fails) or not goal_ok
        print(f"batch={n:<3d} plan={plan_len:3d} actions  fails={len(fails)}  "
              f"goal={goal_ok}  makespan={mk:5.0f}  {status}")
        for f in fails[:4]:
            print(f"    {f}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
