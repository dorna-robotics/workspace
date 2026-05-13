# BT Framework Guide

This is the **discipline** every BT-based workspace project follows.
Read it once, then keep it as the reference for starting a new project
or reviewing a PR.

If you're looking at code that doesn't match what's here, the code is
wrong — not the guide.

---

## 1. What is a BT project?

A project is a recipe for solving a lab protocol. It declares:

1. **Predicates** — facts about the world.
2. **Actions** — what each atomic step does, including its precondition,
   effects, duration, resource claim, and how it's executed.
3. **Goal** — what "done" means.
4. **Initial state** — what's true at t=0.

That's the whole declaration. The framework derives everything else:

* a PDDL action sequence to the goal,
* an OR-style schedule across parallel resources,
* a BT for execution with retry / recovery / replan,
* condition leaves for any predicate (auto-generated when needed),
* `apply_effects` mirroring the declared `eff()` so state propagates.

Authors never write duplicated declarations. One action = one block.

---

## 2. Project skeleton

```
projects/<name>/
├── config/
│   └── base.j2              # scene
├── main.py                  # orchestrator launcher (Workspace + RuntimeServer)
├── launch.yaml              # scene paths + GUI kwargs schema
├── actions.py               # predicates + initial_state + make_goal +
│                            # one Action subclass per atomic step
├── workflow.py              # run(workspace, core, **kwargs) entry
├── tree.py                  # OPTIONAL — custom tree shape; default works
└── README.md                # 30 lines max — what + where-to-edit table
```

**Two substantive files for the protocol** (`actions.py` +
`workflow.py`) plus `main.py` + `launch.yaml` (orchestrator
boilerplate, ~30 lines each — copy from `pace_bt/`), the scene, and
a README. No `domain.py`, no `conditions.py`, no `schedule.py`, no
`_LEAVES` dict — the framework generates them from the `Action`
subclasses you declare.

A new project is a copy of `pace_bt/` with `actions.py` filled in for
your protocol, and tiny tweaks to `launch.yaml` for kwargs.

### What each file does

* **`main.py`** — the orchestrator spawns this with `python3 main.py
  --port N`. It reads `launch.yaml`, instantiates `Workspace`, defines
  a `workflow_fn(*, workspace, core, **kwargs)` that calls
  `workflow.run()`, and starts the `RuntimeServer`. Identical across
  projects except for which `workflow.run` they call.
* **`launch.yaml`** — declares the scene paths (passed to `Workspace`)
  and the kwargs schema rendered into the operator's Parameters modal.
* **`actions.py`** — the **only** file you usually edit when defining
  a protocol. Predicates, initial state, goal, and one `Action`
  subclass per atomic step.
* **`workflow.py`** — short. Wires the auto-populated
  `ActionRegistry` into the `BTEngine`. Copy from `pace_bt/`, change
  the import lines if needed.
* **`tree.py`** — optional. Only when you want a custom tree shape
  (extra retry layers, parallel composites, custom recovery
  subtrees). The default tree comes from `workflow.py` calling
  `from_schedule(...)` directly.

---

## 3. The authoring style — one block per action

```python
from workspace.bt import Action, predicate

# Predicates — declare once at the top.
in_source     = predicate("in_source")
in_working    = predicate("in_working")
has_cap       = predicate("has_cap")
weighed       = predicate("weighed")
weight_heavy  = predicate("weight_heavy")
dosed         = predicate("dosed")

# Initial world.
def initial_state(tubes, heavy=()):
    facts = set()
    for t in tubes:
        facts.add((in_source.name, t))
        facts.add((has_cap.name, t))
        if t in heavy:
            facts.add((weight_heavy.name, t))
    return frozenset(facts)

# Goal.
def make_goal(tubes):
    return lambda s: all((in_done.name, t) in s for t in tubes)

# One block per action.
class Decap(Action):
    """Remove cap, transfer tube to working rack."""
    params   = ["tube"]
    duration = 10
    resource = "robot"

    def pre(self, tube):
        return in_source(tube) & has_cap(tube) & weighed(tube)

    def eff(self, tube):
        return -has_cap(tube), -in_source(tube), +in_working(tube)

    def execute(self, tube):
        # Real-mode only — in sim the framework sleeps for ``duration``.
        return self.ctx.recipes["decapper"].decap(tube)
```

What's happening:

* `predicate("x")` declares a relation. Apply it to args (`has_cap(3)`)
  to get a fact you can use in `pre` / `eff` expressions or check
  against state.
* `Action` is a class — subclass and override `pre`, `eff`, `execute`.
* The decorator-like attributes (`params`, `duration`, `resource`)
  declare scheduling and parameter info.
* `eff` returns a tuple of facts. `+fact` means add, `-fact` means
  remove. No PDDL-side mirror to keep in sync.
* `execute` is optional in sim-only projects (the framework sleeps for
  the declared duration and returns success). Override when wiring
  real recipes.

Subclassing `Action` auto-registers the class — the framework picks
it up when `workflow.py` imports `actions`.

---

## 4. Fact arithmetic — the precondition / effect mini-language

In `pre()` you write boolean expressions over facts:

| Operator | Meaning | Example |
|---|---|---|
| `&` | AND | `in_source(t) & has_cap(t)` |
| `|` | OR | `weight_heavy(t) | weight_unknown(t)` |
| `~` | NOT | `~dosed(t)` |

In `eff()` you return a tuple of facts:

| Operator | Meaning | Example |
|---|---|---|
| `+fact` | Add to state | `+in_working(t)` |
| `-fact` | Remove from state | `-has_cap(t)` |

A bare fact (without `+`/`-`) in `eff()` is invalid — be explicit
about whether you're adding or removing.

---

## 5. Naming conventions

Reviewers must be able to tell a file's role from the identifier alone.

| Thing | Style | Examples |
|---|---|---|
| **Predicate** | `snake_case` fact-form | `has_cap`, `weighed`, `in_done` |
| **Action class** | `PascalCase` verb-form | `Decap`, `DispenseHeavy`, `Shelve` |
| **Resource** | `snake_case` singular noun | `robot`, `scale`, `dispenser` |
| **Parameter name** | `snake_case` | `tube`, `slot`, `tip` |

A predicate is not a verb. An action class is not a noun. A resource
is not a verb.

---

## 6. Adding a new action (the canonical example)

You want to add a `weigh` action. **One edit**, in one file.

In `actions.py`:

```python
class Weigh(Action):
    """Place tube on scale, read weight."""
    params   = ["tube"]
    duration = 8
    resource = "scale"

    def pre(self, tube):
        return in_working(tube) & ~weighed(tube)

    def eff(self, tube):
        return (+weighed(tube),)

    def execute(self, tube):
        return self.ctx.recipes["scale"].weigh(tube)
```

That's all. The framework:

* Auto-derives a PDDL `ActionTemplate` from `pre` / `eff`.
* Auto-registers the duration/resource into the scheduler meta.
* Auto-builds a BT leaf wrapping `execute` (or sim-sleep in sim mode).
* Auto-mirrors `eff` into the runtime state-update logic.
* Auto-generates `weighed.condition(tube)` if you ever need a BT
  condition leaf for it.

No edits to a separate `domain.py`. No edits to `schedule.py`. No
edits to a `_LEAVES` dict. The class is the single source of truth.

---

## 7. Conditional branching (PDDL handles it)

PDDL doesn't have `if`. Branching emerges from preconditions.

To express "if tube is heavy, use `DispenseHeavy`; else `DispenseLight`":

```python
class DispenseLight(Action):
    def pre(self, tube):
        return in_working(tube) & ~weight_heavy(tube) & ~dosed(tube)

class DispenseHeavy(Action):
    def pre(self, tube):
        return in_working(tube) & weight_heavy(tube) & ~dosed(tube)
```

The planner picks whichever action's preconditions hold. No `if` in
the workflow. No `if` in the tree. Branching is in the action
preconditions, where it belongs.

---

## 8. Recovery and replanning (three levels)

### 8.1 Retry — same action, same parameters

The default `workflow.run()` wraps every leaf in `with_retry(..., max_attempts=2)`
so one flake retries automatically. Override `tree.py` if you want
different retry counts per action.

### 8.2 Recovery subtree — try something else, then retry

In a custom `tree.py`:

```python
from workspace.bt import with_recovery

decap = leaf_factory("decap", tube)
recover = leaf_factory("vibrate_then_release", tube)
recoverable_decap = with_recovery("decap_safe", decap, recover)
```

### 8.3 Full replan — observe + re-plan from current state

In a custom `tree.py`:

```python
from workspace.bt import replan_on_failure
top = replan_on_failure(sequence("body", *steps), reason="…")
```

The default `workflow.run()` already wraps the whole body in this.

**Use full replan sparingly.** It's the right answer for "the world
drifted" (drip, technician intervention, device recovery). It's the
wrong answer for "this action flaked once" — that's what retry is for.

---

## 9. Diagnose APIs — uniform across every project

The framework exposes three observation surfaces every project gets
for free:

| Question | Call |
|---|---|
| What's the current world state? | `ctx.dump_state()` → JSON facts |
| What's running right now? | `engine.active_path()` → list of node names from root to active leaf |
| Full tree snapshot? | `engine.snapshot()` → ASCII status |
| Last plan? | `replanner.last_plan` → list of `Action(name, params)` |
| Last schedule? | `replanner.last_schedule` → list of `(action_name, item, start_t)` |

These work the same way in every BT project. An operator who learns
them once can debug any protocol.

---

## 10. The data flow at runtime

```
batch description (kwargs from operator)
        │
        ▼
ActionRegistry.current()        ← auto-populated when actions.py is imported
        │
        ├─► .to_templates(ctx)  → list of ActionTemplate (PDDL planner inputs)
        ├─► .to_meta()          → dict of ActionMeta (scheduler durations + resources)
        └─► .leaf_factory(ctx)  → callable that turns scheduled tasks into BT leaves
        │
        ▼
plan(initial_state, templates, goal)
        │  ordered Action list
        ▼
build_schedule(plan)                  ← uses META durations + resources
        │  [(action_name, item, start_t), ...]
        ▼
build_tree(schedule, ctx)             ← composes leaves via from_schedule
        │  py_trees root behaviour
        ▼
BTEngine.run()                        ← tick @ 10 Hz, runtime pause/kill,
                                         handle ReplanRequested via
                                         replanner.rebuild() (observe →
                                         plan → schedule → build_tree)
        │
        ▼
SUCCESS / FAILURE / INVALID (aborted)
```

---

## 11. Authoring rules (the load-bearing ones)

1. **One action class per atomic step.** No grouping of "related"
   actions into one class with conditional branches inside.
2. **`pre` and `eff` are pure.** No side effects. They're consulted at
   plan time, possibly many times for many candidate parameter bindings.
3. **`execute` is impure but cancellable.** It can take seconds to
   minutes; the framework runs it on a worker thread and aborts via
   `runtime.stop()` if the BT cancels. Recipes already poll runtime
   stop, so for sim/real action authors this is automatic.
4. **No two-file-mirroring.** If you find yourself writing the same
   effect logic twice, the framework has a gap — file a PR.
5. **Custom tree shapes go in an optional `tree.py`.** The default
   tree (from `from_schedule`) is what 90% of projects use.
6. **Imports are one-way.** `workflow.py` imports `actions.py`.
   `actions.py` imports only `workspace.bt`. No cross-project imports.

---

## 12. Performance characteristics

On a Pi 5, lab-sized problems:

| Layer | Cost | Notes |
|---|---|---|
| BT tick (10 Hz) | <1 ms | Pure-Python tree walk |
| PDDL plan | <100 ms typical | BFS forward search, batch of 10-100 items |
| Greedy schedule | <10 ms | Linear over plan length |
| Replanning total | <200 ms | observe → plan → schedule → rebuild tree |

Robot motions (1-10 s per move) and lab equipment (5 s to minutes per
action) dwarf all of these. The framework is never the bottleneck.

---

## 13. Migration checklist (linear → BT-driven)

If you have a pace_or-style linear project and want to convert it:

1. Copy `pace_bt/` to `your_project_bt/`.
2. Replace `pace_bt/actions.py` predicates + actions with yours.
3. Update `initial_state` + `make_goal` to match your protocol's
   inputs and outputs.
4. (Optional) Add a `tree.py` if you want custom retry / recovery
   shape beyond the default.
5. Run `workflow.run(...)` in sim. Verify SUCCESS.
6. Wire `execute` methods to your real recipes for production.

Nothing else moves. The scene (`base.j2`), recipes, device bus, and
runtime layer are all unchanged.
