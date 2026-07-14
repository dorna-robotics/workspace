# HMI — operator-facing pendant, declarative per project

**Status: design discussion captured, implementation NOT started.** This
document records the direction agreed after client feedback on the pendant;
we will return to it. Companion visual reference: the static mockups in
`docs/internal/hmi_mockups/` (disposable — see §9 Housekeeping).

## 1. The trigger (client feedback, verbatim spirit)

A sample client saw the pendant during a run. Verdict:

- The **buttons are good** — but put the four control tiles on ONE line.
- The **monitoring section is bad**: the step list (`transfer 1: tip A1,
  A1 → A2`) is written for programmers, not people on site. They want
  something like an HMI — big, bold, relevant — but modern like the rest
  of the GUI.
- The **Parameters dialog** is a tiny debug form, not a run-setup screen.

This echoes a platform-wide issue: operator surfaces must speak operator
language, and per-project relevance must not mean per-project web code.

## 2. The principle

**The project declares, the platform renders** — the same pattern as
`operator_actions` (labels → icons → groups, all declared by components,
all rendered by one UI). Concretely:

- Each project ships an **`hmi.j2`** (separate file, next to `recipes.j2`).
- It lists **widgets from a platform-owned catalog**, each bound to a data
  source. No project ever contains HTML/JS.
- A project with no `hmi.j2` gets today's default pendant — nothing breaks.

Rejected alternative: per-project HTML pages. Maximum flexibility, but it
turns protocol authors into web developers, fractures the design language,
and rots. Every serious player (Grafana panels, Ignition faceplates,
Opentrons' run app) converged on declarative widgets + data binding.

## 3. Data channels (bindings)

Three sources; two exist, one is a small addition:

1. **Facts** — the BT world state (`done(t)`, `in_shaker(t)`…). Drives
   rack/bench slot colors and checklists with zero new action code.
2. **Platform state** — progress %, runtime state, elapsed time, device
   health, joint stream (rail position). Already streamed.
3. **`rt.op(key=value, ...)` — NEW, the operator-language channel.**
   Sibling of `rt.step`: actions publish HMI-worthy values explicitly —
   `rt.op(state="Filling tube 3 of 8")`, `rt.op(weight=12.4)`. Key-value
   store pushed over the existing status WS; widgets bind by key.
   `rt.step` stays as the engineer timeline, reachable behind a
   "details" toggle in the pendant — never its default face.

## 4. Widget catalog (v1 candidates — all mocked in the gallery)

| Widget | Shows | Binds |
|---|---|---|
| `state` | big bold headline ("Filling tube 3 of 8") | `rt.op` key |
| `stat` | large number + label + unit | `rt.op` key |
| `progress` / `ring` | run progress bar / ring | platform progress |
| `timer` | elapsed / estimated remaining | platform |
| `rack` | slot map of one rack, colored by facts | component + fact map |
| `bench` | the subway bench map (see §5) | scene + facts + rt.op |
| `alert` | attention banner (ok/warn/error) | pause reason / device events / rt.op |
| `devices` | health dots per device | device bus |
| `checklist` | per-item step list in operator words | facts |
| `trend` | sparkline of a value's history | `rt.op` key history |
| `gauge` | analog-style value (temp, pressure) | `rt.op` key |
| `camera` | live vision feed frame | vision server |
| `scan` | last barcode, big mono | `rt.op` key |
| `queue` | upcoming batches | platform |
| `keyval` | small table (operator, recipe, started) | mixed |

Catalog is **expandable by design**: one renderer entry per widget (exactly
like the operator-icon set); projects gain new widgets with zero changes.

`hmi.j2` sketch:

```yaml
hmi:
  - {widget: state,    bind: op_state}
  - {widget: bench}
  - {widget: progress, bind: progress}
  - {widget: stat,     bind: weight, label: Last weight, unit: g}
```

Layout: stacked in declaration order (v1); a `row:` grouping hint like the
operator-action `group` may follow if needed.

## 5. The `bench` widget — subway map (the differentiator)

**Decision: subway map, not satellite photo.** The 3D viewer already owns
realism (and is available in the pendant PiP); chasing realism here would
duplicate it and bury state. The bench widget answers exactly two
questions: *what state is every position in* and *where do I walk*.

- **Uniform station cards** — one card per top-level component, same size
  and visual weight regardless of physical size. No scaled footprints.
- **Spatially arranged** — cards placed by their true plate/position from
  the kinematic tree; the rail is a bar with the live carriage ("robot at
  shaker" readable in half a second). Rough spatial fidelity for
  orientation; millimeters give nothing.
- **One dot language.** A dot = an **item position** (rack slot, shaker
  seat, scale seat, vortex socket, tool-rack seat). Filled = something is
  there, hollow = empty; color = process state:
  - muted sage `#cfe3d4` — done
  - saturated accent + breathing halo — active
  - hollow gray — empty / waiting
  - filled gray — occupied, idle (e.g. a parked tool)
  - red — attention
  Stations with no item positions (camera, waste) are plain cards — label
  only, accent border when active.
- **Fully derived, zero drawing**: geometry from component poses +
  collision-box footprints (placement only), seats from slot/place
  anchors, occupancy from the kinematic tree ("is something attached
  there"), process state from facts, active station auto-published by the
  Recipe layer (every recipe knows its component), robot position from
  the rail joint. Add a device to `layout.j2` → it appears on the map.
  The map cannot drift from reality because it is not authored.
- Composition with 3D: tap a station card → pendant's 3D PiP flies to
  that station. Schematic for state, 3D for detail.

## 6. Guided recovery (the attention screen)

High-performance-HMI doctrine: operators need *the next action*, not a
dashboard. When the run pauses on a fault, the pendant becomes an
attention screen:

- Amber header, **one sentence** ("Scale is not responding"),
- one reassurance line ("paused safely after capping tube 5 — nothing was
  lost"),
- **one big fix button — a real `operator_action`** ("Reconnect Scale"),
- Resume visibly waiting below, unlocking when the device recovers,
- quiet context row (done 4/8 · paused at tube 5 · waiting 1:42).

All the pieces exist: pause reasons, device states, `operator_actions`.
This fuses them. Normal running = quiet screen; abnormal = one sentence +
one button.

## 7. Color discipline (applies to every widget)

Muted/neutral for normal; **saturated color reserved for active and
attention**. A screen that is colorful when everything is fine trains
operators to ignore color. Done-states are muted sage, not saturated
green; idle stations are gray; the only things that pop are the active
position and problems. (ISA-101 / high-performance HMI practice.)

## 8. Parameters → run setup

Keep the kwargs schema (good declarative bones); grow operator-ward:

- **Presets** — named parameter sets in `launch.yaml` ("Standard run",
  "QC run", "Custom…" seeded from last run). Operators pick a card, not
  seven fields. The full form remains for engineers.
- **Touch widgets by type** — bool → toggle, choice → segmented buttons,
  int → big ± stepper. Same schema, better renderers.

## 9. Housekeeping — the mockups (REMOVE LATER)

Static visual references, fake data, built on the platform design tokens:

- **Canonical copy (versioned):** `docs/internal/hmi_mockups/` in this
  repo — index.html links all seven pages (A headline, B rack-centric,
  C split dashboard, D parameters, E widget gallery, F subway bench map,
  G guided recovery).
- **Preview copy (this bench only):** `/home/dorna/Downloads/hmi_mockups/`
  served at `http://10.0.0.20:8123/` by a manually-started
  `python3 -m http.server 8123` (dies on reboot; restart from that folder
  if needed; stop with `pkill -f "http.server 8123"`).
- **Cleanup when HMI ships:** delete both folders and kill the server —
  the real widgets supersede them. This section is the reminder.

## 10. Rough build order (when we pick this up)

1. Pendant control tiles on one line (pure CSS, independent).
2. `rt.op()` channel + `state` / `stat` / `progress` widgets + `hmi.j2`
   loader — smallest slice that transforms the top of the pendant.
3. Guided-recovery screen (fuses existing pause/device/operator-action
   machinery).
4. `bench` subway widget (scene-derived; the demo piece).
5. Parameters presets + touch inputs.
6. Remaining catalog widgets on demand; end-of-run report screen
   (composes the same widgets + `rt.op` history) as a later chapter.

## 11. Decision log

| Decision | Why |
|---|---|
| Declarative widget catalog, no per-project HTML | Platform pattern (operator_actions precedent); industry convergence; protocol authors are not web developers. |
| Separate `hmi.j2` per project | User decision; mirrors recipes.j2 / datasets.j2. |
| `rt.op()` operator-language channel; `rt.step` demoted to details | Monitoring text must be operator words; engineer timeline preserved but not the default face. |
| Bench map = subway map, not realistic top view | 3D viewer already owns realism; HMI's job is state-at-a-glance; schematic scales to any bench automatically. |
| Dots = item positions only; filled/hollow = occupancy; color = process state | Fixes "what do circles on tools mean" — one grammar for tubes, tools, seats. Camera/waste get plain cards. |
| Color discipline: muted normal, color for active/attention | High-performance-HMI practice; colorful-when-fine trains color blindness. |
| Guided recovery: one sentence + one operator_action button | Operators need the next action, not a dashboard; all machinery already exists. |
| Parameters: presets + touch widgets over the existing kwargs schema | Converts a debug form into run setup without discarding the schema. |
| Differentiators to invest in: bench map + guided recovery | Bench map is zero-config (derived from scene — competitors hand-draw screens); recovery kills the "written for programmers" complaint. |
