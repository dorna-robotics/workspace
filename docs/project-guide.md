# Project Guide

How to create and run a workspace project.

---

## 1. Project structure

One shape for every project — the examples under `examples/` are the
gold copies, `main.py` is byte-identical across all of them, and
`launch.yaml` is a list of pointers to these files (§3 shows the one
canonical launch file):

```
projects/my_project/
├── main.py              # canonical entry point — copy verbatim from any example
├── launch.yaml          # THE pointers: scene, recipes, actions, checks, hmi, where the platform writes (§3)
├── actions.py           # the protocol: predicates, Start → per-item actions → Park, the ROUTE
│                        #   (a phased protocol: actions/ package + phases.py, bt-framework-guide §13)
├── checks.py            # vision / sensor checks referenced by name from the actions
├── recipes.j2           # component aliases → recipe classes, with their solved parameters
├── hmi/                 # OPERATOR-FACING files (§3)
│   ├── default.j2       # the kwargs' defaults — data, read headlessly
│   ├── setup.js         # screen to SET the kwargs before the run (optional)
│   └── pendant.js       # screen shown DURING the run (optional; or hmi.j2 widgets)
├── scene/
│   ├── core_500.j2      # the chassis (core_500 / core_1000 / core_2000, from scenes/core/)
│   ├── layout.j2        # the bench: racks, stations, tools, cameras, meters
│   ├── stock.j2         # hand-maintained consumables, listed after layout.j2 (optional)
│   ├── bench.j2         # THIS unit's values — IPs, ports, serials, sim flags, rail offset (git-ignored, §2)
│   └── bench.example.j2 # the committed template for bench.j2
├── vision/              # the detections: a config per model, the model beside it, a vlm key (*.key, git-ignored)
├── components/          # the project's own component classes (@register) and their CAD/ (optional)
├── dev/                 # bring-up notebooks (optional)
├── core/                # THIS station: calibration, caches, motion book, logs (launch.yaml core_dir:; git-ignored)
├── records/             # one folder per run — records.jsonl / .csv (launch.yaml records:; git-ignored)
├── replays/             # replay recordings (launch.yaml replays:; git-ignored)
├── uploads/             # operator INPUT files — a file parameter's Open (launch.yaml uploads:; git-ignored)
├── captures/            # the detections' pictures — client_save_* in vision/*.yaml (git-ignored)
├── counts/              # rt.count's totals across every run — counts.json (launch.yaml counts:; git-ignored)
└── log/                 # the project's console — log/workspace.log, written by the orchestrator (git-ignored)
```

The last six are DATA folders (§3 "The project's folders"), never
checked in. Each is there because something names it: launch.yaml's
`records:` / `replays:` / `uploads:` (where the platform writes and
reads), `counts:`, a detection's own save path, or the orchestrator
itself (`log/`: every stdout/stderr line of the project, timestamped —
the admin page's Log panel is a live view of that file). Listing a
folder in `folders:` only SHOWS it. `captures/` holds a run's pictures
because the detections say so: `display.client_save_img: "../captures/tube_od/"`
in `vision/tube_od.yaml` (relative to that file's folder) writes every
run's frame there — on THIS machine, not the vision unit (vision-guide §5).

**The one path rule: a relative path is relative to the file it is
written in.** The same rule as a web page's `src`, a stylesheet's
`url()`, a compose file — and the only one here:

| Written in | Relative to |
|---|---|
| `launch.yaml` (`scene:`, `recipes:`, `records:`, `folders:` paths, ...) | its folder — the project folder |
| `recipes.j2` (`detection_preset: {config: vision/tube_od.yaml}`) | its folder (bna's recipes.j2 sits in `bna/`, so `vision/` is `bna/vision/`) |
| a scene `.j2` (`{% import "bench.j2" %}`) | its folder |
| a detection config (`detection.path`, `references[].image`, `key_path`, `display.client_save_*`) | its folder (`vision/`); `true` is the folder `output/` |
| Python — `main.py`, a notebook, `rt.*` calls | the folder that program runs in: the orchestrator runs `main.py` from the project folder, Jupyter runs a notebook from its own |

A path that names a place on ANOTHER computer (a vision unit's
`save_img`, a `config: {server: ...}` file) cannot be relative to a file
here; it is written absolute.

### The `vision/` folder — the detections

Everything a project's detections use sits in ONE checked-in folder:

```
vision/
├── tube_od.yaml              # EVERY setting of one detection (below)
├── tube_autosampler_2ml.pkl  # the model, beside the config that names it
├── tube_cls.yaml
├── tube_cropped.pkl
├── cap_check.yaml            # a vlm config (vision-guide §9) ...
├── refs/                     # ... its reference images
└── vlm.key                   # ... and its key — git-ignored (*.key)
```

A config holds EVERY setting of its detection — nothing split between
it and `recipes.j2`, which only names the file:

```yaml
# vision/tube_od.yaml
detection:
  cmd: od
  path: tube_autosampler_2ml.pkl        # relative to vision/ — the model beside it
  conf: 0.5
frames_avg: 1
display:
  label: 1
  save_img: false
  save_img_roi: false
  client_save_img: "../captures/tube_od/"   # relative to vision/ too — <project>/captures/tube_od/
  client_save_img_roi: "../captures/tube_od/"
```
```yaml
# recipes.j2
inspector:
  class: workspace.recipes.inspector.Inspector
  kwargs: {..., detection_name: tube_od, detection_preset: {config: vision/tube_od.yaml}}
```

- One config per detection setup; several detections may share it
  (apc's station and robot cameras both use `disc_od.yaml`). An inline
  key would win over the config's, key by key (vision-guide §5 "Config
  files") — keep that for a one-off test, not a project.
- Every path inside a config is relative to `vision/`, and `config:` is
  relative to recipes.j2 — the project runs from any folder on any
  machine, from `main.py` or a notebook alike; never an absolute
  `/home/.../model/x.pkl`.
- `.gitignore` gets `*.key`, before any key exists. `vision/` is NEVER a
  `folders:` entry: only declared folders are shown, so the file browser
  cannot list or serve the key.
- A config detection that cannot be added (missing file, bad key) fails
  the launch.

bna (`vision/tube_*.yaml`) and apc (`vision/disc_*.yaml`) follow it; the
vision repo's `example/vlm/` is the vlm example.

**Convention for new projects: operator-facing declarations live in
`hmi/`.** `launch.yaml` stays a short list of pointers (scene,
recipes, actions, checks, kwargs, hmi) — one file per concern, none of
them inline. Run parameters and the pendant HMI are the same concern
(what the operator sees and sets), so they share the folder.

To create a new project, copy `projects/pace_or/` as a template and edit each file. The sections below explain them in detail.

### `main.py` — entry point

This is how all the pieces connect:

```python
import os, argparse, yaml
from pathlib import Path
from workspace.workspace import Workspace
from workspace.ortools.workflow import BaseWorkflow
from workspace.runtime_server import RuntimeServer
from states import States
from checks import Checks

_BASE_DIR = Path(__file__).parent

def workflow_fn(*, workspace, core, **kwargs):
    BaseWorkflow(workspace, core, _BASE_DIR, States, Checks, **kwargs).run()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=int(os.getenv("PORT", "5010")))
    args = p.parse_args()

    with open(_BASE_DIR / "launch.yaml") as f:
        launch = yaml.safe_load(f)

    ws = Workspace(config_path=launch["scene"], port=args.port)
    RuntimeServer(runtime=ws.rt, workflow_fn=workflow_fn, workspace=ws).run()

if __name__ == "__main__":
    main()
```

- `launch.yaml` defines the scene files and runtime parameters
- `Workspace` loads the scene and creates the runtime
- `RuntimeServer` exposes start/pause/end/kill commands and serves the 3D viewer
- `workflow_fn` is called on **Start** — it receives `**kwargs` from the Parameters modal and passes them to `BaseWorkflow`, which passes them to `States` and `Checks`
- The orchestrator launches this with `sudo python3 main.py --port 5010`

---

## 2. Scene — `scene/`

Defines the physical hardware: robots, racks, tools, peripherals. Built using the **Scene Builder** GUI. The Jinja2 templates (`.j2` files) describe every component and its position. This is the source of truth for component names used in recipes.

**This unit's values — `scene/bench.j2`.** The same project runs on
several benches, and each one's robot IP, device IPs, serial ports,
simulation flags and measured rail offset differ. Those live in ONE git-ignored file per unit, and the scene files
import it — so a `git pull` never carries one bench's addresses to
another, and nothing has to be edited back after a pull. Set this up
when you make a project:

1. `scene/bench.j2` — one `set` per value, nothing else:

   ```
   {% set robot_sim     = false %}   {# the robot (core_*.j2) #}
   {% set sim           = false %}   {# every device in layout.j2 #}
   {% set rail_offset   = -212.1 %}  {# the rail's measured zero, mm #}
   {% set robot_ip      = "10.0.3.10" %}
   {% set scale_ip      = "10.0.3.50" %}
   {% set vision_ip     = "10.0.3.40" %}
   {% set camera_serial = "130322274110" %}
   {% set pump_port     = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0" %}
   ```

2. Every scene file that uses one imports it at the top and uses the
   names — `core_*.j2` for the robot, `layout.j2` for the rest:

   ```
   {%- import "bench.j2" as bench -%}
   core:
     simulation: {{ bench.robot_sim }}
     ip: "{{ bench.robot_ip }}"
     rail_cfg:
       offset: {{ bench.rail_offset }}
   ```

3. `.gitignore` gets `scene/bench.j2`; commit `scene/bench.example.j2`
   with the same names, placeholder addresses and simulation on. A new
   unit copies the example to `bench.j2` and fills it in.

What goes in it: what is true of THIS unit and not of the design — IPs,
ports, serial-port paths, camera serials, which devices are simulated,
measured calibration like the rail offset. Positions, anchors and device
settings (a pump's bus `address:`, a baud rate) describe the design and
stay in the scene files. bna is the reference (`scene/bench.j2`,
`core_1000.j2`, `layout.j2`).

The rules, all plain Jinja: `import` / `include` paths are relative to
the importing file's folder; an undefined name FAILS the render naming
it (a typo like `bench.robot_ipx`, or no `bench.j2` on a fresh clone) —
never an empty value. Every renderer — the workspace, launcher, replay,
the orchestrator and the scene builder — goes through `workspace/j2.py`,
so the scene renders the same everywhere; `recipes.j2` and
`hmi/default.j2` can import the same way. The scene builder's export
(`projects/builder/config.j2`) is rendered output: these values come out as
literals — when merging an export into
`layout.j2`, keep the `{{ bench.* }}` references.

### Riding the arm — a component attached to a robot link

A component can be attached under a robot link exactly as under a
fixture plate: `attach: {parent_name: core, parent_solid: robot_A5,
parent_anchor: hole_0, ...}`. A robot-mounted camera is the usual case
(`inspection_d405_robot`, `inspection_poe_robot`); a bracket or a
sensor on the forearm is the same thing. The core has no camera keys —
a camera is always its own component, wherever it sits.

What riding the arm means, and nothing else:

- **Pose from the tree.** Its anchors (a camera's `lens`) move with the
  link; a capture reports that pose. No eye-in-hand / eye-to-hand
  concept, no mount transform, no robot on the vision side.
- **Its collision box is part of the link.** The planner gets it as a
  *link box* on that link (`robot_A1..A5` → the URDF's `j1..j5_link`;
  the flange's children are the tool's boxes, as before): it moves with
  the link, collides with the world and with non-adjacent links as the
  link does, and is never a self-hit against its own link or its
  neighbours. One body. Give such a component its real box — nothing
  else protects it.
- **No station geometry.** An `Inspector` on a camera attached under a
  robot link has no reference IK and refuses `present()`; the arm moves
  the camera to the part. The attach is the only thing that says
  "riding the arm" (`Workspace.rides_robot` walks the tree); a class
  never declares it.

The scene tree and the planner's URDF are two models of one robot with
different link frames, so a link box is placed from its world pose at
the current joints through the planner's own FK (`Core.
_link_boxes_to_cubes`), never by assuming the frames coincide.
`compute_collision_boxes` returns three groups — world, flange, link —
and every planner update passes all three. The viewer's collision-box
toggle also draws the robot's own boxes, every URDF link box where the
planner places it, in yellow next to the scene's robot (components:
red, on the flange: blue): the way to see where collision is really
checked. The Display computes boxes only while a viewer shows them —
a frame with them hidden (the usual case) is one pose pass, a few ms
even on a 250-solid bench — and resends every solid's boxes in a
snapshot when they are turned on.

---

## 3. Launch config — `launch.yaml`

Top-level keys:

| Key | Description |
|-----|-------------|
| `scene` | List of scene file paths (relative to project folder). Loaded in order to build the 3D scene and component registry. Typically `base.j2` for hardware, `layout.j2` for consumables. |
| `core_dir` | *Optional, default `core`.* THE STATION'S OWN FOLDER — calibration (`calibrate.json`), every cache (`ik`, `path`, `fold`, `traj`), the motion book and the logs, read and written. Relative to the project folder, or absolute. **Set it explicitly whenever projects share a scene** (`scene: [../scene/...]`): point them at the same folder to share one calibrated bench, or at their own to keep separate caches. The folder is resolved from the project `main.py` declares (`Workspace(project_dir=...)`), never guessed from where the scene happens to live. |
| `records` | *Optional.* WHERE RUN RECORDS GO — the folder `rt.record` writes one sub-folder per run into (`<run start>/records.jsonl`, `records.csv`). Relative to the project folder, or absolute. Not declared = OFF: records stay in memory (the pendant and `rt.records()` still show them), nothing is written. |
| `replays` | *Optional.* WHERE REPLAY RECORDINGS GO — the viewer recorder's `rec_<start>.jsonl` files, and the folder the scene builder's Replay panel lists. Not declared = OFF: the record button says so. |
| `uploads` | *Optional.* WHERE A FILE PARAMETER'S **Open** BROWSES — operator input files, manifests, parameter presets. Not declared = OFF: Open has no folder. |
| `folders` | *Optional.* THE FILE BROWSER'S TABS — DISPLAY ONLY. One entry each, all four fields written out: `{key, label, path, read_only}`; one tab per entry, in list order, `label` as its text; Files opens on the first. `key` is a plain name (letters, digits, `_ -`), `path` relative to the project folder (`../x` shares a parent's) or absolute. `read_only: true` = browse, preview and download only — upload, new folder and delete are refused by the server; read on every request, so a flip applies on the next open. Listing a folder never makes anything be saved into it. Not declared = no tabs. The old `data_dir` / `results_dir` / `rec_dir` / `captures_dir` keys are refused, naming the line to write. See "The project's folders". |
| `counts` | *Optional.* The file `rt.count` keeps its totals in, relative to the project (`counts/counts.json`; `../counts/counts.json` to share one bench's totals between sibling projects). No key: `rt.count` totals stay in memory only, said once. Explicit and separate from `folders:` — a folder tab never decides where counts are written. See "`rt.count(name, **amounts)`". |
| `default` | The kwargs' defaults / schema — each key becomes a run parameter. **Either inline (a dict) or a file path** — new projects use `default: hmi/default.j2` (see §1); inline stays supported for small projects. The file's top level IS the schema, rendered as Jinja2 then parsed. Both shapes work everywhere (orchestrator form, `bt.replay`). |
| `actions` | Protocol module — `actions.py`, or a **package** `actions/` (one module per phase; bt-framework-guide §2 and §13 "The package layout"). `bt.replay` and `bt.dryrun` import it by the name `actions` either way. |
| `route` | *Optional.* The module holding `ROUTE` — the order an item meets the actions. A flat project keeps `ROUTE` in its actions module and omits this key; a phased project sets `route: phases.py`, whose `Phase` classes each carry their `route` and whose `ROUTE` lists the phases in order — DEPTH, how far an item is carried before the batch regroups, a different limit from `plan_window` (WIDTH) and from capacity facts (HARDWARE). A phase whose items overlap declares the order across them as its `cycle` (`Phase.group`, `Phase.cycle`; bt-framework-guide §13 "The cycle"). See bt-framework-guide.md §13. |
| `plan_window` | *Optional, default 4.* How many items one schedule holds. A `Phase` may override it for the span it is open (`Phase.plan_window`). See bt-framework-guide.md §13. |

**The canonical `launch.yaml`** — every project carries this shape, in
this order, with these folder names. A key is a pointer or a folder,
never an inline block; the `default:` schema lives in `hmi/default.j2`.
The write folders and the tabs are declared, never assumed: a folder
key that is absent is OFF, and the orchestrator creates every declared
folder at launch.

```yaml
project_name: my_project
port:         5010
scene:        [scene/core_500.j2, scene/layout.j2]
recipes:      recipes.j2
actions:      actions.py          # or actions/ with route: phases.py (phased protocol)
checks:       checks.py
default:      hmi/default.j2      # the kwargs' defaults (data — Python reads it)
setup:        hmi/setup.js        # run-setup screen (optional)
pendant:      hmi/pendant.js      # during-run screen (optional)

# This station's own folder — calibration, every cache, the motion book, logs.
core_dir:     core

# Where the platform writes — relative to this folder, or absolute.
# Not declared = off: run records stay in memory, no replay is saved,
# a file parameter's Open has no folder, rt.count totals stay in memory.
records:      records             # each run's rt.record files: <run>/records.jsonl, .csv
replays:      replays             # replay recordings
uploads:      uploads             # where a file parameter's Open browses
counts:       counts/counts.json  # rt.count's totals across every run (only if the project counts)

# The file browser's tabs, in this order — display only: listing a
# folder never makes anything be saved into it. captures/ is written by
# the detections themselves (vision/*.yaml display.client_save_*).
folders:
  - {key: records,  label: Records,  path: records,  read_only: false}
  - {key: uploads,  label: Uploads,  path: uploads,  read_only: false}
  - {key: captures, label: Captures, path: captures, read_only: false}
  - {key: replays,  label: Replays,  path: replays,  read_only: false}
  - {key: counts,   label: Counts,   path: counts,   read_only: false}
  - {key: log,      label: Log,      path: log,      read_only: true}   # the project's console, orchestrator-written

plan_window:  4
scheduler:    cpsat
```

`log/` is the one folder no launch key names: the orchestrator writes
`log/workspace.log` there for every project it launches, so the tab is
read-only — browse and download, never upload or delete.

Sibling projects that share one bench (bna's `_bna`, `_tph`,
`_calibration`) point the shared keys one level up — `scene: [../scene/...]`,
`recipes: ../recipes.j2`, `core_dir: ../core`, `counts: ../counts/counts.json`,
a `captures` tab at `../captures` — and keep their own `records`,
`replays` and `uploads`.

A `default:` entry is either bare (the value is the default) or a spec:

```yaml
tubes: {"A1": 0.4, "A2": 0.4}     # bare — a map kwarg with its default
batch_size:
  type: int
  default: 4
  label: Number of tubes
  min: 1
  max: 20
```

### Two entry shapes — bare, or a spec

Each schema entry is either **bare** (the value IS the default) or a
**spec dict** — one deterministic rule tells them apart: *a dict
containing the key `"default"` is a spec; anything else is a bare
default.* (Corollary: a bare map default must not itself contain a key
named `default` — use the spec form then.)

```yaml
tubes: {"A1": 0.4, "A2": 0.4}     # bare — a map kwarg with its default
print_label: false                 # bare
batch_size: {type: int, default: 4, min: 1, max: 20}   # spec
```

**Bare is the shape for a project with its own `setup:` screen** — the
schema stays "the kwargs themselves", and labels/units/limits live in
the screen, which is where presentation belongs (bd is the exemplar).
The generic form still renders bare entries by inferring the widget
from the default's type (bool → toggle, number → number input,
list/map → JSON textarea).

**The spec form is for a project with NO screen** that wants typed
widgets, labels and server-enforced limits on the auto-generated form
(apc is the exemplar). Its properties:

### Kwarg field properties (spec form)

| Property | Required | Description |
|----------|----------|-------------|
| `default` | No | Pre-filled value. `null` = empty |
| `label` | No | Display name in the modal (defaults to the key name) |
| `hint` | No | Help text shown below the input |
| `placeholder` | No | Greyed-out text inside the input when empty |
| `optional` | No | `true` = field can be left empty, sent as `null` |
| `min` / `max` | No | Numeric bounds (for `int` and `float` types) |
| `type` | For the generic form | Widget type: <br>• `int` — number input (`min`, `max`, `step=1`) <br>• `float` — number input (`min`, `max`, `step=any`) <br>• `str` — text input <br>• `bool` — touch switch <br>• `choice` — dropdown (requires `options: [a, b, c]`) <br>• `textarea` — multi-line text (`rows` default 4, tries JSON parse) <br>• `file` — file upload (`accept: ".csv,.xlsx"`) |

**`type:` exists to pick the generic form's widget — nothing else.**
A collection kwarg (a list or map, like bd's `tubes`) has NO type: its
default's shape declares it, and replay batches on that shape. The
standing rule for this file: **every key must have a reader** (the
generic form, validation, replay, or your screen) — a key nothing
reads is deleted, not kept for documentation's sake. Taken to its
conclusion: a project whose screen owns all presentation declares
bare entries only.

### `setup` — the project's own run-setup screen

The generic form covers scalars. When an operator must pick *which*
positions to run — a rack, a tray, a carousel — that is a picture of
the project's own hardware, so the project draws it. Declare it in
`launch.yaml` next to the schema:

```yaml
default: hmi/default.j2     # the kwargs' defaults (data)
setup:   hmi/setup.js      # screen to SET them
```

The schema still declares every parameter, because it is read with no
browser anywhere (`bt.replay`, the CLI, launch). It just stops
describing how anything looks:

```yaml
tubes:
  type: map                 # or list
  label: Tubes to process
  value: {label: Dispense, unit: mL, default: 0.4, min: 0.1, max: 5.0, step: 0.1}
  default:                  # the project's own grid, in the project
    "A1": 0.4
    "A2": 0.4
```

The screen is hosted exactly like the pendant screen (hmi-guide §4b) —
shadow root, design tokens, `.html` with `data-field="key"` or `.js`
with `{css, mount(root, api), value(), validate()}`. Its `api` carries
`{schema, values, frozen, theme, onTheme}`. Two rules make it safe:

* **The platform validates whatever the screen returns** against the
  schema — required, `min`, `max`. A project screen is not trusted to
  enforce its own contract, and `validate()` only ADDS a message.
* **Fields the screen does not draw keep their schema default**, so a
  screen can cover only the parameters it cares about (bd draws the
  rack; `print_label` keeps its default).

Filter excluded positions **in `setup` too** — the screen prevents
picking them, but a replay/API caller can pass anything:

```python
picked = kwargs.get("tubes") or {}          # {"A1": 0.4, ...}
tubes = sorted({SLOTS.index(s) for s in picked
                if s in SLOTS and s != SOURCE_SLOT})
```

`workspace.bt.replay --batch N` slices the first N entries of the
first collection-typed kwarg's default, so the schedule gate keeps
meaning "run N items" with no rack knowledge in the platform. Verify a
real selection with `--kw 'tubes={"A1": 0.4, "C2": 1.5}'`.

### `replan:` — the Replan choice, drawn the project's way

The same idea for the paused-run Replan dialog: by default it lists the
items; a project that already draws its bench can show the choice there.

```yaml
replan: hmi/replan.js       # explicit path; absent → the plain list
```

A view only — the items on offer, the run's parameters in, the chosen
items out (`value()`); the dialog, the request and the replan itself
stay the platform's. bna's `replan.js` imports its bench from
`setup.js`, so the operator sees the run-setup racks with a sample's
bottle and both vials crossed out. Contract and rules:
bt-framework-guide §8.6 "The Replan view".

### `_layout` — arranging the run-setup form

The schema renders stacked in declaration order. To place fields side
by side, declare rows — a HINT, not a field:

```yaml
_layout:
  - row: [batch_size, print_label]    # side by side
```

`row` places fields side by side. That is the whole vocabulary: a
project that needs more than rows of scalars ships a `setup` screen
and arranges it however it likes (multiple racks, tabs, wizards — all
project markup, none of it platform vocabulary).

Unlisted fields stack below in declaration order; rows wrap to a
single column on narrow screens (pendant portrait). This is the only
layout vocabulary for the **run-setup form** — a project never ships
markup or CSS for its parameters, because a list of typed fields is a
genuine declaration. Its pendant SCREEN is the opposite case and is a
project-owned file (hmi-guide §2, §4b).

### Compatibility rule for declarative files

Projects are `.j2`/`.yaml` declarations that outlive the platform
version they were written against. The rule that keeps old projects
working:

1. **New features are opt-in keys. Absent = previous behaviour.**
   Everything added this cycle obeys it: no `setup:` → the generic
   form; no `pendant:` → the default pendant; no `_layout` → fields stack;
   `default:` as a dict still works exactly as before the file form
   existed.
2. **Never repurpose an existing key.** A changed meaning is a silent
   behaviour change on every project that already uses it. Add a new
   key instead and let the old one keep working.
3. **Deprecate loudly, never silently.** If a key must go, the loader
   warns with the project name and the replacement for at least one
   release; it does not quietly do something else.
4. **Readers degrade, they don't crash.** Unknown keys are ignored;
   an unresolvable reference (a `component:` that no longer exists)
   falls back to a plain field. A display concern must never block a
   run.
5. **`examples/` is the compatibility suite.** Before shipping a
   platform change that touches loaders, recipes or the schedule, run
   `workspace.bt.replay` (and `workspace.recipes.solve` for motion
   changes) over the example projects — they are the cheap regression
   net for "did I just break older projects".

**One rename, on the record.** The schema's launch key was `kwargs:`
until 2026-08; every in-tree example and bench project now declares
`default:`. The old key still loads with the same meaning but prints a
rename warning at load (rule 3) — new work never writes it.

**One removal, on the record.** `type: slots` — a rack-position picker
the platform drew — was deleted rather than deprecated, against rule 3.
It had exactly one user (bd), in-tree, migrated in the same commit, and
keeping it would have meant carrying a rack in the platform forever
(hmi-guide §2). A project still declaring it degrades to a plain text
field rather than crashing (rule 4); the replacement is a `params`
screen.

### How kwargs flow

All kwargs defined here are passed to `States.__init__(rcp, rt, **kwargs)` and `Checks.__init__(rcp, rt, **kwargs)`. Use `kwargs.get("my_param", default)` to access them.

Two reserved keys are also used by the scheduler if present:

| Key | Default | Description |
|-----|---------|-------------|
| `batch_size` | `1` | Number of items to process. Each state runs once per item (index 0 to n-1). |
| `horizon` | `60` | Rolling window size for replanning. `null` = plan all tasks at once. |

If `batch_size` or `horizon` are in your kwargs, they override the defaults for the scheduler. They are still passed to States and Checks like everything else.

---

### `rt.op(**values)` — operator-facing values

`rt.step` is the engineer timeline (append). `rt.op` is the operator
value channel (replace): actions publish what the project's pendant
screen binds to, and writing a key again overwrites it.

An action publishes plain values and stays ignorant of the UI; the
screen (`hmi/pendant.html`, hmi-guide §4b) decides how they look. A
map value is how a rack is driven — `rt.op(tubes={"A1": "done"})` sets
`data-state` on whatever element claims `data-slot="A1"`.

```python
rt.step(f"tube {t+1}: weighed")          # timeline entry
rt.op(state=f"Weighing tube {t+1}", weight=grams)   # current values
rt.op(last_image="/captures/bd/tube_06.jpg")        # assets BY REFERENCE
rt.op(weight=None)                                   # remove a key
```

Rules: values must be JSON-able and small (≤4 KB each, ≤200 keys,
≤64 KB total — over-limit values are dropped with one log line, never
a crash); large data is passed as a URL, not inlined; the call never
blocks and never raises into the workflow. Values are stored as
**detached copies** — keep and mutate your own dict freely, the store
never sees it. Publish plain data only (a barcode STRING, not the
driver's Scan object): an object sneaks a nice repr into ``rt.step``
but is not a value. Values are memory-only and
cleared at run start. Delivery is a coalesced WS push — see
`docs/hmi-guide.md` §3 for the channel spec.

### `rt.record(item, **fields)` — the per-item audit record

`rt.op` is what the operator sees now. `rt.record` is what the client
gets afterwards: one record per ITEM the run worked on, keyed by the
project's own identity for it, persisted as it is written, exported as
CSV when the run ends. bd's `_record` helper and bna's L-number sheet
are this, owned by the platform once instead of copied per project.

```python
rt.record("L2508-0142")                                   # Start: seed the row
rt.record("L2508-0142", barcode=code, barcode_ok=code == expected)
rt.record("L2508-0142", weight_g=grams)                   # in Weigh, on a valid read
rt.record("L2508-0142", ph_1=[7.2, 8.9], naoh_ul=600)
rt.record("L2508-0142", status="done")                    # at Park, from the facts
rt.record("L2508-0142", ph_1=None)                        # remove a field
```

Rules, and the reasons behind them:

* **Key by identity, not position.** The item is the L-number, sample id
  or serial the client will look for — a slot is where it sat, and goes
  in as a field. Seed every row at Start (a call with no fields registers
  the item) so the table shows the whole batch before anything is measured.
* **Write where the value is produced.** The action that read the device
  writes the field, right after the reading is valid — the same place it
  asserts its fact (§8 "Device reads + declarative retry"). Never rebuild
  the record from memory at the end; the end only adds `status`.
* **Status is derived from facts.** At Park, "done" is the item's final
  fact; a removed item reports how it left (`self._ctx_removed(t)` — the
  action, its outcome, the phase; bt-framework-guide §8.5); anything else
  reports the last phase fact it reached plus its anomaly flags. The
  project owns the words, the facts own the truth.
* **Merge semantics per item.** Writing a field again overwrites it;
  `None` removes it; a value equal to the stored one writes nothing.
  Values are plain JSON — a barcode string, a number, a small list —
  ≤ 4 KB each, ≤ 10 000 items; over-cap values are dropped with one log
  line per reason, never a crash.
* **Never blocks.** Memory only from the workflow thread; the server
  drains on the `rt.op` cadence and does the file IO there.

What the platform does with it:

| | |
|---|---|
| `<records>/<YYYY-mm-dd_HH-MM-SS>/records.jsonl` | one line per call, `{"t", "item", "set", "unset"}`, appended as the run goes — the HISTORY; a crash mid-run loses nothing already drained |
| `<records>/<YYYY-mm-dd_HH-MM-SS>/records.csv` | written when the run ends (IDLE / ERROR / KILLED): `item` first, then every field in first-seen order; nested values as JSON |
| `GET /records` · `GET /records.csv` | the same, live, at any moment of the run — the download link on the pendant |
| `record_state` on `/ws` | snapshot then deltas, the `op_state` shape keyed `item → {field: value}`; feeds the pendant's `records` widget (hmi-guide §4) and `api.onRecords` for a project screen (§4b) |

launch.yaml's `records:` names that folder (not declared: records stay
in memory, nothing is written); keep it out of the project's git — it is
data, one folder per run. List it in `folders:` too and the GUI's file
browser shows it, so an operator can pull a run's records without a shell.
A script without a server (a dev notebook) sets `rt.record_dir` itself
and calls `rt.record_drain()` once at the end; otherwise records stay in
memory and `rt.records()` / `rt.record_csv()` still answer.

### `rt.count(name, **amounts)` — the project's counters

`rt.record` is per run, per item. `rt.count` is the project's running
totals ACROSS runs — how many doses, how much of each reagent, how many
tubes moved — kept in one file and never reset by a run, a
restart or a `git pull`. The file is named explicitly in `launch.yaml`,
relative to the project:

```yaml
counts: counts/counts.json     # rt.count's totals (+ counts.json.bak beside it)
```

No `counts:` key: the totals stay in memory and nothing is written —
said once, at the first `rt.count`. A `folders:` entry over the same
folder only gives it a file-browser tab; it has no say in where counts
go. Projects sharing a bench's totals point at one file
(`counts: ../counts/counts.json`) — one of them runs at a time; two at
once would each save their own totals over the other's.

```python
rt.count("dose.MeCl", n=1, ul=3000)     # one more MeCl dose, 3000 µL more
rt.count("tube.moved", n=1)
```

Rules, and the reasons behind them:

* **Explicit.** Every keyword adds its value to the field of the same
  name, and nothing else changes — no implicit "+1": `n=1` is written
  out. A call with no amounts adds nothing. The name is any text; the
  dot is only a grouping convention.
* **Amounts are finite numbers ≥ 0** — a counter only grows. A call with
  a bad amount is dropped WHOLE, never half-applied, with one log line
  per reason.
* **Count where it happened** — the action that did it, after it
  succeeded (a dose after the pump accepted it), like `rt.record`. The
  platform counts what it is told: a simulated run counts too, unless
  the project guards the call.
* **Never blocks.** `rt.count` is a few µs in memory; the save (an
  fsync on the SD card, ~5–30 ms measured) runs on the server's own
  worker thread — never the workflow thread, never the IO loop — at
  most every `Runtime.COUNT_SAVE_S` (5 s) while a run counts, at once
  when a run ends, and at exit. A save still running skips the next
  tick instead of queueing.
* **Missing is not an error.** No folder at launch: created at the first
  save. No file: the totals start from zero. Folder or file deleted
  mid-run: written again from the totals in memory, which always hold
  everything. Not writable: counting goes on in memory, said once, and
  every next save retries.
* **Crash-safe.** Each save is a temp file, fsync, the old file kept as
  `<file>.bak`, then an atomic rename. At launch the totals are loaded
  from the file, else from the backup (one save behind); if both are
  unreadable the bad file is set aside as `<file>.corrupt-<stamp>`,
  never overwritten, and counting starts from zero — said once in the
  log.

```json
{"updated": "2026-10-02T11:32:13",
 "counts": {"dose.MeCl": {"n": 412, "ul": 1236000}, "tube.moved": {"n": 1650}}}
```

**`run.time` — the one counter the platform keeps itself**, because
only it sees every way a run ends (done, error, kill, operator park):
one explicit `count("run.time", n=1, s=<seconds>)` in `Runtime._set_state`
when the run ends — the seconds are the GUI's "Up", start to end,
paused time included. A run cut by a power loss never reaches its end,
so its time is not counted.

`rt.counts()` returns the totals (a copy). Keep the file out of the
project's git. A script without a server sets `rt.count_file` itself —
the totals already there are loaded, and anything counted before is
added on top — and calls `rt.count_drain(force=True)` at the end.


### The project's folders, and the file browser

Two separate, explicit things in `launch.yaml`. Where the platform
WRITES and READS is one key per job — `records:`, `replays:`,
`uploads:` (and `counts:` for rt.count's file); a key not declared is
OFF, there is no default folder. What the file browser SHOWS is
`folders:` — display only, one tab per entry, in its order:

```yaml
records:      records             # where each run's rt.record files go
replays:      replays             # where replay recordings go
uploads:      uploads             # where a file parameter's Open browses
folders:                          # the file browser's tabs, in this order — display only
  - {key: records,  label: Records,  path: records,     read_only: false}
  - {key: captures, label: Captures, path: ../captures, read_only: false}
  - {key: uploads,  label: Uploads,  path: uploads,     read_only: false}
  - {key: replays,  label: Replays,  path: replays,     read_only: false}
  - {key: manuals,  label: Manuals,  path: ../manuals,  read_only: true}
```

(Modelled on bna's `_bna`, whose three subprojects share `../captures`.
`read_only: true` lets the bench browse and download a folder but never
upload or delete there. `vision/` is never a tab — it holds the key,
§1 "The `vision/` folder". The captures tab only shows
the folder — the inspector writes into it because its own
`client_save_img: "../captures/tube_od/"` says so.)
`workspace/project_dirs.py` is the one place that reads both
(`project_paths`, `project_folders`); the runtime server writes records
and replays through it, the display and the scene builder's Replay panel
find replays through it, and the orchestrator's browser lists, guards
and serves through it. Nothing else may hardcode `runs/`, `rec/`,
`data/` or a captures path again. A malformed `folders:` fails the launch
with the entry and the field that is wrong.

The operator reaches them from the workspace page:

* **Files** in the top bar — the browser, opened on the FIRST tab of
  `folders:`. A run folder's `records.csv` opens as a table in the panel,
  an image shows fitted in the same pane, any file downloads and any
  folder downloads as a zip.
* **Open**, next to **Load** in the Parameters modal — the same panel
  in *pick* mode over the `uploads:` folder (whether or not it is a tab),
  for choosing a parameter file that is already on the bench.
* **Open** on any `type: file` parameter — pick that field's file from
  the `uploads:` folder instead of uploading it again.

Uploading, creating a folder and deleting are available in every folder
not marked `read_only` (a read-only folder shows a "read-only" tag and
no Upload, New folder or Delete — and the server refuses them anyway);
delete takes a confirm, and refuses a folder that still has anything in it, so
a run's records cannot go in one click. Every path from the browser is
checked against its root by `fslive.safe_join` before it reaches the
filesystem — a request that climbs out of its folder is refused,
symlinks included.

The browser is live. `gui/orchestrator/fslive.py` carries it, and the
vision server's file manager runs a byte-identical copy
(`dorna_vision/server/fslive.py`) — change one, copy it to the other:

* **Listing, mkdir and delete go over one WebSocket**
  (`/orchestrator/ws/files/<workspace>`). The open folder is watched
  with Linux inotify, so a file a run writes appears in the panel on
  its own, ~150 ms later; bursts collapse into one push. Nothing polls:
  an idle open panel costs the Pi nothing measurable.
* **Bytes go over HTTP, streamed.** Download and zip stream in 64 KB
  chunks; upload is a raw `PUT …/files/<root>/upload?path=&name=`
  written straight to disk (`.name.part`, renamed when complete). Memory
  stays flat whatever the file size — the server never holds a file.
* **Filesystem work runs off the event loop** (a two-thread pool), so a
  large zip never stalls the pendant or the device bus.
* A remote workspace's socket and bytes are relayed to the node that has
  the files.

## 4. Recipes — `recipes.yaml` or `recipes.j2`

Maps human-readable aliases (like `gripper`, `pipette`) to recipe classes with their configuration. A recipe knows how to pick, place, dose, etc. using a specific component from the scene. You write the alias once here and use it everywhere in your states.

Supports both `.yaml` and `.j2` (Jinja2 template). If `recipes.j2` exists, it's rendered first. Use `.j2` to define shared variables like `speed_factor` — change one value, every recipe gets it.

```yaml
{% set speed_factor = 1 %}
{% set base_distance = 200 %}

gripper:
  class: workspace.components.gripper.Gripper
  kwargs:
    component: gripper_1
    left_approach: true
    speed_factor: {{ speed_factor }}

pipette:
  class: workspace.components.pipetting_site.DosingSite
  kwargs:
    component: pipette_1
    base_distance: {{ base_distance }}
    speed_factor: {{ speed_factor }}

tube_rack:
  class: workspace.components.rack.Rack
  kwargs:
    component: tube_rack_50ml_1
    base_distance: {{ base_distance }}
    speed_factor: {{ speed_factor }}
```

Each recipe entry has:
- `class` — full Python import path to the recipe class
- `kwargs.component` — required, matches a name from `scene/base.j2`
- Everything else in `kwargs` is recipe-specific and passed to the constructor

The loader creates each recipe as: `cls(workspace, core, component, **kwargs)`

Access in states: `self.rcp["gripper"].pick(i)`

---

## 5. Protocol — `protocol.yaml` or `protocol.j2`

Defines the workflow states with dependencies, tool assignments, and checks. The OR-Tools scheduler reads this to figure out the optimal execution order. You don't write the scheduling logic — you just declare "dosed requires picked" and the solver handles the rest.

Also supports `.j2` format (same as recipes).

```yaml
states:
  picked:
    duration: 8
    requires: []
    tool: gripper
    post_check: tube_picked

  dosed:
    duration: 15
    requires: [picked]
    tool: pipette
    pre_check: tube_in_rack

  placed:
    duration: 6
    requires: [dosed]
    tool: gripper

  shaken:
    duration: 5
    requires: [placed]
    background: true

  shutdown:
    trigger: park

goal: [placed]

tool_swap_duration: 10
```

Each key under `states` is the state name — must match a key in `states.py` `make()`.

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `duration` | No | Estimated seconds (used by scheduler, default: 1) |
| `requires` | No | List of state names that must complete first |
| `tool` | No | Which tool the robot holds. Auto-swaps via tool rack between states. <br>• **not set** — keep current tool, no swap <br>• **`tool: gripper`** — swap to named tool <br>• **`tool: null`** — return current tool, run bare |
| `tool_swap_duration` | No | Per-state override (seconds) of the global `tool_swap_duration`. Represents the gap inserted *before* this state when transitioning from a different tool. Falls back to the top-level value if unset |
| `background` | No | `true` = runs in parallel, completes all items at once (default: `false`) |
| `pre_check` | No | Check name or list — runs **before** the tool swap and state handler. If it fails, the state is skipped entirely (no tool swap happens). Must match a key in `checks.py` `make()` |
| `post_check` | No | Check name or list — runs **after** the state handler completes. Must match a key in `checks.py` `make()` |
| `trigger` | No | `"park"` — state is not scheduled, only runs on Park signal. <br>• A trigger state is **a normal state with a different invocation point** — it goes through the same execution path (`_execute_state` in `runner.py`) as scheduled states, so every state-level field above (`tool`, `pre_check`, `post_check`, etc., plus any future additions) applies naturally <br>• When Park is pressed, the current state finishes, then this trigger runs before the process exits <br>• `tool:` on the trigger is the **authoritative final tool state** — no auto-release runs after <br>• If `trigger: park` is **not** defined, Park simply exits after the current state finishes — tools are **not** auto-released. To release the held tool on Park, define a `trigger: park` state with `tool: null` <br>• Scheduling-only fields (`requires`, `duration`, `tool_swap_duration`, `background`) are ignored on triggers since they're not part of the OR-tools plan <br>• Use it for cleanup (return tools, home robot, safe position) |

### Goal

The `goal` list defines which states mark the protocol as done. Goal names must be states defined in the `states` section of the same YAML file.

- A state is "completed" when its handler returns without raising an exception — no return value is checked
- The protocol finishes when every goal state has run for every item in the batch
- If `batch_size` is not set, it defaults to 1 — each state runs once, like a simple sequence
- If your project processes multiple items (e.g. tubes, vials), pass `batch_size` via `launch.yaml` kwargs — each state runs once per item (index 0 to n-1)

### Tool swap duration

`tool_swap_duration` (top-level, optional) — estimated seconds to swap between tools (default: `0`). When set, the scheduler adds this as a penalty between consecutive tasks that use different tools, so it naturally batches same-tool work together to minimize total time.

Per-state overrides are also supported — add `tool_swap_duration: N` to any individual state to override the global value. The override represents the cost of swapping **into** that state's tool, so when transitioning A→B the gap is B's value, and B→A uses A's value.

---

## 6. States — `states.py`

Implements what the robot actually does for each state defined in `protocol.yaml`.

### Structure

- **`__init__(self, rcp, rt, **kwargs)`** — called once when the workflow starts
  - `rcp` — recipe dict from `recipes.yaml` (e.g. `rcp["gripper"]`)
  - `rt` — runtime object (for `rt.call()`, `rt.step()`, `rt.sleep()`)
  - `**kwargs` — all kwargs from `launch.yaml`, access via `kwargs.get("my_param", default)`
- **State handlers** — one method per state, each must accept `i`
  - `i` is the item index (`0` to `batch_size - 1`), passed by the runner
  - The runner calls your handler once per item — you don't loop yourself
  - You must accept `i` even if you don't use it
  - Background states always receive `i=0` and run once for all items
- **`register(self, runner)`** — framework-reserved hook. Called once by `BaseWorkflow` at workflow startup. Bind each handler to its protocol-state name with `runner.register_state(name, fn)`. Names must match the keys in `protocol.yaml`.

### Example

```python
class States:
    def __init__(self, rcp, rt, **kwargs):
        self.rcp = rcp
        self.rt  = rt
        # Access any kwarg from launch.yaml — these are just examples:
        # self.dry_run = kwargs.get("dry_run", False)
        # self.speed = kwargs.get("speed", 100)

    def picked(self, i):
        """Pick tube i from the rack."""
        self.rcp["gripper"].pick(i)

    def dosed(self, i):
        """Dose 40ml into tube i."""
        self.rcp["pipette"].dose(volume=40)

    def placed(self, i):
        """Place tube i into output rack."""
        self.rcp["gripper"].place(i)

    def homed(self, i):
        """Home the robot — same action regardless of i."""
        self.rcp["robot"].home()

    def register(self, runner):
        runner.register_state("picked", self.picked)
        runner.register_state("dosed",  self.dosed)
        runner.register_state("placed", self.placed)
        runner.register_state("homed",  self.homed)
```

---

## 7. Checks — `checks.py`

Verification functions that run before/after states. Same signature as States — receives `rcp`, `rt`, and all `**kwargs`.

### Structure

- **`__init__(self, rcp, rt, **kwargs)`** — same as States, has access to recipes, runtime, and all kwargs
- **Check methods** — each accepts `i` (item index) and returns `True` (passed) or `False` (failed)
- **`register(self, runner)`** — framework-reserved hook. Bind each check to its name with `runner.register_check(name, fn)`. Names are referenced in `pre_check` / `post_check` in `protocol.yaml`.

### Example

```python
class Checks:
    def __init__(self, rcp, rt, **kwargs):
        self.rcp = rcp
        self.rt  = rt

    def tube_in_rack(self, i):
        return True

    def tube_picked(self, i):
        ok = self.rcp["gripper"].has_object()
        if not ok:
            self.rt.step(f"Tube {i} pick failed — fix and resume", level="warning")
            self.rt.pause()   # wait for operator, then skip
        return ok

    def register(self, runner):
        runner.register_check("tube_in_rack", self.tube_in_rack)
        runner.register_check("tube_picked",  self.tube_picked)
```

```yaml
# protocol.yaml — references the check names registered above
states:
  picked:
    pre_check: tube_in_rack
    post_check: tube_picked
```

- On `False`, the task is skipped and the runner moves to the next task
- Use `self.rt.step()` inside the check to show a message to the operator
- Use `self.rt.pause()` if you want to pause and wait for the operator before skipping
- Checks are optional — states without `pre_check`/`post_check` just run directly

---

## 8. Runtime API

### Logging steps

```python
rt.step("Picking tube 3")                          # info — appears in timeline
rt.step("All tubes placed", level="success")        # success — green dot
rt.step("Tube misaligned", level="warning")          # warning — amber banner
rt.step("Robot alarm", level="error")                # error — red banner + beep
rt.step(45, level="progress")                        # progress bar (0-100)
```

| Level | Timeline | Banner | Sound | Progress bar |
|-------|----------|--------|-------|-------------|
| `info` | Blue dot | — | — | — |
| `success` | Green dot | — | — | — |
| `warning` | Amber dot | Amber banner | — | — |
| `error` | Red dot | Red pulsing banner | Beep + notification | — |
| `progress` | — | — | — | Updates bar |

### Robot commands

In States, call recipe methods directly — recipes handle robot commands and checkpoints internally:

```python
self.rcp["gripper"].pick(i)
```

If you need to call a raw robot command outside of a recipe, call it directly on `rt` — it proxies any method to `robot_api` and wraps it with checkpoint and alarm handling automatically:

```python
self.rt.jmove(j0=0, j1=0, j2=0, j3=0, j4=0, j5=0)
```

If the robot returns a negative value (alarm), `rt` will automatically log an error, pause, and wait for the operator to clear and resume.

### Sleep

```python
rt.sleep(5.0)  # Interruptible — responds to pause/kill
```

### Pause gate

```python
rt.checkpoint()  # Blocks if paused, raises KillRequested if killed
```

#### The one rule

> **Observability never blocks. Work always checkpoints.**

Anything you call on `rt.*` that *does work* observes pause. Anything that
just *records* or *reads* state doesn't. This single rule covers every
runtime method without exception.

#### What is and isn't pause-aware

| Category | Methods | Pause-aware? |
|---|---|---|
| **Waiting** | `rt.sleep(s)`, `rt.delay(s)` | ✅ |
| **Robot / tool work via runtime** | `rt.<robot_method>(...)` — `rt.motor(1)`, `rt.jmove(...)`, `rt.lmove(...)`, `rt.cmove(...)`, any tool/IO method exposed by the robot api | ✅ |
| **Explicit checkpoint** | `rt.checkpoint()`, `rt.call(fn)` | ✅ |
| **Observability** | `rt.step(label, level)` — for every level: `info`, `success`, `warning`, `error`, `progress` | ❌ |
| **Runtime state reads** | `rt.status()`, `rt.state`, `rt.step_info` | ❌ |
| **Runtime control** | `rt.pause()`, `rt.resume()`, `rt.kill()`, `rt.start()`, `rt.park()` | ❌ |
| **Component ops from a recipe** | `self.component.bar()` — the recipe's component is behind a gate (`workspace/recipes/gated.py`): every op settles a held motion tail and checkpoints first; `@pure` helpers skip it | ✅ |
| **Direct driver / core calls** | `core.dorna.x()`, `self.driver.cmd()` | ❌ |

#### How robot calls inherit pause-awareness for free

The Runtime's `__getattr__` automatically wraps every `rt.<some_robot_method>(...)`
call through `self.call(...)`, which checkpoints before running the underlying
robot method. So you don't have to mark each robot method as pause-aware — it
inherits the property the moment you reach it via `rt.*`.

The corollary is equally important: **anything you call without going
through `rt.*` bypasses the gate.** Recipe calls (`rcp["x"].read()`), direct
component calls (`self.component.foo()`), raw driver SCPI commands — none of
those check the pause flag. That's deliberate: data and I/O calls don't
impose timing semantics.

#### When to call `rt.checkpoint()` explicitly

You almost never need to. The pause flag is observed naturally on the
next `rt.sleep` / `rt.delay` / `rt.<robot>` call your action or recipe
makes. Explicit `rt.checkpoint()` is only useful when:

- Your action runs a long pure-computation loop (uncommon — workflows
  are I/O-bound, not CPU-bound).
- You want a guaranteed pause point between two non-pause-aware calls
  without any incidental waiting.

#### What this means for action authors

When you write an action, ask: *"When the operator clicks Pause, where
will my code stop?"*

- If the action has `rt.sleep(...)` between sensor reads → pause is
  observed there. Common case.
- If the action only does `rt.step` + recipe reads (no sleep, no robot
  call) → pause is **not** observed inside this action; the next
  action's pause-aware call picks it up.
- If the action runs a tight loop without any pause-aware call →
  add `rt.checkpoint()` once per iteration.

Most actions get pause behavior automatically because they call
`rt.<robot>(...)` for motion or `rt.sleep(...)` for timing. Nothing
extra to wire.

Note: `checkpoint()` does **not** raise `ParkRequested` — Park is observed
between states, not mid-state. See [§9 Pause / Park / Kill](#pause--park--kill--runtime-control-semantics).

### Device reads + declarative retry

A device read (`rcp["scale"].weight()`, `rcp["meter"].read_resistance()`,
`rcp["inspector"].detect()`) can fail mid-run — the instrument drops, the
TCP link stalls, the camera server hiccups. The platform handles this in
two cooperating-but-independent layers. Understand both; the second is the
one you write.

#### Layer 1 — who pauses the robot (bus-driven, automatic)

When a **`critical: true`, non-sim** device goes `down`, the **orchestrator**
pauses the runtime. The chain is entirely bus-driven and has *nothing to
do with your action*:

```
read fails → station._set_state("down") → adapter publishes device/<id>/state=down
          → MQTTOrchestrator sees critical+down → runtime.pause()
```

So the robot pauses whether or not your action notices the failed read.
The pause lands at the **next pause-aware call** (`rt.<robot>`, `rt.sleep`)
— the device read itself isn't a checkpoint (it bypasses the gate, per the
table above), so an in-flight read finishes, then the next motion holds.
This pause does **not** fire when the device is sim (publisher-sim or the
project claims sim) — there's no real failure to react to.

You don't write any of this. Set `critical: true` on the device and it
happens.

#### Layer 2 — re-doing the read after recovery (declarative, you write it)

Pausing stops the robot, but the *reading was never captured*. To redo
**just the read** — without repeating the motions around it — make the
read its own action and let the **planner** retry it. Don't write a retry
loop or reach for `with_retry`; encode it in pre/eff so retry falls out of
the plan:

**The three rules:**

1. **Make the read its own action**, separate from the motions. Don't
   bundle place + read + pick into one action — then a failed read forces
   you to redo the arm moves. Split them: `PlaceOnScale` / `Weigh` (read
   only) / `PickFromScale`.

2. **Assert the success fact only on success.** The read action's `eff`
   adds `weighed(item)`; its `execute` returns the eff branch **only when
   it got a value**, and returns **`False`** otherwise:

   ```python
   class Weigh(Action):           # pure read, no motion
       params = ["tube"]
       resource = "robot"
       def pre(self, tube):  return on_scale(tube) & ~weighed(tube)
       def eff(self, tube):  return {"weighed": (+weighed(tube),)}
       def execute(self, tube):
           grams = self.ctx.recipes["scale"].weight()
           if grams is None:
               return False        # FAIL — weighed(tube) NOT asserted
           return "weighed"        # success → weighed(tube) becomes true
   ```

   Returning `False` fails the leaf and **applies no effect** (BT contract:
   `execute` returns the eff-branch string on success, `False` on failure
   — never `None`).

3. **Let the existing replan do the retry.** The launcher already wraps the
   body in `replan_on_failure`, so a failed leaf rebuilds the plan from the
   **observed world**. There, `on_scale(item) & ~weighed(item)` is still
   true (the item never left the pan), so the planner **re-selects the read
   action**. The retry *is* the plan — no loop, no special case.

Putting the layers together, end to end:

```
Weigh runs → weight() returns None → return False  → leaf FAILS, weighed(t) not set
  (meanwhile, if critical+real: bus down → orchestrator paused the runtime)
operator/AutoRecover reconnects → resume
engine replans from observed state → on_scale(t) & ~weighed(t) still holds
  → Weigh re-selected → weight() succeeds → weighed(t) set → flow continues
```

The tube stayed on the pan the whole time (`on_scale` held), so **only the
read was retried — no motion repeated.** This same shape works for *any*
device read: keep it its own action, assert the fact only on success,
return `False` otherwise. The reference implementation is
`examples/scale/actions.py` (`PlaceOnScale` / `Weigh` /
`PickFromScale`).

#### `resource` on a read-only action — it's the scheduling lock, not the device

Tempting question: a read-only `Weigh` touches the *scale*, so shouldn't
its `resource` be `"scale"`, not `"robot"`? **No — `resource` is the
mutual-exclusion lock the scheduler uses to decide what may run
concurrently, not "which device this action reads."** Pick it by asking:
**during this action, is the robot free to do other work, or committed?**

The scheduler interleaves actions on *different* resources and on
*different* items freely — and it has **no model of "the robot stays put
while the gripper is open at the pan."** So if `Weigh(tube N)` declared
`resource="scale"`, the scheduler would consider the robot free during the
read and could slot `PlaceOnScale(tube N+1)` into that window — sending the
arm off while tube N sits ungripped on the pan, which `PickFromScale(tube
N)` must still return to. Nothing validates that away; the schedule is
"valid" by the scheduler's rules and physically wrong.

In the weigh flow the robot is **committed** to the tube for the whole
`PlaceOnScale → Weigh → PickFromScale` sequence (released on the pan, must
re-grip the same tube), so `resource="robot"` is correct — it keeps that
per-tube sequence serial against all other robot work. The only cost is a
short idle block on the robot timeline during the read, which is honest:
the arm genuinely is occupied.

Use a **device resource** (e.g. `resource="shaker_1"`, cf. sample_prep's
`ShakerOne`/`ShakerTwo`) **only when the robot is genuinely free during the
operation** — load the item, leave, come back later (a shaker runs
autonomously for minutes). That unlocks real parallelism: weigh/shake one
item while the arm works another. A ~instant, hands-on read where the arm
never leaves is *not* that case. Rule of thumb: **device-as-resource when
the robot leaves; `"robot"` when the robot waits.**

> **Why not `with_retry`?** `with_retry` re-runs the *same leaf* blindly N
> times. The declarative approach replans from the real world, so it
> naturally composes with pause/recover (it waits for the device to come
> back), with windowed slicing, and with anything else the planner knows.
> Reach for `with_retry` only for a transient that needs an immediate
> in-place retry with no world change; for "redo this until its goal-fact
> holds," use pre/eff.

### Single-occupancy resources — the gripper holds one item

A subtle planning trap, and one almost every multi-item protocol hits.
The planner is free to reorder actions whose preconditions are
independently satisfiable. If each action only references **its own
item's** facts — `Pick`'s pre is `started() & ~picked(t)` — then `Pick(0)`,
`Pick(1)`, `Pick(2)` are all independently applicable, and the planner
will happily schedule **all the picks first**, then all the places. That's
physically impossible: there's one gripper, it holds one item.

The symptom is a schedule like
`Pick(3) Pick(0) Pick(1) Pick(2) PlaceOnScale(0) PlaceOnScale(2) …` —
batched by action across items instead of one item completed end-to-end.

The fix is to model the **capacity-1 physical resources** as facts the
planner must respect — most commonly the **gripper** (`hand_empty`), and
any fixture an item rests on exclusively (a scale pan `pan_empty`, a single
inspection nest, etc.). A capacity-1 resource is a **no-arg fact** that's
*consumed* when the slot fills and *restored* when it empties:

```python
hand_empty = predicate("hand_empty")     # gripper holds no item

class Start(Action):
    def eff(self): return {"started": (+started(), +hand_empty())}   # seed it

class Pick(Action):
    def pre(self, t):  return started() & hand_empty() & ~picked(t)
    def eff(self, t):  return {"picked": (+picked(t), -hand_empty())} # hand now full

class Place(Action):
    def pre(self, t):  return off_scale(t) & ~placed(t)
    def eff(self, t):  return {"placed": (+placed(t), +hand_empty())} # hand frees
```

Now `Pick(1)` can't be scheduled until whatever filled the hand has
emptied it (a `Place` or a hand-off), so the planner is forced to finish
one item's hand-occupancy before starting the next. Use `+fact` to
restore, `-fact` to consume (the `eff` branch is a tuple of these). Seed
the resource's initial state in `Start.eff`.

Rule: **for every physical slot that holds at most one item — the gripper,
a pan, a nest, a single-tube fixture — add a no-arg `_empty` fact,
consume it on fill, restore it on empty.** The reference implementation is
`examples/scale/actions.py` (`hand_empty` + `pan_empty`). Without
it, single-item protocols look fine in small batches by luck and produce
impossible schedules as soon as the planner finds the reordering.

#### Always flag them `capacity=True`

```python
hand_empty = predicate("hand_empty", capacity=True)
pan_empty  = predicate("pan_empty",  capacity=True)
```

Declaring the fact is only half the job. A capacity fact is **shared**
— the same `hand_empty` toggles as *every* item passes through the
gripper — whereas an ordinary fact like `weighed(t)` belongs to one
item. The scheduler derives its ordering constraints from "which
earlier action last set this fact", and for a shared fact that answer
is whichever item the plan's own linearization happened to touch last.
The result is a chain welding every item's actions into one serial
sequence.

You only *see* the damage when an item **revisits a tool** — puts the
gripper down for another tool and comes back to it later (bd's re-cap
chain: gripper → pipettor → gripper). Batching then requires
interleaving items, which the weld forbids, and a batch that used to
run "all the decaps, then all the doses" collapses into strict
one-item-at-a-time with a tool change each way. On a 4-item bd batch
that was 10 tool swaps instead of 4.

`capacity=True` keeps the mutual exclusion (two items still can never
share the slot — it becomes a scheduler mutex constraint instead of an
ordering edge) while letting the solver interleave items to cluster by
tool. It is never *wrong* to omit — the schedule stays correct, just
needlessly serial — and the framework only pays the extra solve cost
on protocols where an item actually revisits a tool, so there is no
reason not to flag every predicate of this shape. Full rationale in
`workspace/bt/dsl.py`'s module docstring ("Capacity facts").

---

## 9. Running a project

### From the orchestrator GUI

1. Open `http://<ip>:5000/orchestrator/`
2. Click **+ Add Workspace**
3. Set name, port, path to `main.py`
4. Click the **gear icon** → set parameters → **Set**
5. Click **Launch** → **Start**
6. To park gracefully: **Park** (finishes current action → runs shutdown → exits)
7. To take items out of a running batch: **Pause**, then **Replan** (choose the items → the actions tied to them stop, their 3D models are cleared → clear them from the bench → Resume; the run carries on without them) — bt-framework-guide §8.6
8. To emergency halt: **Kill** (instant stop, may leave robot in dirty state)

### From the command line

```bash
sudo python3 projects/my_project/main.py --port 5010
```

Then open `http://<ip>:5010` for the 3D viewer, or use the orchestrator to send start/pause/kill commands.

### Autoreload is off

The runtime server does not restart itself when a source file changes.
That is a dev convenience with a sharp edge: it re-execs the process on
any write to an imported `.py` — a `git pull` into the project, an edit
in the library — silently and mid-run, run state lost. Turn it on only
on a dev box with `WORKSPACE_AUTORELOAD=1` in the environment of
`main.py`; the log then says so at boot.

### Recording a run — the replay recorder

The 3D viewer's record button (red while capturing) drives a recorder
that lives in the WORKSPACE PROCESS, not the page: it writes every
scene update to `<replays>/rec_2026-09-12_15-35-17.jsonl` (launch.yaml `replays:`), the file the
scene builder's Replay tab scrubs. Because it is server-side:

* closing or refreshing the page does not stop it — the viewer asks
  `/record/status` on load and lights the button if a recording is on;
* it **stops by itself when the run ends** — done, error or killed.
  This hangs off the runtime's ``on_run_end`` hook, fired at the exact
  moment the runtime stamps ``run_finished_at`` (``on_run_start`` is its
  twin at ``run_started_at``); a recorder armed on an idle bench stays
  armed until the run it was waiting for finishes;
* frames are COMPACT deltas — the pose (and joints) of what moved,
  the full spec only for a solid added mid-run — about 150 bytes each;
  the Replay tab loads any length: the builder streams the file,
  thins it to a chosen playback rate (10 fps default, merging dropped
  frames so no solid's last position is lost) and sends it as a
  columnar binary — one typed array of times and one of poses per
  solid (``gui/scene_builder/web/replay_format.js``). A 7-hour run is
  ~35 MB on the wire where JSON frames were 400 MB;
* the file is flushed every 25 lines and closed at process exit;
* **the run's schedule is recorded beside the frames** — every plan
  slice and every action / swap start and end, on the recording's
  clock (``{"t", "ev"}`` lines; a recording started mid-run first
  writes the schedule so far, with negative times). The scene builder's
  Replay tab turns them into **phase chapters under the slider**: one
  segment per phase, click to jump, the current phase named beside the
  time.

`GET /record/status`, `POST /record/start|stop` are the endpoints.


The runtime exposes four control signals. Each interacts differently with the currently executing state:

| Signal | When it takes effect | What runs after | Use when |
|---|---|---|---|
| **Pause** | At the next pause-aware call (`rt.sleep` / `rt.delay` / `rt.<robot>` / `rt.checkpoint`) — see [§8 Pause gate](#pause-gate) for the full list | Blocks until you Resume — state continues from where it stopped | You want to inspect, intervene, or wait |
| **Park** | **Between states** — current state runs to completion first | If `trigger: park` is defined → that trigger runs and is the authoritative final cleanup. Otherwise → exit immediately, tools stay where they are | Graceful shutdown — the safe default |
| **Replan** | **Only inside Pause** — applied once every action in flight stands at a checkpoint; nothing is halted | The actions tied to the chosen items are dropped, the items leave the plan and their 3D models the scene; on Resume the untied paused actions finish, then the new plan runs | An item must leave the batch (dropped, broken, bad) — bt-framework-guide §8.6 |
| **Kill** | At the next pause-aware call (same set as Pause) | Nothing — process exits immediately, no cleanup | Emergency halt only — may leave robot/tools in a dirty state |

**Why Park is "between states", not mid-state:** many states perform multi-step atomic operations (most importantly tool swaps: `place(old)` then `pick(new)`). Interrupting between those steps would leave the robot in an inconsistent state — e.g. tool placed back but the next pick never happened, while the runtime still thinks a tool is held. Park therefore lets the current state finish, then exits cleanly between states.

If you need to stop *immediately* and accept the consequences, use Kill.

#### What triggers Pause

Five distinct sources can transition the runtime into PAUSED. The
runtime treats them identically — once `paused == True`, the next
pause-aware call blocks regardless of source. The differences are
purely in **who set the flag** and **how the operator should respond**.

| # | Trigger | Set by | Operator action to resume | Code path |
|---|---|---|---|---|
| 1 | **Operator clicks Pause** in the dashboard or pendant | The user, via UI | Click Resume when ready | WS cmd `pause` → `runtime_server.py:184` → `rt.pause()` |
| 2 | **Critical device goes down on the bus** (USB unplug, TCP drop, daemon crash, etc.) | Auto — by `MQTTOrchestrator` watching `device/+/state` topics | Fix the hardware → click Recover on the device row → wait for state=ok → click Resume | `devices/orchestrator.py:351` |
| 3 | **Robot motion command returns an alarm code** (negative int from a `rt.<robot>` call — limit hit, IK failed, E-stop pressed) | Auto — by `rt.call` itself when a wrapped robot method returns < 0 | Fix the cause → **Disable Alarm** (Operator Controls; the robot's device row and modal offer the same button instead of Recover, which cannot clear an alarm) → click Resume | `runtime.py:532` (inside `rt.call`) |
| 4 | **Project code calls `rt.pause()`** directly (custom checks, action policy, "I want to wait for the operator here") | Your code | Whatever the project documents — usually Resume after handling the situation | Anywhere a `Check` / action / recipe calls `rt.pause()` |
| 5 | **An action raises** — an exception escapes `pre()`, the tool swap, a check or `execute()` (a dead robot link, a device that refused, a bug) | Auto — by the BT leaf, which puts the error on the timeline at level `error` (red banner, beep) before it pauses | Read the error, fix the cause (Recover the device), click Resume — the engine replans from observed state and the action runs again; or Park / Kill | `bt/dsl.py` (`_DSLActionLeaf._execute_body` → `_pause_on_raise`) |

Trigger 2 (device-down auto-pause) has additional gates: it fires only
when the device is **critical**, **not sim**, and either entering `down`
from its last *settled* state or first-observed-down — an AutoRecover
retry's `recovering → down` is the same outage, not a new edge, so an
outage pauses once however many retries it takes. Sim devices and
project-claimed-sim devices never auto-pause.

Trigger 5 is why a run never spins: with the facts unchanged, a replan
reproduces the plan and the same action meets the same error — on the
bna bench (2026-09-24) 8–14 replans a second, as fast as the plan could
be rebuilt, until the no-progress cap ended the run INVALID. A raise now waits for the operator instead;
`return False` keeps its meaning (this attempt failed — replan, no
pause; bt-framework-guide "execute() returns False"). See [device-guide.md §1 rule 4](device-guide.md)
and [§16 simulation model](device-guide.md) for the full claim
aggregation logic.

#### Pause atomicity — entry, not middle

A pause is **observed at the entry to a pause-aware call**, not in the
middle of one. The atomic rule is:

> When the runtime is paused, the **next** pause-aware call your code
> reaches blocks until Resume. Work already in progress when the pause
> flag was set runs to completion, and the next call is where the
> operator sees the freeze.

Why this matters: a robot mid-motion can't be interrupted safely.
Pause therefore happens at the **boundary** before the next call, never
mid-execution. If you need an instant freeze accepting the
consequences, use **Kill** instead.

#### Start and Resume turn the motors on

The operator's **Start** and **Resume** presses mean "the robot may move
now", so the runtime server turns the robot's motors on first and waits
`Runtime.MOTOR_SETTLE_S` (0.5 s) for the drives before the run starts or
continues (`Runtime.motors_on`). An operator who switched the motors off
to move the arm by hand — a Replan's clearing, a jam — gets them back
with the press that says "go"; nothing else ever turns them on (Pause,
Replan and Park never do). If the robot refuses (in alarm) or is
unreachable, a warning goes on the timeline and the press goes on: the
first motion fails and pauses with its cause. Only those two presses do
it — a notebook or a dry run calling `rt.start()` does not. Already on:
a no-op plus the settle. Sim: it reaches the simulator.

#### Resume semantics — work runs, nothing is skipped

After Resume, the call that was blocked **runs its work** — it isn't
skipped or aborted. The internal shape of every pause-aware call is:

```
1. Enter the call → internal checkpoint()
2. Checkpoint blocks while paused
3. Operator clicks Resume → checkpoint returns
4. The actual work runs (robot move / IO write / sleep tick / …)
5. Call returns
```

So `rt.jmove(...)` that was paused at entry will execute the move once
Resume fires. No silent drops.

**One subtle case: `rt.sleep(seconds)`.** Sleep uses wall-clock
end-time, not "N seconds of unpaused time." If you pause during a
`rt.sleep(10)` and resume after the original 10 s window has elapsed,
sleep **returns immediately** on Resume (the conceptual wait already
passed). Useful for "wait until ~T seconds from now" semantics; if you
need "exactly 10 unpaused seconds," compose with `rt.checkpoint()` and
a custom loop. For typical workflows the wall-clock behavior is what
you want — by the time the operator resumes, the wait is done.

#### Single mental model

> **Pause sets a flag. The next pause-aware call observes it and
> blocks. Resume releases the block; the work runs after Resume. Mid-
> call work is never interrupted — pauses happen at boundaries.**

That one paragraph covers the contract for all four trigger sources.


## 10. Validating a project — the toolchain

Three commands take a project from "scene finished" to "bench-ready"
without guessing, each answering one question and naming its failures.
The step-by-step pipeline that strings them together (who owns which
step, where an error class lives) is the `bootstrap-project` skill;
this section is the reference for the tools themselves. All are `-m`
module runs, so the `cd` is part of the command — from anywhere else
they fail with `ImportError: cannot import name 'Workspace'`. Always
in sim — they force
`simulation: true` on every device, so real hardware is never touched
(or fought over) by validation.

### 10.1 `workspace.recipes.solve` — recipe parameters + geometry

    cd ~/Downloads/workspace/workspace && sudo python3 -m workspace.recipes.solve <project_dir>
    cd ~/Downloads/workspace/workspace && sudo python3 -m workspace.recipes.solve <project_dir> --skeleton skeleton.yaml

Per station: boots the recipe with its declared `left_approach` /
`base_distance` (reference IK), sweeps both approaches × distances at
`rail_span: 1` when the declared values fail, and diagnoses total
failures geometrically (`UNREACHABLE — rail-frame x=941, rail ends at
801` is a bench-design error caught before any flow work).

Geometry is measured along each anchor's **approach ray** — its local
+z signed away from the bench, so tilted stations (a −48° feeder, a
hanging tool rack) are measured on their true axis, not world-vertical.
Boxes owned by the payload stack itself (the tube being entered, its
cap, their own collision boxes) are excluded by `componentName`. Two
numbers per station, both including a **hard 20 mm margin**:

| number | meaning |
|---|---|
| `min pad` | what any pick/place/immerse **hover padding** must reach (pick/place default 50, immerse default 10 — raise per call where the minimum is higher) |
| `min end` | how far above the payload any motion must **end** there — retract distances, exit heights. An arm stranded inside an inflated box poisons the *next* plan's start ("invalid start state") |

The margin is not negotiable: endpoints exactly on an inflated box
surface pass in sim and fail on real joints (measured — the retract
knife edge). Report-only; values are applied to `recipes.j2`
deliberately, with the evidence.

Sample output (bna) and how to read it:

    source_rack_1   la=False bd= 200 (declared)   min pad 31 / min end 31 above load ([305x186x123](collision_box_7) holds the ray to 112 @ A1; incl 20 margin)
    decapper        la=True  bd= 200 (declared)   min pad 122 / min end 132 above load ([88x88x166](collision_box_5) holds the ray to 112 @ place; incl 20 margin)
    scale           la=True  bd=  50 (swept)      ray clear (incl 20 margin)
    needle          UNREACHABLE — rail-frame x=941 y=-337, rail [-199, 801]   ** FIX SCENE **

Column by column:

- `la=... bd=... (declared)` — the recipe's own `left_approach` /
  `base_distance` verified as-is: your recipes.j2 needs no change.
- `(swept)` — the declared values FAILED reference IK; the shown pair
  is what the sweep found. Copy it into recipes.j2.
- `min pad 122` — every pick/place/immerse at this station must use
  hover `padding >= 122` (defaults: pick/place 50, immerse 10 — if
  the minimum exceeds the default, pass it per call).
- `min end 132 above load` — every motion must END at least 132 above
  the payload here: retract distances, exit heights. Lower and the
  arm is stranded inside a box → the NEXT plan dies with "invalid
  start state".
- `([88x88x166](collision_box_5) holds the ray to 112 @ place)` — the
  evidence: WHICH box constrains the approach ray, how far along the
  ray it reaches, at which probe anchor. If a number looks absurd,
  this names the box to inspect.
- `ray clear` — no box constrains this station; defaults suffice.
- `UNREACHABLE ... ** FIX SCENE **` — no (la, bd) solves the
  reference IK; the rail-frame x/y vs rail range says why. This is a
  bench-design decision (move the rack / station), not a parameter
  to tune — nothing downstream can fix it.

Numbers already include the 20 mm margin — use them as-is; do not add
your own on top.

### 10.2 `workspace.bt.replay` — the logic gate

    cd ~/Downloads/workspace/workspace && sudo python3 -m workspace.bt.replay <project_dir> --batch 1 4

The route lookup → precedence → capacity spans → CP-SAT schedule →
replay in **scheduled** order against the real `pre()`/`eff()`. Zero
precondition failures + goal reached, or the exact action and time
that broke. Pure logic — seconds, no workspace, no motion. Run after
any `actions.py` change, at batch 1 AND a multi-item batch: batch 1
catches wrongly-seeded facts, multi-item catches capacity and
interleaving mistakes. Schedules are derived, never authored — this is
what proves the derivation's inputs truthful.

**`--show` prints the staged sequence** — every action in scheduled
order with its start time, params, resources, the tool it holds, and
each tool swap the runtime will insert — so the staging can be read and
checked without a bench, before any motion:

    sudo python3 -m workspace.bt.replay <project_dir> --batch 1 --show

    ── batch 1 — scheduled order (t = start, s) ──
      t=     0  start()                       [robot]  5s
                  swap - -> gripper
      t=    15  pick(0)                       [robot / gripper]  10s
      …
      t=   316  shake1(0)                     [shaker]  300s

A failed precondition is marked ``PRE FALSE`` on its line and the
failure names the facts it is missing at that moment. A phased
project (``route: phases.py`` in launch.yaml) is replayed **phase by
phase, window by window, with the launcher's own phase code**
(``workspace/bt/phase.py``: ``current_phase`` / ``pick_window``), so the
listing is the order the live run takes; each window's schedule is
offset by the makespans before it. A route that does not close is
reported with the item, the step and the missing facts
(bt-framework-guide §13 "The route"). ``--show`` ends with a per-phase
table (windows, actions, plan and CP-SAT seconds) so a slow phase
names itself; ``REPLAY_TRACE=1`` prints one line per window as it is
planned.

**The same plan in the GUI.** The scene builder's fourth sidebar tab,
*Schedule*, plans the project at the builder's project path for a
chosen batch and draws it (``POST /scene-builder/api/schedule_preview``)
with the live Gantt's own renderer, marked PREVIEW, one band per
phase, and every tool swap as an orange bar carrying the tool's name
in front of the first action that needs it — the picture of the
terminal listing, no motion. The workspace page's Schedule tab has no
preview: it draws the run's real schedule. During a real run the chart carries a **now-marker**, a
vertical line through the running block that advances with elapsed
time over the planned duration.

### 10.3 `workspace.bt.dryrun` — optional machinery debug, NOT a gate

    cd ~/Downloads/workspace/workspace && sudo python3 -m workspace.bt.dryrun <project_dir> --batch 2

The standard software gates are solve (10.1) and replay (10.2) —
path judgment belongs to the operator on the bench (step 5), never
to a sim check. Reach for dryrun only when replay is green but the
bench dies deep in recipe/engine plumbing and the failure needs
reproducing off-bench: it runs the real protocol through the real
engine in sim (planning, scheduling, checks, BT leaves) with playback
stubbed, so a batch runs in minutes.

Both bt commands resolve operator kwargs from `launch.yaml` (`--batch`
lands on the first int kwarg; `--kw name=value` overrides any) and are
exit-coded for scripting.

### 10.4 Caches and scene-file ownership

- `core/ik.json` and `core/path.json` are stamped with a **scene
  fingerprint** and auto-discard on mismatch (`[cache] ik.json
  discarded — scene changed since it was built`). The old "delete
  path.json after geometry changes" ritual is obsolete; legacy
  unstamped files count as stale once.
- **The cache contract** (ik, path, traj, fold, the motion book), one
  rule for all of them: a value that carries measurement noise is never
  part of a key. The live start pose, the held item's flange pose and
  the chain's head point are matched within a tolerance against the
  rows stored under the key; every float in a key is rounded AND
  normalised (`-0.0` is `0.0`); j5 is keyed to one turn and re-carried
  onto the live winding on replay; a seam key snaps to a known seam
  within the book's own partner tolerance. Eviction at a cap is
  least-recently-used, so a row every run needs is never the one a
  full cache drops. In the fold cache the rule is structural: the key
  takes DECLARED inputs only, and every value read from the robot goes
  in a `measured` dict whose names must equal `Core.FOLD_MEASURED`
  (each with its tolerance) — put/get raise `ValueError` otherwise, so
  a new noisy input cannot reach the cache without being declared. Why every clause exists: apc bench, 2026-09-23 —
  keyed exactly, one third of the fold rows and a sixth of the IK rows
  were unrepeatable, and every cached certify scanned the whole file.
- The scene **builder owns `layout.j2`** and regenerates it wholesale.
  Hand-maintained scene content — consumable stock like caps in a
  feeder — lives in **`stock.j2`**, listed after `layout.j2` in
  `launch.yaml`'s scene list so the merge applies it on top and
  regeneration can never eat it.

### 10.5 When the viewer lags — the capture

The 3D viewer's motion comes from the Display's frame loop, which
shares one interpreter with the protocol and the simulated robot. A
stutter therefore has a few possible homes, and the platform carries a
detector for each — **off by default**, printing nothing and costing a
few comparisons per frame. To use them, flip the switch in the code and
restart: `LAG_LINES = True` in `workspace/lag.py` (the server side) and
`const LAG_LINES = true` in `gui/orchestrator/web/index.html` (the
browser side). Then run the project from a terminal, open the browser
console (F12), reproduce, and read:

| line | where | meaning |
|---|---|---|
| `[Display] frame loop late by N ms (...) — busy: BT: recipes/recipe.py:L fn` | the server's terminal | the frame loop could not run for N ms; `busy:` names the thread that had the interpreter and where it was (`workspace.lag.busy_threads`) |
| `[Display] the ack for a K-object frame arrived after N ms` | the server's terminal | the frame left on time; the viewer server was slow to take it, or this process too busy to read the answer |
| `[sim] lmove tick T ran N ms late — busy: …` then `[sim] lmove: n late tick(s), worst N ms …` | the server's terminal | the simulated robot's playback stalled, then jumped to catch up (its schedule is absolute); the first line names the culprit |
| `[gc] generation 2 collection took N ms` | the server's terminal | the garbage collector walked the whole heap; no thread is to blame |
| `[viewer] render stall N ms …` / `[viewer] applied K objects in N ms (a snapshot)` | the browser console | the page itself stalled; the line says how many meshes were still loading, so a startup stall reads as one |

A `busy:` naming workspace code is a fix in that code (release the
interpreter, move the work, make it cheaper); "no workspace thread was
busy" means the CPU went to another process on the Pi.

Fixed this way, and kept whether the switch is on or off: the scene,
recipes, planner and caches are moved out of the collector's reach when
the scene is built and when a run starts (`workspace.lag.freeze_heap`),
because a full collection over them froze the whole process for 60–120
ms several times a run, for nothing — they are never garbage.
