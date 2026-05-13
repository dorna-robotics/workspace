# BT Framework Guide

This document is the **discipline** every BT-based workspace project follows.
Read it once, then use it as the reference when starting a new project or
reviewing a PR.

If you're looking at code that doesn't match the conventions here, the code
is wrong — not the guide.

---

## 1. What is a BT project?

A project is a recipe for solving a lab protocol. It declares:

1. **What** the world looks like (predicates).
2. **What actions** can be performed on that world (with preconditions and
   effects).
3. **What the goal** is.
4. **How long** each action takes and **what resources** it claims.
5. **How** each action is implemented as a recipe call (the BT leaves).
6. **How** to assemble the leaves into a tree (with retries / recovery /
   replan triggers).

Steps 1-4 are *declarative*. Steps 5-6 are *executable*. The framework
generates an action sequence from steps 1-3, schedules it across resources
using step 4, and executes it through steps 5-6 with runtime pause / kill /
replan support.

---

## 2. Project skeleton

Every project lives at `projects/<name>/` with **exactly seven files**:

```
projects/<name>/
├── config/
│   └── base.j2              # scene
├── domain.py                # PDDL: predicates, actions, goal
├── schedule.py              # OR meta: durations + resources
├── actions.py               # BT leaves: one Behaviour per atomic action
├── conditions.py            # BT leaves: one Behaviour per condition
├── tree.py                  # build_tree(schedule, ctx) → root behaviour
├── workflow.py              # run(workspace, core, **kwargs) entry
└── README.md                # 30 lines max, what + where-to-edit table
```

**No file mixes concerns.** Atomic actions live only in `actions.py`.
Conditions live only in `conditions.py`. The protocol shape lives only in
`tree.py`.

A new project is a copy of `pace_bt/` with the seven files filled in.

---

## 3. Naming conventions

Reviewers must be able to tell a file's role from the identifier alone.

| Thing | Style | Examples |
|---|---|---|
| **Predicate** | `snake_case` fact-form | `has_cap`, `weighed`, `in_done` |
| **Action** | `snake_case` verb-form | `decap`, `dispense_heavy`, `shelve` |
| **Condition** | `snake_case` is-question | `is_capped`, `is_heavy`, `is_dosed` |
| **Resource** | `snake_case` singular noun | `robot`, `scale`, `dispenser` |
| **Object type** | `PascalCase` | `Tube`, `ShakerSlot` |
| **BT leaf class** | `PascalCase` of the action/condition | `Decap`, `DispenseHeavy`, `IsCapped` |

A predicate is *not* a verb. A condition is *not* a noun. An action is *not*
a question. Reviewer rejects PRs that mix them.

---

## 4. The framework's contract with project code

The framework (`workspace/bt/`, `workspace/planner/`) provides:

| Capability | Class / function |
|---|---|
| BT leaf base + recipe-call threading | `workspace.bt.RecipeAction` |
| BT condition base | `workspace.bt.PredicateCondition`, `DeviceCondition` |
| Tick loop + pause/resume/kill + replan | `workspace.bt.BTEngine` |
| Replan signal | `workspace.bt.ReplanRequested` |
| Tree helpers | `workspace.bt.{sequence, selector, guarded, with_retry, with_recovery, replan_on_failure, parallel_any, parallel_all, from_schedule}` |
| PDDL forward-search | `workspace.planner.plan`, `ActionTemplate` |
| Greedy resource scheduler | `workspace.planner.schedule_greedy`, `make_schedule_builder` |
| Plan → schedule → tree glue | `workspace.planner.Replanner` |
| Inspection / debugging | `workspace.bt.visualizer.ascii_status` |

The project provides:

| Capability | Where |
|---|---|
| Predicates, actions, goal | `domain.py` |
| Per-action durations + resource | `schedule.py` |
| BT leaf classes (one per action) | `actions.py` |
| BT condition classes | `conditions.py` |
| Tree shape | `tree.py` |
| Entry point | `workflow.py` |

---

## 5. Authoring rules

These are the load-bearing rules. Code that breaks any of them is wrong.

1. **One concept per file.** Atomic actions only in `actions.py`. Conditions
   only in `conditions.py`. Never mix.
2. **No project imports another project.** Shared concerns go to
   `workspace/bt/` or `workspace/planner/`. If two projects need the same
   thing, push it down to the framework.
3. **No recipe redefinitions.** Recipes live in `workspace/recipes/`.
   Projects only *call* them via `RecipeAction.execute`.
4. **No hidden state.** Anything an action depends on goes through
   `WorkspaceContext` (the `ctx` injected at construction). No module-level
   mutable state, no globals.
5. **Effects are explicit.** A `RecipeAction` that changes the world's
   predicates implements `apply_effects(state)`. The framework calls it
   after `execute()` returns `True`. Effects must mirror the PDDL
   template's effects in `domain.py`.
6. **Behaviours are dumb.** Actions just execute. Conditions just observe.
   Decisions belong in the tree shape (generated from PDDL + schedule).
7. **Every leaf must terminate cleanly.** `RecipeAction.terminate` calls
   `runtime.stop()` on the workspace runtime if aborted. Workspace recipes
   already poll runtime stop; subclasses don't need to do more.
8. **Replanning is observed, not forced.** Use `replan_on_failure` only
   around big subtrees, not individual leaves. Replanning on every leaf
   failure is chaos.

---

## 6. The data flow at runtime

```
batch description (kwargs from operator)
        │
        ▼
domain.build_templates(tubes)
domain.make_goal(tubes)             ← what protocol to plan
        │
        ▼
workspace.planner.plan(...)         ← PDDL forward search
        │  ordered Action list
        ▼
schedule.build_schedule(plan)       ← uses META durations + resources
        │  [(action_name, item, start_t), ...]
        ▼
tree.build_tree(schedule, ctx)      ← composes leaves via from_schedule
        │  py_trees root behaviour
        ▼
workspace.bt.BTEngine.run()         ← tick @ 10 Hz, respect runtime,
                                       handle ReplanRequested by
                                       calling replanner.rebuild()
                                       (which observes + re-plans +
                                       re-schedules + re-builds tree)
        │
        ▼
SUCCESS / FAILURE / INVALID (aborted)
```

---

## 7. Adding a new action (the canonical example)

Say you want to add a `weigh` action. Five edits, in order:

### 7.1 Declare the action in `domain.py`

```python
def weigh_pre(state, p):
    (t,) = p
    return ("in_working", t) in state and ("weighed", t) not in state

def weigh_eff(state, p):
    (t,) = p
    return state | {("weighed", t)}

weigh = ActionTemplate(name="weigh", param_iter=for_each,
                       preconditions=weigh_pre, effects=weigh_eff)
```

Add `weigh` to the list returned by `build_templates`.

### 7.2 Declare its schedule meta in `schedule.py`

```python
META["weigh"] = ActionMeta(duration=8, resource="scale")
```

### 7.3 Implement the BT leaf in `actions.py`

```python
class Weigh(_ItemAction):
    def __init__(self, ctx, tube):
        super().__init__(ctx, tube, label="weigh")
    def execute(self) -> bool:
        return self._sim_or_real(8.0)
    def apply_effects(self, state):
        state.setdefault("facts", set()).add(("weighed", self.tube))

_LEAVES["weigh"] = Weigh
```

### 7.4 (Optional) Add a condition in `conditions.py`

```python
class IsWeighed(PredicateCondition):
    def __init__(self, ctx, tube):
        super().__init__(name=f"is_weighed(t{tube})", ctx=ctx)
        self.tube = tube
    def check(self) -> bool:
        return ("weighed", self.tube) in self.ctx.state.get("facts", set())
```

### 7.5 (Optional) Reference in `tree.py`

`from_schedule` will pick `weigh` up automatically because step 7.3 added
it to `_LEAVES`. Only edit `tree.py` if you want non-linear composition
(retry policies, recovery subtrees, parallel-resource sections).

That's it. Domain, schedule, leaves, conditions, tree — five small edits
across four files. No engine changes, no framework changes.

---

## 8. Conditional branching (PDDL handles it)

PDDL doesn't have "if / else" syntax. Branching emerges from preconditions.

To express "if tube is heavy, use dispense_heavy; else dispense_light":

```python
def dispense_light_pre(state, p):
    (t,) = p
    return (("in_working", t) in state
            and ("weight_heavy", t) not in state)   # ← only when light

def dispense_heavy_pre(state, p):
    (t,) = p
    return (("in_working", t) in state
            and ("weight_heavy", t) in state)       # ← only when heavy
```

The planner picks whichever action's preconditions hold. No `if` in the
tree, no `if` in the workflow. Branching is in the domain, where it
belongs.

---

## 9. Recovery and replanning

Three levels, finest-grained first:

### 9.1 Retry — same action, same parameters

Wrap a leaf in `with_retry(leaf, max_attempts=3)`. Two transient failures
retry; third fails the leaf. The framework's `pace_bt/tree.py` does this
on every action with `max_attempts=2`.

### 9.2 Recovery subtree — try something else, then retry

Wrap a pair in `with_recovery(name, action, recovery_subtree)`. If
`action` fails, the recovery subtree runs (e.g., "shake the gripper to
release a stuck cap") and then `action` is retried once.

### 9.3 Full replan — observe + re-plan from current state

Wrap a big subtree in `replan_on_failure(subtree, reason="...")`. A
failure inside that subtree raises `ReplanRequested` to the engine. The
engine calls `replanner.rebuild()`, which re-observes the world, re-runs
PDDL, re-runs the scheduler, and re-builds the tree. Tick loop continues
with the fresh tree.

**Use full replan sparingly.** It's the right answer for "the world has
drifted from what I expected" (drip, technician intervention, device
recovery). It's the wrong answer for "this single action flaked once" —
that's what retry is for.

---

## 10. Performance characteristics

For lab-sized problems on a Pi 5:

| Layer | Cost | Notes |
|---|---|---|
| BT tick (10 Hz) | <1 ms / tick | Pure-Python tree walk. Negligible. |
| PDDL planning | <100 ms typical | BFS forward search. Larger domains may need stronger heuristics. |
| Greedy scheduler | <10 ms | Linear over plan length. |
| Replanning total | <200 ms | observe → plan → schedule → rebuild tree |

The robot motions and lab equipment are 100-1000× slower than any of
these. The framework is never the bottleneck. The Pi can host the BT
runner, the planner, the scheduler, AND the orchestrator without
trouble.

---

## 11. Debugging

* Print the tree structure: `workspace.bt.visualizer.ascii_tree(root)`.
* Snapshot live status during a tick: `ascii_status(root)`.
* Export Graphviz DOT for a tree picture: `dot(root)`.
* Set log level to `INFO`: the engine logs every replan and tick-rate
  miss; the planner logs plan length and state count.

If a project is misbehaving, the question to ask in order:

1. Is the **plan** correct? Print it after `replanner.rebuild()`.
2. Is the **schedule** correct? Print the output of `schedule.build_schedule(plan)`.
3. Is the **tree** correct? `ascii_tree(root)`.
4. Is the **tick** correct? Watch `ascii_status(root)` over time.

Each layer is small and reads on its own, so debugging never requires
unwinding the whole stack at once.
