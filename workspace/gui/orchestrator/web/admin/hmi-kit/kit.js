// hmi-kit/kit.js — the shared look of every project HMI.
//
// bna and calibration each carry a setup screen with the same design
// language: token-driven colors, terse uppercase card titles, compact
// tables with monospace numerics, the three-tone message boxes, chart
// identity colors on circular wells. This module IS that language, so a
// new project imports it instead of copying it:
//
//   import { kitCss, wellCss, esc, mL, fmtC, toPpb,
//            rackOrder, slotIndex, bindToggles, runList } from "/orchestrator/hmi-kit/kit.js";
//
// The absolute path works because the orchestrator serves a project's
// setup module same-origin (ProjectSetupFileHandler) and this folder
// under /orchestrator/hmi-kit/ (the admin static route). NOTE: pendant
// modules are served by the RUNTIME server (a different origin), so a
// pendant cannot import this — see HMI_GUIDE.md §7 for what a pendant
// copies instead.
//
// The kit styles only the shadow root it is injected into, under a
// single `.hmi` host class — nothing here can leak into the admin page,
// and the admin page cannot break it.
//
// Read HMI_GUIDE.md (next to this file) before building a screen.

export const KIT_VERSION = 2;

// ── the palette contract ────────────────────────────────────────────────
// Never hard-code a color. The admin theme's custom properties inherit
// through the shadow boundary, so these are always live and always match
// the operator's light/dark choice:
//
//   --surface --surface2   backgrounds (page / inputs+buttons)
//   --border               hairlines
//   --accent               the one interactive highlight
//   --red --green --amber  bad / good / advisory
//
// The ONLY colors a project adds are its identity colors — the exact
// hues of its printed workflow chart's legend, declared as --c-<kind>
// on the .hmi host (see wellCss below). Identity colors do NOT invert
// with the theme; only the empty well does.

// ── shared component styles ─────────────────────────────────────────────
// Inject once into the screen's shadow root (the platform does this for
// you when it is part of the default export's `css`):
//
//   css: kitCss + wellCss({...}) + `/* project extras */`
export const kitCss = `
.hmi { font-size:13px; line-height:1.5; }
.hmi * { box-sizing:border-box; }

/* cards — every section of the screen is one */
.hmi .card { border:1px solid var(--border); border-radius:4px; padding:12px 14px; }
.hmi .card > h4 { margin:0 0 2px; font-size:10.5px; letter-spacing:.13em;
  text-transform:uppercase; font-weight:700; opacity:.75; }
.hmi .stack { display:flex; flex-direction:column; gap:12px; }
.hmi .inner { display:flex; flex-direction:column; gap:9px; }
.hmi .cols { display:grid; grid-template-columns:minmax(0,1fr) 224px; gap:14px; align-items:start; }
@media (max-width:760px){ .hmi .cols { grid-template-columns:1fr; } }

/* field labels — same voice as the card titles, one step quieter */
.hmi label.lab { display:block; font-size:10px; letter-spacing:.1em;
  text-transform:uppercase; opacity:.6; margin-bottom:3px; font-weight:700; }

/* inputs */
.hmi input, .hmi select, .hmi textarea {
  background:var(--surface2); color:inherit;
  border:1px solid var(--border); border-radius:3px; padding:5px 7px;
  font:inherit; font-size:12.5px; width:100%; min-width:0; }
.hmi input[type=number] { font-variant-numeric:tabular-nums;
  font-family:ui-monospace,Menlo,Consolas,monospace; }
/* spinners are dead weight on a typed value and cost ~18px each */
.hmi input[type=number]::-webkit-outer-spin-button,
.hmi input[type=number]::-webkit-inner-spin-button { -webkit-appearance:none; margin:0; }
.hmi input[type=number] { -moz-appearance:textfield; appearance:textfield; }
/* a unit dropdown ("ppm"/"ppb") must always show in full */
.hmi select.unit { flex:0 0 auto; width:70px; padding-left:6px; padding-right:2px; }
.hmi .row { display:flex; gap:6px; align-items:center; }
.hmi .row > input[type=number] { flex:1 1 auto; min-width:0; width:auto; }

/* buttons */
.hmi button { cursor:pointer; font:inherit; font-size:12px; padding:4px 10px;
  border:1px solid var(--border); border-radius:3px;
  background:var(--surface2); color:inherit; }
.hmi button:hover { border-color:currentColor; }
.hmi button.del { border-color:transparent; background:none; opacity:.6; padding:2px 6px; }
.hmi button.del:hover { opacity:1; color:var(--red); }
.hmi button.pos { font-family:ui-monospace,Menlo,Consolas,monospace; }
.hmi button.pos.armed { background:var(--accent); border-color:var(--accent); color:#fff; }

/* tables — compact, hairline rows, right-aligned monospace numerics (.n) */
.hmi table.t { width:100%; border-collapse:collapse; font-size:12px; }
.hmi table.t th { text-align:left; font-size:9.5px; letter-spacing:.09em;
  text-transform:uppercase; opacity:.6; padding:3px 6px 3px 0;
  border-bottom:1px solid var(--border); font-weight:700; }
.hmi table.t td { padding:4px 6px 4px 0; border-bottom:1px solid var(--border); }
.hmi table.t td.n { text-align:right; padding-right:10px;
  font-family:ui-monospace,Menlo,Consolas,monospace; font-variant-numeric:tabular-nums; }
/* a select is as wide as its longest OPTION unless capped — cap it */
.hmi table.t select:not(.unit) { max-width:132px; text-overflow:ellipsis; }
.hmi .scroll { overflow-x:auto; }
.hmi tr.rowbad td { background:rgba(200,40,35,.10); }
.hmi .warnv { color:var(--amber); }

/* messages — the three tones plus neutral. <b> is the tag, <div> the body */
.hmi .msg { padding:8px 10px; border-radius:3px; font-size:12px; display:flex; gap:8px; }
.hmi .msg b { font-size:9.5px; letter-spacing:.1em; text-transform:uppercase;
  flex-shrink:0; padding-top:1px; }
.hmi .msg ul { margin:3px 0 0; padding-left:15px; }
.hmi .msg li { margin-bottom:2px; }
.hmi .m-bad  { background:rgba(200,40,35,.12); color:var(--red); }
.hmi .m-good { background:rgba(30,140,90,.12); color:var(--green); }
.hmi .m-warn { background:rgba(200,150,20,.14); color:var(--amber); }
.hmi .m-info { background:rgba(127,127,127,.10); }
.hmi .flag { display:inline-block; font-size:9px; letter-spacing:.09em;
  text-transform:uppercase; font-weight:700; padding:1px 4px; border-radius:2px;
  background:rgba(200,150,20,.2); color:var(--amber); }

/* wells — circular, hover title carries the detail. Identity colors are
   added per project via wellCss(); the empty well is the only themed one */
.hmi .well { aspect-ratio:1; border:1.5px solid var(--border); border-radius:50%;
  background:var(--surface); font-size:7.5px; font-weight:700; display:flex;
  align-items:center; justify-content:center; padding:0; line-height:1; opacity:.9;
  font-family:ui-monospace,Menlo,Consolas,monospace; color:inherit; }
.hmi .well.pick { cursor:pointer; border-style:dashed; border-color:var(--accent); color:var(--accent); }
.hmi .well.pick:hover { transform:scale(1.12); }

/* selection — a loaded well is a TOGGLE for "runs in this batch".
   .toggle  clickable; hover lifts it so it reads as one
   .skip    ticked OFF: dimmed and slashed, never hidden — the operator
            must still see the vial is physically there
   .dupe    flagged (e.g. a duplicate id): red outline, still selectable
   The frozen screen takes the toggle affordance back. */
.hmi .well { position:relative; }
.hmi .well.toggle { cursor:pointer; transition:transform .12s ease; }
.hmi .well.toggle:hover { transform:scale(1.06); }
.hmi .well.skip { opacity:.45; }
.hmi .well.skip::after { content:""; position:absolute; inset:0; border-radius:50%;
  background:linear-gradient(to top right, transparent 47%, var(--muted,#888) 47%,
    var(--muted,#888) 53%, transparent 53%); }
.hmi .well.dupe { outline:2px solid var(--red); outline-offset:1px; }
.hmi[data-frozen="1"] .well.toggle { cursor:default; }
.hmi[data-frozen="1"] .well.toggle:hover { transform:none; }

/* rack grid — wells in a grid with quiet monospace axis labels (.ax).
   Emit it TURNED: see rackOrder() and HMI_GUIDE.md §4. */
.hmi .rack { display:grid; gap:3px; align-items:center; justify-items:center; }
.hmi .rack .ax { font-size:9px; opacity:.6; text-align:center;
  font-family:ui-monospace,Menlo,Consolas,monospace; }
.hmi .legend { margin-top:9px; display:flex; flex-direction:column; gap:3px; font-size:10.5px; }
.hmi .legend div { display:flex; align-items:center; gap:6px; opacity:.85; }
.hmi .legend i { width:10px; height:10px; border-radius:50%; flex-shrink:0;
  border:1.5px solid var(--c-stroke, #2b3338); }

/* capacity bars — 3px, accent fill, red past the limit */
.hmi .bars { margin-top:9px; padding-top:8px; border-top:1px solid var(--border);
  display:flex; flex-direction:column; gap:6px; }
.hmi .bars .lab { display:flex; justify-content:space-between; font-size:9.5px;
  font-family:ui-monospace,Menlo,Consolas,monospace; font-variant-numeric:tabular-nums;
  opacity:.75; margin-bottom:2px; }
.hmi .bar { height:3px; background:rgba(127,127,127,.2); border-radius:2px; overflow:hidden; }
.hmi .bar span { display:block; height:100%; background:var(--accent); }
.hmi .bar span.over { background:var(--red); }

/* small monospace figure line (totals, "x / y allocated") */
.hmi .fig { font-size:11.5px; opacity:.75;
  font-family:ui-monospace,Menlo,Consolas,monospace; }
.hmi .fig.over { color:var(--red); opacity:1; font-weight:700; }

/* frozen while a run is active — read-only, still legible */
.hmi[data-frozen="1"] input, .hmi[data-frozen="1"] select,
.hmi[data-frozen="1"] button:not(.nofreeze) { pointer-events:none; opacity:.6; }
`;

// ── identity colors ─────────────────────────────────────────────────────
// The colors of the project's printed workflow chart, verbatim, so the
// screen reads like the chart. They are IDENTITY: they do not invert
// with the theme. `on` is the text color that sits on all of them and
// `stroke` their shared outline.
//
//   wellCss({ source:"#a4d89c", sub:"#efe05f" }, { on:"#12191d", stroke:"#2b3338" })
//     -> declares --c-source/--c-sub/--c-on/--c-stroke on .hmi
//        and emits .well.k-source / .well.k-sub to match.
//
// Use them beyond wells too (legend swatches, phase chips): they are
// plain custom properties on the host.
export function wellCss(kinds, { on = "#12191d", stroke = "#2b3338" } = {}) {
  const vars = Object.entries(kinds).map(([k, c]) => `--c-${k}:${c};`).join(" ");
  const rules = Object.keys(kinds).map(k =>
    `.hmi .well.k-${k}{background:var(--c-${k});border-color:var(--c-stroke);color:var(--c-on)}`
  ).join("\n");
  return `.hmi { ${vars} --c-on:${on}; --c-stroke:${stroke}; }\n${rules}\n`;
}

// ── helpers every screen re-implements otherwise ────────────────────────

// HTML-escape anything operator-typed before it goes into innerHTML.
export const esc = (s) => String(s == null ? "" : s)
  .replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// Volumes print as mL with trailing zeros trimmed: 1.234 / 0.0056 mL.
export const mL = (v) => (v == null || !isFinite(v)) ? "—"
  : ((v >= 1 ? v.toFixed(3) : v.toFixed(4)).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "") + " mL");

// Concentration with its unit, em-dash when absent.
export const fmtC = (v, u) => (v == null || v === "") ? "—" : `${v} ${u}`;

// The two units every concentration field offers.
export const toPpb = (v, u) => u === "ppm" ? v * 1000 : v;

// ── racks drawn as the operator sees them ───────────────────────────────
// A rack's LOGICAL shape (letters x numbers) is not how it faces the
// operator on this bench: the letter runs ACROSS as a column with A on
// the RIGHT, the number runs DOWN with 1 at the BOTTOM — so sample 1 is
// the bottom-right well and the count climbs away from the operator.
// Nothing about the rack changes, only which way the picture faces, and
// the setup screen and the pendant MUST agree on it.
//
//   const { cols, rows } = rackOrder("ABCD", 7);
//   // cols = ["D","C","B","A"], rows = [7,6,...,1]
//   // slot index for (letter L, number R): letters.indexOf(L) * 7 + (R - 1)
export const rackOrder = (letters, n) => ({
  cols: [...letters].reverse(),
  rows: Array.from({ length: n }, (_, i) => n - i),
});

// Row-major slot index of a rack address — the same number actions.py
// uses, so a ticked well and the tube the robot picks never disagree.
export const slotIndex = (letters, n, L, R) => letters.indexOf(L) * n + (R - 1);

// ── selection: which loaded wells run ───────────────────────────────────
// One delegated listener on the rack, not one per well: wells are
// re-made on every render and delegation survives that. `sel` is a Set
// of slot indices; the well carries data-i. Ignored while frozen.
//
//   bindToggles(rackEl, st.selected, () => redraw(), () => st.frozen);
export function bindToggles(rackEl, sel, onChange, isFrozen = () => false) {
  if (!rackEl) return;
  rackEl.onclick = (e) => {
    if (isFrozen()) return;
    const w = e.target && e.target.closest ? e.target.closest(".well.toggle") : null;
    if (!w) return;
    const i = parseInt(w.dataset.i, 10);
    if (Number.isNaN(i)) return;
    if (sel.has(i)) sel.delete(i); else sel.add(i);
    onChange(sel);
  };
}

// The run, as actions.py wants it: the ticked indices that are actually
// loaded, in slot order. Trims a selection that outlived its manifest.
export const runList = (sel, loaded) =>
  [...sel].filter(i => i >= 0 && i < loaded).sort((a, b) => a - b);
