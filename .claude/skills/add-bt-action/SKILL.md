---
name: add-bt-action
description: "Use when adding an Action class to a BT project. Covers predicate declaration, pre/eff/execute contract, the params+objects link, multi-branch eff, and the gripper-empty boundary convention."
---

# Add a BT action

## When to use this skill

The user says any of:
- "Add an action to weigh / decap / shake / measure each [item]"
- "Write a Start / Park / Inspect action"
- "Extend an existing project's protocol"

If the user is starting a **new BT project** from scratch (not just adding one action), see also [`write-recipe`](../write-recipe/SKILL.md) for the recipe layer + look at the gold exemplars under `examples/` (`examples/feeder/` simple, `examples/capping/` multi-action) as templates.

## What an action is

An **Action** subclass represents one atomic, transactional step in a BT-planned workflow. It has:
- `params` — the typed arguments (e.g. `["sample"]`)
- `pre(...)` — precondition expression over predicates
- `eff(...)` — effect (dict of named branches, applied on success)
- `execute(...)` — the actual `rt.*` work, returns the chosen eff branch name
- Optional `duration`, `resource`, `tool`, `pre_check`, `post_check`, `trigger`

The framework auto-registers every Action subclass — no domain.py.

## Quick rules

1. **Predicates are nouns or adjectives, not verbs.** `weighed(sample)`, `in_source(tube)`, `read_done(s)`. Declare them at module top: `weighed = predicate("weighed")`.
2. **`pre()` returns an `Expr`**, not a raw bool. `return in_source(t) & ~weighed(t) & has_cap(t)`. The framework reads the dependency graph from this expression — raw bools break `build_precedence`.
3. **`eff()` returns a dict of branches**, not a flat list. `return {"weighed": (+weighed(t), -in_source(t), +in_working(t))}`. `execute()` returns the branch name; framework applies that branch's facts.
4. **One BT-tick per atomic op.** Don't bundle "pick + decap" into one action — split. Tool swaps and intermediate state transitions belong in their own actions so the planner can schedule + the operator can pause cleanly between.
5. **Gripper empty at action boundaries** (or use the explicit `gripper_holds(tool)` predicate for multi-action sequences). bt-framework-guide.md §3.7.
6. **`Start` / `Park` / `OperatorPark` stay the canonical shape across all projects.** Don't bleed per-item motion / IO / state into them — that's what the per-item actions (`FeedCap`, `Inspected`, `ReadMeter`, …) are for.

    **Canonical `Start`**: `params=[]`, `duration=5`, `resource="robot"`, `pre = ~started()`, `eff = +started()`, `execute = rt.motor(1) + rcp["<tool_alias>"].park(joint=[0, 45, -90, 0, -45, 0, 100], has_motion_plan=True)`.

    **Canonical `Park`**: `params=[]`, `duration=5`, `tool=None`, `PARK_JOINTS = [0, 185, -94, 0, 0, 0, 100]` (override in subclass if needed), `pre` walks `_ctx_all_objects` to require every per-item predicate plus `~parked()`, `execute = rcp["<tool_alias>"].park(self.PARK_JOINTS, has_motion_plan=True) + rt.motor(0)`.

    **Canonical `OperatorPark`**: three-line subclass of `Park` that flips `trigger = "park"` — fires on the operator's Park button outside the plan.

    Only **three** things vary per project: the tool recipe alias (`gripper` vs `cap_tool` vs your own), the per-item predicate name (`vial_2ml_capped` vs `cap_fed` vs `read_done`), and the object key for `_ctx_all_objects` (`"tube"` vs `"cap"` vs `"sample"`). Everything else stays.

    Canonical reference: `examples/feeder/actions.py:Start/Park/OperatorPark` — every example follows the same shape.
7. **Use `_ctx_all_objects()`** in `eff()` if you need to seed facts for the FULL object list, not just the current slice — bt-framework-guide.md §12.

## Canonical doc references

| Section | What you'll find |
|---|---|
| `docs/bt-framework-guide.md` §3 | Action authoring (vocabulary, class attrs, pre/eff/execute) |
| `docs/bt-framework-guide.md` §3.3 | Multi-branch `eff()` |
| `docs/bt-framework-guide.md` §3.4 | `self.ctx` runtime context (workspace, core, recipes, runtime) |
| `docs/bt-framework-guide.md` §3.5-3.6 | Predicate / Fact / State internals |
| `docs/bt-framework-guide.md` §3.7 | Atomic gripper convention + `gripper_holds` predicate |
| `docs/bt-framework-guide.md` §8 | Dynamic world / sensing actions / `RescanRack` pattern |
| `docs/bt-framework-guide.md` §9 | Runtime fact mutation (`workspace.add_fact()` / `remove_fact()`) |
| `docs/project-guide.md` §5 | How protocol.j2 references actions by class name |

## Canonical reference implementations

- **feeder actions**: `examples/feeder/actions.py` — minimal per-item template (Start → per-item action → Park)
- **capping actions**: `examples/capping/actions.py` — multi-action protocol with progress reporting
- **runtime actions**: `examples/runtime/actions.py` — full reference incl. runtime scene mutation

## Common pitfalls

- **`pre()` returns a Python `bool`** (`return True if x else False`) — breaks the precedence graph. Always return an `Expr` built from predicates.
- **Eff seeds wrong objects** — using current-slice `objects` instead of `_ctx_all_objects()` for full-batch seeding leaves later slices without the facts. bt-framework-guide.md §12.
- **Heavy work in `pre()` or `eff()`** — they're called by the planner repeatedly; keep them pure / O(1). Real I/O goes in `execute()`.
- **`execute()` returns nothing** — the framework reads the return value as the branch name. Always return a string matching one of `eff()`'s keys, `False` for a recoverable failure (planner replans), or the reserved `"killed"` for a fatal no-motion abort of the whole run. Never name an `eff()` branch `"killed"`. bt-framework-guide.md §3.3 "Fatal abort".
- **Branching on `self.ctx.runtime.state`** in `execute()` to detect pause — wrong layer. `rt.sleep` / `rt.<robot>` calls are pause-aware automatically. project-guide.md §8.
- **Adding a fact-mutation call (`workspace.add_fact()`) inside `execute()`** when an `eff()` branch would do — `eff` is declarative and auditable; ad-hoc mutation isn't. Reserve `workspace.add_fact()` for genuinely state-aware sensing actions. bt-framework-guide.md §8-9.

## After this

- If your action calls device-specific code, also see [`write-recipe`](../write-recipe/SKILL.md) for the recipe layer.
- For pause / recovery semantics during action execution: [`operator-recovery`](../operator-recovery/SKILL.md).
- For testing the workflow end-to-end: run the project in the orchestrator with `simulation: true` first; see [`enable-sim-mode`](../enable-sim-mode/SKILL.md).
