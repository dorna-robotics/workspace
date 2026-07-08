---
name: write-scene-yaml
description: "Use when authoring or editing scene YAML (base.j2 / layout.j2). Covers the explicit-values rule, the attach hierarchy, the type registry, and how scene composition feeds the kinematic tree + 3D viewer."
---

# Write scene yaml

## When to use this skill

The user says any of:
- "Edit / build out the scene"
- "Add this component to the scene yaml"
- "Wire up the attach hierarchy"
- "How do I configure the [robot / camera / meter] in scene yaml?"

## Mental model

Scene yaml is the **source of truth for the workspace's physical layout**:

- Components by name → kinematic tree via `attach:` (parent anchor → child anchor)
- Each component's `type:` resolves to a `@register("...")`-decorated Python class
- Rendered at workspace boot; the 3D viewer reflects it; recipes / states / checks reference components by name
- Jinja2 templating allows DRY — `base.j2` for static / shared parts, `layout.j2` for variable / project parts (typically loaded later in the order)

## Quick rules (load-bearing)

1. **Explicit values, no commented optionals.** Every meaningful field gets a value, even if it matches the default. `port: ""` and `critical: true` go in; do NOT leave them commented as "# port: ..." for the operator to uncomment. Reading the yaml should never require guessing what the default is.
2. **`type:` must match `@register("...")`.** The Python class registers itself with a type string; the yaml's `type:` is the lookup key. Exact match required (case-sensitive).
3. **`attach:` declares the kinematic edge.** `attach: {parent_name, parent_solid, parent_anchor, child_solid, child_anchor, offset}`. Reading top to bottom, each child references a previously-declared parent. The tree forms once at boot.
4. **Component name = stable reference.** Names like `gripper`, `source_rack`, `multi_meter_1` are referenced from `recipes.yaml`, action params, check args. Choose names that read well in `rcp["..."]` lookups.
5. **`simulation:` is component-local.** Each component picks its own simulation flag. There's no global "sim mode" — the operator decides device-by-device.
6. **One scene yaml per project** (composed from multiple j2 files via the `scene:` list in `launch.yaml`). The list order is the rendering order.

## Canonical doc references

| Section | What you'll find |
|---|---|
| `docs/project-guide.md` §2 | Scene as project source of truth + j2 composition |
| `docs/project-guide.md` §3 | `launch.yaml` — the scene list, recipes path, action / check modules |
| `docs/component-guide.md` §3-6 | Component → scene yaml mapping (type registry + DEFAULTS) |
| `docs/component-guide.md` §6 | Detailed reference for every component field (anchors, collision, attach) |
| `docs/component-guide.md` §9 | Runtime scene mutation (add/remove components mid-run) |
| `docs/device-guide.md` §9 | USB-serial path discovery — `ls -d /dev/serial/by-id/*` |
| `docs/device-guide.md` §16 | Per-device `simulation:` and `critical:` semantics |

## Canonical reference implementations

- **runtime example** (full BT project scene): `examples/runtime/scene/core_500.j2` (chassis) and `layout.j2` — robot + fixtures + racks + tools
- **rail_calibration** (minimal scene): `examples/rail_calibration/scene/` — core + a single probe target
- **Core component** scene shape: see `docs/component-guide.md` §6 walkthrough

## Common pitfalls

- **Commented-out optionals** (`# port: "/dev/ttyUSB0"`) — drop them. Use `port: ""` to mean "empty / unset" explicitly. The operator should never need to uncomment to learn the truth.
- **Type-name typo** vs `@register("...")` — yaml fails to resolve component; error at workspace boot ("unknown component type: ..."). Fix the typo in either yaml or `@register`.
- **Cyclic `attach`** — child references parent that hasn't been declared yet, or two components mutually attach. The kinematic builder catches it but you'll lose 5 minutes on a stack trace. Declare parents first.
- **USB path** like `/dev/ttyUSB0` for a real device — unstable. Use `/dev/serial/by-id/...` for reboot safety. See device-guide.md §9.
- **Slashes in device identifiers** (`port: "/dev/...."`) are fine in yaml; the component's `id` property must use `os.path.basename(self.port)` to keep MQTT topics flat. device-guide.md §9.
- **Putting `critical: true` on a daemon-owned component** — daemons own critical; workspace overrides have no effect. Skip the field for daemon-owned devices. device-guide.md §10 (shape B).

## After this

- For a brand-new device requiring scene yaml AND component code, start at [`add-workspace-device`](../add-workspace-device/SKILL.md) or [`add-daemon-device`](../add-daemon-device/SKILL.md).
- For a new physical component (no bus): [`add-custom-component`](../add-custom-component/SKILL.md).
- To toggle a device's simulation mode in yaml: [`enable-sim-mode`](../enable-sim-mode/SKILL.md).
- To add/remove components mid-run (advanced): component-guide.md §9 + bt-framework-guide.md §9.
