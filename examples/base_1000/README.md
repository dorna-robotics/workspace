# base_1000 — the base seed on the core_1000 bench

`examples/base` scaled up: the core_1000 chassis (robot + 1000 mm rail
+ 9 fixture plates + boundary walls) carrying a populated sample-prep
layout — shaker, cap feeder, three decapper posts, amber-40ml in/out
racks, autosampler-2ml racks + cap holder, five tool racks with four
gripper types, scale, vertical inspection, barcode reader, vortex, and
their collision boxes. The protocol is still just the canonical
bookends:

- **Start** — motor on, home the rail (`set_axis_with_stop`; already-homed
  and sim short-circuit to True), move to `START_JOINTS`. A homing
  failure returns the reserved `"killed"` outcome: the run aborts with
  zero motion and the operator must Reset / re-Launch.
- **Park / OperatorPark** — move to `PARK_JOINTS`; the same motion serves
  the planned end-of-run park and the operator's Park button
  (`trigger = "park"`).

Use it as a scene/reach testbed for the 1000 mm bench, or copy it to
start a project on this hardware:

```bash
cp -r examples/base_1000 ~/Downloads/projects/<name>
```

## Run it

```bash
cd examples/base_1000
sudo python3 main.py
```

Operator UI at `http://<ip>:5010/`. Start homes the rail and moves to
the ready pose; Park ends the run. Sim mode by default — works on any
machine, no hardware needed.

## Files

| File | Purpose |
|---|---|
| `main.py` | Standard BT entry point (byte-identical to other examples) |
| `launch.yaml` | Name, port, scene, recipes, empty kwargs |
| `recipes.j2` | One alias: the component-less `robot` recipe |
| `scene/core_1000.j2` | Local copy of the 1000 mm bench chassis |
| `scene/layout.j2` | The populated sample-prep bench |
| `actions.py` | `Start` → `Park` (+ `OperatorPark`), nothing else |
| `checks.py` | Empty stub |
