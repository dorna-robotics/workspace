# Design guide — one system for every platform surface

Applies to every GUI the platform ships: orchestrator admin, pendant,
scene builder, vision server pages, and the HMI widgets to come. The
goal is the same as the recipe/skill conventions: **a new surface starts
from written rules, not from eyeballing the last one.**

**Source of truth for values: `workspace/gui/vendor/base.css`.** Every
color, radius, spacing, type size, shadow and motion constant is a CSS
variable there, in both themes. This document doesn't restate the
values — it says which one to use when, and what every component owes
its user. If a value you need isn't a token, add the token first.

## 1. Token roles (which variable, when)

| Need | Token | Rule |
|---|---|---|
| Page background | `--bg` | never used for panels |
| Panel / card | `--surface` | one elevation step above bg |
| Nested / hover surface | `--surface2` | hover fills, wells, inset lists |
| Hairlines | `--border` / `--border2` | never hand-mixed rgba |
| Primary text | `--text` | |
| Secondary text | `--muted` | labels, hints, units |
| Interactive / active | `--accent` (`--accent-h` hover, `--accent-dim` fills) | the ONLY blue |
| Success / done | `--green` | see color discipline §3 |
| Warning / attention | `--amber` | |
| Error / danger | `--red` | |
| Radii | `--radius-xs/sm/md/lg/xl` | per the comments in base.css; no raw px |
| Spacing | `--space-1…6` (4/6/8/12/16/24) | gaps, paddings; no raw px |
| Type | `--text-xs/sm/md/lg/xl` | §2; no raw px font sizes |
| Fonts | `--font`, `--mono` | mono = data (ids, joints, values), never prose |
| Shadows | `--shadow-sm/`/`--shadow-lg`, `--glow*` | glow only for live/attention |
| Motion | `--motion-fast/med/slow` + `--ease` | §5 |

**Rule zero: no raw values in page CSS.** A hex color, a bare `px`
font-size or padding in a page stylesheet is a defect (exception:
one-off geometry like a specific grid width). Mockups under
`docs/internal/` are exempt — they are disposable negotiation artifacts.

## 2. Type & density

- `--text-xs` — tracked-caps section labels (`letter-spacing:0.05em;
  text-transform:uppercase; color:var(--muted)`) and hints. Nothing else.
- `--text-sm` — secondary/meta text, table cells, badges.
- `--text-md` — body, buttons, inputs. The default.
- `--text-lg` — card headings, primary labels, stat values in compact tiles.
- `--text-xl` — modal/page headings.
- Larger than `--text-xl` (pendant headlines, HMI state text) is a
  surface-specific decision — still declared once in that surface's
  stylesheet, not sprinkled per element.
- Numbers an operator compares (weights, joints, counts) get
  `font-variant-numeric: tabular-nums` and `--mono`.

## 3. Color discipline (platform-wide; adopted from the HMI decisions)

- **Muted when normal, saturated only for active and attention.** A
  screen that is colorful when everything is fine trains people to
  ignore color. Done-states lean muted; the only things that pop are
  the live thing and the problem.
- **`--accent` means "interactive or live" — nothing else.** Not
  decoration, not branding.
- **Never color alone.** Any state carried by color also carries a
  shape: ✓ badge on done, ! badge on attention, hollow vs filled for
  occupancy, slash on a hidden-eye. (Color-blind operators exist;
  monitors vary.)
- Red is for *needs a human*. Amber is for *degraded / check this*.
  Green is for *confirmed good* — not for "merely present".

## 4. Component state matrix

Every interactive component ships ALL of its states the day it ships —
states are not follow-up polish. The platform set:

| Component | rest | hover | pressed | selected/active | disabled | loading | empty | error |
|---|---|---|---|---|---|---|---|---|
| Button (`.btn`) | surface + border | `--surface2` | scale 0.97 | `--accent` fill, white text | 40% opacity, no pointer | spinner replaces label, width kept | — | red border + shake once |
| Icon button | transparent | `--surface2` circle | scale 0.94 | `--accent-dim` fill | 40% opacity | spinner | — | — |
| Card / panel | surface, `--shadow-sm` | — (cards don't hover unless clickable) | — | `--accent` border | — | skeleton block | §6 empty text | inline `--red` banner |
| List row | transparent | `--surface2` | — | `--accent-dim` fill + accent border | 40% | shimmer row | "No X yet" row | row with `--red` dot + reason |
| Pill / badge | per state color | — | — | — | gray | pulsing dot | — | — |
| Input | surface + border | border `--text` | — | `--glow` focus ring | 40% | — | placeholder (`--muted`) | `--red` border + message BELOW |
| Toggle (eye, sim) | per state icon | `--surface2` | scale 0.94 | slashed/filled icon swap | 40% | — | — | — |
| Tabs / segmented | `--muted` text | `--surface2` | — | white/raised segment | 40% | — | — | — |

Two universal rules:
- **Anything async shows progress where the user is looking** — the
  orchestrator "Starting…" idiom (spinner + short text, `.ctrl-starting`)
  for phases; per-row "solving…" for row-level work; button-spinner for
  button-level work. Silent latency is a defect (the builder taught us
  this three times in one day).
- **Anything hidden or disabled says why on hover/title.**

## 5. Motion

- `--motion-fast` — hovers, toggles. `--motion-med` — panels, fades,
  selection. `--motion-slow` — page-level, celebratory. Always `--ease`.
- Breathing/pulse (1.6s) is reserved for *live* things: the active
  slot, the working station, a connecting dot. Never decoration.
- Layout must not jump: reserve space for late content (the sidebar
  width lesson — set state before first paint when it's persisted).
- Respect `prefers-reduced-motion`: pulses drop to static highlights.

## 6. Empty, first-run and loading surfaces

- Every list/panel has a designed empty state: one muted sentence plus
  the action that fills it ("No components yet — Insert adds the
  first"). Never a blank rectangle.
- First paint shows structure (skeletons / reserved boxes), not a
  reflow. Progressive content fills in place.

## 7. Touch & accessibility (operator surfaces: pendant, HMI, builder)

- **Minimum target 44×44 px** for anything an operator taps on glass.
  Desktop-only admin surfaces may go to 32 px.
- Gestures are accelerators, never the only path: anything reachable by
  hold/drag/double-click also exists as a visible control (hold-to-edit
  must have an Edit affordance; drag-select must have Select all).
- Contrast: text ≥ 4.5:1, large text ≥ 3:1, both themes. Muted text on
  `--surface2` is the usual offender — check it.
- Focus is visible (`--glow` ring); dialogs trap focus; Esc closes.

## 8. Language

- Operator surfaces speak operator words ("Filling tube 3 of 8"), the
  engineer timeline lives behind a details toggle (HMI decision).
- Buttons are verbs ("Rescan", "Skip disc"), never nouns or codes.
- One sentence per problem, then the next action — the guided-recovery
  pattern is the template for every error surface.

## 9. New-surface checklist

Before a new page/panel/widget merges:

1. Values from tokens only (rule zero).
2. Every interactive element covers its §4 state row.
3. Async work has a visible indicator at the right level (§4 rules).
4. Empty state designed (§6).
5. Touch targets ≥ 44 px if operators touch it (§7).
6. Color states carry a non-color signal (§3).
7. Language check (§8) — no engineer-speak on operator surfaces.
8. Both themes checked (the light theme is the one that drifts).
9. Anything the surface teaches (a new grammar, a new widget) gets a
   line in the relevant guide (this file, hmi-guide, …) — the rule
   that keeps the NEXT surface consistent.

## 10. Process: mockup first, grammar on sign-off

Visual work follows the HMI workflow: disposable mockups on the design
tokens (`docs/internal/*_mockups/`) → agree → codify the new grammar in
the guide → build the real thing from the guide. Mockups never ship;
grammars do.

## 11. Known debt (fix opportunistically, no big-bang)

- Builder panels (recipes list, drag readout) use inline `cssText` with
  raw px — migrate to classes on tokens when next touched.
- HMI mockup pages duplicate slot CSS per page — fine (disposable), but
  the production rack/bench widgets must define the slot grammar once.
- A contrast audit of `--muted` on `--surface2` in light theme is
  pending.
