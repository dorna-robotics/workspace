# base — the seed of every project

The smallest valid BT project: the bare core_500 chassis (robot + rail
+ 6 fixture plates + boundary walls), no devices, no tools, and the
canonical bookend actions only:

- **Start** — motor on, home the rail (`set_axis_with_stop`; already-homed
  and sim short-circuit to True), move to `START_JOINTS`. A homing
  failure returns the reserved `"killed"` outcome: the run aborts with
  zero motion and the operator must Reset / re-Launch.
- **Park / OperatorPark** — move to `PARK_JOINTS`; the same motion serves
  the planned end-of-run park and the operator's Park button
  (`trigger = "park"`).

## Start a new project from this

```bash
cp -r examples/base ~/Downloads/projects/<name>
```

Then grow it:

1. `scene/layout.j2` — add devices, tool racks, populated items
   (any other example's layout is the shape to copy).
2. `recipes.j2` — one alias per component the actions will drive.
3. `actions.py` — per-item actions between the bookends; keep
   Start / Park / OperatorPark canonical (only the joints, the per-item
   predicate and the tool alias should ever differ across projects).
4. `launch.yaml` — kwargs for the operator form as parameters emerge.

## Run it

```bash
cd examples/base
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
| `scene/core_500.j2` | Local copy of the bench chassis |
| `scene/layout.j2` | Empty on purpose — your devices go here |
| `actions.py` | `Start` → `Park` (+ `OperatorPark`), nothing else |
| `checks.py` | Empty stub |
