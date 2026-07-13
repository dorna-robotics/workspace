# Examples

Self-contained, runnable mini-projects that each demonstrate **one
device or task family** end-to-end. The goal is to give new
contributors copy-paste-ready references for common patterns —
read the example, copy its folder to start your own project, swap
the device positions / counts / params for your bench.

## How each example works

Every folder under `examples/` is a complete BT project — same
shape as `multimeter_test` or `sample_prep`. You launch it the same
way:

```bash
cd examples/<name>
sudo python3 main.py
```

The operator UI then opens at `http://<ip>:5010/` (the platform-wide
default port). Every project — examples, `sample_prep`,
`multimeter_test` — defaults to 5010 for consistency. If you want
to run two side by side, override on the second one with
``--port 5020`` (or any free port).

## Folder layout (every example)

```
<name>/
├── README.md         what pattern this teaches + when to use it
├── main.py           standard BT entry point — identical across examples
├── launch.yaml       project name, port, scene, recipes, actions, kwargs
├── recipes.yaml      recipe wiring — one alias per component
├── scene/
│   ├── base.j2       components (robot + devices + tools + fixtures)
│   └── layout.j2     populated items (caps in feeder, tubes in rack, …)
├── actions.py        BT actions — the meaningful protocol
└── checks.py         predicate stubs (vision / sensor checks)
```

Each example is fully standalone: no shared scenes, no shared
recipes. Copy the folder, rename, edit. No cross-folder coupling.

## Available examples

| Example | Pattern it teaches |
|---|---|
| [`base/`](base/) | The seed of every project: bare core_500 chassis + canonical Start (rail homing, fatal "killed" on failure) / Park bookends. Copy this to start a new project. |
| [`feeder/`](feeder/) | Cap feeder + suction tool → cap holder. Tool-rack swap, feeder rotation, rack placement, vision-driven `present_cap`. |
| [`capping/`](capping/) | Full cap + decap roundtrip with the decapper + 4-finger gripper. Tube rack + cap holder + decapper, split-action planning (`Cap` then `Decap`). |
| [`hotel_swap/`](hotel_swap/) | Swap plates pairwise between two SBS hotels via two plate holders. `Hotel` recipe (lateral slide-in) + `Adapter` recipe (biased approach) + `gripper_sbs_width`. |
| [`pipetting/`](pipetting/) | Pick fresh tip → aspirate from a falcon tube → dispense in another → eject tip. `PipettingSite` recipe with `pick_tip` / `aspirate` / `dispense` / `eject_tip`, fully-loaded 4×5 falcon rack + 8×12 tip rack + waste bin. |
| [`shaker/`](shaker/) | Load 40 ml tubes onto a 2-slot shaker, shake, return. Non-robot resource (`resource="shaker"`), batched device action, `plan_window` = device capacity. |

More examples will be added incrementally as common patterns emerge
that contributors keep needing to look up.

## When to write a new example

If you find yourself explaining the same pattern to a new
contributor twice, that pattern probably belongs in `examples/` —
not buried inside `sample_prep` or another full protocol.

The bar for a new example: **one device family + a small, focused
end-to-end task that actually runs.** Not a half-built skeleton —
either it executes (in sim or real) or it doesn't ship here.
