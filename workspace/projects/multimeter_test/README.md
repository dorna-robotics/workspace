# multimeter_test

A minimal BT-framework project that demonstrates the
`multi_meter_bk879b` component end-to-end. The robot stays idle; the
workflow's only job is to drive the meter and log each reading.

## What it does

For each `batch_size` operator-chosen sample:

1. Wait 10 seconds (pause-aware — operator can Pause mid-wait).
2. Read capacitance from the BK Precision 879B (or the sim stub).
3. Log the reading as a workflow step the operator sees in the
   dashboard timeline.

Then Park.

## Configuration

All in `launch.yaml`. The operator-visible knob is `batch_size`:

```yaml
kwargs:
  batch_size:
    type: int
    default: 3
    min:   1
    max:   100
```

Set `simulation: false` and (optionally) a specific `port` on the
meter in `scene/base.j2` to drive a real BK 879B instead of the sim
stub. The port can be omitted — the driver scans USB serial ports
for the meter's FTDI / CP210x chip.

## Files

| File          | Purpose                                                            |
|---------------|--------------------------------------------------------------------|
| `launch.yaml` | Project config (project name, port, scene, kwargs, plan window)    |
| `main.py`     | Entry point — boilerplate that calls `run_protocol`                |
| `actions.py`  | Three actions: `Start` → `ReadMeter(s)` × N → `Park`               |
| `checks.py`   | Empty `Checks` class (no vision/sensor checks)                     |
| `recipes.j2`  | One alias, `meter`, pointing at `multi_meter_1`                    |
| `scene/base.j2` | Minimal Core + one `multi_meter_bk879b` (sim by default)         |

## Running

```bash
sudo python3 projects/multimeter_test/main.py
```

Then open the orchestrator at `http://localhost:5010`. Hit Launch,
set `batch_size`, and watch readings stream into the Steps timeline.

## How this exercises the framework

- **Sim/real component split** (`multi_meter_bk879b`): the action calls
  `rcp["meter"].read_capacitance()` and never branches on the sim flag.
- **Device bus** (`attach_device`): the meter appears in the Devices
  panel with a `multimeter:sim-multi_meter_1` row (or its real port).
- **Operator Controls panel**: the component exposes `Read C/L/R once`,
  `Reconnect`, `Release` — usable when paused.
- **Pause-aware delay** (`rt.sleep(10)`): demonstrate Pause/Resume
  during the 10 s gap between reads.
- **Steps timeline** (`rt.step`): each reading lands as a
  `success`-level entry so the operator can see the values without
  opening the log.
