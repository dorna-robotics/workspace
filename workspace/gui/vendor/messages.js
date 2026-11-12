/* ── Messages — ONE place and one look for every page ─────────────────
   The admin pages, the pendant and the scene builder; the look is
   base.css .msg-*; design-system §3.3b. Every message an operator gets lands here: a
   click's result (toast), a run's error / warning step, a pause's
   question (rt.notice). The place: the top bar's message line
   (.msg-line — a page puts an empty one in each of its bars) and ONE
   stack of cards under it (#msgStack, made here).

   The line shows the newest message (mark, level word, its first
   words; a count when there are more) and is empty — hidden — when
   there are none. The stack shows them all, newest on top: it opens
   for an error, a warning (folding after 5 s) and a question; a tap on
   the line opens it again, a tap elsewhere or Escape folds it — never
   while a question waits. A card's × drops that message; a question
   has its own buttons instead, and nothing else closes it.

   An item: {id?, level, label?, title?, text?, img?, caption?, ttl?,
   ask?, actions?}. level: ok ✓ · info i · warn ! · bad !. ``id``: an
   OWNED message (one per source: "step", "notice") — a new one with
   the id replaces it, and only its owner (unnotify) or its × takes it
   away; past MSG_MAX the oldest UNOWNED message goes. ``ttl`` (ms): it
   goes on its own. ``actions``: [{label, primary?, wire(btn)}] — the
   caller wires each button. Pages call notifyHost() when their bar
   changes (the pendant); otherwise the cards hang under the first line. */
const MSG_WORD = { ok: "Done", info: "Notice", warn: "Warning", bad: "Error" };
const MSG_MARK = { ok: "✓", info: "i", warn: "!", bad: "!" };
const MSG_MAX = 5;
const MSG_FOLD_MS = 5000;
const _msg = { items: [], seq: 0, host: null, stack: null, open: false, fold: null };

function _msgStack() {
  if (_msg.stack) return _msg.stack;
  const el = document.createElement("div");
  el.className = "msg-stack";
  el.id = "msgStack";
  el.hidden = true;
  document.body.appendChild(el);
  el.addEventListener("click", (e) => {
    const x = e.target.closest(".msg-x");
    if (x) unnotify(x.dataset.id);
  });
  document.addEventListener("pointerdown", (e) => {
    if (!_msg.open || el.contains(e.target) || e.target.closest(".msg-line")) return;
    _msgOpen(false);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") _msgOpen(false); });
  window.addEventListener("resize", () => _msgPlace());
  _msg.stack = el;
  return el;
}

// The line the stack hangs under: the one the page names (the pendant
// switches it), else the first on the page.
export function notifyHost(el) {
  _msg.host = el || null;
  _msgPlace();
}
const _msgHost = () => _msg.host || document.querySelector(".msg-line");

export function notify(item) {
  const it = { ...item, owned: !!item.id, id: item.id || `m${++_msg.seq}` };
  clearTimeout(_msg.items.find(m => m.id === it.id)?.timer);
  _msg.items = _msg.items.filter(m => m.id !== it.id);
  _msg.items.push(it);
  while (_msg.items.length > MSG_MAX) {
    const old = _msg.items.findIndex(m => !m.owned);
    if (old < 0) break;
    clearTimeout(_msg.items[old].timer);
    _msg.items.splice(old, 1);
  }
  if (it.ttl) it.timer = setTimeout(() => unnotify(it.id), it.ttl);
  _msgRender();
  if (it.ask || it.level === "bad" || it.level === "warn") {
    _msgOpen(true);
    clearTimeout(_msg.fold);
    // only warnings open: fold after a while; an error or a question stays
    if (!_msg.items.some(m => m.ask || m.level === "bad")) _msg.fold = setTimeout(() => _msgOpen(false), MSG_FOLD_MS);
  }
  return it.id;
}

export function unnotify(id) {
  const it = _msg.items.find(m => m.id === id);
  if (!it) return;
  clearTimeout(it.timer);
  _msg.items = _msg.items.filter(m => m !== it);
  if (!_msg.items.length) _msgOpen(false);
  _msgRender();
}

// A click's result or a hint — the short form. ok (done) and an
// untyped hint (info) go in 2.5 s, warn in 5 s, bad stays until its ×.
export function toast(text, type = "ok") {
  const level = type || "info";
  return notify({ level, text: String(text), ttl: { ok: 2500, info: 2500, warn: 5000 }[level] });
}

function _msgOpen(open) {
  if (!open && _msg.items.some(m => m.ask)) open = true;    // a question waits for its buttons
  open = open && _msg.items.length > 0;
  if (!open) clearTimeout(_msg.fold);
  _msg.open = open;
  _msgStack().hidden = !open;
  for (const chip of document.querySelectorAll(".msg-chip")) chip.setAttribute("aria-expanded", String(open));
  _msgPlace();
}

function _msgPlace() {
  const host = _msgHost();
  if (!_msg.open || !host || !_msg.stack) return;
  const r = host.getBoundingClientRect();
  const st = _msg.stack;
  st.classList.toggle("big", !!host.closest(".pendant-nav"));
  // the cards line up with the line: the stack's own padding (base.css,
  // room for the cards' shadows) sits outside them
  const cs = getComputedStyle(st);
  const padX = parseFloat(cs.paddingLeft), padTop = parseFloat(cs.paddingTop);
  st.style.minWidth = `${r.width + 2 * padX}px`;          // first: the width the clamp reads
  st.style.top = `${r.bottom + 10 - padTop}px`;
  st.style.left = `${Math.max(0, Math.min(r.left - padX, window.innerWidth - st.offsetWidth))}px`;
}

// Each message is ONE card element, made when it arrives and kept until
// it goes: another message coming in never rebuilds a card — a
// question's Resume keeps its state mid-press. Newest on top.
function _msgCard(m) {
  const el = document.createElement("div");
  el.className = "msg-card";
  el.dataset.level = m.level;
  el.setAttribute("role", m.ask || m.level === "bad" ? "alert" : "status");
  el.innerHTML = `
    <div class="msg-head"><span class="msg-mark" aria-hidden="true">${MSG_MARK[m.level] || "!"}</span>
      <span class="msg-word">${esc(m.label || MSG_WORD[m.level] || "")}</span>
      ${m.ask ? "" : `<button type="button" class="msg-x" data-id="${esc(m.id)}" title="Dismiss" aria-label="Dismiss">×</button>`}</div>
    ${m.title ? `<h3 class="msg-title">${esc(m.title)}</h3>` : ""}
    ${m.text ? `<p class="msg-text">${esc(m.text)}</p>` : ""}
    ${m.img ? `<figure class="msg-img"><img src="${esc(m.img)}" alt="">${m.caption ? `<span class="msg-cap">${esc(m.caption)}</span>` : ""}</figure>` : ""}
    ${m.actions?.length ? `<div class="msg-acts">${m.actions.map((a, i) =>
      `<button type="button" class="btn${a.primary ? " btn-primary" : ""}" data-act="${i}">${esc(a.label)}</button>`).join("")}</div>` : ""}`;
  (m.actions || []).forEach((a, i) => a.wire?.(el.querySelector(`[data-act="${i}"]`)));
  return el;
}

function _msgRender() {
  const st = _msgStack();
  const want = [..._msg.items].reverse();
  for (const el of [...st.children]) if (!want.some(m => m.card === el)) el.remove();
  want.forEach((m, i) => {
    m.card ||= _msgCard(m);
    if (st.children[i] !== m.card) st.insertBefore(m.card, st.children[i] || null);
  });
  _msgRenderLines();
  _msgPlace();
}

function _msgRenderLines() {
  const top = _msg.items[_msg.items.length - 1];
  const n = _msg.items.length;
  for (const line of document.querySelectorAll(".msg-line")) {
    line.hidden = !top;
    if (!top) { line.innerHTML = ""; continue; }
    line.dataset.level = top.level;
    line.classList.toggle("urgent", !!(top.ask || top.level === "bad"));
    line.innerHTML = `<button type="button" class="msg-chip" aria-expanded="${_msg.open}" title="Show the messages">
      <span class="msg-mark" aria-hidden="true">${MSG_MARK[top.level] || "!"}</span>
      <span class="msg-word">${esc(top.label || MSG_WORD[top.level] || "")}</span>
      <span class="msg-first">${esc(top.title || top.text || "")}</span>
      ${n > 1 ? `<span class="msg-count" title="${n} messages">${n}</span>` : ""}</button>`;
    line.firstElementChild.addEventListener("click", () => _msgOpen(!_msg.open));
  }
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
