# Shared scene templates

Reusable scene fragments that projects compose into their full scene.
The launcher's `scene:` field takes a **list** and merges the files in
order (later files override earlier ones), so a project picks a
chassis template here and layers its own components on top.

## Layout

```
scenes/
└── core/                  Bench chassis: Core + rail + fixture plates
    ├── core_500.j2        rail_hd_500mm
    ├── core_1000.j2       rail_hd_1000mm   (add when needed)
    └── core_2000.j2       rail_hd_2000mm   (add when needed)
```

## Using a template

In a project's `launch.yaml`, reference the template by relative path
and add the project's own layout after it:

```yaml
scene: [../../workspace/scenes/core/core_500.j2, scene/layout.j2]
```

The path is relative to the **project directory** (main.py resolves
scene paths against it). Count `../` by how deep the project is nested:

| Project location | path to shared scenes |
|---|---|
| `examples/<name>/` (in-repo)     | `../../workspace/scenes/core/core_500.j2` |
| `projects_old/<name>/` (in-repo) | `../../workspace/scenes/core/core_500.j2` |
| standalone project repo          | keep a local copy at `scene/core_500.j2` (the apc/bd pattern) |

- `core_500.j2` provides the robot + rail + 6-plate fixture chain.
- `scene/layout.j2` (in the project) places that project's tool racks,
  holders, racks, tubes, etc. on the chassis.

Swapping the rail length is a one-line change: point at
`core_1000.j2` instead of `core_500.j2`.

## What a core template provides (and what it doesn't)

**Provides**: `core` (robot + tool changer + motion plan), the rail of
the matching length, and `fixture_plate_1..6` forming the standard
bench chassis.

**Does not provide**: any devices. Tool racks, holders, racks, hotels,
decappers, etc. are project-specific placements — they go in the
project's own scene file, attached to the fixture plates this template
exposes.

## Adding a new rail size

Copy `core_500.j2` → `core_1000.j2`, change `rail_cfg.type` to the
matching rail (e.g. `rail_hd_1000mm`), and adjust the rail's reachable
bounds if they differ (the Core component pins `rail_min` / `rail_max`
per rail type — see `workspace/components/core/core.py`). Keep the
fixture-plate chain identical so projects can switch rail sizes without
re-placing anything.
