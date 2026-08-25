// hmi/setup.js — <PROJECT> run setup.
//
// A minimal working screen in the house style. Copy into a new
// project's hmi/ folder, declare it in launch.yaml (`setup: hmi/setup.js`),
// and grow it card by card. Read /orchestrator/hmi-kit/HMI_GUIDE.md first.
//
// Contract: export default {css, mount(root, api), value(), validate()}.
// The platform owns the modal chrome and the Set/Start buttons; validate()
// returns ONE message string ("" when fine), never an array.

import { kitCss, wellCss, esc, mL, fmtC, toPpb,
         rackOrder, slotIndex, bindToggles, runList }
  from "/orchestrator/hmi-kit/kit.js";

// ── identity colors — the printed chart's legend, verbatim ─────────────
// These do NOT invert with the theme. Rename the kinds to the project's
// own vocabulary; .well.k-<kind> classes are generated to match.
const CSS = kitCss + wellCss({
  sample: "#a4d89c",
  product: "#9fcfe3",
}) + `
/* project-specific rules only — the kit already covers cards, tables,
   inputs, buttons, messages, wells, bars and the frozen state */
.hmi .rack.src { grid-template-columns: 16px repeat(${RACK_LETTERS.length}, ${CELL}px); }
.hmi .rack.src .well { width:${CELL}px; height:${CELL}px; }
`;

// ── constants the screen computes with ─────────────────────────────────
// Mirror actions.py helper-for-helper and say so on both sides.
const MAX_ITEMS = 14;
// The source rack, as actions.py indexes it: row-major, letter * 7 + n.
const RACK_LETTERS = "ABCD", RACK_N = 7;
const CELL = 28;   // px — keep real relative sizes between racks

// ── state ──────────────────────────────────────────────────────────────
const emptyParams = () => ({ name: "", items: [], selected: [] });

function coerce(raw) {
  let p = raw;
  if (typeof p === "string") { try { p = JSON.parse(p); } catch { p = null; } }
  if (!p || typeof p !== "object" || Array.isArray(p)) return emptyParams();
  p = Object.assign(emptyParams(), p);
  // The selection is a Set while the screen is open, a list when saved.
  // Absent on a first open -> every loaded item, which is what the
  // operator loaded them for; narrowing is a click away.
  p.selected = Array.isArray(p.selected) && p.selected.length
    ? new Set(p.selected.map(Number))
    : new Set(p.items.map((_, i) => i));
  return p;
}

// ── validation — always live, three tones ──────────────────────────────
function check(p) {
  const errs = [], warns = [], soft = [];
  if (!String(p.name || "").trim()) errs.push("Name: required.");
  if (!p.items.length) errs.push("Items: at least one is required.");
  if (p.items.length > MAX_ITEMS) errs.push(`Items: at most ${MAX_ITEMS}.`);
  p.items.forEach((it, i) => {
    if (!(it.v > 0)) errs.push(`Item ${i + 1}: volume must be above zero.`);
  });
  // Which loaded wells run — any subset, in slot order.
  const run = runList(p.selected, p.items.length);
  if (p.items.length && !run.length)
    errs.push("No items selected — click the wells to run, or Select all.");
  if (run.length && run.length < p.items.length)
    soft.push(`Running ${run.length} of ${p.items.length} loaded: ${run.map(i => i + 1).join(", ")}. The rest are slashed.`);
  return { errs, warns, soft, run };
}

function valHtml(V) {
  return (V.errs.length
      ? `<div class="msg m-bad"><b>${V.errs.length} blocking</b><div><ul>${V.errs.map(e => `<li>${esc(e)}</li>`).join("")}</ul></div></div>`
      : `<div class="msg m-good"><b>Pass</b><div>Ready to start.</div></div>`)
    + (V.warns.length ? `<div class="msg m-warn"><b>${V.warns.length} advisory</b><div><ul>${V.warns.map(w => `<li>${esc(w)}</li>`).join("")}</ul></div></div>` : "")
    + (V.soft.length ? `<div class="msg m-info"><b>Tidy up</b><div><ul>${V.soft.map(w => `<li>${esc(w)}</li>`).join("")}</ul></div></div>` : "");
}

// ── render ─────────────────────────────────────────────────────────────
// The rack TURNED, as the operator sees it: letters across with A on the
// right, numbers down with 1 at the bottom — item 1 is bottom-right.
function rackHtml(p) {
  const { cols, rows } = rackOrder(RACK_LETTERS, RACK_N);
  let h = `<div class="ax"></div>` + cols.map(L => `<div class="ax">${L}</div>`).join("");
  for (const R of rows) {
    h += `<div class="ax">${R}</div>`;
    for (const L of cols) {
      const i = slotIndex(RACK_LETTERS, RACK_N, L, R), it = p.items[i];
      if (!it) { h += `<div class="well" title="${L}${R} — empty"></div>`; continue; }
      const skip = !p.selected.has(i);
      h += `<div class="well k-sample toggle${skip ? " skip" : ""}" data-i="${i}"
              title="${L}${R} · item ${i + 1} · ${esc(it.name || "")}${skip ? " · NOT IN THIS RUN — click to select" : " · click to unselect"}">${i + 1}</div>`;
    }
  }
  return h;
}

function draw(root, st) {
  const p = st.p, V = check(p);

  const rows = p.items.map((it, i) => `<tr>
    <td style="font-family:ui-monospace,monospace">${i + 1}</td>
    <td><input data-i="${i}" data-f="name" value="${esc(it.name || "")}" placeholder="Item ${i + 1}"></td>
    <td><div class="row"><input type="number" step="any" min="0" data-i="${i}" data-f="v" value="${it.v ?? ""}" placeholder="0"><span style="opacity:.6">mL</span></div></td>
    <td class="n" data-ml="${i}">${mL(it.v)}</td>
    <td><button class="del" data-del="${i}">×</button></td></tr>`).join("");

  root.innerHTML = `
    <div class="stack">
      <div class="card"><h4>Run</h4>
        <div class="inner"><div><label class="lab">Name</label>
          <input id="p-name" value="${esc(p.name)}" placeholder="e.g. Batch 12"></div></div></div>

      <div class="card"><h4>Items</h4>
        <div class="inner"><div class="scroll"><table class="t">
          <thead><tr><th>#</th><th>Name</th><th>Volume</th><th class="n">Planned</th><th></th></tr></thead>
          <tbody>${rows || `<tr><td colspan="5" style="opacity:.6">No items yet.</td></tr>`}</tbody></table></div>
          <div><button id="p-add" ${p.items.length >= MAX_ITEMS ? "disabled" : ""}>+ Add item</button></div>
        </div></div>

      <div class="card"><h4>Rack — tick what runs</h4>
        <div class="inner">
          <div class="rack src">${rackHtml(p)}</div>
          <div class="row"><button id="p-selall">Select all</button><button id="p-selnone">Select none</button>
            <span style="flex:1"></span><span class="fig">${V.run.length} / ${p.items.length} selected</span></div>
        </div></div>

      <div class="card"><h4>Validation</h4>
        <div class="inner" id="p-val">${valHtml(V)}</div></div>
    </div>`;

  if (st.frozen) return;   // read-only while the workspace is running

  // Structural changes re-render; typing re-renders ONLY computed cells
  // and the validation card, so the caret stays where the operator left it.
  const live = () => {
    root.querySelector("#p-val").innerHTML = valHtml(check(p));
    root.querySelectorAll("[data-ml]").forEach(td => {
      td.textContent = mL(p.items[+td.dataset.ml].v);
    });
  };

  root.querySelector("#p-name").oninput = (e) => { p.name = e.target.value; live(); };
  root.querySelector("#p-selall").onclick  = () => { p.selected = new Set(p.items.map((_, i) => i)); draw(root, st); };
  root.querySelector("#p-selnone").onclick = () => { p.selected = new Set(); draw(root, st); };
  bindToggles(root.querySelector(".rack.src"), p.selected, () => draw(root, st), () => st.frozen);
  root.querySelector("#p-add").onclick = () => {
    p.items.push({ name: "", v: null }); p.selected.add(p.items.length - 1); draw(root, st);
  };
  root.querySelectorAll("[data-del]").forEach(b => b.onclick = () => {
    const i = +b.dataset.del;
    p.items.splice(i, 1);
    p.selected = new Set([...p.selected].filter(k => k !== i).map(k => k > i ? k - 1 : k));
    draw(root, st);
  });
  root.querySelectorAll("[data-i]").forEach(i => i.oninput = () => {
    const it = p.items[+i.dataset.i], f = i.dataset.f;
    it[f] = f === "name" ? i.value : (i.value === "" ? null : parseFloat(i.value));
    live();
  });
}

// ── the platform contract ──────────────────────────────────────────────
let _root = null;

export default {
  css: CSS,

  mount(root, api) {
    _root = root;
    const st = {
      p: coerce({ ...((api.values || {}).params || {}),
                  selected: (api.values || {}).selected }),   // ← the kwargs this screen owns
      frozen: !!api.frozen,
    };
    root._state = st;
    // APPEND, never assign innerHTML: the platform's <style> nodes are
    // already in the shadow root and assigning would destroy them.
    root.querySelectorAll(":scope > .hmi").forEach(n => n.remove());
    const host = document.createElement("div");
    host.className = "hmi";
    host.dataset.frozen = st.frozen ? "1" : "0";
    root.appendChild(host);
    draw(host, st);
  },

  value() {
    const st = _root && _root._state;
    if (!st) return { params: emptyParams(), selected: [], batch_size: 0 };
    const run = runList(st.p.selected, st.p.items.length);
    return {
      params: { name: st.p.name, items: JSON.parse(JSON.stringify(st.p.items)) },
      // WHICH items run — slot indices. setup() in actions.py runs exactly
      // these; empty/absent falls back to the first batch_size, which is
      // what a headless bt.replay --batch N gets.
      selected: run,
      batch_size: run.length,
    };
  },

  validate() {
    const st = _root && _root._state;
    if (!st) return "";
    const errs = check(st.p).errs;
    if (!errs.length) return "";
    return errs.length === 1 ? errs[0]
      : `${errs.length} problems, first: ${errs[0]}`;
  },
};
