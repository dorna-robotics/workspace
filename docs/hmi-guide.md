# HMI — operator-facing pendant, declarative per project

**Status: shipped.** The `rt.op` channel (§3), the project-screen host
(§4b) and the fallback widget catalog (§4) are built and in use by bd.
The bench map (§5) and guided recovery (§6) remain design.

**Start at §2 and §4b** — the project owns its screen, the platform owns
the data and the frame. §2 records a reversal of this document's original
principle and why. Companion visual reference: the static mockups in
`docs/internal/hmi_mockups/` (disposable — see §9 Housekeeping).
Task playbook: `.claude/skills/project-ui/SKILL.md`.

## 0. The three operator-UI keys

Everything operator-facing a project declares, in one place:

```yaml
default:  hmi/default.j2     # the kwargs' defaults (data — Python reads it)
setup:    hmi/setup.js       # screen to SET the kwargs, before the run
pendant:  hmi/pendant.html   # screen shown DURING the run
```

| key | what | format | read by | served by | doc |
|---|---|---|---|---|---|
| `default:` | the kwargs' defaults / schema | yaml/j2 (forced) | replay, launch, CLI, GUI | — | project-guide §3 |
| `setup:` | run-setup screen | `.html`/`.js` | browser only | orchestrator (pre-launch, same-origin) | project-guide §3 |
| `pendant:` | during-run screen | `.html`/`.js` | browser only | runtime server `/hmi/` (CORS) | §4b below |

Only `default:` is required; each screen is opt-in with a clean fallback
(generic form / default pendant). The format rule is §10b: **if Python
reads it, it's yaml; if only the browser reads it, it's the project's
file.** Reference implementation: bd's `hmi/` folder.

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

**The platform owns the data and the frame; the project owns its
screen.** The workspace carries no domain UI — no tube widget, no disc
widget, no rack-that-knows-what-a-falcon-is. Concretely:

- A project ships **its own screen file** — `hmi/pendant.html` (+ an
  optional sibling `.css`) or `hmi/pendant.js` — and points `launch.yaml`
  at it with `pendant: hmi/pendant.html`. This is the primary path. §4b is
  the contract.
- The platform hosts that file in the pendant's content area, hands it
  live values from `rt.op`, and gives it the design tokens. It never
  interprets what the values MEAN.
- A project with no `pendant:` key gets today's default pendant — unchanged,
  forever.

**This reverses an earlier decision, deliberately.** v1 of this document
rejected per-project HTML in favour of a platform-owned widget catalog
(the `operator_actions` precedent; Grafana/Ignition/Opentrons all
converged on declarative widgets). We built that catalog — `state`,
`stat`, `progress`, `rack` — and then used it for bd. What it showed:

- every genuinely useful widget was **domain-shaped**. The `rack` widget
  needed a slot-state grammar, then a detail pane, then per-slot records
  — each one a piece of one project's problem, permanently in the
  platform, carried by every project that will never use it.
- the catalog can only ever be as expressive as its last addition, so
  each project's real ask arrived as a platform change — the opposite of
  scalable.

The generic half was never the problem, and it stays: the channel, the
transport, the hosting, the tokens. Only the *domain* half moves out.

**What keeps this from fracturing the design language** — the objection
that drove v1, which is real:

- the screen renders inside the platform's frame (navbar, control rail,
  state pill, alarms) — a project styles its content area, not the
  pendant;
- it inherits the **design tokens** (`--accent`, `--surface`, `--space-*`,
  `--radius-*`). Following them is the path of least effort, and light/
  dark then works with no theme code in the project (verified: bd's CSS
  names no colour, and its screen flips with the toggle);
- `docs/design-system.md` is the reference a project screen is held to.

The cost — a protocol author now writes some HTML — is real and accepted.
It is bounded: the HTML shape (§4b) is markup plus `data-bind`
attributes, no JavaScript, and bd's whole screen is 45 lines.

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

## 4b. The project screen — the contract (IMPLEMENTED, primary path)

`launch.yaml`:

```yaml
pendant: hmi/pendant.html     # or hmi/pendant.js
```

Everything under the project's `hmi/` folder is served by the runtime
server at `/hmi/…`, so a screen can pull in its own css, modules and
assets with plain relative paths. The pendant is served by the
orchestrator on a **different port**, so those files carry
`Access-Control-Allow-Origin: *` (`HmiStaticFileHandler`) — without it
`fetch()` and `import()` refuse the body and the screen never mounts.

**Hosting.** The file is mounted in a **shadow root** on the pendant's
content area. That gives isolation in both directions — project CSS
cannot reach the rail or the navbar, platform CSS cannot restyle the
project's markup — while **CSS custom properties inherit through the
shadow boundary**, so design tokens and the theme toggle just work.
A screen that fails to load leaves a quiet note in place; **the run is
never affected**.

### The HTML shape — markup + `data-bind`, no JavaScript

```html
<h1 data-bind="state">—</h1>
<span data-bind="weight" data-unit="g">—</span>
<div class="slot" data-bind-map="tubes" data-slot="A1">A1</div>
<img data-bind-attr="last_image" data-attr="src">
```

| attribute | effect |
|---|---|
| `data-bind="key"` | element text ← `rt.op` key (`data-unit` appends a unit) |
| `data-bind-map="key"` + `data-slot="A1"` | sets `data-state` from `{slot: state}`; **style it in your own CSS** |
| `data-bind-attr="key"` + `data-attr="src"` | sets any attribute — images, links, progress values |

`data-bind-map` is how a rack is drawn now: the project writes 20 divs
and its own CSS for `[data-state="done"]`, and the platform stays
ignorant of racks. The state names are the project's own — the
six-state grammar in §4 is a *recommendation* that reads well, not a
platform rule.

### The JS shape — for screens with logic

```js
export default {
  css: `.chart { color: var(--accent); }`,        // optional
  mount(root, api) { … },                          // root = the shadow root
  update(values) { … },                            // every rt.op delta
};
```

`api` is deliberately small: `values` (current snapshot), `theme`
(`"light"`/`"dark"`), `onTheme(cb)` (fires on toggle — for canvas and
anything else that must repaint), and `invoke(component, method)`,
which routes to the **same declared `operator_action` path** the
platform's own buttons use. A screen can trigger what a component
already declares, and nothing more — it gets no runtime handle, no
device handle, no way to command the robot.

Use JS when the screen computes (charts, canvas, animation, derived
values); use HTML for everything else. A `.js` screen can import its
own modules from `hmi/` normally.

Verified end to end for both shapes: shadow attach, `mount`/`update`,
token resolution inside the shadow CSS (`#007aff` light →
`rgb(10,132,255)` dark), `onTheme` on toggle, and isolation from the
host document.

## 4. Widget catalog — the no-front-end fallback

`pendant: hmi/hmi.j2` gets a stacked list of platform-drawn widgets instead
of a project screen. It exists for the project that wants a usable
operator face without writing any markup — a bring-up bench, an
internal test rig, a first day on a new protocol.

**It is not where new work goes.** Widgets already shipped
(`state`, `stat`, `progress`, `rack`) stay and keep working; the rest of
this table is a record of what was *considered*, not a roadmap. A new
domain need is a project screen (§4b), not a new catalog entry —
anything domain-shaped added here is carried by every project forever
(§2). The generic ones that may still earn their place are the ones
bound to platform data, not project data: `timer`, `devices`, `alert`.

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

### Declaration + widgets (implemented: `state`, `stat`, `progress`, `rack`)

`launch.yaml` points at the file (`pendant: hmi/hmi.j2`); the runtime
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
* A project with no `pendant:` key is unchanged, forever.

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
  platform owns only the GRAMMAR:

  | state | means | reads as |
  |---|---|---|
  | `empty` | nothing here / not in this run | dashed ring |
  | `queued` | selected, not started | solid ring, muted fill |
  | `working` | started, not finished | accent tint |
  | `active` | **the robot is here now** | saturated accent + breathing ring |
  | `attention` | needs a human | amber + `!` badge |
  | `done` | finished | muted green + `✓` badge |

  `working` vs `active` is the one that matters in practice: a batch
  INTERLEAVES items, so several are underway at once. Without the
  distinction every started item reads as active and the operator
  cannot see where the robot actually is. A project keeps exactly one
  position `active` — bd demotes the previous one to `working` inside
  its `_mark()` helper.

  Done is muted and only active/attention are saturated (§7); each
  state also differs in FORM (ring style, badge, animation) so colour
  is never the only signal.
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

**Run inputs stay in the schema** (`hmi/default.j2`, pointed at
by `launch.yaml`). One schema, one source of truth;
`hmi.j2` declares how the pendant *presents* run setup, never a second
definition of it. (Rejected: moving kwargs into `hmi.j2` — two files
defining the same inputs is how they drift.)

**Why YAML holds for the SCHEMA.** It is a genuine declaration — a
list of fields with types and defaults — and, decisively, it is read
with **no browser anywhere**: `bt.replay`, the CLI and launch all
resolve kwargs headlessly. A schema in HTML would mean
`replay --batch 4` could not know what a run takes. That constraint,
not taste, is what keeps kwargs YAML.

**Why the FORM is not YAML.** The first version of this section
claimed `slots` proved declarations scale, because the platform
derived the rack grid from the scene. It proved the opposite: the
platform ended up owning a rack picker, tab bars and slot thumbnails —
one project's hardware, carried by everyone, and the next project's
tray or carousel would have been another platform change. So the
schema stayed YAML and the FORM became a project file (`setup:`,
project-guide §3), hosted exactly like the pendant screen.

The split that makes both true at once:

| | schema (`default:`) | form (`setup:`) |
|---|---|---|
| answers | what a run takes | how an operator picks it |
| read by | replay, CLI, launch, GUI | the GUI only |
| owns | types, defaults, limits | markup, layout, interaction |
| enforced by | the platform, on whatever the form returns | — |

**Where YAML genuinely stops for the schema too**: computed schemas (a
default that depends on the scene), conditional fields ("only when
this device exists"), cross-field validation ("volume × tubes ≤
reservoir"). Jinja fakes some of it and gets ugly fast.

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

Until a project actually needs it, neither is built — the `params`
screen covered the case that motivated the question, and it needs no
project code in the GUI process: the screen is fetched over HTTP and
runs in the browser, never imported by the orchestrator.

**The HMI is where YAML stopped, and we crossed the line on purpose**
(§2). A screen is not a declaration — it is a layout, and every attempt
to declare one ends in the platform owning domain widgets. So the
project's screen is a FILE it writes. Note the asymmetry with kwargs
above: run inputs are genuinely a list of typed fields, so they stay
YAML; a screen is not, so it does not.

The constraint that made this safe is that the pendant is the only
thing that reads it — no orchestrator import, no project code in the
GUI process. The screen is fetched over HTTP and mounted in a shadow
root, so a broken screen is a blank content area, not a broken launch.

## 11. Decision log

| Decision | Why |
|---|---|
| ~~Declarative widget catalog, no per-project HTML~~ | Platform pattern (operator_actions precedent); industry convergence; protocol authors are not web developers. **REVERSED — see the row below.** Kept here so the argument is not re-litigated from scratch: the design-language risk it names is real, and §2 says how hosting answers it. |
| **The project's screen is a file it owns** (`hmi/pendant.html` / `.js`); the platform hosts it and feeds it `rt.op` | Building the catalog proved the objection: every useful widget was domain-shaped, so the platform accumulated one project's problem (rack → slot grammar → detail pane → per-slot records) and each new need arrived as a platform change. The workspace carries the channel, the frame and the tokens — never the domain. |
| Hosted in a shadow root, tokens inherit through the boundary | Isolation both ways (project CSS cannot reach the rail; platform CSS cannot restyle the project) while `--accent`/`--surface`/`--space-*` still resolve — so following the design system is the low-effort path and light/dark needs no project code. |
| Screen reaches the platform only via `invoke()` → declared `operator_action` | A project screen gets no runtime, device or motion handle — it can trigger what a component already declares, and nothing else. |
| Widget catalog demoted to the no-front-end fallback | Still the right answer for a bring-up bench with no markup; wrong as the place domain features land. |
| Separate `hmi.j2` per project | User decision; mirrors recipes.j2 / datasets.j2. |
| `rt.op()` operator-language channel; `rt.step` demoted to details | Monitoring text must be operator words; engineer timeline preserved but not the default face. |
| Bench map = subway map, not realistic top view | 3D viewer already owns realism; HMI's job is state-at-a-glance; schematic scales to any bench automatically. |
| Dots = item positions only; filled/hollow = occupancy; color = process state | Fixes "what do circles on tools mean" — one grammar for tubes, tools, seats. Camera/waste get plain cards. |
| Color discipline: muted normal, color for active/attention | High-performance-HMI practice; colorful-when-fine trains color blindness. |
| Guided recovery: one sentence + one operator_action button | Operators need the next action, not a dashboard; all machinery already exists. |
| Parameters: presets + touch widgets over the existing kwargs schema | Converts a debug form into run setup without discarding the schema. |
| Run inputs stay in the kwargs schema; the form only presents them | One source of truth for what a run takes; two definitions drift. |
| Schema stays YAML, the run-setup FORM becomes a project file (`setup:`) | The schema is read headlessly (replay, CLI, launch) so it cannot be markup; the form is read only by the GUI. `type: slots` had put a rack picker, tab bar and slot thumbnails in the platform — deleted, and bd now draws its own (project-guide §3). |
| YAML/j2 for kwargs + HMI; Python only via the workspace process, never imported by the orchestrator | Both are declarations, and the platform derives the rest (grid from the scene). The GUI must not import project code — hardware imports; the builder needed dorna2 stubs for the same reason. |
| Differentiators to invest in: bench map + guided recovery | Bench map is zero-config (derived from scene — competitors hand-draw screens); recovery kills the "written for programmers" complaint. |
