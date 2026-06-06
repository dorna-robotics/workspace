# Workspace Skills

This directory holds task-focused **skills** that future Claude sessions (or
human contributors) can invoke when working on the Dorna Workspace
platform. Each skill scopes to one common task and points to the canonical
doc sections — skills navigate, they don't duplicate.

## When to use which skill

| Task | Skill |
|---|---|
| Add a workspace-owned device (USB/serial — robot, multimeter, in-process pump) | [`add-workspace-device`](add-workspace-device/SKILL.md) |
| Add a daemon-owned device (camera + vision server, printer service, etc.) | [`add-daemon-device`](add-daemon-device/SKILL.md) |
| Write a new recipe (motion + workflow coordination) | [`write-recipe`](write-recipe/SKILL.md) |
| Add a BT action class to a project | [`add-bt-action`](add-bt-action/SKILL.md) |
| Add a custom physical component (rack, holder, tool, fixture) | [`add-custom-component`](add-custom-component/SKILL.md) |
| Author scene YAML (`base.j2` / `layout.j2`) | [`write-scene-yaml`](write-scene-yaml/SKILL.md) |
| Configure simulation mode for a device or component | [`enable-sim-mode`](enable-sim-mode/SKILL.md) |
| Recover the workflow after a pause (operator-side) | [`operator-recovery`](operator-recovery/SKILL.md) |
| Debug the device bus / MQTT state | [`debug-device-bus`](debug-device-bus/SKILL.md) |

## Canonical doc references

Every skill points back to `docs/`:

- `docs/device-guide.md` — device bus, contract, adapter wiring, sim model
- `docs/component-guide.md` — component authoring, atomic ops, operator actions, runtime mutation
- `docs/project-guide.md` — project structure, runtime API, pause semantics
- `docs/recipe-guide.md` — recipe patterns, motion primitives, `rt.*` API
- `docs/bt-framework-guide.md` — BT projects, PDDL planning, actions, slicing
- `docs/design-system.md` — UI tokens, surfaces, theme
- `docs/parameter-guidelines.md` — recipe parameter heuristics

## Conventions every skill assumes

These are always in scope and not repeated in each skill:

- **Use `sudo python3` and `sudo pip3`** for commands that touch serial / hardware / install packages.
- **Scene yamls use explicit values** — no commented-out optionals (`port: ""`, not `# port: "..."`).
- **Commit messages**: short imperative title, optional bullet body, end with `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` when appropriate.
- **Never amend or force-push** without explicit instruction.
- **Sim is orthogonal to connection state** — bus dot reflects hardware truth, SIM pill reflects operator intent (device-guide §16).
- **Observability never blocks. Work always checkpoints.** Pause-aware methods are anything that does work via `rt.*` (project-guide §8).
- **No backwards-compat hacks**: if something is unused, delete it. Don't keep dead aliases or `# removed: …` comments.

## Adding a new skill

When a contributor task starts repeating and the answer isn't covered by an
existing skill, add a new one here. Keep skills:

- **Task-scoped, not doc-scoped** — one task per skill, not one skill per doc file.
- **Short** — under 200 lines. Cross-reference docs instead of duplicating.
- **Concrete** — every claim should have a doc:section pointer.
- **Self-contained** — readable in isolation.
