# Parameter Guidelines

> Practical heuristics for choosing parameter values when writing or adapting recipes.
> Audience: recipe authors (human or AI). For *what* a parameter does, see the recipe/component code; this doc is for *how to pick a value*.

---

## How to use this doc

- Each section below covers one parameter (or one closely related group).
- Defaults live in **code/config**. This doc explains the *reasoning* so you know when to deviate.
- If you discover a new heuristic in practice, add a new section using the template at the bottom.

---

## `gravity_offset`

Vertical offset (mm) applied during a `.place(...)` (or related motion). Positive = leave the item higher above the target; negative = push further down into the target. **Default: `1`.**

**Rule of thumb:**
- **Suction gripper with elbow / leveler** → use a *negative* offset (e.g. `-10`). The elbow/leveler compresses on contact, so going further down ensures the item is fully seated before release.
- **Solid-body grippers (2-finger, 4-finger)** → use a *small positive* offset (e.g. `+4` to `+5`). Releasing slightly above the target avoids jamming the item or the gripper fingers against the rack.

**Examples:**
```python
.place(..., gravity_offset=5)    # two- or four-finger gripper
.place(..., gravity_offset=-10)  # suction cup with leveler / elbow
```

**Why:** prevents bad releases — either the item not seating (rigid grippers placing too low) or the item not being pushed in fully (compliant grippers placing too high).

**When to deviate:**
<!-- taller/shorter tubes, soft vs rigid racks, gravity-fed racks, unusual gripper geometries, etc. -->

**Default location in code:**
<!-- e.g. tube_rack_gravity_offset in projects/syringe/main.ipynb -->

---

## `tool_tcp_z_offset`

Z offset (mm) applied to the tool's TCP during a `.pick(...)`. Negative = the gripper drives further down before closing.

**Rule of thumb:**
- **Decappers** → the tube does not sit on a flat surface (the decapper bottom is open), so during capping it gets forced downward and shifts. Compensate with `tool_tcp_z_offset=-2` (or similar) so the gripper reaches the displaced tube position.
- **Suction cup grippers** → on pick, set `tool_tcp_z_offset=-5`. Going further down lets the cup compress against the item, making contact more reliable and the pick more secure.

**Examples:**
```python
.pick(..., tool_tcp_z_offset=-2)  # decapper picking a tube that shifted during capping
.pick(..., tool_tcp_z_offset=-5)  # suction cup — compress for a more secure pick
```

**Why:** without compensation, the gripper closes above the actual item position (decapper case) or makes only light contact (suction case) and grips weakly or misses.

**When to deviate:**
<!-- different decapper geometry, different tube length, capping force changes, rigid items that won't compress, etc. -->

**Default location in code:**
<!-- link to where this is set -->

---

## `soft_approach`

Boolean. When `True`, the final descent into a target anchor is split into two steps: first move the center of the carried item to the **top of the receiving component** (e.g. rack top), then descend to the actual anchor position. When `False`, a single direct motion goes straight to the anchor.

**Rule of thumb:**
- For **place into racks** → set `soft_approach=True`. The two-step motion ensures the tube enters the rack hole cleanly from directly above instead of arcing in at an angle.

**Example:**
```python
.place(..., soft_approach=True)  # placing a tube into a rack
```

**Why:** a single-step approach can come in off-axis and catch the tube on the rack lip, jamming or knocking it loose. The intermediate "top of rack" waypoint forces a vertical final descent.

**When to deviate:**
<!-- open targets with no surrounding walls (no lip to catch on), tight cycle-time constraints, etc. -->

**Default location in code:**
<!-- link to where this is set -->

---

<!--
## Template — copy this when adding a new parameter

## `parameter_name`

Short one-line description of what the parameter controls.

**Rule of thumb:**
- Case A → value/range and reasoning
- Case B → value/range and reasoning

**Example:**
```python
.method(..., parameter_name=value)
```

**Why:** failure mode this prevents.

**When to deviate:** edge cases that change the answer.

**Default location in code:** file:line or config key.

---
-->

## Open questions / things to document later

<!-- park half-formed ideas here so they aren't lost -->
-
