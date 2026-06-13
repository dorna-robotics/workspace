"""multimeter protocol.

Three-action pattern, fully PDDL-planned:

    Start             — runs once at workflow start (seeds per-sample facts)
    ReadMeter(t)      — for each sample t, waits 10s, reads capacitance,
                         and logs the value as a workflow step
    Park              — runs once at workflow end (motors off, parked flag)

``batch_size`` (operator kwarg) controls how many ReadMeter actions
fire. Each one calls ``rt.sleep(10)`` (pause-aware), then
``rcp["meter"].read_capacitance()`` (sim-agnostic — the component
handles real vs. sim under the hood), then ``rt.step(label)`` so the
reading appears in the dashboard timeline + log.

Framework reference: ../../../docs/bt-framework-guide.md
"""

from __future__ import annotations

from workspace.bt import Action, predicate


# ── 1. Predicates ──────────────────────────────────────────────────────────

started   = predicate("started")    # workspace initialised (global, no args)
parked    = predicate("parked")     # robot parked at end of run (global, no args)
read_done = predicate("read_done")  # sample t has been read


# ── 2. setup — operator kwargs → planning inputs ───────────────────────────


def setup(**kwargs):
    batch_size = int(kwargs.get("batch_size", 1))
    samples = list(range(batch_size))

    # Empty initial state — Start.eff seeds the per-sample facts after
    # the operator's init runs. Same pattern as pace_bt/actions.py.
    facts: set = set()

    def item_done(state, sample):
        return (read_done.name, sample) in state

    def goal(state):
        return (
            (started.name,) in state
            and all(item_done(state, s) for s in samples)
            and (parked.name,) in state
        )

    goal_facts = frozenset(
        [(read_done.name, s) for s in samples]
        + [(started.name,), (parked.name,)]
    )

    return {
        "initial_facts": frozenset(facts),
        "goal":          goal,
        "item_done":     item_done,
        "goal_facts":    goal_facts,
        "objects":       {"sample": samples},
    }


# ── 3. Actions ─────────────────────────────────────────────────────────────


class Start(Action):
    """First action of every run — initialise once. Sets motors on
    (sim no-op) and seeds the per-sample ``read_done(s)`` slot so the
    plan's per-sample preconditions become satisfiable."""

    params   = []
    duration = 2
    resource = "robot"

    def pre(self):
        return ~started()

    def eff(self):
        # ``_ctx_all_objects`` gives the un-sliced sample list so later
        # slices still see the seeds. See pace_bt/actions.py:Start for
        # the same trick.
        samples = self._ctx_all_objects().get("sample", [])
        seeds = [+started()]
        # No per-sample seed facts are needed (read_done starts false
        # for every sample, which is exactly ReadMeter's precondition).
        return {"started": tuple(seeds)}

    def execute(self):
        rt = self.ctx.runtime
        rt.motor(1)
        rt.step("workspace initialised", level="success")
        return "started"


class ReadMeter(Action):
    """Per-sample read. Waits 10 s (pause-aware), reads the meter,
    logs the value as a workflow step. Pure sensor op — no tool, no
    motion. ``resource = "robot"`` so the scheduler serialises reads
    (the meter has one bus anyway)."""

    params   = ["sample"]
    duration = 11  # 10 s sleep + ~1 s read
    resource = "robot"

    def pre(self, sample):
        return started() & ~read_done(sample)

    def eff(self, sample):
        return {"read_done": (+read_done(sample),)}

    def execute(self, sample):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes

        # Pause-aware delay — operator can pause mid-wait and the
        # timer halts until they resume. (rt.sleep, not time.sleep.)
        rt.step(f"sample {sample}: waiting 10s before read…")
        rt.sleep(10)

        # Sim-agnostic call. Component routes to BK879BDriver or the
        # sim stub based on its constructor flag — the action stays
        # clean.
        m = rcp["meter"].read_capacitance(mode="Cp", frequency=1000)

        if m is None:
            # Meter is critical=true, so a real disconnect auto-pauses
            # the runtime. This branch logs the miss for operator
            # visibility; ``rt.step`` is observability-only (does not
            # block), so the pause is observed on the NEXT pause-aware
            # call — usually the next iteration's ``rt.sleep``. See
            # docs/project-guide.md §9.
            rt.step(
                f"sample {sample}: read returned None (meter disconnected)",
                level="warning",
            )
        else:
            rt.step(
                f"sample {sample}: C = {m.primary:.4g} {m.primary_unit} "
                f"@ {m.frequency} Hz",
                level="success",
            )

        return f"read sample {sample}"


class Park(Action):
    """Last action of every run — motors off, parked flag set so the
    goal becomes satisfiable. Mirrors pace_bt/actions.py:Park."""

    params   = []
    duration = 1
    resource = "robot"

    def pre(self):
        # All samples done + not yet parked.
        samples = self._ctx_all_objects().get("sample", [])
        # ``pre`` returns Expr so build_precedence can read deps.
        expr = ~parked() & started()
        for s in samples:
            expr = expr & read_done(s)
        return expr

    def eff(self):
        return {"parked": (+parked(),)}

    def execute(self):
        rt = self.ctx.runtime
        rt.motor(0)
        rt.step("workspace parked", level="success")
        return "parked"
