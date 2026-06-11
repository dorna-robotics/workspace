# runtime_disc — spawn and kill a component at runtime

Standalone BT example that demonstrates **runtime scene mutation**: a
disc is created in the scene *programmatically while the workflow runs*
— not declared in the scene yaml like every other example — and removed
again later.

Two differences from the other examples:

| | Others | runtime_disc |
|---|---|---|
| **Chassis** | local `scene/base.j2` | the **shared** `scenes/core/core_500.j2` |
| **The item** | declared in `scene/layout.j2` | spawned at runtime with `workspace.add_component`, removed with `workspace.remove_component` |

## The runtime API

On `self.ctx.workspace` inside an action's `execute`:

```python
ws = self.ctx.workspace

# Spawn — cfg is the same dict shape as a scene yaml entry:
disc = ws.add_component("disc_1", {
    "type": "disc_22mm",
    "attach": { "parent_name": "...", "parent_solid": "body",
                "parent_anchor": "...", "child_solid": "body",
                "child_anchor": "center", "offset": [0, 0, 0, 0, 0, 0] },
})
ws.add_fact(...)         # tell the planner the world changed

# Kill — detaches its solids and drops it from the scene:
ws.remove_component("disc_1")
ws.remove_fact(...)
```

`add_component` refuses a duplicate name and (mid-run) device-backed
components. `remove_component` refuses `core`, a tool mounted on the
robot, and (mid-run) device-backed components. Both take the scene lock,
so they're safe to call from the BT thread.

## Current state

Scaffold only — the canonical `Start` → `Park` trio with no per-item
action yet. The spawn-disc and kill-disc actions go between them.

## Run it

```bash
cd workspace/projects/examples/runtime_disc
sudo python3 main.py
```

Operator UI at `http://<ip>:5010/`. Sim mode by default.

## Files

| File | Purpose |
|---|---|
| `main.py` | Canonical BT entry point (byte-identical to other examples) |
| `launch.yaml` | Port 5010; scene composes the shared `core_500.j2` |
| `recipes.j2` | `gripper` (ToolRack) — add disc-handling recipes as needed |
| `scene/layout.j2` | Tool rack + parked gripper only — the disc is spawned at runtime |
| `actions.py` | `Start` → `Park`; documents the `add_component`/`remove_component` hooks |
| `checks.py` | Empty stub |
