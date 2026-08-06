# Give a project its operator UI (kwargs / setup / pendant)

Use this skill when a project needs run parameters, a run-setup screen,
or a pendant screen — or when you're changing one and need the contract.

## The three keys (launch.yaml)

```yaml
kwargs:   hmi/kwargs.j2      # the kwargs themselves (data — Python reads it)
setup:    hmi/setup.js       # screen to SET the kwargs, before the run
pendant:  hmi/pendant.html   # screen shown DURING the run
```

One contract, two screens. Only `kwargs:` is required; each screen is
opt-in and its absence falls back cleanly (generic form / default
pendant). Gold exemplar for all three: **the bd project's `hmi/`
folder** (`~/Downloads/projects/bd` on the bench Pi).

**The rule that decides format** (hmi-guide §10b): *if Python reads it,
it's yaml; if only the browser reads it, it's the project's file.*
`kwargs` is read headlessly — `bt.replay`, launch, Start-with-defaults,
server-side validation — so it stays yaml forever. The screens are read
only by the pendant/modal, so they're `.html` or `.js`, project-owned.
Do NOT try to merge them into one file or execute project JS from
Python (node subprocess, etc.) — rejected, hmi-guide §10b.

## `kwargs:` — the schema (project-guide §3)

- Inline dict, `.yaml`, or `.j2` (Jinja renders first — use it to
  generate collection defaults, e.g. bd's 19-tube grid loop).
- Top level IS the schema; keys starting with `_` are reserved
  presentation hints (`_layout`, `_setup`) and never reach the run.
- **Two entry shapes, one rule**: a dict containing `"default"` is a
  SPEC (`{type: int, default: 4, min: 1}` — for the generic form's
  widgets/labels/limits, no-screen projects like apc); anything else
  is BARE — the value IS the default (`print_label: false`,
  `tubes: {"A1": 0.4}`). **A project with its own `setup:` screen
  declares bare entries only** — presentation (labels, units, limits)
  lives in the screen (bd). Every key must have a reader; delete keys
  nothing reads.
- `replay --batch N` lands on the first `int` kwarg, else slices the
  first collection kwarg's **declared default** to N entries — so a
  collection's default should list everything processable, in the
  order the operator would expect items to run.
- The schema never describes how anything looks.

## `setup:` — the run-setup screen (project-guide §3)

Hosted in a **shadow root inside the Parameters modal**; the platform
keeps the modal chrome, Set/Start buttons, and validation. Served by
the **orchestrator** (`/orchestrator/api/workspace/<ws>/setup/…`) —
NOT the runtime server, which isn't up before launch. Same-origin.

- HTML shape: plain inputs with `data-field="key"` — platform seeds
  values and reads them back.
- JS shape: `export default {css, mount(root, api), value(), validate()}`
  with `api = {schema, values, frozen, theme, onTheme}`.
- The platform validates whatever `value()` returns against the schema
  (required / min / max) — a screen is NOT trusted to enforce its own
  contract; `validate()` only ADDS a message.
- Fields the screen doesn't draw keep their schema default (bd draws
  the rack; `print_label` rides its default).
- A screen can never invent a parameter the schema doesn't declare.

## `pendant:` — the during-run screen (hmi-guide §4b)

Hosted in a **shadow root in the pendant's content area**; the platform
keeps the frame (navbar, control rail, state pill, alarms). Served by
the **runtime server** at `/hmi/…` with CORS (the pendant page comes
from the GUI on a different port — without CORS, fetch/import refuse
the body and nothing mounts).

- Data arrives from **`rt.op(key=value)`** in actions.py — replace
  semantics, coalesced ~100 ms, bounded; assets by URL, never inlined
  (project-guide §3 "rt.op").
- HTML shape (no JS): `data-bind="key"` (+`data-unit`),
  `data-bind-map="key"` + `data-slot="A1"` → sets `data-state` (style
  it in the project's own CSS), `data-bind-attr` + `data-attr`.
- JS shape: `export default {css, mount(root, api), update(values)}`
  with `api = {values, theme, onTheme, invoke}` — `invoke` reaches
  ONLY declared `operator_action`s; no runtime/device/motion handle.
- Fallback: `pendant: hmi/hmi.j2` widget list (`state` `stat`
  `progress`) for a project that writes no markup — never where
  domain features go (hmi-guide §2, §4).

## Styling both screens

Design tokens inherit **through** the shadow boundary: use
`var(--accent)`, `var(--surface)`, `var(--space-*)` … and light/dark
works with zero theme code. Never a raw hex (design-system §2).
**Trap:** `:root {}` does nothing inside a shadow root — use `:host`.
A sibling `.css` next to the screen file is auto-linked (`setup.css`,
`pendant.css`).

## Traps (each cost a debugging session — don't rediscover)

1. `hmi:` and `params:` are the OLD key names — both warn at startup
   and load nothing. Use `pendant:` / `setup:`.
2. The HTML shape never executes `<script>` tags (innerHTML). Logic ⇒
   use the `.js` shape. HTML for static markup, JS when it computes
   (generated grids, charts, canvas).
3. Generated/interactive UI (a 96-well plate, per-cell values) is the
   `.js` shape — don't hand-write 96 divs.
4. A broken screen must never block: pendant shows a quiet note (run
   unaffected); setup blocks Launch with a visible error. Check server
   startup logs for `[setup]` / `[pendant]` / `[hmi]` warnings.
5. Domain UI (racks, trays, discs) never goes in the platform — it's
   the project's screen. Platform-side work is transport/hosting/
   tokens only (hmi-guide §2; the `type: slots` removal is the
   cautionary tale, project-guide §3).
6. Exclusions (e.g. a reservoir slot) must ALSO be filtered in
   `setup()` in actions.py — replay/API callers bypass the screen.

## Verify before shipping

1. `sudo python3 -m workspace.bt.replay <project> --batch 1 N` — the
   schema still resolves headlessly and the schedule gate passes.
2. Launch and open Parameters (setup) / the pendant view (pendant) —
   or render headlessly with chromium `--screenshot` against the real
   server; check BOTH themes.
3. Watch startup output for `[setup]` / `[pendant]` warnings.

## Canonical docs

- `docs/project-guide.md` §3 — kwargs types, `setup` contract, `_layout`, rt.op
- `docs/hmi-guide.md` §2 (ownership principle), §4b (pendant contract),
  §10b (format decisions + decision log)
- `docs/design-system.md` — tokens, color discipline, touch targets
