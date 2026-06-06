---
name: add-custom-component
description: "Use when adding a new physical component to the workspace — a rack, holder, tool, fixture, mount, or any other static or semi-static asset that lives in the kinematic tree. For active devices that talk to the bus, use add-workspace-device or add-daemon-device."
---

# Add a custom component

## When to use this skill

The user says any of:
- "Add a new rack / 96-well plate / vial holder / tool mount / fixture"
- "Model this part in 3D and bring it into the workspace"
- "Adapt the existing Rack class for a different geometry"

If the component **talks to the bus** (USB, serial, MQTT, TCP), use [`add-workspace-device`](../add-workspace-device/SKILL.md) or [`add-daemon-device`](../add-daemon-device/SKILL.md). Devices use the same component skeleton but add the Device protocol on top.

## What a component is

A component is the **kinematic representation** of a physical asset:
- A 3D model (`.glb`)
- A Python class declaring **anchors** (named transform frames) + optional **collision boxes**
- A `type` string that matches the GLB filename + the class's `@register("...")`
- An optional **slot** dict mapping anchors to child names (for `pick`/`place` recipes)

Scene yaml composes components into a kinematic tree via `attach:` (parent anchor → child anchor).

## Quick rules

1. **Three files**: `model.glb` under `workspace/static/CAD/`, Python class under `workspace/workspace/components/<name>/<name>.py`, and a scene yaml entry that uses `type: "<name>"`. The type string is the glue.
2. **Anchors are named transform frames**, all relative to the component's `center`. The required one is `center: [0,0,0,0,0,0]`. Add `place` for pick/place targets, `top` for grip points, `hole_0..3` for child mounts, custom names as needed.
3. **`@register("...")` matches the GLB filename** AND the `type` field in scene yaml. They must agree exactly.
4. **DEFAULTS merge pattern** — `prm = deepcopy(self.DEFAULTS); merge(prm, cfg); merge(prm, kwargs)`. Same shape as recipes and devices.
5. **Atomic ops go on the component, workflows go on the recipe.** Test: "Could the operator press one button?" → component. component-guide.md §7.
6. **Inherit when possible** — Rack / ToolRack / Gripper / Solid are extension points. Custom geometry inherits Solid; custom storage inherits Rack; custom tooling inherits ToolRack.

## Canonical doc references

| Section | What you'll find |
|---|---|
| `docs/component-guide.md` §1-2 | What a component is + the file triad |
| `docs/component-guide.md` §3 | Python class skeleton (the canonical pattern) |
| `docs/component-guide.md` §4 | Anchor conventions (`center`, `place`, `top`, etc.) |
| `docs/component-guide.md` §5 | Collision boxes + the `[pose, scale]` shape |
| `docs/component-guide.md` §6 | Scene-yaml reference for the component's `type` |
| `docs/component-guide.md` §7 | The component-vs-recipe rule (atomic ops live here) |
| `docs/component-guide.md` §8 | Operator actions contract |
| `docs/component-guide.md` §9 | Runtime scene mutation (`workspace.add_component` / `remove_component`) |

## Canonical reference implementations

- **Rack** (storage): `workspace/workspace/components/rack/rack.py`
- **ToolRack** (tool changer): `workspace/workspace/components/tool_rack/tool_rack.py`
- **Gripper** (active gripper): `workspace/workspace/components/gripper/gripper.py` — has `enable()/disable()` atomic ops
- **MultiMeterBk879b** (device, but shares the component skeleton): `workspace/workspace/components/multi_meter/multi_meter_bk879b.py`

## Common pitfalls

- **GLB origin offset** — the model's center must match the `center` anchor's `[0,0,0,0,0,0]`. If the GLB was exported with a non-origin pivot, scene attach math goes wrong and the collision boxes drift. Re-export with origin at the geometric center.
- **Anchor name collision across scene** — anchor names within a component are local, but the assembly + anchor pair becomes globally referenced by recipes. Use descriptive names (`hole_top_left`, not `h1`).
- **Storing run state on the component instance** — components are per-instance, not per-run. Use BT facts or workspace state for run-scoped data.
- **Custom collision box copies the GLB shape exactly** — collision boxes are convex bounding volumes for motion planning; over-fitting kills planner performance. Use the simplest box / sphere / cylinder that covers the physical extent.
- **Skipping `@register`** — without it, the component isn't discoverable from scene yaml; `type: "..."` would fail to resolve.

## After this

- For **scene yaml** entries: see [`write-scene-yaml`](../write-scene-yaml/SKILL.md).
- If you need **operator buttons** for the component: component-guide.md §8 — declare `operator_actions()` returning `[{"label": "...", "method": "..."}, ...]`.
- If the component needs **device-bus visibility**, use [`add-workspace-device`](../add-workspace-device/SKILL.md) or [`add-daemon-device`](../add-daemon-device/SKILL.md) — they extend this same skeleton.
- For **runtime mutation** (add/remove components mid-run): see component-guide.md §9 + bt-framework-guide.md §9.
