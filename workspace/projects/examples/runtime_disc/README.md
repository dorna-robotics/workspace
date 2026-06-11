# runtime_disc — spawn discs at runtime, then transfer them

Standalone BT example that demonstrates **runtime scene mutation**: 14
discs are created in the scene *programmatically while the workflow
runs* (`workspace.add_component`) — not declared in the scene yaml like
every other example — and the robot then transfers each one with the
suction tool.

## The flow

```
Start          motor on + park (canonical)
SpawnDiscs     add 14 discs at runtime: in_1[A1..A7] + in_2[A1..A7]
Transfer(d)    ×14 — pick disc from its in slot, place in the paired out slot
Park           motor off (canonical)
```

Pairing (same slot index, holder paired by number):

```
in_1[A_k]  →  out_1[A_k]      discs 0..6
in_2[A_k]  →  out_2[A_k]      discs 7..13
```

**14 picks total.** Suction tool drives deeper on pick
(`tool_tcp_z_offset=-10`) and presses on release (`gravity_offset=-5`).
(`out_3` is spare — the scene has three out-holders but only two are
paired.)

## What's different from the other examples

| | Others | runtime_disc |
|---|---|---|
| **The items** | declared in `scene/layout.j2` | spawned at runtime via `workspace.add_component` |

The chassis is a local `scene/core_500.j2` (copied from
`scenes/core/core_500.j2`) so the project is fully self-contained. The
in/out holders are `adapter_disc_holder` + `stack_holder_disc_in/out`
(flat `A1..A7` slots); the Rack recipes target the adapters and the
resolver walks down to the stack holder underneath.

## The runtime API (in `SpawnDiscs.execute`)

```python
ws = self.ctx.workspace
ws.add_component("disc_0", {
    "type": "disc_22mm",
    "attach": { "parent_name": "stack_holder_disc_in_1", "parent_solid": "body",
                "parent_anchor": "A1", "child_solid": "body",
                "child_anchor": "center", "offset": [0, 0, 0, 0, 0, 0] },
})
```

`add_component` takes the scene lock, so it's safe from the BT thread.
The transfers move the discs kinematically (pick attaches to the tool,
place re-attaches to the out slot) — no add/remove needed for the move.

## Run it

```bash
cd workspace/projects/examples/runtime_disc
sudo python3 main.py
```

Operator UI at `http://<ip>:5010/`. Sim mode by default. Progress bar
tracks completed transfers.

## Files

| File | Purpose |
|---|---|
| `main.py` | Canonical BT entry point (byte-identical to other examples) |
| `launch.yaml` | Port 5010; scene composes the local `core_500.j2` |
| `scene/core_500.j2` | Local copy of the bench chassis (self-contained) |
| `scene/layout.j2` | Tool rack + suction gripper + the in/out disc holders |
| `recipes.j2` | `gripper` (ToolRack) + `disc_in_1/2`, `disc_out_1/2` (Rack) |
| `actions.py` | `Start` → `SpawnDiscs` → `Transfer(d)` ×14 → `Park` |
| `checks.py` | Empty stub |
