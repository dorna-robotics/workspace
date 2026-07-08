# pipetting — pick tip, transfer liquid between falcon tubes, eject tip

Standalone BT mini-project that uses the pipettor to transfer
liquid between falcon 15 ml tubes in a fully loaded rack. Source
is fixed (A1); destinations walk A2 → A3 → A4 → A5. Each transfer
uses a fresh tip from the tip rack and ejects the used tip into a
waste bin.

A standalone pipette-transfer workflow with the falcon rack
fully loaded (20 tubes).

## What this teaches

| Pattern | Where |
|---|---|
| **`PipettingSite` recipe** | One recipe alias per pipetting target: `tip_rack`, `waste_bin`, `falcon_pipette`. The recipe class has `pick_tip` / `eject_tip` / `aspirate` / `dispense` / `immerse` / `retract` — all the building blocks. |
| **immerse / aspirate / retract triple** | The aspirate side of a transfer: descend tip below the liquid surface (`immerse(depth=N)`), suck up `vol` µL, lift back out (`retract`). Mirror it on the dispense side. |
| **Fresh tip per transfer** | Every `Transfer(t)` action picks a different tip slot (`TIP_ANCHORS[t]`) to avoid cross-contamination. The framework's tool-changer doesn't enter here — tips are handled by the PipettingSite recipe. |
| **Adapter-resolver pattern** | All three PipettingSite recipes target an `adapter_plate_*` component. The resolver walks the kinematic tree down to whatever rack (`rack_falcon_15ml`, `rack_axygen_180ul`, `rack_tip_waste_bin`) is sitting on it. Same trick the capping example uses with `cap_holder`. |
| **Per-transfer PDDL planning** | One `Transfer(t)` action per t in 0..transfer_count-1, scheduled in order by the `~transferred(t)` precondition. |

## Per-transfer flow (one cycle, 6 calls)

```
tip_rack.pick_tip(A{t+1})              # grab a fresh tip
falcon_pipette.immerse(A1, depth=20)   # descend into source
falcon_pipette.aspirate(vol=400)       # suck up 400 µL
falcon_pipette.retract(A1)             # lift out
falcon_pipette.immerse(A{t+2}, depth=20)  # descend into dest
falcon_pipette.dispense(vol=400)       # push out 400 µL
falcon_pipette.retract(A{t+2})         # lift out
waste_bin.eject_tip()                  # drop the used tip
```

## Run it

```bash
cd examples/pipetting
sudo python3 main.py
```

Operator UI at `http://<ip>:5010/`. Pick `transfer_count` (1–4),
start. Progress bar tracks completed transfers.

Sim mode by default — no real pipettor needed.

## Convention: `Start` / `Park` / `OperatorPark` stay canonical

All pipetting work lives in `Transfer`. Start and Park stay the
same shape as every other example. Only the per-item predicate
(`transferred(t)`) varies.

Full rule + canonical shapes: `.claude/skills/add-bt-action/SKILL.md`
rule 6.

## How to adapt

1. **Different tube model**: swap `tube_falcon_15ml` for
   `tube_amber_40ml` etc. in `layout.j2`, update the rack type to
   match (`rack_amber_40ml_4x7` / `rack_amber_40ml_2x4`).
2. **More transfers**: extend `DEST_ANCHORS` and `TIP_ANCHORS` in
   `actions.py`, raise `transfer_count.max` in `launch.yaml`.
3. **Different source / pairing**: change the `SOURCE_ANCHOR` and
   `DEST_ANCHORS` mapping. For a 1-to-1 column swap (A1→B1,
   A2→B2, …) build them off the transfer index.
4. **Real pipettor**: flip `pipettor.simulation: true` to `false`
   and set `port: "/dev/ttyUSB0"` (or wherever it lands). The
   `PipettingSite` recipe is sim-agnostic — same workflow runs
   against the real Keyto pipettor with no edits.

## Files

| File | Purpose |
|---|---|
| `main.py` | Standard BT entry point (byte-identical to other examples) |
| `launch.yaml` | Port 5010, `transfer_count` kwarg (1–4) |
| `recipes.j2` | 4 recipes: `gripper`, `falcon_pipette`, `tip_rack`, `waste_bin` |
| `scene/core_500.j2` | Local copy of the bench chassis (core + rail + 6 plates + boundary collision boxes) |
| `scene/layout.j2` | Devices (adapters/racks/holders/tool rack) + populated items |
| `actions.py` | `Start` → `Transfer(t) × transfer_count` → `Park` |
| `checks.py` | Empty stub |

## See also

- [`feeder/`](../feeder/), [`capping/`](../capping/), [`hotel_swap/`](../hotel_swap/) — other examples on the same fixture footprint
- [`recipe-guide.md`](../../../../docs/recipe-guide.md) — §8 catalog entry for `PipettingSite`
