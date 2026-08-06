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
3. **`rt.op(key=value, ...)` — the operator-language channel. BUILT.**
   Sibling of `rt.step`: actions publish HMI-worthy values explicitly —
   `rt.op(state="Filling tube 3 of 8")`, `rt.op(weight=12.4)`.
   `rt.step` stays as the engineer timeline, reachable behind a
   "details" toggle in the pendant — never its default face.

   **Why a second channel and not a `rt.step` level.** They differ in
   semantics, and the difference is what a widget needs:

   | | `rt.step` | `rt.op` |
   |---|---|---|
   | behaviour | **append** — every call is a timeline entry | **replace** — a key has one current value |
   | payload | a formatted human string | typed data (`12.4`, plus the widget's unit) |
   | per call | one line | many keys at once |

   Overloading `rt.step` would force the server to filter value-entries
   back out of the timeline, deliver pre-formatted strings a widget
   cannot re-format or trend, and spend one timeline entry per value.

   ### The channel (implemented)

   * **Transport** — the runtime server's WS. `/ws/op` for a dedicated
     client, and `op_state` on the multiplexed `/ws` — the same
     dual-broadcast pattern `runtime_status` / `step_state` use. No new
     socket, no polling.
   * **Wire shape** — deltas with a monotonic revision:
     `{"rev": 417, "set": {"weight": 12.4}, "unset": ["last_error"]}`.
     A connecting client first gets `{"rev": n, "set": {...},
     "snapshot": true}`, so a freshly-opened pendant is never blank and
     a gap in `rev` tells a client to resync instead of trusting a
     stale reading.
   * **Coalescing** — writes mark keys dirty; the server flushes on a
     100 ms cadence (`OP_FLUSH_MS`). Measured: **500 writes in 6.9 ms
     cost exactly one message.** Last-write-wins, so a slow client
     loses intermediate values (correct for a value channel) and never
     causes queue growth. The pending set is a send queue — drained
     even with nobody connected, since the store itself holds the
     values and a new client is served by the snapshot.
   * **Never blocks** — `rt.op` writes memory and returns; it never
     pauses, never raises into the workflow, never touches the socket
     (project-guide §8).
   * **Bounded by construction** — ≤200 keys, ≤4 KB per value, ≤64 KB
     total; an unserialisable or oversized value is dropped with **one
     log line per distinct reason** (deduping on the key alone would
     still flood when the cap rejects a different key each call).
   * **Assets by reference** — small JSON values inline; images and
     other large data as a URL the widget loads over HTTP
     (`rt.op(last_image="/captures/apc/d_0142.jpg")`). The socket stays
     small and reconnects stay cheap.
   * **Lifetime** — memory only, cleared at run start, nothing written
     to the SD card. `rev` keeps climbing across runs so a reconnecting
     client never sees it go backwards.

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

### Declaration + widgets (implemented: `state`, `stat`, `progress`, `rack`)

`launch.yaml` points at the file (`hmi: hmi/hmi.j2`); the runtime
server parses it ONCE at construction, validates every entry against
the platform's widget catalog, and ships the result to the pendant as
an `hmi_spec` envelope on connect. Rules:

* An **unknown widget name is dropped with a startup warning** — a
  typo must be visible when the workspace launches, not silently
  missing on a pendant hours later.
* **Binding keys are not validated**: a key legitimately appears only
  once the action that publishes it runs.
* A broken or missing file **never blocks a launch** — the project
  falls back to the default pendant.
* A project with no `hmi:` key is unchanged, forever.

**The `rack` widget** is the site-visit ask — every position of a real
rack, live:

```yaml
  - widget: rack
    component: rack_falcon_15ml_1   # a rack in this project's scene
    bind: tubes                     # rt.op key holding {slot: state}
    label: Falcon rack
```

The split that keeps it generic:

* **The grid is derived, never authored** — rows, columns and slot
  names are read off the LIVE scene component at launch, so the
  pendant cannot show a rack the bench does not have. A component
  without rows/cols is skipped with a startup warning.
* **The states come from the project** — it owns its facts, so it
  publishes `rt.op(tubes={"A1": "done", "B1": "active", …})`. The
  platform owns only the GRAMMAR: `done` · `active` · `attention` ·
  `queued` · `empty`, with muted done, saturated active/attention, and
  a ✓ / ! badge so state is never carried by colour alone (§7).
* **`detail:` makes positions tappable** — a second op key holding
  `{slot: {label: value}}`; tapping a position opens a persistent
  pane showing that item's record (weight, barcode, dose, whatever the
  project records). A pane, not a popover: it never occludes
  neighbours and it still works on a 96-well plate. Omit `detail:` and
  the rack is display-only, exactly as before.

```python
# in an action, as the readings happen
_record(rt, tube, Weight=f"{grams} g")        # → rt.op(tube_info={...})
_record(rt, tube, Barcode=scan)
```

Adding a widget to the catalog is one entry in the pendant's registry
plus one row in the table above — no change to the loader, the
transport, or any project.

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

## 10b. Format decisions — what YAML holds, and where it stops

Recurring question: is `.j2`/YAML enough for kwargs and HMI, or should
these be Python / HTML? The answers, and the boundary that makes them
work:

**Run inputs stay in the kwargs schema** (`hmi/kwargs.j2`, pointed at
by `launch.yaml`). One schema, one source of truth;
`hmi.j2` declares how the pendant *presents* run setup, never a second
definition of it. (Rejected: moving kwargs into `hmi.j2` — two files
defining the same inputs is how they drift.)

**Why YAML holds for both today.** Both are *declarations*: a list of
fields with types, a list of widgets with bindings. The `slots` field
is the proof of the pattern — the yaml says `component:
rack_falcon_15ml_1`, and everything else (grid, slot names, geometry)
is DERIVED server-side from the scene. Declarations stay small when
the platform does the deriving.

**Where YAML genuinely stops**: computed schemas (a default that
depends on the scene), conditional fields ("only when this device
exists"), cross-field validation ("volume × tubes ≤ reservoir"), live
capacity checks. Jinja can fake some of it and gets ugly fast.

**The escape hatch when we need it** — and the constraint that shapes
it: today `launch_config()` is a pure YAML read; the orchestrator
never imports project code (project modules pull hardware imports —
the scene builder had to stub dorna2 for exactly this reason). So a
Python kwargs file must NOT be read by the orchestrator directly.
Two acceptable designs, in preference order:

1. **Schema from the workspace process** — the launched project
   already imports its own code; it can expose a computed schema over
   the existing API, and the orchestrator renders whatever it is told.
   Projects that need computation get Python; everyone else keeps
   YAML; the orchestrator stays import-free.
2. **A declared hook** — `kwargs_hook: params.py:build` executed in
   the *project* process (launch/replay), not the GUI.

Until a project actually needs it, neither is built: `slots` covered
the case that motivated the question.

**HTML for the HMI stays rejected** (§2): per-project HTML fractures
the design language and turns protocol authors into web developers.
If declaration-order layout proves too thin, the answer is layout
HINTS in the same YAML (`row:` grouping, like operator-action groups),
not markup.

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
| Run inputs stay in launch.yaml; hmi.j2 only presents them | One source of truth for what a run takes; two definitions drift. `slots` shipped this way (project-guide §3). |
| YAML/j2 for kwargs + HMI; Python only via the workspace process, never imported by the orchestrator | Both are declarations, and the platform derives the rest (grid from the scene). The GUI must not import project code — hardware imports; the builder needed dorna2 stubs for the same reason. |
| Differentiators to invest in: bench map + guided recovery | Bench map is zero-config (derived from scene — competitors hand-draw screens); recovery kills the "written for programmers" complaint. |
