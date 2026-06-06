---
name: write-recipe
description: "Use when writing a new recipe — the workflow coordination layer between BT actions and devices. Covers motion primitives, the DEFAULTS merge pattern, the sim-agnostic rule, and pause-aware rt.* API usage."
---

# Write a recipe

## When to use this skill

The user says any of:
- "Write a recipe for the [pump / printer / new device]"
- "Add a `pick` / `place` / `dose` recipe method"
- "Refactor this into a recipe"

If the user is adding a **device** itself (not a recipe for a device), use [`add-workspace-device`](../add-workspace-device/SKILL.md) or [`add-daemon-device`](../add-daemon-device/SKILL.md).
If they're adding a BT **action** that orchestrates recipes, use [`add-bt-action`](../add-bt-action/SKILL.md).

## What a recipe is (and isn't)

A recipe is the **workflow coordination** layer:

- ✅ Multi-step sequences combining motion + device ops + sensing
- ✅ Per-station calibration (anchors, approach distances, dispense volumes)
- ✅ Per-project or per-tool variation of "how to do pick"
- ❌ Atomic device operations (those live on the **component**)
- ❌ Sim/real branching (handled by the component constructor; recipes stay agnostic)

The two-line ownership test (component-guide.md §7):
> Could an operator press a button to trigger it? → **Component** (atomic op).
> Could a project replace it with a different version? → **Recipe** (workflow).

## Quick rules

1. **Inherit from `Recipe`** (`workspace.recipes.recipe.Recipe`). For IO-only recipes with no robot motion (e.g. the multimeter recipe), inheriting bare and bypassing `Recipe.__init__` is fine — see `workspace/recipes/multi_meter.py`.
2. **DEFAULTS merge pattern** — `prm = deepcopy(Recipe.DEFAULTS); merge(prm, self.DEFAULTS); merge(prm, kwargs)`. Drop into `super().__init__(**prm)`. See any recipe under `workspace/recipes/`.
3. **Never branch on `self.simulation`** — the component constructor already picked the API (real driver vs. sim stub vs. `SimulationAPI`). Recipes call whatever the component exposes; same code path in sim or real. device-guide.md §10.5.
4. **All work goes through `rt.*`** — `rt.<robot_method>(...)`, `rt.sleep`, `rt.delay`, `rt.checkpoint`. These are pause-aware by default. Calls that bypass `rt.*` (recipe → component → raw driver) are NOT pause-aware. project-guide.md §8.
5. **Logging never blocks.** `rt.step(label, level)` is observability only — call it freely but don't rely on it for pause checkpoints. project-guide.md §8.
6. **Tool-changer recipes inherit `ToolRack`** — automatic tool-swap accounting. Direct-mount recipes (no swap) inherit `Recipe`.

## Canonical doc references

| Section | What you'll find |
|---|---|
| `docs/recipe-guide.md` §1-2 | What a recipe is + the sim-agnostic rule |
| `docs/recipe-guide.md` §5 | Motion primitives — pick/place/above/stand/immerse |
| `docs/recipe-guide.md` §7 | DEFAULTS merge pattern (the canonical inheritance chain) |
| `docs/recipe-guide.md` §8 | `rt.*` runtime API + which calls are pause-aware |
| `docs/project-guide.md` §8 | Comprehensive pause-aware reference |
| `docs/component-guide.md` §7 | Component-vs-recipe ownership rule |
| `docs/parameter-guidelines.md` | Heuristics for `gravity_offset`, `tcp_z_offset`, `soft_approach`, etc. |

## Canonical reference implementations

- **Feeder recipe**: `workspace/workspace/recipes/feeder.py` — thin coordination layer; `rotate_in_step` delegates to `component.rotate(step)`, the atomic op lifted to the component
- **MultiMeter recipe**: `workspace/workspace/recipes/multi_meter.py` — IO-only recipe, bypasses `Recipe.__init__`, pure delegation
- **Tool-rack recipes**: `workspace/workspace/recipes/tool_rack.py` — extends `ToolRack` for automatic tool-swap accounting

## Common pitfalls

- **Putting `if self.simulation:` in a recipe method** — wrong layer. Move the branch to the component. recipe-guide.md §1, device-guide.md §10.5.
- **Calling raw driver methods directly from a recipe** (`self.component.driver.write(...)`) — bypasses both the component's safe-read error handling AND `rt.*` pause-awareness. Always go through the component's public API.
- **Inlining IK / kinematic math in a recipe** — recipes coordinate, they don't compute. Defer to `core.dorna.kinematic.inv(...)` or similar.
- **Recipe stores per-run state** that should live in workspace state — recipes are per-instance/per-component, not per-run. Use BT facts or workspace state for run state.

## After this

- If a recipe operation should be triggerable by the operator from the UI: also surface it via [`add-bt-action`](../add-bt-action/SKILL.md) or [`operator-recovery`](../operator-recovery/SKILL.md) (operator_actions on the component).
- If you discover the operation is actually atomic, move it to the component instead. component-guide.md §7.
