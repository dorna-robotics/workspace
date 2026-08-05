---
name: build-gui
description: "Use when building or changing ANY platform GUI surface — orchestrator admin, pendant, scene builder, vision pages, HMI widgets. Covers the design-system contract, the state matrix, the mockup-first workflow, and where every shared CSS/JS piece lives."
---

# Build or change a GUI surface

## When to use this skill

- "Add a panel / page / widget to the [builder / orchestrator / pendant / vision GUI]"
- "Make the UI show X" / "improve how Y looks"
- Any HMI widget work (`hmi.j2` catalog)
- Visual redesign discussions → mockups

## The contract (read before writing CSS or DOM)

**`docs/design-system.md`** is the single source of truth:

- §1 file map — where tokens, nav, page CSS live
- §2 tokens — colors, radius scale, `--space-1..6`, `--text-xs..xl`,
  shadows, `--motion-*`. Raw hex/px in page CSS is a defect.
- §3 surface catalogue — reuse before inventing (cards, pills, step
  rows, modals, toasts)
- §6 anti-patterns — no tinted state backgrounds, no gradients, no
  inline style overrides, no new colors
- §8 state matrix — every interactive element ships
  rest/hover/pressed/selected/disabled/loading/empty/error the day it
  ships; async work is ALWAYS visible where the user looks
- §9 color discipline — muted normal, saturated active/attention,
  NEVER color alone (✓/! badges, hollow/filled)
- §10 touch — 44px targets on operator glass; gestures never the only path
- §13 the new-surface checklist — run it before merging
- §14 process — visuals go mockup-first (`docs/internal/*_mockups/`),
  agree, codify the grammar, then build

## HMI-specific work

`docs/hmi-guide.md` — the pendant HMI design: widget catalog, hmi.j2
declaration model, rt.op() channel, bench subway map grammar, guided
recovery, parameter presets. Mockups: `docs/internal/hmi_mockups/`
(preview: `python3 -m http.server 8123` from the bench copy — see
hmi-guide §9).

## Wiring facts (hard-won, do not rediscover)

- The combined server is `workspace/gui/server.py` — it imports
  handlers from orchestrator/scene_builder and builds ITS OWN route
  table. A handler registered only on a sub-app's `app` 404s in
  production: **mount new endpoints in gui/server.py too.**
- Builder viewer internals (scene/camera/renderer, `upsertObject`,
  `objectsByName`) live inside `boot()`; cross-closure access goes
  through the deliberate `window.__*` exposures.
- Heavy platform work (Workspace builds, IK) never runs in the GUI
  server process — the builder patches dorna2 with preview stubs.
  Use a subprocess (`ref_solve.py` one-shot, `ik_worker.py`
  persistent stdin/stdout JSON lines; stdout = protocol, platform
  prints → stderr).
- Client state that must survive refresh (file slots, hidden set)
  persists per-project in localStorage (`sb_persist::`), cleared by
  New Scene. Server world_state is memory-only.
- 3D canvas: `setPixelRatio(min(devicePixelRatio, 2))` — WebGL runs
  on the VIEWING machine, not the Pi. Integer font sizes only
  (fractional px renders soft).

## Checklist shortcut

design-system.md §13, verbatim — run it before calling any surface done.
