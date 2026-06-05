# BT Framework Guide

This is the **discipline** every BT-based workspace project follows.
Read it once, then keep it as the reference for starting a new project
or reviewing a PR.

If you're looking at code that doesn't match what's here, the code is
wrong — not the guide.

---

## 1. What is a BT project?

### The mental model (30 seconds)

A BT project is a **lab protocol described declaratively**. You don't
tell the robot *how* to do the protocol — you describe *what* counts
as each step (precondition, effect, real-world execute), and the
framework figures out the order, parallelism, retry, and recovery.

Three layers of vocabulary, top to bottom:

```
Predicates        ←  "things that can be true"   (has_cap, in_source, dosed, ...)
   │
   ▼
Actions           ←  "atomic steps"               (Decap, Inspect, Dispense, ...)
   │              each declares pre() / eff() / execute() in those predicates
   ▼
setup(**kwargs)   ←  "this particular run"       (4 tubes, these are heavy, …)
                   builds initial state + goal from operator inputs
```

The framework reads all three, builds a plan, schedules it across
hardware, and ticks a behavior tree at 10 Hz until the goal is met.

### What you declare vs what the framework derives

You declare:

1. **Predicates** — the vocabulary of facts your world can hold.
2. **Actions** — one Python class per atomic step. Each says: what
   must be true to run me (`pre`), what changes after (`eff`), what
   the robot actually does (`execute`).
3. **Goal** — a callable that returns True when the protocol is done.
4. **Initial state** — which facts are true at t=0.

The framework derives:

* a **PDDL plan** (ordered action sequence to the goal),
* a **schedule** (parallelism across resources, tool-swap gaps),
* a **behavior tree** for execution (with retry, recovery, replan),
* **condition leaves** for any predicate (auto-generated when needed),
* **effect propagation** mirroring your declared `eff()` into runtime state.

Authors never write duplicated declarations. One action = one block.

### Pace_bt — the worked example

The `pace_bt/` project is the framework's canonical example. Every
concept in this guide is illustrated by code there; if a section
feels abstract, open [pace_bt/actions.py](../workspace/projects/pace_bt/actions.py)
and read the parallel implementation.

---

## 2. Project skeleton

```
projects/<name>/
├── scene/
│   └── base.j2              # scene (j2 templates)
├── main.py                  # orchestrator entry — explicit wiring, same shape as pace_or
├── launch.yaml              # SINGLE config file — project_name / port / scene / recipes / actions / checks / GUI kwargs
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
  main.py. Parses `launch.yaml` once into a module-level `LAUNCH`
  dict, dynamically imports the `actions` and `checks` modules
  named in launch.yaml (defaulting to `actions.py` / `checks.py`),
  defines a `workflow_fn` that calls `bt.launcher.run_protocol`,
  starts `Workspace` + `RuntimeServer`. ~50 lines. **Copy from
  `pace_bt/` for new projects verbatim** — every per-project
  detail lives in launch.yaml.
* **`launch.yaml`** — the single config file for the project. Top-level
  keys: `project_name`, `port` (default for direct invocation),
  `scene` (list of scene files), `recipes` (recipes file path),
  `actions` (protocol module path — default `actions.py`),
  `checks` (Checks module path — default `checks.py`),
  `plan_window` (optional, default 4 — see §12 on slicing), and
  `kwargs` (the GUI form schema rendered into the operator's
  Parameters modal). main.py reads everything from here via
  `LAUNCH["..."]` lookups and uses `importlib` to load the
  `actions` and `checks` modules dynamically.
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

### 2.1 Where to edit for…

Task-oriented routing — go straight to the file for what you want to change.

| You want to… | Edit |
|---|---|
| Add / remove an atomic action | `actions.py` (one `Action` subclass) |
| Change branch logic | `pre()` method on the relevant action |
| Change scheduling (duration / resource / tool / tool_swap_duration) | class attrs on the relevant action |
| Add a multi-branch outcome (sensing) | `eff()` dict + `execute()` returns the branch — see §3.3 |
| Change the goal | `setup()`'s `goal` callable |
| Change initial state from kwargs | `setup()` |
| Change the GUI form | `launch.yaml` kwargs |
| Add a new vision / sensor check | `checks.py` (method + `register_check` line) |
| Wire a check into an action | `pre_check` / `post_check` class attr on the action |
| Add a Park-cleanup action | new `Action` subclass with `trigger="park"` (see §3.2) |
| Change the scene | `scene/base.j2` |
| Change recipe bindings (which class implements which alias) | `recipes.yaml` |
| Override the protocol runner | add a `workflow.py` and change `main.py`'s `workflow_fn` |
| React to camera-driven world changes (tubes appearing / leaving) | add a sensing action that mutates ctx + replans (see §8.4) |

### 2.2 Running a BT project

Two ways:

**Via the orchestrator (normal operation).** The orchestrator
auto-discovers all projects under `projects/` and starts them on
operator click. Browse to:

```
http://<ip>:5000/orchestrator/
```

Each project gets its own port (`pace_bt` defaults to 5010).

**Directly (development / debugging).**

```bash
cd projects/<your_project>
sudo python3 main.py --port 5010
```

The framework reads `launch.yaml`, loads `recipes.yaml`, imports
`actions` (which auto-registers Action classes via `__init_subclass__`),
and starts the BT engine. The operator UI is then at
`http://<ip>:5010/`.

---

## 3. The authoring style — one block per action

Every `actions.py` file has the same three parts, in this order:

1. **Predicates** at the top — the vocabulary your protocol speaks.
2. **One `setup(**kwargs)` function** — converts operator inputs into
   the planner's starting state and goal.
3. **One `Action` subclass per atomic step** — declares its
   preconditions, effects, scheduling info, and hardware logic.

That's it. No domain.py, no schedule.py, no separate conditions —
the framework derives everything from these three things.

Here's the full skeleton:

```python
from workspace.bt import Action, predicate

# ─── 1. Predicates — declare once at the top. ──────────────────────────
#     These are the building blocks. Every fact in your world has to
#     reference one of these names.
in_source     = predicate("in_source")
in_working    = predicate("in_working")
in_done       = predicate("in_done")
has_cap       = predicate("has_cap")
weighed       = predicate("weighed")
weight_heavy  = predicate("weight_heavy")
dosed         = predicate("dosed")

# ─── 2. setup() — the per-run translator. ──────────────────────────────
#     Called once per Start click. Turns operator kwargs into the
#     three things the planner needs: initial facts, the goal, and
#     the parameter pools.
def setup(**kwargs):
    batch_size = int(kwargs.get("batch_size", 1))
    tubes = list(range(batch_size))

    facts = set()
    for t in tubes:
        facts.add((in_source.name, t))
        facts.add((has_cap.name, t))

    def goal(state):
        return all((in_done.name, t) in state for t in tubes)

    return {
        "initial_facts": frozenset(facts),
        "goal":          goal,            # callable state -> bool
        "objects":       {"tube": tubes},
    }

# Note: weight_heavy isn't seeded in initial facts — Inspect's sensing
# eff adds it at runtime when the scale reads above HEAVY_THRESHOLD.

# ─── 3. Actions — one class per atomic step. ───────────────────────────
#     Each class declares scheduling info (class attrs), preconditions
#     (pre), effects (eff), and hardware logic (execute). The framework
#     auto-registers it.
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
        return {"decapped": (-has_cap(tube), -in_source(tube), +in_working(tube))}

    def execute(self, tube):
        self.ctx.recipes["decapper"].decap(tube)
        return "decapped"     # ← name of the chosen eff branch
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
      `tool_swap_duration`, `trigger`, `schedule`.
    - Operational checks: `pre_check`, `post_check` (names registered
      in `checks.py`).
  See §5 for the full vocabulary.
* `eff` returns a dict of named branches; each branch's value is the
  facts that branch produces. `+fact` adds, `-fact` removes. Single-key
  dicts are deterministic actions; multi-key dicts are sensing actions
  whose `execute()` picks the branch at runtime. See §3.3.
* `execute` is the hardware logic. **Simulation is the workspace
  SDK's concern**, not the framework's — `core.robot_api` is
  `SimulationAPI` in sim and `Dorna()` in real, and recipes go
  through that. The framework just calls `execute()` either way.

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
| `tool` | `str \| None \| (unset)` | Tool the robot must hold. The framework auto-swaps before `execute()`. Three distinct states, **not collapsed**: **unset** (attribute not declared) = "leave the held tool alone — tool-agnostic action"; **`None`** (explicit) = "make sure nothing is held — drop the current tool"; a string = "make sure this tool is held". The swap fires **regardless of the action's resource** — declaring `tool=` on a shaker-only action still triggers a swap on the robot before the action starts, and the scheduler reserves time for it. Internally the framework derives a `tool_required` flag in `ActionMeta` from "did the author declare anything?" so the scheduler can tell drops apart from unset. One tool per action. |
| `tool_swap_duration` | `int` (`10`) | Seconds added before this action when the held tool needs to change. Per-action so different swaps can have different costs. |
| `pre_check` | `str \| list[str] \| None` | Name(s) from `checks.py` to run **before** the tool swap. Returning False **skips** the action (success — BT moves on). |
| `post_check` | `str \| list[str] \| None` | Name(s) to run after `execute()`. Returning False **fails** the action (BT may retry / replan). |
| `trigger` | `str \| None` (`None`) | Lifecycle-event hook. ``"park"`` marks the action as scene cleanup invoked when the operator clicks Park. Not part of the PDDL plan or the schedule. `params` must be empty. See §3.2. |
| `register` | `bool` (`True`) | Whether to add the class to the `ActionRegistry`. `False` on abstract bases; concrete subclasses opt back in with `register = True`. See §3.3. |

### 3.2 `trigger` — lifecycle event hooks

`trigger` controls **when** an action runs. Today it has one
recognised value, but the attribute exists so the framework can
grow more lifecycle hooks (`"start"`, `"error"`, …) without
overloading other attributes.

| Value | Effect |
|---|---|
| `None` *(default)* | Action fires as part of the goal-directed plan. |
| `"park"` | Action runs only when the operator clicks Park. Excluded from PDDL templates and the scheduler. The launcher collects every `trigger="park"` class into the Park-cleanup subtree (registry order, one leaf per class, `item_index=0`). |

Typical use — park the tool when the operator stops the run:

```python
class ParkTool(Action):
    """Release whatever tool is held — runs on operator Park."""
    params   = []        # scene-level, no per-item iteration
    duration = 5
    resource = "robot"
    tool     = None      # "release current tool"
    trigger  = "park"

    def execute(self):
        return "none"
```

The auto-swap path drops the held tool before `execute()` runs — so
the typical body is just `return "none"`.

**All attributes inherit like normal Python.** `trigger` is no
special case — if a parent declares `trigger = "park"`, the child
inherits it. To opt out, the child must explicitly redeclare
(e.g. `trigger = None`). Same applies to `schedule`,
`pre_check`, `tool`, and every other class attribute.

```python
class Park(Action):
    # planned action — no trigger declared, so trigger == Action's default (None)
    ...

class OperatorPark(Park):
    trigger = "park"     # set on the subclass; further subclasses inherit this
```

**`trigger` vs `register` — which one hides the action?**

`trigger = "park"` is **enough on its own** to keep the action out
of the plan and the schedule: it's filtered from `to_meta()`
(scheduler) and `to_templates()` (PDDL planner). You do **not** also
need to set `register = False`. Conceptually:

| You want… | Set… |
|---|---|
| Action that runs only on operator Park | `trigger = "park"` |
| Abstract base class subclasses inherit from | `register = False` |

### 3.3 `register` — registry opt-out

`register` is a single boolean class attribute. Default `True` —
every `Action` subclass is added to the `ActionRegistry` and seen by
the planner, scheduler, and live Gantt.

Set `register = False` on **abstract base classes** that exist only
to share code (typically `pre`, `eff`, `execute` skeletons across
several concrete actions). The opt-out **inherits normally**, so
concrete subclasses must redeclare `register = True` to participate.
Verbose by design: every class makes its intent explicit at the
point of declaration, no silent MRO surprises.

**Examples:**

```python
class Inspected(Action):                # registered (default True)
    params = ["tube"]
    ...

class ShakerCycleBase(Action):          # abstract base
    register = False
    ...

class ShakerOne(ShakerCycleBase):       # concrete — opt back in
    register = True
    SHAKER   = "shaker_1"
    resource = "shaker_1"
```

### 3.3 `eff()` — always a dict of named branches

**One shape for every action.** `eff()` returns a `dict` keyed by
branch name; values are the facts that branch produces.

Deterministic actions have **one** key. Name it after the outcome
state (past tense reads naturally):

```python
class Decap(Action):
    def eff(self, tube):
        return {"decapped": (-has_cap(tube), -in_source(tube), +in_working(tube))}
```

Sensing / observation actions (when the outcome depends on a sensor)
have **multiple** keys. `execute()` returns the chosen key:

```python
class Inspect(Action):
    def eff(self, tube):
        # ORDER MATTERS — first key is the planner's default projection.
        return {
            "light": +weighed(tube),
            "heavy": (+weighed(tube), +weight_heavy(tube)),
        }

    def execute(self, tube):
        w = self.ctx.recipes["scale"].weight()
        return "heavy" if w > HEAVY_THRESHOLD else "light"
```

Branch values may be:

| Shape | Meaning |
|---|---|
| `+fact` / `-fact` | single fact |
| `(+fact_a, -fact_b)` (tuple) | multiple facts applied together |
| `None` | no facts in this branch |

#### The execute / eff contract

`execute()` **must return a string** — the name of the chosen branch.
For deterministic actions that means returning the single key
(`return "decapped"`); for sensing actions, return whichever key
matches what you observed (`return "heavy"` or `"light"`).

There is **no shortcut**. Returning `None`, `True`, or anything else
is rejected with a warning and treated as failure — same one-way-to-do-it
rule as `eff()` being a dict.

| Phase | Behavior |
|---|---|
| **PDDL planning** | Uses the *first* dict key as the projected effect. Single-key dicts behave deterministically; multi-key dicts plan optimistically for the first branch. |
| **Scheduling** | Same regardless — duration, resource, tool all read from the class. |
| **Runtime — execute() returns first key** | That branch applies. No replan. |
| **Runtime — execute() returns non-first key** | That branch applies. The framework raises `ReplanRequested` so downstream actions re-evaluate. |
| **Runtime — execute() returns unknown key** | Warning + fall back to the default (first) key. |
| **Runtime — execute() returns False** | Action failed; no effects applied. |
| **Runtime — execute() returns anything else (None / int / etc.)** | Programmer error — warning + treated as failure. |

#### When to use multiple branches

* Weighing, density / volume sensing, vision detection
* Barcode / RFID scanning where the value drives branching
* Calibration steps that succeed-with-offset vs need-retry
* Any "act-then-observe-then-branch" sequence

For pure failures / retries, use `pre_check` / `post_check` and
`with_retry` instead — that's a different mechanism.

#### Lineage

This is PDDL's `oneof` non-deterministic effect (PPDDL, ~2002), with
named branches instead of anonymous ones for readability. Single-key
dicts are the degenerate-deterministic case of the same construct —
one shape, two uses.

### 3.4 What `self.ctx` carries — the per-action context

Every Action subclass has `self.ctx` available inside `pre()`, `eff()`,
`execute()`, `param_iter()`, and any helper method. It's the **only**
handle each action needs to reach hardware, runtime, and shared state.

Same role as pace_or's `self.rcp` + `self.rt` on the States class —
just bundled under one attribute so the framework can hand the whole
package to every leaf with one variable.

| Attribute | Type | What it is | pace_or equivalent |
|---|---|---|---|
| `self.ctx.workspace` | `Workspace` | SDK root — scene + components | `self.workspace` |
| `self.ctx.core` | `Core` | robot API (sim or real — chosen by core) | `self.core` |
| `self.ctx.runtime` | `Runtime` | `rt.step(...)` (observability, non-blocking), `rt.sleep(...)` / `rt.<robot>(...)` / `rt.checkpoint()` (pause-aware). See [project-guide.md §8 Pause gate](project-guide.md#pause-gate) for the full pause-aware map. | `self.rt` |
| `self.ctx.recipes` | `dict[str, Recipe]` | name → recipe instance loaded from `recipes.yaml`. **Preferred way to drive hardware.** | `self.rcp` |
| `self.ctx.state` | `dict` | live world state. `ctx.state["facts"]` is the fact set. **Managed by the framework — don't write to it directly**, return effects from `eff()` instead. | — |
| `self.ctx.meta` | `dict` | per-run scratch space. Keys: `kwargs`, `objects`, `checks`, `current_tool`, `project`. | — |
| `self.state` | `frozenset[tuple]` | **Frozen snapshot** of the world the framework hands you right before calling `pre()` or `eff()`. Different from `self.ctx.state`: the former is live + mutable; `self.state` is a stable, immutable view tied to this specific evaluation. Read it for state-aware preconditions/effects — see §3.5.1. | — |

#### Common patterns

Drive hardware via a recipe (most common):

```python
def execute(self, tube):
    rcp = self.ctx.recipes
    rcp["source_rack"].pick(SOURCE[tube])
    rcp["scale"].weight()
```

Emit a progress message (only when you want one — framework never
emits these for you):

```python
def execute(self, tube):
    self.ctx.runtime.step(f"Inspecting tube {tube}")
    ...
```

Read a live device value directly (no recipe wrapper):

```python
def execute(self, tube):
    w = self.ctx.workspace.components["scale_1"].weight
    ...
```

Reach an operator kwarg from execute-time:

```python
def execute(self, tube):
    speed = self.ctx.meta["kwargs"].get("speed_factor", 1.0)
    ...
```

#### What `setup()` does *not* get

`setup(**kwargs)` is called **before** the context exists — it
receives kwargs only. Its job is to compute the planning inputs
(`initial_facts`, `goal`, `objects`) from kwargs alone. If your
setup() would need recipes or runtime to compute the initial state,
that work belongs in an early action's `execute()` instead.

### 3.5 Under the hood — Predicate, Fact, State

The three types that carry the protocol's world model. Knowing how
they fit together makes everything else easier to reason about.

#### The data model — three layers

| Layer | Python type | Where it comes from | Example |
|---|---|---|---|
| **Predicate** | framework class (`Predicate`) | declared once at module top: `in_source = predicate("in_source")` | object with `.name = "in_source"`, callable |
| **Fact** | framework class (`Fact`) | created on-the-fly inside `pre()` / `eff()`: `in_source(3)` | object with `(pred, args, polarity)` |
| **Tuple (in state)** | plain Python tuple | `Fact.as_tuple()` — what the framework stores in state | `("in_source", 3)` |

Same logical concept ("tube 3 is in source"), three representations,
each used in a different layer:

```python
in_source                # the Predicate — what you reference in declarations
in_source.name           # → "in_source"  — the string, used in setup()/goal()
in_source(3)             # → a Fact — used in pre() / eff() with & | ~ + -
in_source(3).as_tuple()  # → ("in_source", 3) — what lives in state
```

#### The `Predicate` class — tiny

```python
class Predicate:
    __slots__ = ("name",)
    def __init__(self, name):  self.name = name
    def __call__(self, *args): return Fact(self.name, args)
```

One field (`name`), one method (call → make a Fact). Nothing else.

#### The `Fact` class — also tiny

```python
class Fact:
    __slots__ = ("pred", "args", "polarity")
    def __init__(self, pred, args, polarity=True): ...
    def __pos__(self): return Fact(self.pred, self.args, polarity=True)   # +fact
    def __neg__(self): return Fact(self.pred, self.args, polarity=False)  # -fact
    def __and__(self, other): ...   # &
    def __or__(self, other):  ...   # |
    def __invert__(self):     ...   # ~
```

Three fields:

| Field | Meaning |
|---|---|
| `pred` | the predicate name (a string, e.g. `"in_source"`) |
| `args` | the arguments tuple (e.g. `(3,)`) |
| `polarity` | `True` = "add this fact", `False` = "remove this fact" (only relevant inside `eff()`) |

#### `state` — what `goal(state)` actually receives

A **`frozenset` of plain tuples**, each tuple `(predicate_name, *args)`.
That's it. No Fact objects, no Predicate references — just the bare
tuples that `Fact.as_tuple()` produces.

```python
state = frozenset({
    ("in_source", 0), ("has_cap", 0),
    ("in_source", 1), ("has_cap", 1),
})
```

Membership check is O(1) (set hashing):

```python
def goal(state):
    return all((in_done.name, t) in state for t in tubes)
    #          └────── tuple ──────┘    O(1) hash lookup
```

#### How state evolves through pace_bt (batch_size=2, both heavy)

| Phase | state |
|---|---|
| **t=0** (after `setup()`) | `{("in_source", 0), ("has_cap", 0), ("in_source", 1), ("has_cap", 1)}` |
| **after `Inspect(0)`, `Inspect(1)`** | adds `("weighed", 0)`, `("weighed", 1)`, `("weight_heavy", 0)`, `("weight_heavy", 1)` (sensing branch fired) |
| **after `Decap(0)`, `Decap(1)`** | removes `has_cap`/`in_source` for both, adds `("in_working", 0)`, `("in_working", 1)` |
| **after `DispenseHeavy(0/1)`** | adds `("dosed", 0)`, `("dosed", 1)` |
| **after `Recap(0/1)`** | adds `("has_cap", 0)`, `("has_cap", 1)` back |
| **after `Shelve(0/1)`** | removes `in_working`, adds `("in_done", 0)`, `("in_done", 1)` → goal satisfied |

State grows and shrinks freely. No pre-sizing, no schema enforcement
beyond "tuples must be hashable" (so don't put lists or dicts in `args`).

#### Where each layer lives

```
┌─────────────────────────────────────────────────────────┐
│  Module top (declared once, never changes)              │
│    in_source = predicate("in_source")   ← Predicate     │
└─────────────────────────────────────────────────────────┘
                       │ uses
                       ▼
┌─────────────────────────────────────────────────────────┐
│  Inside pre() / eff()                                   │
│    return in_source(tube) & ~weighed(tube)              │
│           └──────────────── Fact arithmetic ──────────┘ │
└─────────────────────────────────────────────────────────┘
                       │ framework converts via .as_tuple()
                       ▼
┌─────────────────────────────────────────────────────────┐
│  ctx.state["facts"]      (set, mutable)                 │
│    {("in_source", 3), ("has_cap", 3), ...}              │
│  Snapshotted to frozenset when handed to the planner    │
│  or to goal(state).                                     │
└─────────────────────────────────────────────────────────┘
```

#### Mental model

> A **predicate** is a database column definition (the schema).
> A **fact** is one row instance, signed with `+`/`-` while in an `eff()`.
> The **state** is the live table of currently-true rows — a `set` while
> the protocol runs, a `frozenset` when frozen for planner consumption.

### 3.5.1 State-aware `pre()` and `eff()` — reading `self.state`

Most actions are *static*: their preconditions and effects depend only
on the action's parameters. The planner's `state` doesn't matter for
their authoring — they're a fact-level Expr (`pre`) or a fact list
(`eff`).

A small minority of actions need to look at *what the world actually
contains right now*. Examples:

* A shaker physically shakes **every tube currently on it** in one
  mechanical call. The effect set is "mark every loaded tube as
  shaken" — which depends on which tubes happen to be loaded.
* A doser dispenses into **whichever wells are currently present**
  in the holder.
* A pre-condition like "every tube destined for this shaker has been
  loaded" can't be enumerated statically — it needs to inspect state.

For these, the framework exposes the planning-time state as a
**frozenset snapshot** on `self.state`. Read it normally:

```python
class ShakerOne(Action):
    params      = []                  # per-shaker, not per-tube
    resource    = "shaker_1"
    duration    = 10

    def pre(self):
        # Only fire when every tube destined for shaker_1 (current
        # slice window, looked up via SHAKER_SLOTS) is already loaded
        # AND not yet shaken.
        destined = [t for t in self.ctx.meta["objects"]["tube"]
                    if SHAKER_SLOTS[t][0] == "shaker_1"]
        if not destined or all((shaken.name, t) in self.state for t in destined):
            return False
        expr = in_shaker(destined[0]) & ~shaken(destined[0])
        for t in destined[1:]:
            expr = expr & in_shaker(t) & ~shaken(t)
        return expr

    def eff(self):
        # +shaken(t) for every tube currently loaded on this shaker.
        return {"shaken": tuple(
            +shaken(t)
            for t in self.ctx.meta["objects"]["tube"]
            if (in_shaker.name, t) in self.state
            and SHAKER_SLOTS[t][0] == "shaker_1"
        )}
```

#### Rules

* `self.state` is a **`frozenset` of tuples** `(predicate_name, *args)`
  — same shape as `goal(state)` receives (§3.5). Membership lookup is
  O(1).
* It's a **snapshot for this one call**. The framework sets it
  immediately before invoking `pre()` / `eff()` and never mutates it.
  Don't try to write to it.
* `self.state` reflects the *planning-time* world during plan search
  (which may be a hypothetical future state) and the *pre-mutation*
  runtime world during effect application (so eff() sees the same
  state the planner saw at that step).
* If your action is static (the 95% case), don't reference
  `self.state` — keep `pre()` returning an Expr and `eff()` returning
  a fixed dict. That's the documented contract for "this action's
  behavior is purely a function of params."

#### When NOT to use it

* Don't read `self.state` from `execute()`. By the time `execute()`
  runs, `self.state` may be stale (planner's view, not real world).
  Use `self.ctx.state["facts"]` if you genuinely need live runtime
  state (rare — usually a sign the model is wrong).
* Don't use it for things that should be `param_iter()` filters.
  If you find yourself iterating in `pre()` to skip-invalid bindings,
  push that into `param_iter` instead — that's what it's for.

#### `_ctx_objects()` vs `_ctx_all_objects()` — sliced vs full view

When slicing is active (§12), the framework narrows
`ctx.meta["objects"]` to the **current window** between replans —
`param_iter` and most `pre()` bodies should read this sliced view via
`self._ctx_objects()` so they only consider items the planner is
currently thinking about.

For effects or preconditions that must reference the **entire batch**
regardless of slice — a `Start` action seeding initial facts for
every tube, or a final `Park` whose pre checks every tube is done —
read the un-sliced snapshot:

```python
def eff(self):
    tubes = self._ctx_all_objects().get("tube", [])   # full batch
    seeds = [+started()]
    for t in tubes:
        seeds.append(+in_source(t))
        seeds.append(+has_cap(t))
    return {"started": tuple(seeds)}
```

If you use `_ctx_objects()` for cross-batch seeding, later slices
won't have the facts you seeded in the first slice — a subtle bug
that surfaces only when `batch_size > plan_window`.

### 3.6 `params` and `objects` — the planner's wiring

The `params` attribute on an Action class and the `objects` dict
returned by `setup()` are linked by **name** — they're how the
planner knows what values to enumerate when looking for plans.

#### The three places one name appears

```python
class Inspect(Action):
    params = ["tube"]            # ← (1) declare the parameter NAME

    def pre(self, tube):         # ← (3) function argument
        return in_source(tube)

    def eff(self, tube):
        return {"weighed": +weighed(tube)}

    def execute(self, tube):
        ...
        return "weighed"


# in setup():
return {
    "objects": {"tube": [0, 1, 2, 3]},   # ← (2) values for the NAME
    ...
}
```

Same word `tube` in three places. Each plays a different role:

| Where | Role | Required? |
|---|---|---|
| `params = ["tube"]` | "this action takes one parameter, and its name is `tube`" | Mandatory — it's how the planner knows what pools to look up. |
| `objects["tube"]` | "for the name `tube`, here are the values to try" | Mandatory — without this, the planner gets an empty pool and skips the action. |
| `def pre(self, tube)` | Python argument name | **Convention only** — the framework passes positionally, but matching the name keeps the code readable. |

#### How the planner uses them at plan time

```
For each Action class:
  1. Read its params              → ["tube"]
  2. Look each name up in objects → [[0, 1, 2, 3]]
  3. Cartesian product            → [(0,), (1,), (2,), (3,)]
  4. For each tuple of values:
       call pre(self, *values) — does it hold in current state?
       if yes → this binding becomes a plan candidate.
```

For pace_bt batch_size=2 with 5 atomic actions and one tube param,
the planner has **5 × 2 = 10** candidate Action instances per state.
Most get filtered by `pre()`; the survivors enter the plan.

#### Two-param example

If an action needs two inputs, list both:

```python
class TransferBetweenRacks(Action):
    params = ["tube", "slot"]           # ← order matters

    def pre(self, tube, slot):           # ← same order
        return in_source(tube) & ~occupied(slot)

    def execute(self, tube, slot):
        self.ctx.recipes["arm"].move(SOURCE[tube], DEST[slot])
        return "transferred"


# in setup():
"objects": {
    "tube": [0, 1, 2, 3],
    "slot": ["A1", "A2", "B1", "B2"],
}
```

The planner enumerates the **Cartesian product** — 4 × 4 = 16
candidates — then filters via `pre()`.

#### Zero-param actions

For scene-level actions (e.g. Park-trigger cleanup), `params = []`.
The planner doesn't enumerate; one candidate exists.

```python
class ParkTool(Action):
    params  = []
    trigger = "park"

    def execute(self):
        return "none"
```

#### Common mistakes

| Symptom | Cause |
|---|---|
| Action never appears in any plan | A name in `params` isn't in `objects` — the pool is empty. |
| Wrong values passed to `execute` | Order of `params` doesn't match order of function arguments. |
| Combinatorial explosion in plan time | You added a parameter the planner doesn't really need to enumerate. Move it to `ctx.meta["kwargs"]` instead. |

#### TL;DR

> `params` lists the **names** of an action's parameters. `objects`
> provides the **values** for each name. The planner takes the
> Cartesian product of pools, filters by `pre()`, and the survivors
> form the plan. Function argument names are convention — match them
> to the `params` strings for sanity.

### 3.7 Convention — actions are atomic (empty gripper in, empty gripper out)

**Rule:** every `Action` class is expected to start with the gripper
empty and end with the gripper empty.

The framework's auto-swap path manages **which tool is mounted** in
the tool changer; it doesn't know **what that tool is holding**. If
action A ends with a tube in the gripper and action B starts running,
B's `pre`/`eff` have no way to "see" that tube — the planner thinks
it's still in whatever rack it came from, and the runtime will mis-
sequence the next move (drop the gripper into the rack while it's
still holding the tube, etc.).

So write actions whole:

```python
def execute(self, tube):
    rcp = self.ctx.recipes
    rcp[SOURCE[tube][0]].pick(SOURCE[tube][1])    # gripper now holds tube
    rcp["scale"].place("place")                   # tube on scale, gripper empty
    rcp["scale"].weight()
    rcp["scale"].pick("place")                    # gripper now holds tube
    rcp[WORKING[tube][0]].place(WORKING[tube][1]) # tube placed, gripper empty
    return "inspected"
```

The gripper transitions through `held` states inside the body, but at
the function's boundaries (entry / return) it's empty. That keeps
each action a self-contained unit the planner can sequence freely.

#### When you genuinely need to hand off through the gripper

Sometimes a logical step really does span what looks like two
actions — e.g. "fetch a tube, then later place it after some
condition resolves." The clean way to model this without breaking
the convention is to **declare what the gripper is holding as a
predicate** and let the planner reason about it:

```python
gripper_holds = predicate("gripper_holds")

class FetchTube(Action):
    params = ["tube"]
    tool = "gripper"

    def pre(self, tube):
        return in_source(tube) & ~gripper_holds(tube)

    def eff(self, tube):
        return {"fetched": (-in_source(tube), +gripper_holds(tube))}

    def execute(self, tube):
        self.ctx.recipes[SOURCE[tube][0]].pick(SOURCE[tube][1])
        return "fetched"


class PlaceOnScale(Action):
    params = ["tube"]
    tool = "gripper"

    def pre(self, tube):
        return gripper_holds(tube)

    def eff(self, tube):
        return {"placed": (-gripper_holds(tube), +on_scale(tube))}

    def execute(self, tube):
        self.ctx.recipes["scale"].place("place")
        return "placed"
```

Now the planner *knows* the gripper is holding `tube` between the
two actions and can't schedule anything in between that would drop
it. The tool-swap path also stays safe — both actions declare
`tool="gripper"`, so the framework won't try to switch tools (which
would mean physically detaching the gripper-with-tube from the
robot, which the swap can't safely do).

**Don't** mix the two patterns within one tool — either an action is
atomic (empty in, empty out) or it's part of a predicate-tracked
hand-off chain. Mixing creates blind spots where the planner thinks
the gripper is empty but the runtime knows otherwise.

---

## 4. Fact arithmetic — the precondition / effect mini-language

In `pre()` you write boolean expressions over facts:

| Operator | Meaning | Example |
|---|---|---|
| `&` | AND | `in_source(t) & has_cap(t)` |
| `|` | OR | `weight_heavy(t) | weight_unknown(t)` |
| `~` | NOT | `~dosed(t)` |

In `eff()` you return a **dict** of named branches (see §3.3). Inside
each branch value, facts carry a sign:

| Operator | Meaning | Example |
|---|---|---|
| `+fact` | Add to state | `+in_working(t)` |
| `-fact` | Remove from state | `-has_cap(t)` |

A bare fact (without `+`/`-`) is invalid — be explicit about whether
you're adding or removing.

**Always return an `Expr` (or `Fact`) from `pre`, never a raw `bool`.**

The framework will *accept* a bool — `pre_fn` treats it as a literal
pass/fail — but `build_precedence` walks the `Expr` tree to discover
causal dependencies. A bool hides those deps from the precedence
builder, and the CP-SAT scheduler will then slot the action anywhere
on its resource, ignoring the constraint you intended.

If your precondition needs to enumerate items (e.g. "all tubes
done"), build the expression dynamically:

```python
# ✗ Bad — bool hides the dependency from the scheduler
def pre(self):
    tubes = self._ctx_objects().get("tube", [])
    return all((in_done.name, t) in self.state for t in tubes)

# ✓ Good — Expr the precedence builder can walk
def pre(self):
    tubes = self._ctx_objects().get("tube", [])
    if not tubes:
        return True
    expr = in_done(tubes[0])
    for t in tubes[1:]:
        expr = expr & in_done(t)
    return expr
```

The bool form *runs* but reorders incorrectly — easy to miss because
nothing crashes.

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
        return {"weighed": +weighed(tube)}

    def execute(self, tube):
        self.ctx.recipes["scale"].weigh(tube)
        return "weighed"     # ← name of the chosen eff branch
```

That's all. The framework:

* Auto-derives a PDDL `ActionTemplate` from `pre` / `eff`.
* Auto-registers the duration/resource into the scheduler meta.
* Auto-builds a BT leaf wrapping `execute`.
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

### 8.4 Dynamic world — external observers updating objects/state

If something outside the protocol changes the world during a run —
operator drops a new tube into the source rack, a vision system spots
a previously-hidden item, a sample disappears — the framework can
adapt **without restarting**. The mechanism re-uses what's already
here: mutate `ctx`, then signal `ReplanRequested`.

#### What you can mutate at runtime

| Mutation | Effect |
|---|---|
| `ctx.state["facts"].add((...))` / `.discard((...))` | Add or remove a fact. Replan if downstream actions depend on it. |
| `ctx.meta["objects"]["tube"] = new_list` | Update the parameter pool. Next plan re-enumerates over the new pool — new items get scheduled, missing items get dropped. |
| `ctx.meta["objects"]["new_param"] = [...]` | Introduce a new parameter pool. Any action that lists `"new_param"` in `params` becomes plannable. |
| `ctx.meta["checks"][name] = new_callable` | Swap a check at runtime (rarely needed). |

These are live data structures — change them and the next planning
pass picks the changes up.

#### Recommended pattern — sensing action (continuous observation)

The canonical way: declare an action whose job is to observe and
update the world. The planner schedules it periodically (gated by a
predicate). Its `execute()` reads the sensor, mutates `ctx`, and
returns `"changed"` to trigger a replan when needed.

```python
class RescanRack(Action):
    """Vision-driven rescan of the source rack. Updates the tube
    pool when the operator adds, removes, or swaps tubes."""
    params  = []
    duration = 2
    resource = "camera"
    tool     = "camera"

    def pre(self):
        return needs_rescan()                 # gate on a periodic predicate

    def eff(self):
        return {
            "no_change": +rescanned(),                       # default — no replan
            "changed":   (+rescanned(), +rack_dirty()),      # replan triggered
        }

    def execute(self):
        seen    = set(self.ctx.recipes["camera"].detect_tubes("source_rack"))
        current = set(self.ctx.meta["objects"]["tube"])

        if seen == current:
            return "no_change"

        # Update the pool so the planner sees the new world.
        self.ctx.meta["objects"]["tube"] = sorted(seen)

        # Sync facts: add ones for newly-arrived tubes, remove for departed.
        facts = self.ctx.state["facts"]
        for t in seen - current:            # newly appeared
            facts.add(("in_source", t))
            facts.add(("has_cap", t))
        for t in current - seen:            # gone
            facts.discard(("in_source", t))
            facts.discard(("has_cap", t))
            facts.discard(("weighed", t))   # …and any other tube-keyed facts

        return "changed"      # → framework raises ReplanRequested
```

Why this is the cleanest pattern:

* **All framework hooks are already there** — sensing eff (§3.3),
  branch-return contract (§3.3), replan signal (§8.3).
* **The planner schedules it** — `RescanRack` is just another action;
  it lands in the schedule alongside Inspect, Decap, etc. Predicates
  like `needs_rescan()` let you control how often (every N items,
  every M seconds with a clock predicate, etc.).
* **Observable from the outside** — `RescanRack` appears in plans,
  schedules, and tree visualisations. No hidden background magic.

#### Alternatives (when sensing-action isn't enough)

* **`pre_check` re-validation** — every action's `pre_check` does a
  cheap vision check before tool swap. Quick, local; doesn't proactively
  re-scan when nothing is about to run.
* **Background observer thread** — a separate thread polls the camera
  continuously and signals replan when state changes. More reactive
  than a scheduled rescan but adds threading complexity; not built into
  the framework today.

#### Safety notes

| Scenario | Behavior |
|---|---|
| Tube appears between actions | Picked up on next replan, scheduled cleanly. |
| Tube disappears between actions | Removed from pool + facts; planner re-routes. |
| Tube currently being processed disappears | The mid-action mutation is racy. Let `post_check` fail → BT retries → eventually replan surfaces the change. |
| Camera noise / false positives | Framework trusts the observation. Add confidence smoothing in the sensing action's `execute()` itself. |
| Vision crashes | Recovery action that re-establishes ground truth (e.g. operator confirms via GUI). |

#### TL;DR

> Mutate `ctx.state["facts"]` and/or `ctx.meta["objects"]` from inside
> a sensing action's `execute()`, return a non-default branch name to
> trigger replan, and the planner re-derives the schedule from the
> updated world. No `setup()` re-call, no engine restart. The sensing
> action is the visible, schedulable, debuggable home for "the world
> keeps changing under us" logic.

---

## 9. Runtime fact mutation — `add_fact` / `remove_fact` / `facts`

Inside an active run, PDDL facts live in `ctx.state["facts"]` — a
mutable set of tuples like `("capped", "tube_5")`. The framework
re-evaluates preconditions on every BT tick, so any fact you add
or remove takes effect immediately on the next tick. The replanner
reads the same set, so observations also propagate into the
planner's world view.

The launcher registers the active run's `ctx` on the workspace at
start, so callers don't have to chase it. Use the three APIs on
`Workspace`:

```python
workspace.add_fact("capped", "tube_5")       # idempotent
workspace.remove_fact("at", "tube_5", "src") # silent no-op if absent
workspace.facts()                            # snapshot — set of tuples
```

`add_fact` / `remove_fact` raise `RuntimeError` outside an active
run (facts only exist inside a run). `facts()` returns an empty
set in that case so it can be used in `if workspace.facts(): …`
checks without guarding.

### The explicit-mutation rule (cross-link)

Scene topology and PDDL state are **separate concerns**. The
framework never infers one from the other. A caller that mutates
the scene is responsible for mutating any corresponding facts, and
vice versa. See [component-guide.md §9](component-guide.md) for
the scene-side surface (`add_component` / `remove_component`).

Two calls, two intents:

```python
workspace.add_component("cap_99", {"type": "cap_2ml", "attach": {...}})
workspace.add_fact("at", "cap_99", "rack_A1_slot_2")
```

If you forget the second call, the planner has no idea the new cap
exists. If you forget the first, you have a predicate referring to
a phantom object. The framework can't tell which is wrong, so it
trusts the caller in both directions.

### Where this gets used

- **Operator recovery actions** — when the operator manually caps a
  tube during a paused run, the recovery action calls
  `workspace.add_fact("capped", t)` so the BT skips the failed
  `CapTube` action and moves on.
- **Vision-driven state correction** — a check that reads the camera
  and updates predicates without an action wrapping it (rare; most
  vision is done inside an action's `execute`).
- **Manual diagnose tools** — power-user inspection and debugging.

### What this does **not** do

- Does **not** invalidate plans on its own. The replanner kicks in
  on its own schedule (slice boundaries, replan triggers). If you
  need an immediate replan after a fact change, call the project's
  replanner trigger explicitly.
- Does **not** sync to the scene. See the explicit-mutation rule
  above.

---

## 10. Diagnose APIs — uniform across every project

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

## 11. The data flow at runtime

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

## 12. Authoring rules (the load-bearing ones)

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

## 13. Performance characteristics and slicing

### Per-layer cost

On a Pi 5, lab-sized problems:

| Layer | Cost | Notes |
|---|---|---|
| BT tick (10 Hz) | <1 ms | Pure-Python tree walk |
| PDDL plan (window of 4) | <30 ms | GBFS forward search with auto-derived heuristic |
| PDDL plan (window of 8) | ~130 ms | scales mildly super-linear with window size |
| Greedy schedule | <10 ms | Linear over plan length |
| Replanning total | <200 ms | observe → plan → schedule → rebuild tree |

Robot motions (1-10 s per move) and lab equipment (5 s to minutes per
action) dwarf all of these. The framework is never the bottleneck.

### Slicing — handling large batches

The in-house GBFS planner OOMs around batch ≈ 16+ on a Pi 5 — the
closed set of visited frozensets grows too fast. To handle larger
operator-supplied batches (48, 100, 200) without changing planners,
the launcher slices work into windows of `plan_window` items
(default 4).

Two concepts, intentionally separate:

| Knob | Who sets it | Where | Meaning |
|---|---|---|---|
| `batch_size` (or whatever the project calls it) | operator | `launch.yaml`'s `kwargs` | "I have N samples" — scientific quantity |
| `plan_window` | platform / project | `launch.yaml` top-level (default 4) | "plan this many at once" — planner-shaped tuning |

If `batch_size ≤ plan_window`, slicing is a no-op — one plan, one
tree, identical to non-slicing behavior.

#### Enabling slicing in a project

Add `item_done(state, item)` to `setup()`'s return dict. It's the
per-item completion predicate:

```python
def setup(**kwargs):
    batch_size = int(kwargs.get("batch_size", 1))
    tubes = list(range(batch_size))

    def item_done(state, tube):
        return (in_done.name, tube) in state \
           and (vial_2ml_capped.name, tube) in state

    def goal(state):
        return all(item_done(state, t) for t in tubes)

    return {
        "initial_facts": frozenset(...),
        "goal":          goal,
        "item_done":     item_done,        # ← opts in to slicing
        "objects":       {"tube": tubes},
    }
```

The launcher then:

1. Splits `all_items` into windows of `plan_window` items.
2. Restricts `ctx.meta["objects"][slice_dim]` (default `"tube"`) to
   the current window before each rebuild — so `param_iter` only
   enumerates current-slice items, and the planner only thinks about
   them.
3. Appends a `slice_check` leaf to the tree. After the window
   completes: if the global `goal` is met → engine exits; otherwise
   raise `ReplanRequested` so the framework picks the next window.
4. Re-derives `goal_facts` (heuristic hint) lazily per slice so the
   GBFS heuristic matches the active window.
5. Bumps the engine's replan cap to `ceil(N / plan_window) + 50` so
   many-slice runs don't trip the safety limit.

#### Choosing `plan_window`

* **Stay with default 4** unless you have a specific reason. It's the
  sweet spot for the in-house GBFS planner on a Pi 5: <30 ms per
  plan, no memory pressure.
* If you wire in Fast Downward or another industrial-strength planner
  later, raise `plan_window` to 50–500 — that planner won't choke and
  the per-slice overhead disappears.
* If your project has hardware reasons to slice differently (e.g. the
  source rack has exactly 6 slots), set `plan_window: 6`.

The operator never sees `plan_window`. They see `batch_size`. The
project advertises a sensible `max` for `batch_size` based on lab
capacity (rack slots, time available), not planner capacity.

### Where slicing comes apart

* **Per-slice slot tables.** If your project uses fixed slot tables
  like `SOURCE = [("rack","A1"), ("rack","A2"), ...]` indexed by
  tube number, those tables must cover the full operator-supplied
  range — not just one window. Otherwise `SOURCE[tube]` is an
  `IndexError` when tube ≥ table length. Make the table generator
  parametric on `batch_size`, or accept a hard cap matching the
  table.
* **Cross-slice resource state.** A predicate like `shaker_shaken(s)`
  that persists across slices needs explicit clearing logic
  (state-aware `Retrieved.eff()` checking "is this the last tube on
  the shaker", or a per-slice transient predicate scheme). Without
  it, the next slice's loads will be blocked.

---

## 14. Checks (pre_check / post_check) — pace_or-compatible

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

Wiring in `main.py` (the `checks` module is loaded dynamically from
`launch.yaml`'s `checks:` key, defaulting to `checks.py`):

```python
checks    = _import_module(LAUNCH.get("checks", "checks.py"))
recipes   = load_recipes(workspace, core, _BASE_DIR / LAUNCH["recipes"])
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

## 15. Common questions (FAQ)

Confusion points that come up the first time someone authors a
protocol. Each answer points to the deeper section for full detail.

### Q: What's the relationship between `params`, `objects`, and the function arguments?

They're three views of the **same name**. For `params = ["tube"]`:

* The planner looks up values in `setup()`'s `objects["tube"]`.
* It calls `pre(self, tube=...)`, `eff(self, tube=...)`,
  `execute(self, tube=...)` with each candidate value.

The string in `params`, the key in `objects`, and the function argument
name should all match. One source of truth — change one, change all.
(§3.1 for the full vocabulary.)

### Q: My `execute()` needs more data than what's in `params`. Where does that go?

`params` is reserved for **planning identity** — values the planner
enumerates over. Anything else `execute()` needs has four sources:

| Need | Lives in |
|---|---|
| Per-run operator knob (speed, threshold) | `self.ctx.meta["kwargs"]` — declared in `launch.yaml` |
| Per-protocol constant (slot maps, thresholds) | module-level constant in `actions.py` |
| Per-action constant (default volume, retry count) | class attribute on the Action subclass |
| Per-action-instance value the planner needs to reason about | **another entry in `params`** |

Don't dump runtime config into `params` — the planner would
enumerate over it for no reason. (See `self.ctx` table in §3.4.)

### Q: Where does branching happen? An `if` inside `execute()`?

**No `if` inside `execute()`.** Each branch is its own Action class.
Branching condition lives in `pre()` — the planner picks whichever
action's preconditions hold against the current state:

```python
class DispenseLight(Action):
    def pre(self, tube):  return in_working(tube) & ~weight_heavy(tube) & ~dosed(tube)

class DispenseHeavy(Action):
    def pre(self, tube):  return in_working(tube) &  weight_heavy(tube) & ~dosed(tube)
```

For branching on a **sensed value** (the outcome isn't known at plan
time), use the dict-eff pattern from §3.3 — `eff()` declares the
possible outcomes, `execute()` returns the observed one, the framework
replans.

### Q: Why are some `pre()` expressions wrapped in `(...)` and others aren't?

Pure Python line-wrapping. Multi-line expressions need to be inside
parens (or backslash-continued):

```python
return in_source(tube) & has_cap(tube)                   # short — no parens

return (                                                  # long — wrap to span lines
    in_working(tube)
    & ~has_cap(tube)
    & weight_heavy(tube)
    & ~dosed(tube)
)
```

Both return a single boolean expression — parens here are **not** a
tuple (a tuple needs a comma: `(x,)`). Doesn't affect semantics.

### Q: Why `&`, `|`, `~` and not `and`, `or`, `not`?

Python doesn't let you overload `and` / `or` / `not` — those keywords
always coerce to plain `True` / `False`. The framework needs to
capture the expression as an *object* (so the planner can later
evaluate it against a state set), so it overloads the bitwise
operators `&` / `|` / `~`. Same trick numpy and pandas use. (§4.)

### Q: What does the string in `predicate("in_source")` refer to?

It's the **internal name** stored in fact tuples. The Python variable
on the left (`in_source`) is just your handle for typing.

```python
in_source = predicate("in_source")
in_source(3)                          # → Fact("in_source", (3,))
ctx.state["facts"]                    # contains tuples like ("in_source", 3)
```

Convention: make the variable name and the string identical. Nothing
forces it, but mismatching them lies to anyone reading state dumps.
(See §3.5 for the full Predicate / Fact / state data model.)

### Q: What is `setup()` exactly?

The one function the framework calls **per Start click**. Its job is
to translate operator kwargs into the three things the planner needs:

```python
return {
    "initial_facts": frozenset(...),   # what's true at t=0
    "goal":          goal_fn,          # callable state -> bool
    "objects":       {"tube": [...]},  # candidate values for params
}
```

Predicates and Action classes are declared at module level (vocabulary,
static). `setup()` decides which **facts** (instances) are true at t=0,
not which predicates exist. The goal is always a callable — write
whichever predicate you need over the state set; see the next Q.

### Q: What does the `goal` callable look like, and what can it express?

A pure function `state -> bool`. The planner calls it after every
state expansion to check "are we done yet?". Common patterns:

```python
# Every item must reach a specific predicate
def goal(state):
    return all((in_done.name, t) in state for t in tubes)

# Disjoint terminal outcomes — done if shelved OR archived
def goal(state):
    return all(
        ("in_done", t) in state or ("in_archived", t) in state
        for t in tubes
    )

# Threshold — at least N items finished
def goal(state):
    return sum(1 for f in state if f[0] == "in_done") >= 3

# Conditional invariant — heavy tubes must also be recorded
def goal(state):
    heavy = {f[1] for f in state if f[0] == "weight_heavy"}
    recorded = {f[1] for f in state if f[0] == "recorded"}
    return heavy <= recorded
```

Two rules:

1. **Pure.** Same `state` in → same `bool` out. No I/O, no mutation.
2. **Cheap.** Called many times during search — use set membership
   (`(fact, t) in state` is O(1)) rather than scans.

The callable is **fully expressive** for boolean predicates over the
declared world state — same power as formal PDDL goal expressions.
Out of scope (by design): wall-clock conditions, plan-cost objectives,
external sensors. For sensor-driven goals, use a sensing action to
lift the value into state first (§3.3).

### Q: What is `frozenset` (in `setup()`'s `initial_facts`)?

Pure Python — an immutable, hashable version of `set`. The PDDL
planner needs states to be hashable (so it can de-duplicate visited
states during search), and a plain `set` isn't. You build the set
normally (`facts = set()`, `facts.add(...)`), then freeze it on the
way out. (§3.5 shows what the state actually looks like in memory.)

### Q: Where do I access `recipes` / `runtime` / `workspace` inside an action?

All on `self.ctx`:

```python
self.ctx.recipes["scale"]              # recipe instance from recipes.yaml
self.ctx.runtime.step("...")           # rt — for progress messages
self.ctx.workspace.components["..."]   # raw scene components
self.ctx.meta["kwargs"]                # operator inputs
self.ctx.meta["current_tool"]          # what tool is currently held
```

Same role as pace_or's `self.rcp` / `self.rt`, just bundled under
`ctx`. Full table in §3.4.

### Q: What's the difference between deterministic and sensing actions?

Both return a dict from `eff()` — the difference is **how many keys**:

```python
# Deterministic — one outcome, always
def eff(self, tube):
    return {"decapped": (-has_cap(tube), +in_working(tube))}

def execute(self, tube):
    ... do the work ...
    return "decapped"                # return the only key

# Sensing — multiple outcomes, runtime picks
def eff(self, tube):
    return {"light": ..., "heavy": ...}

def execute(self, tube):
    w = self.ctx.recipes["scale"].weight()
    return "heavy" if w > THRESHOLD else "light"
```

The planner uses the first key for projection. Non-first choices at
runtime trigger a replan. (§3.3 for the full contract.)

### Q: Why does `execute()` have to return a string? Can't I just omit the return?

For consistency with `eff()`. Every action declares its outcomes as
dict keys; every `execute()` picks one by name. Returning `None`
would mean "guess the default" — a shortcut we removed for the same
reason we removed the single-fact `eff()` shortcut. One way to do
each thing.

Returning `False` from `execute()` still signals failure. Anything
else is rejected as a programmer error.

### Q: Can the world change under the protocol mid-run (e.g. a camera adding/removing tubes)?

Yes. Mutate `ctx.state["facts"]` and `ctx.meta["objects"]` directly
from inside a sensing action, then return a non-default branch name
to trigger a replan. The planner re-derives the schedule from the
updated world — no `setup()` re-call, no engine restart.

See §8.4 for the canonical pattern (a `RescanRack` sensing action
the planner schedules periodically).

---

## 16. Migration checklist (linear → BT-driven)

If you have a pace_or-style linear project and want to convert it:

1. Copy `pace_bt/` to `your_project_bt/`.
2. Replace `pace_bt/actions.py` predicates + `setup(**kwargs)` +
   `Action` subclasses with yours. **The vocabulary mostly matches**
   `protocol.yaml`: `tool`, `duration`, `pre_check`, `post_check`,
   `tool_swap_duration`, `trigger`, `schedule`. Differences from
   pace_or:
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
