# barcode — present each tube to a barcode reader and scan it

Standalone BT example: for each of `batch_size` 2 ml tubes, the robot
**picks** the tube, **presents** it to a Zebra DS457 barcode reader,
**scans** the barcode, and **places** it back.

## Flow

```
Start        motor on + park (canonical)
  per tube ×batch_size:
    Pick     pick the tube from its rack slot
    Present  position the tube at the reader's window
    Scan     read the barcode (pure device read — no motion)
    Place    return the tube to its slot
Park         motor off (canonical)
```

Each step is its own BT action, gated by facts (`picked → presented →
scanned → placed`) so they run in order per tube. Tubes are the single
objects dim, so windowed planning auto-engages (`plan_window`).

Two platform patterns are demonstrated (see `docs/project-guide.md` §8):

- **Declarative retry on `Scan`.** The scan is its own read-only action.
  It asserts `scanned(tube)` only on a valid read; on a failed read it
  returns `False`, the leaf fails, and the planner re-selects `Scan`
  after the reader recovers — without redoing the present/place motions
  (the tube stays presented). If the reader is `critical` + real, its bus
  `down` also pauses the runtime until reconnect.
- **Single-occupancy `hand_empty`.** Consumed on `Pick`, restored on
  `Place`, so the planner can't batch all the picks before any place.

## Scene

| Item | |
|---|---|
| `barcode_reader_1` | Zebra DS457 (vertical), on `fixture_plate_1 / H18` |
| `adapter_plate_sbs` → `rack_autosampler_2ml` | SBS rack, 48 slots |
| 48 `tube_autosampler_2ml` | a plain tube in every slot |
| tool rack + `gripper_4_finger` | the tube-handling tool |

Built on the shared `core_500.j2` chassis (self-contained local copy).

## Recipes

| Alias | Recipe | Target |
|---|---|---|
| `gripper` | `ToolRack` | `tool_rack_144mm_1` |
| `tube_rack` | `Rack` | `sbs_adapter_0` → rack |
| `barcode_reader` | `BarcodeReader` | `barcode_reader_1` |

## Run it

```bash
cd examples/barcode
sudo python3 main.py
```

Operator UI at `http://<ip>:5010/`. Sim mode by default — `code()`
returns a canned `TUBE-NNNN` per tube offline, so no scanner is needed.
For the real DS457: set `port` (e.g. `/dev/ttyACM0`) + `simulation: false`
on `barcode_reader_1` in the layout. Pick `batch_size` (1–48), press
Start. Progress bar tracks the four per-tube steps.

## Files

| File | Purpose |
|---|---|
| `main.py` | Canonical BT entry point |
| `launch.yaml` | Port 5010; `batch_size` kwarg; scene composes local `core_500.j2` |
| `scene/core_500.j2` | Local bench chassis (self-contained) |
| `scene/layout.j2` | Barcode reader + SBS rack of 48 tubes + tooling |
| `recipes.j2` | `gripper`, `tube_rack`, `barcode_reader` |
| `actions.py` | `Start` → `Pick`/`Present`/`Scan`/`Place` ×tube → `Park` |
| `checks.py` | Empty stub |
