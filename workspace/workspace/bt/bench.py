"""Bench — run ONE phase of a phased project, from a notebook, with the
project as the only source of truth.

    from workspace.bt.bench import Bench
    bench = Bench(PROJ, port=8000)                 # the project folder (launch.yaml)
    bench.phase("ph1_measured", selected=[0, 1])   # run that phase, those items

Nothing is typed into the notebook but the phase name and the operator
kwargs the HMI would send. Scene, recipes, actions, parameters, checks
and the phase list all come from ``launch.yaml`` — the same files
``main.py`` loads — so a green notebook is the run's own code passing.

HOW A PHASE RUNS ALONE. The launcher runs the first phase whose closure
fact does not hold yet. ``phase(name)`` therefore SEEDS the fact state
the earlier phases leave behind — not declared anywhere: it is what
their actions assert, FACT-REPLAYED (planned and their effects applied,
no motion — ``bt.replay.state_before``) — puts the model where those
phases leave things (``Phase.layout``, the one thing a phase declares
for the bench), and runs the launcher with ``until_phase=name``: it
plans and executes this one phase and returns. The planner orders the
actions exactly as a real run would; the notebook never lists them.

Phases can be run back to back (5, then 7) or cold (13 straight away).
``prepare(name)`` puts the model where the phase starts and prints every
item it moved — the checklist for the real bench — without moving the
robot; ``phase(name)`` prepares (a no-op the second time) and runs. The
tool on the flange is whatever the model says: empty in a fresh kernel,
what the last phase left otherwise; the phase's own actions bring the
tool they declare, as in a run. A phase the seeds left unmet (a Start
that populates a scope at run time, say) is run first, and the launcher
says so in its log.

``run(ActionCls, item)`` is the building block underneath: one of the
project's action classes executed with the project's context — the
same leaf the engine uses, so pre/post checks, tool swaps and effects
behave as in a run — for the times you want to drive actions by hand.
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("workspace.bt.bench")


class Bench:
    def __init__(self, project_dir, *, port: int = 8000, workspace=None):
        from workspace.recipes.solve import load_launch
        from workspace.bt.launcher import load_recipes

        self.project_dir = Path(project_dir).resolve()
        if not (self.project_dir / "launch.yaml").exists():
            raise FileNotFoundError(f"{self.project_dir} holds no launch.yaml")
        self.launch = load_launch(str(self.project_dir))

        # The project's modules import by name from its folder, the way
        # main.py has them (cwd) and bt.replay has them (sys.path).
        if str(self.project_dir) not in sys.path:
            sys.path.insert(0, str(self.project_dir))
        self._fresh_registry()
        self.actions = self._import(self.launch.get("actions", "actions.py"))
        self.checks = self._import(self.launch.get("checks", "checks.py"))

        if workspace is None:
            from workspace.workspace import Workspace
            scene = self.launch["scene"]
            scene = [scene] if isinstance(scene, str) else list(scene)
            scene = [str(self.project_dir / p) for p in scene]
            workspace = Workspace(config_path=scene, port=port,
                                  project_dir=self.project_dir)
        self.workspace = workspace
        self.core = workspace.components["core"]
        self.rt = workspace.rt
        self.rcp = load_recipes(workspace, self.core, self.project_dir / self.launch["recipes"])
        self._ctx = None            # for run(): facts accumulate across calls

    # ── project loading ───────────────────────────────────────────────
    @staticmethod
    def _fresh_registry() -> None:
        """A second Bench in the same kernel must re-register the
        actions: a cached ``actions.<phase>`` module skips registration
        on re-import and leaves the registry empty (same as bt.replay)."""
        for m in [m for m in sys.modules
                  if m in ("actions", "phases", "checks") or m.startswith("actions.")]:
            sys.modules.pop(m, None)
        import workspace.bt.dsl as dsl
        dsl.ActionRegistry._stack = []
        dsl._CAPACITY_PREDICATE_NAMES.clear()

    @staticmethod
    def _import(rel_path: str):
        name = rel_path.rstrip("/").removesuffix("/__init__.py").removesuffix(".py")
        return importlib.import_module(name.replace("/", "."))

    def kwargs(self, **overrides) -> Dict[str, Any]:
        """The run kwargs: launch.yaml's ``default:`` values, overridden
        per call — what the HMI's Start would send."""
        from workspace.bt.replay import resolve_kwargs
        kw = resolve_kwargs(self.launch, project_dir=str(self.project_dir))
        kw.update(overrides)
        return kw

    def _objects(self, kw) -> Tuple[str, list]:
        spec = self.actions.setup(**kw)
        objects = dict(spec.get("objects") or {})
        dim = self.launch.get("slice_dim") or (next(iter(objects)) if len(objects) == 1 else None)
        if dim is None or dim not in objects:
            raise ValueError(f"cannot tell the item dimension from objects={list(objects)}; "
                             "set slice_dim in launch.yaml")
        return dim, list(objects[dim])

    def _protocol(self):
        """The project's ROUTE as a Protocol — the launcher's own loader."""
        from workspace.bt.launcher import _load_route
        return _load_route(self.launch.get("route"), self.actions,
                           project=str(self.launch.get("project_name") or self.project_dir.name))

    def _phases(self, kw) -> list:
        """The project's ``Phase`` instances, in ROUTE order."""
        phases = self._protocol().phases
        if not phases:
            raise ValueError(f"{self.project_dir.name} declares no phases (ROUTE holds none)")
        return phases

    # ── the seeded start state ────────────────────────────────────────
    def start_state(self, name: str, **overrides):
        """What starting at phase ``name`` assumes: ``(seed_facts,
        layout, skipped)``.

        The facts are the state the earlier phases leave behind,
        FACT-REPLAYED from the project's own actions (``bt.replay
        .state_before``: each earlier phase planned and its effects
        applied, no motion) — minus the handful Start's own ``pre``
        reads, because Start still executes in the run (motors, homing)
        and must stay applicable (``_gate_facts``). The layout is every
        closed phase's ``layout``; the skipped list is their names.
        Pure; ``prepare`` applies it."""
        from workspace.bt.replay import state_before

        kw = self.kwargs(**overrides)
        _dim, all_items = self._objects(kw)
        phases = self._phases(kw)
        names = [p.name for p in phases]
        if name not in names:
            raise ValueError(f"{name!r} is not a phase of {self.project_dir.name}: {names}")

        state, failures = state_before(self.actions, self.launch, kw, name)
        if failures:
            raise RuntimeError(
                f"the phases before {name!r} do not replay clean — fix the project first:\n  "
                + "\n  ".join(failures))
        seeds = sorted(state - self._gate_facts(kw))

        layout: List[Tuple[str, dict]] = []
        skipped: List[str] = []
        for p in phases:
            if p.name == name:
                break
            items = list(p.scope(state, all_items))
            if items and p.reached(state, items):
                skipped.append(p.name)
                # An item's LAST resting place wins: a cap parked, put
                # back, parked again is one move to the bench, not three.
                for child, att in p.layout(items):
                    layout = [(c, a) for c, a in layout if c != child] + [(child, att)]
        return seeds, layout, skipped

    def _gate_facts(self, kw) -> set:
        """The facts a seeded start must WITHHOLD so the parameterless
        actions (Start and its kin) still run.

        Start asserts a whole opening state — capacity facts, the
        manifest's per-item facts — and it runs for real in a seeded
        phase run, so seeding all of that again would be redundant.
        It is not optional, though: a phase that SCOPES on one of those
        facts (bna's ``spiked_bna`` / ``spiked_pest`` scope on the
        manifest's spike facts) reads as vacuous at the first observe,
        before Start has executed — and a vacuous target phase is a
        reached one, so the run either ends without moving or walks on
        to a later phase and raises PhaseNotReady.

        So seed everything Start asserts EXCEPT what its own ``pre``
        reads — for bna, ``started``. Those alone would stop it from
        running; the rest it simply re-asserts, which costs nothing."""
        from workspace.bt.behaviours import WorkspaceContext
        from workspace.bt.dsl import Expr, Fact, _normalise_eff, _default_branch

        def gated(expr) -> set:
            """Predicate names an opening action's ``pre`` reads."""
            if isinstance(expr, Fact):
                return {expr.pred}
            if not isinstance(expr, Expr):
                return set()
            fact = getattr(expr, "fact", None)
            if fact is not None:
                return {fact.pred}
            out: set = set()
            for a in expr.args:
                out |= gated(a)
            return out

        spec = self.actions.setup(**kw)
        objects = dict(spec.get("objects") or {})
        ctx = WorkspaceContext(
            workspace=None, core=None, runtime=None, state={"facts": set(spec["initial_facts"])},
            recipes={}, meta={"project": self.launch.get("project_name"), "kwargs": kw,
                              "objects": objects,
                              "all_objects": {k: list(v) for k, v in objects.items()},
                              "checks": {}, "current_tool": None, "event_publisher": None})
        out = set(spec["initial_facts"])
        bookends = [cls for cls in self._protocol().run_steps if not cls.params]
        changed = True
        while changed:
            changed = False
            for cls in bookends:
                inst = cls(); inst.ctx = ctx; inst.state = frozenset(out)
                expr = inst.pre()
                ok = expr if isinstance(expr, bool) else (
                    expr.as_tuple() in out if isinstance(expr, Fact)
                    else expr.evaluate(frozenset(out)))
                if not ok:
                    continue
                eff = _normalise_eff(inst.eff(), cls.__name__)
                for f in eff[_default_branch(eff)]:
                    if isinstance(f, Fact) and f.polarity and f.as_tuple() not in out:
                        out.add(f.as_tuple()); changed = True
        reads: set = set()
        for cls in bookends:
            inst = cls(); inst.ctx = ctx; inst.state = frozenset(out)
            reads |= gated(inst.pre())
        return {t for t in out - set(spec["initial_facts"]) if t[0] in reads}

    def _apply_layout(self, layout) -> None:
        """Over the model AS IT STANDS — what the last phase left, or the
        launch scene in a fresh kernel. Never an implicit reset: phases
        run back to back must keep the tool on the flange and the items
        where the previous phase put them. ``reset`` is the explicit
        way back to the launch scene."""
        for child, att in layout:
            self.workspace.attach(child, att)

    def reset(self) -> None:
        """Snap the model back to the launch scene (every ``attach:``
        re-applied) — before an earlier phase than the last one run,
        with the real bench set the same way."""
        self.workspace.reset_scene()

    def held_tool(self) -> Optional[str]:
        """Recipe alias of the tool on the flange per the model, or
        None. A tool belongs to the rack the launch scene attached it
        to; the alias is the ToolRack recipe over that rack."""
        held = self.core.current_tool()
        if held is None:
            return None
        rack = next((att.get("parent_name")
                     for n, att in getattr(self.workspace, "_initial_attachments", [])
                     if n == held.name), None)
        for alias, r in self.rcp.items():
            comp = getattr(r, "component", None)
            if rack is not None and comp is not None and getattr(comp, "name", None) == rack:
                return alias
        return None

    # ── prepare, then run one phase ───────────────────────────────────
    def prepare(self, name: str, **overrides):
        """Put the MODEL where phase ``name`` starts and say what that
        is — nothing moves on the robot. The earlier phases' layouts
        are applied (the viewer shows them) and every move is printed:
        the checklist for setting the real bench before ``phase``.
        Idempotent. Returns ``(seed_facts, layout, skipped)``."""
        seeds, layout, skipped = self.start_state(name, **overrides)
        self._apply_layout(layout)
        print(f"[bench] phase {name!r} — skipping {len(skipped)}: {', '.join(skipped) or '-'}")
        print(f"[bench] seeded {len(seeds)} fact(s)")
        for child, att in layout:
            print(f"[bench]   {child} -> {att['parent_name']}.{att['parent_anchor']}")
        print("[bench] set the real bench the same way before running"
              if layout else "[bench] items rest where the launch scene puts them")
        return seeds, layout, skipped

    def phase(self, name: str, **overrides):
        """Run phase ``name`` for the operator kwargs given (launch.yaml
        defaults otherwise) and return the engine status. ``prepare``
        first (a no-op if already done), then the launcher runs Start
        and this one phase, with whatever tool the model says is held."""
        from workspace.bt.launcher import load_checks, run_protocol

        kw = self.kwargs(**overrides)
        seeds, _layout, _skipped = self.prepare(name, **overrides)

        checks = load_checks(self.workspace, self.core, self.rcp, checks_module=self.checks, **kw)
        rt = self.rt
        rt.start()
        rt.wait_for_start()
        rt.mark_running()
        try:
            return run_protocol(
                self.workspace, self.core, self.actions,
                recipes=self.rcp,
                checks=checks,
                project_name=self.launch.get("project_name"),
                plan_window=int(self.launch.get("plan_window", 4)),
                slice_dim=self.launch.get("slice_dim"),
                scheduler=str(self.launch.get("scheduler", "cpsat")),
                route=self.launch.get("route"),
                seed_facts=seeds,
                until_phase=name,
                reset_scene=False,          # the layout above IS the start state
                initial_tool=self.held_tool(),
                **kw,
            )
        finally:
            if not getattr(rt, "killed", False):
                rt.mark_idle()

    # ── run one action ────────────────────────────────────────────────
    def run(self, action_cls, item: int = 0, **overrides) -> bool:
        """Execute one of the project's action classes for ``item`` —
        pre/post checks, tool swap and effects as in a run. Facts
        accumulate across calls so a hand-driven sequence reads like
        the planner's. Returns True on success."""
        from workspace.bt.dsl import _DSLActionLeaf
        ctx = self._context(**overrides)
        leaf = _DSLActionLeaf(ctx=ctx, action_cls=action_cls, item_index=item)
        rt = self.rt
        rt.start()
        rt.wait_for_start()
        rt.mark_running()
        try:
            ok = leaf._execute_body()
            if ok:
                leaf.apply_effects(ctx.state)
            return ok
        finally:
            if not getattr(rt, "killed", False):
                rt.mark_idle()

    def _context(self, **overrides):
        if self._ctx is None:
            from workspace.bt.behaviours import WorkspaceContext
            from workspace.bt.launcher import load_checks
            kw = self.kwargs(**overrides)
            spec = self.actions.setup(**kw)
            objects = dict(spec.get("objects") or {})
            self._ctx = WorkspaceContext(
                workspace=self.workspace, core=self.core, runtime=self.rt,
                state={"facts": set(spec["initial_facts"])},
                recipes=self.rcp,
                meta={
                    "project": self.launch.get("project_name"),
                    "kwargs": kw,
                    "objects": objects,
                    "all_objects": {k: list(v) for k, v in objects.items()},
                    "checks": load_checks(self.workspace, self.core, self.rcp,
                                          checks_module=self.checks, **kw) or {},
                    "current_tool": self.held_tool(),
                    "event_publisher": None,
                },
            )
            if hasattr(self.workspace, "set_active_ctx"):
                self.workspace.set_active_ctx(self._ctx)
        return self._ctx

    @property
    def facts(self) -> set:
        """The facts ``run`` has accumulated (empty before the first call)."""
        return set(self._ctx.state.get("facts", set())) if self._ctx else set()
