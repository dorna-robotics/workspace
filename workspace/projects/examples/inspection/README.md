# inspection — present each tube to two vision stations

Standalone BT example: for each of `batch_size` capped 2 ml tubes, the
robot **picks** the tube, **presents** it to two inspection stations in
turn (running a detection at each), and **places** it back.

## Flow

```
Start         motor on + park (canonical)
  per tube ×batch_size:
    Pick      pick the capped tube from its rack slot
    Present1  present to inspection station 1 + detect()
    Present2  present to inspection station 2 + detect()
    Place     return the tube to its slot
Park          motor off (canonical)
```

Each step is its own BT action, gated by facts (`picked → presented1 →
presented2 → placed`) so they run in order per tube. Tubes are the
single objects dim, so windowed planning auto-engages (`plan_window`).

## Scene

| Item | |
|---|---|
| `inspection_vertical_144mm_1` | fixed vision station, on `fixture_plate_1 / H18` (printer-like) |
| `inspection_horizontal_1` | second station, on `fixture_plate_1 / D16` |
| `adapter_plate_sbs` → `rack_autosampler_2ml` | SBS rack, 48 slots |
| 48 `tube_autosampler_2ml` + 48 `cap_autosampler_2ml` | a capped tube in every slot (cap on each tube's `cap_seat`) |
| tool rack + `gripper_4_finger` | the tube-handling tool |

Built on the shared `core_500.j2` chassis (self-contained local copy).

## Recipes

| Alias | Recipe | Target |
|---|---|---|
| `gripper` | `ToolRack` | `tool_rack_144mm_1` |
| `tube_rack` | `Rack` | `sbs_adapter_0` → rack |
| `inspector_1` | `FixedInspector` | `inspection_vertical_144mm_1` |
| `inspector_2` | `FixedInspector` | `inspection_horizontal_1` |

## Run it

```bash
cd workspace/projects/examples/inspection
sudo python3 main.py
```

Operator UI at `http://<ip>:5010/`. Sim mode by default — `detect()`
returns canned values offline, so no vision server is needed. Pick
`batch_size` (1–48), press Start. Progress bar tracks the four per-tube
steps.

## Files

| File | Purpose |
|---|---|
| `main.py` | Canonical BT entry point |
| `launch.yaml` | Port 5010; `batch_size` kwarg; scene composes local `core_500.j2` |
| `scene/core_500.j2` | Local bench chassis (self-contained) |
| `scene/layout.j2` | Two stations + SBS rack of 48 capped tubes + tooling |
| `recipes.j2` | `gripper`, `tube_rack`, `inspector_1`, `inspector_2` |
| `actions.py` | `Start` → `Pick`/`Present1`/`Present2`/`Place` ×tube → `Park` |
| `checks.py` | Empty stub |
