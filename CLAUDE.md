# Dorna Workspace — AI agent instructions

You're working in the **Dorna Workspace platform** — a Python SDK + Tornado
orchestrator + 3D viewer + device bus for robotic lab automation. Robots,
multimeters, cameras, racks, recipes, BT-planned workflows. The
operator-facing UI is at `workspace/gui/orchestrator/`.

## Read these FIRST

For any non-trivial task, start here. Don't grep docs blindly.

1. **`.claude/skills/README.md`** — task → skill index. Pick the skill that
   matches what you're about to do; read its SKILL.md before writing code.
2. **`docs/`** — canonical reference docs. Skills point at specific
   sections; only read more deeply if the skill says so.

The skill set covers the common contributor tasks:
adding a device (workspace-owned vs daemon-owned), writing a recipe,
writing a BT action, adding a physical component, authoring scene yaml,
enabling sim mode, operator recovery flows, debugging the device bus.

## Gold exemplars — READ the matching one BEFORE writing code

The example projects under `examples/` (top level, next to `docs/`) are
**curated, working, canonical reference code**; `projects_old/` keeps
retired real-bench projects for archaeology. Real production projects
live OUTSIDE this repo as standalone repos (e.g. `~/Downloads/projects/`
on the bench Pi). The examples are the source of truth for "how we do it here" —
match their structure, naming, and conventions exactly. Before writing
any of the following, OPEN and study the listed exemplar(s) first; do
not author from memory or first principles.

| Writing a… | Read first (gold) |
|---|---|
| **BT action / protocol** (`actions.py`, predicates, `setup`, pre/eff/execute, per-item + Start/Park) | `examples/runtime/actions.py`, plus `examples/feeder/actions.py` (simple) or `examples/capping/actions.py` (multi-action + progress) |
| **Device read with declarative retry** (read is its own action; assert the success fact only on a valid reading, `return False` otherwise; planner re-selects it after recover — no `with_retry`/loop) | `examples/scale/actions.py` (`PlaceOnScale`/`Weigh`/`PickFromScale`); the why is `docs/project-guide.md` §8 "Device reads + declarative retry" |
| **Runtime scene mutation** (`add_component`/`remove_component` paired with facts, the explicit-mutation rule) | `examples/runtime/` (whole project — the reference for this) |
| **Recipe wiring** (`recipes.j2`) | `examples/feeder/recipes.j2`, `examples/capping/recipes.j2` |
| **Scene yaml** (chassis + layout, attach hierarchy) | `examples/runtime/scene/core_500.j2` (chassis) + any example's `scene/layout.j2`; the true chassis template is `scenes/core/core_500.j2` |
| **Custom component** | the apc repo's `components/*.py` (standalone repo, `~/Downloads/projects/apc` on the bench Pi); library components under `workspace/components/` |
| **Project entry point** (`main.py`, `launch.yaml`, `checks.py`) | any example — `main.py` is byte-identical across all projects (copy verbatim); `launch.yaml` + `checks.py` follow the example shape |

Convention these encode (don't re-derive): `main.py` is canonical and
identical everywhere; `Start` / `Park` / `OperatorPark` keep the same
shape across projects (only the per-item predicate + tool + object key
vary); projects compose a `core_500.j2` chassis + a `layout.j2`. When in
doubt, copy the closest example and change the minimum.

## Always-on conventions

These are platform-wide; every skill assumes them. Don't re-derive.

- **Use `sudo python3` and `sudo pip3`** for any hardware-touching or
  install-modifying command. The user's group memberships rely on it.
- **Scene yaml uses explicit values, never commented optionals.** Write
  `port: ""` to mean "unset," not `# port: "..."`. Reading the yaml should
  never require uncommenting to learn the truth.
- **Commit style**: short imperative title with a topical prefix
  (`device-guide:`, `ui:`, `multi_meter:`, etc.), optional bullet body,
  always end with `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`
  when you wrote or assisted with the changes. Never amend or force-push
  without explicit instruction. Never `--no-verify`.
- **No backwards-compat hacks.** If something's unused, delete it. No
  `# removed: …` comments, no dead aliases, no "kept for back-compat
  until we migrate" cruft unless the back-compat path is genuinely
  load-bearing AND documented (the WS multiplex's legacy endpoints are
  the rare exception — see `docs/internal/ws-multiplexing-plan.md`).
- **Sim is orthogonal to connection state.** Bus dot reflects hardware
  truth; SIM pill reflects operator intent. Both visible, never
  conflated. `docs/device-guide.md` §16.
- **Observability never blocks. Work always checkpoints.** `rt.step` and
  friends never pause; `rt.sleep` / `rt.delay` / `rt.<robot>` /
  `rt.checkpoint` always do. `docs/project-guide.md` §8.
- **The component owns atomic ops. The recipe owns workflows.** Test: "Could
  the operator press one button?" → component. `docs/component-guide.md` §7.

## What this repo is NOT

- Not a generic Python project — assumes a Dorna robot bench setup.
- Not a library you import — it's a platform you launch (the orchestrator
  Tornado server is the main entrypoint).
- Not multi-tenant — one orchestrator manages one bench's worth of
  workspaces.

## Workflow expectations

- Before sweeping changes across the platform: read the skill that
  matches your task, then check the canonical doc section it points to.
- After non-trivial changes, run a syntax check on touched files
  (`python3 -c "import ast; ast.parse(open(...).read())"`) and make sure
  any cross-references you might have invalidated still hold.
- If a task doesn't match any existing skill, consider whether one should
  be added — `.claude/skills/README.md` covers the criteria.

## Where the code lives (mental map)

```
docs/                    canonical reference (read second, after skills)
.claude/skills/          task-focused playbook (read first)
examples/                gold exemplar BT projects (feeder, capping, runtime, …)
projects_old/            retired real-bench projects, kept for reference
workspace/               the platform itself
  workspace/             Python package (SDK)
    components/          device + physical component classes
    recipes/             recipe classes (workflow coordination)
    bt/                  BT framework — Action, leaf engine, replanner
    devices/             MQTT device bus, AutoRecover, attach_device
    runtime.py           Runtime — pause/resume, rt.* API
    runtime_server.py    Tornado server — admin REST + WS + multiplexer
  gui/                   web UIs (admin dashboard, pendant, viewer)
  static/CAD/            3D models (GLB) for components
```

Real production projects (apc, bd, bna, …) are standalone git repos
outside this one — on the bench Pi they live under `~/Downloads/projects/`.
