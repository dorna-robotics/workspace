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
├── scene/
│   └── base.j2              # scene (j2 templates)
├── main.py                  # orchestrator entry — explicit wiring, same shape as pace_or
├── launch.yaml              # scene paths + GUI kwargs schema
├── recipes.yaml             # recipe aliases → class + component bindings (or recipes.j2)
├── actions.py               # predicates + setup(**kwargs) + one Action subclass per step
├── checks.py                # Checks class — pre/post-check methods referenced by name
├── workflow.py              # OPTIONAL — override the default protocol runner
└── README.md                # 30 lines max — what + where-to-edit table
```

**One substantive file** for the protocol (`actions.py`). Everything
else is either:

* **Boilerplate** copied verbatim across projects (`main.py`).
* **Configuration** edited only when scene / kwargs change
  (`launch.yaml`, `recipes.yaml`).
* **Scene** in j2 (`scene/base.j2`).
* **Optional escape hatch** if the default protocol runner isn't
  enough (`workflow.py`).

### What each file does

* **`main.py`** — explicit orchestrator entry, same shape as pace_or's
  main.py. Opens `launch.yaml`, calls `load_recipes`, imports
  `actions`, defines a `workflow_fn` that calls
  `bt.launcher.run_protocol`, starts `Workspace` + `RuntimeServer`.
  ~50 lines. **The wiring is visible** — an operator can read it and
  see exactly where each piece is hooked in. Copy from `pace_bt/`
  for new projects; the only edit is the import of `actions` (and
  optionally `project_name`).
* **`launch.yaml`** — scene paths (`scene: [scene/base.j2, ...]`) and
  the kwargs schema rendered into the operator's Parameters modal.
* **`recipes.yaml`** — same format as pace_or. Maps recipe aliases
  (`"gripper"`, `"scale"`) to `{class, kwargs}` so an Action's
  `execute(...)` body can call `self.ctx.recipes["gripper"].pick(...)`.
  `recipes.j2` is read first if present (Jinja2 template → YAML).
* **`actions.py`** — THE file. Predicates at top, then a single
  `setup(**kwargs)` function that returns `{initial_facts, goal,
  objects}`, then one `Action` subclass per atomic step.
* **`checks.py`** — pace_or-style verification class. Methods take
  `(item_index)`, return `bool` or `(bool, message)`. Wired into
  the framework via `load_checks(...)` and looked up by name from
  `Action.pre_check` / `Action.post_check`.
* **`workflow.py`** — *optional*. Use when the default `run_protocol`
  isn't sufficient (custom tree shapes, multi-stage planning, etc.).
  If you create one, change `main.py`'s `workflow_fn` to call it
  instead of `run_protocol`.

---

## 3. The authoring style — one block per action

```python
from workspace.bt import Action, predicate

# Predicates — declare once at the top.
in_source     = predicate("in_source")
in_working    = predicate("in_working")
in_done       = predicate("in_done")
has_cap       = predicate("has_cap")
weighed       = predicate("weighed")
weight_heavy  = predicate("weight_heavy")
dosed         = predicate("dosed")

# Map operator kwargs → planning inputs. ONE function.
def setup(**kwargs):
    batch_size = int(kwargs.get("batch_size", 1))
    heavy = _parse_heavy(kwargs.get("heavy", ""))  # project-local parser
    tubes = list(range(batch_size))

    facts = set()
    for t in tubes:
        facts.add((in_source.name, t))
        facts.add((has_cap.name, t))
        if t in heavy:
            facts.add((weight_heavy.name, t))

    def goal(state):
        return all((in_done.name, t) in state for t in tubes)

    return {
        "initial_facts": frozenset(facts),
        "goal":          goal,            # or e.g. ["Shelve"] — see §3.2
        "objects":       {"tube": tubes},
    }

# One block per atomic action.
class Decap(Action):
    """Remove cap, transfer tube to working rack."""
    params     = ["tube"]
    duration   = 10
    resource   = "robot"
    tool       = "gripper"
    pre_check  = "source_tube_present"
    post_check = "tube_in_working_rack"

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
* `setup(**kwargs)` is the single hook the framework calls to turn
  GUI kwargs into the three things planning needs: `initial_facts`,
  `goal`, and `objects` (the parameter pools used to enumerate
  candidate action bindings).
* `Action` is a class — subclass and override `pre`, `eff`, `execute`.
* The class attributes carry **two flavours of metadata**:
    - Scheduling / runtime: `params`, `duration`, `resource`, `tool`,
      `tool_swap_duration`, `trigger`.
    - Operational checks: `pre_check`, `post_check` (names registered
      in `checks.py`).
  See §5 for the full vocabulary.
* `eff` returns a tuple of facts. `+fact` means add, `-fact` means
  remove. No PDDL-side mirror to keep in sync.
* `execute` is the real-hardware logic. **Sim vs. real is a
  framework-level decision** based on `core._simulation_mode`: sim
  mode sleeps for `duration` and skips `execute`; real mode calls
  it. Action classes don't carry a sim flag.

Subclassing `Action` auto-registers the class — the framework picks
it up the moment `actions.py` is imported.

### 3.1 Action class vocabulary (the pace_or terms, kept verbatim)

Every attribute except `params` is optional. Defaults are noted in
parentheses.

| Attribute | Type | Purpose |
|---|---|---|
| `params` | `list[str]` (`[]`) | Parameter names — usually `["tube"]`. The planner enumerates the Cartesian product across `objects[name]`. |
| `duration` | `int` (`1`) | Scheduler estimate in seconds. |
| `resource` | `str \| list[str] \| None` (`None`) | Lock(s) this action claims exclusively. `"robot"` = one lock; `["robot","scale"]` = both held at once (arm holds tube on scale); `None` = unlimited parallel. Autonomous peripheral (shaker running alone) → its own lock name, robot stays free. |
| `tool` | `str \| None \| (unset)` | Tool the robot must hold. The framework auto-swaps before `execute()`. **unset** (default) = "keep whatever's currently held"; **None** = "release current tool"; a string = "make sure this tool is held". One tool per action — keep actions atomic. |
| `tool_swap_duration` | `int` (`10`) | Seconds added before this action when the previous same-resource action used a different `tool`. Per-action so swap costs can differ between tool changes. |
| `pre_check` | `str \| list[str] \| None` | Name(s) from `checks.py` to run **before** the tool swap. Returning False **skips** the action (success — BT moves on). |
| `post_check` | `str \| list[str] \| None` | Name(s) to run after `execute()`. Returning False **fails** the action (BT may retry / replan). |
| `trigger` | `str \| None` (`None`) | `"end"` marks the action as scene cleanup invoked when the operator clicks End. Not part of the PDDL plan or the schedule. `params` must be empty. See §3.3. |

### 3.2 Goals as a list of terminal action class names

`setup()`'s `goal` can be a callable `state -> bool`, or — for the
common case "every item must finish at action X" — a list of action
class names:

```python
return {
    ...
    "goal": ["Shelve"],   # equivalent to:
                          # lambda state: all((in_done.name, t) in state
                          #                   for t in tubes)
}
```

The framework expands the list by running each named action's `eff()`
across every parameter binding drawn from `objects`, and requires
every **positive** fact to be in state. Negative effects are ignored
— the goal cares about facts that must hold, not those that must be
absent. Use a callable when you need anything fancier.

### 3.3 End-trigger actions (operator clicks End)

The operator can request a graceful stop via the GUI's End button.
The framework completes the current action, then **runs every action
declaring `trigger="end"` once in a sequence**, then exits. Typical
uses: park the held tool, return the robot to home, dispose of waste.

```python
class ParkTool(Action):
    """Release whatever tool is held — invoked when operator clicks End."""
    params  = []        # required: end-trigger actions are scene-level
    duration = 5
    resource = "robot"
    tool     = None     # "release current tool"
    trigger  = "end"

    def execute(self):
        # Auto-swap dropped the tool before this runs — nothing to do.
        pass
```

End-trigger actions are *excluded* from PDDL templates and from the
scheduler — they're run by the engine outside the planned schedule.

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

## 13. Checks (pre_check / post_check) — pace_or-compatible

`checks.py` mirrors pace_or's convention exactly so vision /
sensor methods written for pace_or transfer verbatim:

```python
class Checks:
    def __init__(self, rcp, rt, **kwargs):
        self.rcp = rcp
        self.rt  = rt

    def source_tube_present(self, item_i) -> tuple[bool, str]:
        # TODO: camera.detect("tube", SOURCE[item_i])
        return True, "source tube present"

    def register(self, runner):
        runner.register_check("source_tube_present", self.source_tube_present)
```

Wiring in `main.py`:

```python
import checks
...
recipes  = load_recipes(workspace, core, _BASE_DIR / "recipes.yaml")
check_fns = load_checks(workspace, core, recipes, checks_module=checks, **kwargs)
run_protocol(workspace, core, actions, recipes=recipes, checks=check_fns, **kwargs)
```

Actions reference checks by name:

```python
class Decap(Action):
    pre_check  = "source_tube_present"          # single name…
    post_check = ["tube_in_working_rack", "cap_in_holder"]   # …or a list
```

Semantics:
* **`pre_check` fails** → the action is **skipped** entirely (no tool
  swap, no `execute`, no effects). Treated as success so the BT moves
  on. Matches pace_or's "the world already looks done — don't re-do it"
  intent.
* **`post_check` fails** → the action **fails**. The BT may retry, the
  outer `replan_on_failure` may rebuild from observed state.
* Checks return `bool` or `(bool, message)`. On failure the message
  is logged at INFO level.

---

## 14. Migration checklist (linear → BT-driven)

If you have a pace_or-style linear project and want to convert it:

1. Copy `pace_bt/` to `your_project_bt/`.
2. Replace `pace_bt/actions.py` predicates + `setup(**kwargs)` +
   `Action` subclasses with yours. **The vocabulary mostly matches**
   `protocol.yaml`: `tool`, `duration`, `pre_check`, `post_check`,
   `tool_swap_duration`, `trigger`. Differences from pace_or:
   * `pre()` / `eff()` **replace** the `requires:` list — ordering
     comes from facts now, derived by the planner.
   * `resource` **replaces** the implicit-robot + `background:` flag
     — a string like `"shaker_1"` already means "the shaker is busy,
     robot is free". An action can claim multiple locks at once
     (`resource = ["robot", "scale"]`).
3. Copy `checks.py` over unchanged — its `(item_i) -> (bool, msg)`
   convention is shared between frameworks.
4. Copy `recipes.yaml` (or `recipes.j2`) over unchanged.
5. (Optional) Add a `workflow.py` if you want custom retry / recovery
   shape beyond the default.
6. Run `main.py` in sim. Verify SUCCESS.
7. Wire `execute` methods to your real recipes for production.

Nothing else moves. The scene (`base.j2`), recipes, device bus, and
runtime layer are all unchanged.
