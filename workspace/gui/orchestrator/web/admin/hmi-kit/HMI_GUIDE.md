# Project HMI Guide

How to build a project setup screen (and pendant) that looks and behaves
like the ones that already exist. **bna** and **calibration** are the
worked examples of this language; this folder is its reference:

| File | What it is |
|---|---|
| `HMI_GUIDE.md` | This document — the design language and the rules |
| `kit.js` | The shared CSS + helpers, importable by any setup screen (`KIT_VERSION` 2: adds turned racks and well selection) |
| `setup-template.js` | A minimal working screen to copy into a new project |

---

## 1. Where an HMI lives

A project declares its screens in `launch.yaml`:

```yaml
setup:   hmi/setup.js      # the Parameters window, BEFORE launch
pendant: hmi/pendant.js    # the operator view, DURING the run
default: hmi/default.j2    # the kwargs schema the platform validates against
```

The **setup screen** is served by the *orchestrator*
(`/orchestrator/api/workspace/<name>/setup/…`) into a shadow root inside
the Parameters modal. The **pendant** is served by the project's
*runtime server*. Both are ES modules with the same default-export
contract (§5). Files beside them in `hmi/` (a `methods/` library,
images) are served too — a screen may pull in its own siblings, never
anything above them.

## 2. Using the kit

A setup screen imports the kit by absolute path — same origin as the
module itself, so it just works:

```js
import { kitCss, wellCss, esc, mL, fmtC, toPpb,
         rackOrder, slotIndex, bindToggles, runList } from "/orchestrator/hmi-kit/kit.js";

const CSS = kitCss + wellCss({ source: "#a4d89c", target: "#9fcfe3" }) + `
  /* project-specific extras only */
`;
```

Wrap everything the screen draws in one `<div class="hmi">` host (set
`data-frozen="1"` on it while a run is active). Everything in §3–§4
then applies automatically.

**Pendants cannot import the kit** — they are served from the runtime
server, a different origin. A pendant is small: copy the few rules it
needs from `kit.js` and keep the same tokens and voice (§7).

## 3. The design language

**Colors are tokens, never hex.** The admin theme's custom properties
inherit through the shadow boundary: `--surface` / `--surface2`
(backgrounds), `--border` (hairlines), `--accent` (the one interactive
highlight), `--red` / `--green` / `--amber` (bad / good / advisory).
Using them is what makes a screen follow the operator's light/dark
theme for free. The **only** hex a project adds are its *identity
colors*: the exact hues of its printed workflow chart's legend,
declared via `wellCss()`. Identity colors do **not** invert with the
theme — the chart on the wall doesn't either. Only the empty well is
themed.

**Typography.** 13px base, line-height 1.5. Card titles and field
labels are small uppercase letterspaced bold at reduced opacity — the
screen's voice is quiet labels over legible values. Every number the
operator compares (volumes, concentrations, counts) is monospace with
`tabular-nums` (`ui-monospace, Menlo, Consolas`), right-aligned in
tables (`td.n`).

**Shapes.** Cards: 1px `--border`, 4px radius, 12–14px padding, one
`h4` title. Inputs and buttons: `--surface2` fill, 3px radius. Wells
are circles with a 1.5px stroke; a well being *placed* is dashed
`--accent` and scales up on hover. Capacity bars are 3px tall,
`--accent` fill, `--red` past the limit. Number inputs have their
spinners removed.

**Messages.** One idiom for all feedback: a `.msg` box with an
uppercase tag and a body, in four tones — `.m-bad` (blocking),
`.m-good` (pass), `.m-warn` (advisory), `.m-info` (neutral). Lists of
problems go in a `<ul>` inside the body. A row that is *impossible*
also gets `tr.rowbad`; a value that is merely *suspect* gets `.warnv`.

**Layout.** One page, no wizard. Cards stack in a main column with a
narrow (~224px) right column for the tray/bench picture, collapsing to
one column under 760px. Wide tables sit in a `.scroll` wrapper —
the page itself never scrolls horizontally.

## 4. The behavioural rules

These are as much "the style" as the colors:

- **Validate live, on every keystroke.** Blocking problems, advisories
  and tidy-ups are always visible in a Validation card; nothing waits
  for a Save click to complain. Save and Start are refused while
  blocking problems exist.
- **Preserve the caret.** On input, re-render only computed cells and
  the validation card (write `textContent` of marked nodes) — a full
  re-render mid-number throws the operator's cursor away. Full
  re-render only on structural change (add/delete row, dropdown).
- **Decide for the operator where the spec allows it** (calibration's
  automatic subs): run decisions when a field *settles* (`change`, not
  `input`) so a half-typed 500 never acts as 5 — and never touch
  anything the operator set by hand.
- **Draw racks as the operator sees them.** A rack's logical shape
  (letters × numbers) is not how it faces the bench: the letter runs
  *across* as a column with **A on the right**, the number runs *down*
  with **1 at the bottom** — sample 1 is the bottom-right well and the
  count climbs away from the operator. `rackOrder(letters, n)` gives
  the emit order; `slotIndex()` gives the same row-major index
  `actions.py` uses. The setup screen and the pendant **must** draw the
  same orientation — one person reads both for the same physical rack,
  and a disagreement between them is worse than either orientation on
  its own. Cells keep real relative sizes (a 40 mL vial is twice the
  diameter of a 2 mL one, so it gets twice the cell).
- **Let the operator tick which wells run** (bna's sample selection):
  a loaded well is a toggle (`.well.toggle`), a fresh load starts
  **fully selected** (they loaded those bottles to run them; narrowing
  is a click away), *Select all* / *Select none* sit beside the rack,
  and a de-selected well is **slashed, never hidden** (`.well.skip`) —
  the vial is still physically there. One delegated listener on the
  rack (`bindToggles`), not one per well. The screen writes `selected`
  (slot indices, via `runList`) and derives `batch_size` as its count;
  `setup()` in actions.py runs exactly those and falls back to the first
  `batch_size` when the list is empty — which is what a headless
  `bt.replay --batch N` gets. An empty selection with samples loaded is
  a **blocking** error; a partial one is an info note ("Running 3 of 7
  loaded: 2, 5, 6").
- **Freeze during a run.** The platform passes `frozen`; the screen
  stays visible but read-only (`data-frozen="1"`).
- **Derive, don't re-enter.** If a position or volume can be computed
  from what the operator already typed, show it read-only. Two places
  to type the same fact is one place to get it wrong.
- **Escape everything operator-typed** (`esc()`) before it enters
  `innerHTML`.
- **Physical confirmations are not method data.** A "tray matches"
  tick belongs to the bench: never saved with the method, cleared the
  moment the depicted layout changes.
- **Numbers the robot uses and numbers the screen shows come from the
  same formulas.** Mirror the actions.py math helper-for-helper and
  say so in comments on both sides.

## 5. The module contract

```js
export default {
  css: CSS,                 // injected as <style> into the shadow root
  mount(root, api) {},      // root = the shadow root; APPEND, never assign
                            //   root.innerHTML (the platform's <style> is
                            //   already in there and would be destroyed)
  value() {},               // -> the kwargs object, e.g. { method: {...} }
  validate() {},            // -> "" when fine, else ONE message STRING —
                            //   the platform does `if (msg)`, and an empty
                            //   array is truthy: never return [].
};
```

`api` provides `schema`, `values`, `frozen`, `theme` and `onTheme(cb)`.
The platform owns the modal chrome and the Set/Start buttons; the
screen only fills the body.

## 6. A method library (optional)

If the project saves named parameter sets, follow calibration:
`hmi/methods/*.json` listed by `hmi/methods/index.json`, fetched
relative to the module (`new URL("./methods/", import.meta.url)`) —
read-only. Saving goes through the platform's
`…/hmi-methods/save` endpoint, with a plain file download as the
fallback on an older platform. Guard method switches behind an
unsaved-changes confirm.

## 7. Pendants

Same contract, served by the runtime. Keep it to the operator's words —
what is happening and what they must do — not the planner's action
names. Reuse the tokens and the `.msg` idiom by copying the handful of
rules needed; a pendant should be small enough that this costs a
screenful. (The admin page's ACTIVE ROUTINE hero can be turned off per
project with `pendant_hero: false` in `launch.yaml` when the pendant
already says it better.)

## 8. Checklist for a new project's screen

1. Copy `setup-template.js` to `<project>/hmi/setup.js`; declare it
   under `setup:` in `launch.yaml`.
2. Pick identity colors **from the project's printed chart**, declare
   them with `wellCss()`.
3. Build each section as a `.card`; put every number in `td.n`.
4. Wire live validation with the three tones; block Save/Start on
   `.m-bad` only.
5. Freeze correctly, preserve the caret, escape user text.
6. If the operator must set up hardware the robot can't verify, end
   with a depiction + a confirm tick that clears on change.
7. Draw every rack turned (`rackOrder`), identically on setup and
   pendant; if wells are chosen, make them toggles and ship `selected`.
8. Compare it side-by-side with bna's and calibration's screens — if
   yours looks like the odd one out, it is.
