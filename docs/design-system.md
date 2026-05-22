# Dorna GUI Design System

Single source of truth for the orchestrator + workspace + pendant UI.
Everything visible to an operator routes through the tokens below —
if you find yourself typing a raw `px` value or an `rgba()` for a
status colour in a `.css` file, you're working against the system.

The design language is **industrial operator console** — Linear /
Tailscale, not Bootstrap dashboard. Dense, deliberate, no chrome for
chrome's sake.

## 1. Where the system lives

| File | What's in it |
|---|---|
| [workspace/gui/vendor/base.css](../workspace/gui/vendor/base.css) | All tokens. Buttons, pills, inputs, toasts, modal chrome, confirm dialog, scrollbar, reduced-motion, shared step-list primitive. |
| [workspace/gui/vendor/nav.css](../workspace/gui/vendor/nav.css) | App-wide left nav. Uses the same tokens; no extra CSS variables. |
| [workspace/gui/orchestrator/web/admin/style.css](../workspace/gui/orchestrator/web/admin/style.css) | Workspace + pendant specifics that don't fit in the shared primitives. |
| [workspace/gui/orchestrator/web/admin/index.html](../workspace/gui/orchestrator/web/admin/index.html) | Dashboard page-scoped CSS in a `<style>` block — kept page-local because dashboard layout doesn't belong in `base.css`. |
| [workspace/gui/orchestrator/web/admin/workspace.html](../workspace/gui/orchestrator/web/admin/workspace.html) | Workspace page-scoped CSS, same reasoning. |

If a rule is used by ≥ 2 pages, hoist it to `base.css`. If it's
truly page-specific (sidebar layout, viewer placeholder), it belongs
in the page's inline `<style>`.

## 2. Tokens — the vocabulary

All tokens are defined on `:root` in `base.css`, with `[data-theme="light"]`
overrides where needed (`--glow-*`, surface colours).

### 2.1 Radius

| Token | Value | Use for |
|---|---|---|
| `--radius-xs` | 6 px | Mini chips, scrollbar thumb, tag pills. |
| `--radius-sm` | 10 px | Buttons (`.btn` family), inputs, search field, small chips. |
| `--radius-md` | 12 px | Meta rows, list cells, pendant nav pills, info chips. |
| `--radius-lg` | 16 px | **Cards** — `.ws-card`, modal, confirm dialog, stats bar, pendant steps. |
| `--radius-xl` | 22 px | Reserved for the pendant cinematic tiles only (`.pendant-btn`). |

`999 px` is allowed for fully-pill-rounded shapes (`.pill`, count
badges). Never invent a new pixel value.

### 2.2 Spacing

`--space-1..6` → `4 / 6 / 8 / 12 / 16 / 24`.

Use them on `gap`, `padding`, `margin`. Reach for them before
typing a raw `px`. Examples:

```css
.foo  { gap: var(--space-3); padding: var(--space-4) var(--space-5); }
.bar  { margin-bottom: var(--space-2); }
```

The scale is loose — you don't have to hit every step. But two
adjacent rules using `8 px` and `10 px` is wrong; pick one (`--space-3`).

### 2.3 Typography

5 steps, sized for an operator app where most text is data:

| Token | Value | Use for |
|---|---|---|
| `--text-xs` | 11 px | Tracked-caps section labels, hints, badge text. |
| `--text-sm` | 12 px | Meta values, secondary text, device rows. |
| `--text-md` | 13 px | Body, buttons, default. |
| `--text-lg` | 15 px | Card headings, primary action labels, dashboard card name. |
| `--text-xl` | 17 px | Modal `<h3>`, page H1, confirm dialog title, ws-title. |

Tabular numerics (`font-variant-numeric: tabular-nums`) for any
time / count / measurement display.

The pendant timer (`.pendant-timer`, 26 px / 700 / mono) is the
**one place** that explicitly exits the type scale. It's the hero
number; you'll have noticed it's deliberately too big for the
operator to miss across the room.

### 2.4 Motion

| Token | Value | Use for |
|---|---|---|
| `--motion-fast` | 0.12 s | Hover / colour shifts. |
| `--motion-med` | 0.20 s | Background / box-shadow / position. |
| `--motion-slow` | 0.40 s | Overlay tint changes, schedule block fill morphs. |
| `--ease` | `cubic-bezier(0.2, 0.9, 0.3, 1)` | The default curve. |

Every transition in the app uses these. Two documented exceptions:

- `transform 0.05 s` on `.pendant-btn:active` — press feedback,
  intentionally snappier than `--motion-fast`.
- `cubic-bezier(0.25, 0.8, 0.25, 1)` on the nav width / margin
  sweeps — bespoke layout-shift curve.

If you need a *new* exception, document it in a comment next to
the declaration so the next person doesn't "fix" it.

### 2.5 Glow + shadow

| Token | Use for |
|---|---|
| `--glow` | Focus ring on `:focus-visible`. |
| `--glow-ok` | State-coloured glow (green): `.ws-card.is-running::before`, future status rails. |
| `--glow-warn` | Amber state. |
| `--glow-bad` | Red state. |
| `--shadow-sm / --shadow / --shadow-lg` | Standard surface elevations. |

Never hardcode an `rgba()` for a state-coloured glow. Use the
tokens; if a new shade is needed, *add* it to `base.css` and
document why.

## 3. Surfaces — the pattern catalogue

### 3.1 Cards

Flat surface (`var(--surface)`), `--radius-lg`, no border, modest
`var(--shadow-sm)`. State is communicated by a **3 px coloured rail
on the left edge** (`::before`), not by background tinting.

```css
.foo-card {
  position: relative;
  background: var(--surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
}
.foo-card::before {
  content: "";
  position: absolute; left: 0; top: 0; bottom: 0;
  width: 3px;
  background: var(--surface3);             /* default: muted */
  transition: background var(--motion-med) var(--ease);
}
.foo-card.is-running::before { background: var(--green); box-shadow: var(--glow-ok); }
.foo-card.is-error::before   { background: var(--red);   box-shadow: var(--glow-bad); }
```

Reference: `.ws-card` in [index.html](../workspace/gui/orchestrator/web/admin/index.html).

### 3.2 Pills

| Use | Pattern |
|---|---|
| Status pill (running / paused / error) | `.pill` from base.css — fully rounded, uppercase, tracked. |
| Pendant nav pill (state, exit) | `--radius-md`, `var(--surface2)` background, `--shadow-sm`. |
| Pendant timer chip | Same metrics as the nav pills, monospace + tabular-nums, larger font. |
| Count badge | `999 px` radius, `--text-xs`, accent fill. |

### 3.3 Buttons

`.btn` family always uses `--radius-sm`. Three sizes
(`.btn-sm / .btn / .btn-lg`) and four variants (`.btn-primary /
.btn-danger / .btn-warn / .btn-ghost`). Size and variant compose:
`<button class="btn btn-sm btn-ghost">…</button>`.

The pendant cinematic tile (`.pendant-btn`) uses `--radius-xl` — a
deliberate "this is a big touch target, not a normal button" cue.
Don't apply that radius elsewhere.

### 3.4 Step lists

One shared primitive in `base.css` (`:is(.step-card, .pendant-step-card)`)
drives color states, dot glow, opacity ladder, transitions. Per-density
overrides (sidebar = compact, pendant = large) stay in each consumer's
file.

When adding a new view that needs a step list, alias your class into
the primitive:

```css
:is(.step-card, .pendant-step-card, .my-new-step-card) { … }
```

### 3.5 Modal

`.modal` uses `--radius-lg`, `--shadow-lg`, slides in with the
shared `attachModalIn` keyframe. Body padding `20 px`. Head/foot
padded `16 px 20 px`. Don't introduce a second modal shell.

## 4. Pendant-specific guidelines

The pendant is operator-facing on a tablet. The rules are tighter:

- **Layout**: navbar (state pill, timer, exit) is fixed; body
  scrolls underneath. New widgets go in the body, never floated
  over the nav.
- **State at a glance**: the navbar must communicate "what is the
  workspace doing right now" without the operator reading any text.
  State pill background tint + dot pulse does this.
- **Press feedback is non-negotiable**: every `.pendant-btn`
  scales to 0.93 on `:active` in 50 ms. Operators on touch need
  this confirmation.
- **Disabled = ghosted, not hidden**. `opacity: 0.2` + `filter:
  grayscale(0.6)` + `cursor: not-allowed`. Don't `display: none`
  the wrong-state buttons; muscle memory needs them in place.
- **No more uppercase + tracked text outside of `.pendant-btn`,
  `.pendant-state-text`, and `.section-label`**. Anything else
  reads as shouting.

## 5. Accessibility

### 5.1 Focus rings

Every interactive surface has a `:focus-visible` rule that puts
`var(--glow)` on it. If you add a new clickable element, **add a
focus ring**. This is non-optional — the pendant is keyboard-
operable when a Bluetooth keyboard is plugged in, and a missing
focus ring leaves the operator with no idea where they are.

```css
.my-clickable:focus-visible { outline: none; box-shadow: var(--glow); }
```

### 5.2 Reduced motion

`base.css` ends with a `@media (prefers-reduced-motion: reduce)`
block that collapses every animation and transition to ~0 ms. If
you add a new ambient animation (breathing, pulsing, blink), it
will be killed by this block automatically. Don't override it.

### 5.3 Touch contexts

Scrollbars widen to 10 px under `@media (hover: none) and
(pointer: coarse)`. If you build a new draggable / scrollable
surface, follow the same pattern.

## 6. Anti-patterns

Things that look like they'd work but break the system:

- **Tinted backgrounds for state.** We removed them from `.ws-card`
  for a reason — they introduce a fourth "design language" per state
  and clash with the focused, flat surface elsewhere. Use a coloured
  rail instead.
- **`text-transform: uppercase` outside the documented places.**
  Section labels, the pendant action grid, state pills. Anywhere
  else, lowercase / title-case.
- **`gradient` backgrounds for chrome.** Flat surfaces only. Glow
  comes from `box-shadow`, not from a `linear-gradient`.
- **Inline `<style>` overrides on a single element.** If you find
  yourself writing `<div style="border-radius: 8px">`, the rule
  belongs in a class, and the value belongs in a token.
- **Mixing `px` and `rem`.** The whole system is `px`. Don't switch
  half-way.
- **Adding a new colour.** Use the existing palette — `--green`,
  `--amber`, `--red`, `--accent`. The single exception is the SIM
  cyan in `.device-pill--sim`, which is deliberately *not* a state
  colour. If you need a new informational hue, add it to `base.css`
  with a comment explaining what it means.

## 7. Adding a new component

Workflow:

1. Identify the closest existing pattern. (Is it a card, a pill, a
   button, a step row, a modal?)
2. Use the matching radius / type / spacing tokens.
3. Add focus-visible ring.
4. If the component animates, drive durations through `--motion-*`.
5. If it carries state colour, drive glow through `--glow-*`.
6. If it's used by ≥ 2 pages, define it in `base.css`. Otherwise
   keep it in the page's `<style>` block.
7. If you needed to introduce a new token to fit the component,
   add it to `base.css` *and* document it here.

## 8. When the system feels wrong

If you're fighting the tokens — e.g. a designer says "this button
needs to be 18 px radius" and we don't have a token for that —
**don't add `border-radius: 18px` to the element**. Either:

- Use the closest existing token (`--radius-lg` = 16 px), or
- Add `--radius-lg-plus: 18 px` to the scale with a comment on
  what it's for, and use it.

The tokens are the system; ad-hoc values erode it.

---

*Style decisions land here when they're settled. If you find a
disagreement between this doc and the CSS, the CSS won and this doc
needs updating — open a PR.*
