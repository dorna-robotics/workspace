// files.js — the project's file browser, one panel for all three roots.
//
// A project owns three folders (launch.yaml: data_dir / results_dir /
// rec_dir — see workspace/project_dirs.py). This panel is how an
// operator sees them: upload an input file once and pick it again next
// run instead of hunting for it on a laptop, read a finished run's
// records without leaving the bench, pull a recording down.
//
// TWO MODES, one component:
//   pick    — "Use this file" returns a path to the caller (the file
//             kwarg's Open button). Folders are navigable, files
//             selectable.
//   browse  — no return value; download / preview / manage.
//
// The panel stacks OVER whatever opened it (the Parameters modal is
// still there underneath), so it uses the shared .modal shell at a
// higher layer rather than inventing a second one.

const ROOTS = [
  { key: "data",    label: "Data",       hint: "Input files" },
  { key: "results", label: "Results",    hint: "One folder per run" },
  { key: "rec",     label: "Recordings", hint: "Replay captures" },
];

const api = (ws, root, tail = "") =>
  `/orchestrator/api/workspace/${encodeURIComponent(ws)}/files/${encodeURIComponent(root)}${tail}`;

// ---- the folder socket (server: fslive.py, twin of the vision unit's) ----
// Listing, New folder and Delete go over one WebSocket, answered by id;
// the server PUSHES changes to the folder on screen (a run writing its
// records, an upload landing), so the list updates without reloading.
// File BYTES stay on HTTP (download, zip, preview, upload): streamed,
// with the browser's own download handling. One socket per open panel,
// closed with it — the server stops watching the folder.
class FolderSocket {
  constructor(url, onEvent) {
    this.url = url; this.onEvent = onEvent;
    this.seq = 0; this.wait = new Map(); this.closed = false; this.backoff = 500;
    this.ready = this._connect();
  }
  _connect() {
    return new Promise((resolve) => {
      const ws = new WebSocket(this.url);
      this.ws = ws;
      ws.onopen = () => { this.backoff = 500; resolve(); this.onEvent({ ev: "open" }); };
      ws.onmessage = (m) => {
        let d; try { d = JSON.parse(m.data); } catch { return; }
        if (d.id != null && this.wait.has(d.id)) {
          const { ok, fail } = this.wait.get(d.id); this.wait.delete(d.id);
          d.ok ? ok(d) : fail(new Error(d.error || "request failed"));
        } else if (d.ev) this.onEvent(d);
      };
      ws.onclose = () => {
        for (const { fail } of this.wait.values()) fail(new Error("connection lost"));
        this.wait.clear();
        if (this.closed) return;
        this.onEvent({ ev: "down" });
        setTimeout(() => { if (!this.closed) this.ready = this._connect(); }, this.backoff);
        this.backoff = Math.min(this.backoff * 2, 8000);
      };
    });
  }
  async request(op, args) {
    await this.ready;
    const id = ++this.seq;
    return new Promise((ok, fail) => {
      this.wait.set(id, { ok, fail });
      this.ws.send(JSON.stringify({ id, op, ...args }));
    });
  }
  close() { this.closed = true; try { this.ws.close(); } catch {} }
}

const socketUrl = (ws) =>
  `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/orchestrator/ws/files/${encodeURIComponent(ws)}`;

// Folders first, then newest first — the server's order, kept on update.
const byOrder = (a, b) => (a.dir === b.dir ? b.mtime - a.mtime : a.dir ? -1 : 1);

function fmtSize(n) {
  if (!n) return "—";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
}

function fmtWhen(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return sameDay ? time : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

const ICON = {
  folder: '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>',
  file:   '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/>',
  down:   '<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  up:     '<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
  trash:  '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/><path d="M10 11v6M14 11v6"/>',
  newdir: '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/>',
  close:  '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  zip:    '<path d="M21 8v13H3V8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/>',
};

const svg = (d, size = 14) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;

let el = null;          // the single panel instance, built once

function build() {
  const ov = document.createElement("div");
  ov.className = "modal-overlay fb-overlay";
  ov.innerHTML = `
    <div class="modal modal-wide fb-modal">
      <div class="modal-head">
        <h3 class="fb-title">Files</h3>
        <div class="spacer"></div>
        <button class="btn btn-ghost btn-sm btn-icon fb-close" title="Close">${svg(ICON.close, 13)}</button>
      </div>
      <div class="fb-purpose" hidden></div>
      <div class="fb-roots" role="tablist"></div>
      <div class="fb-bar">
        <div class="fb-crumbs"></div>
        <div class="spacer"></div>
        <button class="btn btn-sm fb-mkdir" title="Create a folder here">${svg(ICON.newdir)} New folder</button>
        <button class="btn btn-sm fb-zip" title="Download this folder as a zip">${svg(ICON.zip)} Download folder</button>
        <button class="btn btn-sm fb-upload" title="Upload a file into this folder">${svg(ICON.up)} Upload</button>
        <input type="file" class="fb-file" hidden />
      </div>
      <div class="fb-body">
        <div class="fb-list" role="listbox"></div>
        <div class="fb-split" role="separator" aria-orientation="vertical"
             title="Drag to resize" hidden></div>
        <div class="fb-preview" hidden></div>
      </div>
      <div class="modal-foot fb-foot">
        <div class="fb-where"></div>
        <div class="spacer"></div>
        <button class="btn fb-cancel">Close</button>
        <button class="btn btn-primary fb-use" hidden disabled></button>
      </div>
    </div>`;
  document.body.appendChild(ov);
  return ov;
}

// PUT one file as the raw request body; resolves with the server's JSON.
function putFile(url, file, onPct) {
  return new Promise((ok, fail) => {
    const x = new XMLHttpRequest();
    x.open("PUT", url);
    x.upload.onprogress = (e) => { if (e.lengthComputable) onPct(Math.round((e.loaded / e.total) * 100)); };
    x.onload = () => {
      let d = {}; try { d = JSON.parse(x.responseText); } catch {}
      x.status < 300 ? ok(d) : fail(new Error(d.error || `upload failed (${x.status})`));
    };
    x.onerror = () => fail(new Error("upload failed — connection lost"));
    x.send(file);
  });
}

/**
 * Open the browser.
 *   opts.wsName  workspace identifier (required)
 *   opts.root    which folder to open on — "data" | "results" | "rec"
 *   opts.mode    "pick" | "browse"
 *   opts.accept  file input accept string, pick mode
 *   opts.title   panel title — say what is being chosen
 *   opts.purpose one sentence under the title: what the pick will DO
 *   opts.pickLabel  the confirm button's words, a verb ("Load
 *                parameters", "Use for Manifest"). "Use this file"
 *                answers nothing — for what?
 *   opts.toast   (msg, kind) notifier
 * Resolves to the picked entry {path, name, abs} or null.
 */
export function openFileBrowser(opts = {}) {
  const { wsName, mode = "browse", accept = "", toast = () => {} } = opts;
  if (!el) el = build();
  const q = (s) => el.querySelector(s);

  let root = opts.root || "data";
  let path = "";
  let selected = null;
  let resolveFn = null;
  let entries = [];              // the folder on screen, kept live by the socket
  const rows = new Map();        // path -> its row element
  const sock = new FolderSocket(socketUrl(wsName), onEvent);

  const list = q(".fb-list");
  const preview = q(".fb-preview");
  const split = q(".fb-split");
  const body = q(".fb-body");
  const useBtn = q(".fb-use");
  const fileInput = q(".fb-file");

  // The divider's position survives the panel closing and the page
  // reloading — layout state the operator set, applied before the pane
  // is shown so nothing jumps (design-system §11).
  const SPLIT_KEY = "fb_split_pct";
  const readSplit = () => {
    const v = parseFloat(localStorage.getItem(SPLIT_KEY) || "");
    return Number.isFinite(v) ? Math.min(75, Math.max(20, v)) : 46;
  };
  const applySplit = (pct) => { preview.style.width = `${pct}%`; };
  applySplit(readSplit());

  function showPane(on) {
    preview.hidden = !on;
    split.hidden = !on;
    if (on) applySplit(readSplit());
  }

  if (!split.dataset.wired) {
    split.dataset.wired = "1";
    split.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      split.setPointerCapture(ev.pointerId);
      split.classList.add("is-dragging");
      body.classList.add("is-resizing");
      const move = (e) => {
        const r = body.getBoundingClientRect();
        if (!r.width) return;
        const pct = Math.min(75, Math.max(20, ((r.right - e.clientX) / r.width) * 100));
        preview.style.width = `${pct}%`;
        localStorage.setItem(SPLIT_KEY, String(Math.round(pct)));
      };
      const up = (e) => {
        split.releasePointerCapture(ev.pointerId);
        split.classList.remove("is-dragging");
        body.classList.remove("is-resizing");
        split.removeEventListener("pointermove", move);
        split.removeEventListener("pointerup", up);
      };
      split.addEventListener("pointermove", move);
      split.addEventListener("pointerup", up);
    });
    // Double-click resets to the default share — the escape hatch for
    // a divider dragged somewhere useless.
    split.addEventListener("dblclick", () => {
      localStorage.removeItem(SPLIT_KEY);
      applySplit(46);
    });
  }

  q(".fb-title").textContent = opts.title || (mode === "pick" ? "Choose a file" : "Files");
  useBtn.hidden = mode !== "pick";
  useBtn.textContent = opts.pickLabel || "Use this file";
  fileInput.accept = accept || "";

  const purpose = q(".fb-purpose");
  const types = accept
    ? ` Expected: ${accept.split(",").map((t) => `<code>${t.trim()}</code>`).join(" ")}.`
    : "";
  if (opts.purpose || types) {
    purpose.innerHTML = (opts.purpose || "") + types;
    purpose.hidden = false;
  } else {
    purpose.hidden = true;
  }

  // ---- root tabs ----
  const rootsWrap = q(".fb-roots");
  rootsWrap.innerHTML = "";
  for (const r of ROOTS) {
    const b = document.createElement("button");
    b.className = "fb-root" + (r.key === root ? " is-active" : "");
    b.type = "button";
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", String(r.key === root));
    b.title = r.hint;
    b.innerHTML = `<span class="fb-root-name">${r.label}</span>`;
    b.addEventListener("click", () => { root = r.key; path = ""; select(null); load(); });
    rootsWrap.appendChild(b);
  }

  function syncRoots() {
    [...rootsWrap.children].forEach((b, i) => {
      const on = ROOTS[i].key === root;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-selected", String(on));
    });
  }

  function select(entry) {
    selected = entry;
    useBtn.disabled = !entry || entry.dir;
    [...list.querySelectorAll(".fb-row")].forEach((r) =>
      r.classList.toggle("is-selected", !!entry && r.dataset.path === entry.path));
  }

  // ---- breadcrumbs ----
  function crumbs() {
    const wrap = q(".fb-crumbs");
    wrap.innerHTML = "";
    const parts = path ? path.split("/") : [];
    const mk = (label, target, last) => {
      const b = document.createElement("button");
      b.className = "fb-crumb" + (last ? " is-last" : "");
      b.type = "button";
      b.textContent = label;
      if (!last) b.addEventListener("click", () => { path = target; select(null); load(); });
      wrap.appendChild(b);
      if (!last) wrap.insertAdjacentHTML("beforeend", '<span class="fb-sep">/</span>');
    };
    mk(ROOTS.find((r) => r.key === root)?.label || root, "", parts.length === 0);
    parts.forEach((p, i) =>
      mk(p, parts.slice(0, i + 1).join("/"), i === parts.length - 1));
  }

  // ---- listing ----
  // One "open" per folder change; afterwards the server pushes what
  // changed in it (onEvent) and only those rows are touched.
  let opening = 0;
  async function load() {
    syncRoots();
    crumbs();
    showPane(false);
    const mine = ++opening;
    list.innerHTML = `<div class="fb-loading">${[0, 1, 2].map(() =>
      '<div class="fb-skel"></div>').join("")}</div>`;
    let data;
    try {
      data = (await sock.request("open", { root, path })).folder;
    } catch (err) {
      if (mine !== opening) return;
      list.innerHTML = `<div class="fb-error">${svg(ICON.file)} ${err.message}</div>`;
      q(".fb-where").textContent = "";
      return;
    }
    if (mine !== opening) return;      // a newer folder was opened meanwhile
    q(".fb-where").innerHTML =
      `<span class="fb-path" title="${data.abs || ""}">${data.abs || ""}</span>` +
      (data.declared ? "" : `<span class="fb-default" title="launch.yaml does not name ${root}_dir — this is the default">default</span>`) +
      (data.live ? "" : `<span class="fb-default" title="this server cannot watch the folder — reopen it to refresh">not live</span>`);
    entries = data.entries;
    render();
  }

  function render() {
    rows.clear();
    if (!entries.length) {
      list.innerHTML = `<div class="fb-empty">Nothing here yet — <b>Upload</b> adds the first file.</div>`;
      return;
    }
    const frag = document.createDocumentFragment();
    for (const e of entries) frag.appendChild(rowFor(e));
    list.innerHTML = "";
    list.appendChild(frag);
    select(selected && entries.find((x) => x.path === selected.path) || null);
  }

  function rowFor(e) {
    const row = document.createElement("div");
    row.className = "fb-row";
    row.dataset.path = e.path;
    row.setAttribute("role", "option");
    row.innerHTML = `
      <span class="fb-ic ${e.dir ? "is-dir" : ""}">${svg(e.dir ? ICON.folder : ICON.file, 15)}</span>
      <span class="fb-name">${e.name}</span>
      <span class="fb-size">${e.dir ? "" : fmtSize(e.size)}</span>
      <span class="fb-when">${fmtWhen(e.mtime)}</span>
      <span class="fb-acts"></span>`;
    const acts = row.querySelector(".fb-acts");
    // A file downloads as itself, a folder as a zip (streamed by the
    // server, so a large run folder never sits in memory).
    const dl = document.createElement("a");
    dl.className = "btn btn-ghost btn-sm btn-icon";
    dl.title = e.dir ? `Download ${e.name} as a zip` : `Download ${e.name}`;
    dl.href = api(wsName, root, `?path=${encodeURIComponent(e.path)}&${e.dir ? "zip" : "download"}=1`);
    dl.setAttribute("download", e.dir ? `${e.name}.zip` : e.name);
    dl.innerHTML = svg(e.dir ? ICON.zip : ICON.down, 13);
    dl.addEventListener("click", (ev) => ev.stopPropagation());
    acts.appendChild(dl);
    const del = document.createElement("button");
    del.className = "btn btn-ghost btn-sm btn-icon fb-del";
    del.title = e.dir ? `Delete the folder ${e.name}` : `Delete ${e.name}`;
    del.innerHTML = svg(ICON.trash, 13);
    del.addEventListener("click", (ev) => { ev.stopPropagation(); remove(e.path); });
    acts.appendChild(del);

    row.addEventListener("click", () => {
      const cur = entries.find((x) => x.path === e.path) || e;
      if (cur.dir) { path = cur.path; select(null); load(); }
      else { select(cur); showPreview(cur); }
    });
    row.addEventListener("dblclick", () => {
      const cur = entries.find((x) => x.path === e.path) || e;
      if (!cur.dir && mode === "pick") { select(cur); done(cur); }
    });
    rows.set(e.path, row);
    return row;
  }

  // What the server pushes for the folder on screen: rows added,
  // changed or gone — applied in place, order kept.
  function onEvent(d) {
    if (d.ev === "open" && entries.length + rows.size > 0) { load(); return; } // reconnected: resync
    if (d.root !== root || d.path !== path) return;
    if (d.ev === "gone") {                 // the folder itself was deleted
      path = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
      select(null); load(); return;
    }
    if (d.ev !== "change") return;
    if (d.reset) { entries = d.upsert.sort(byOrder); render(); return; }
    const rm = new Set(d.remove || []);
    const up = new Map((d.upsert || []).map((e) => [e.path, e]));
    entries = entries.filter((e) => !rm.has(e.path) && !up.has(e.path))
                     .concat([...up.values()]).sort(byOrder);
    if (!entries.length) { render(); return; }
    if (!rows.size) { render(); return; }       // was the empty placeholder
    for (const p of rm) { rows.get(p)?.remove(); rows.delete(p); }
    for (const [p, e] of up) {                    // changed rows are rebuilt
      rows.get(p)?.remove();
      rows.delete(p);
      rowFor(e);
    }
    const frag = document.createDocumentFragment();   // one reorder pass
    for (const e of entries) frag.appendChild(rows.get(e.path));
    list.appendChild(frag);
    if (selected) {
      if (rm.has(selected.path)) { select(null); showPane(false); }
      else if (up.has(selected.path)) { select(up.get(selected.path)); showPreview(up.get(selected.path)); }
      else select(selected);
    }
  }

  // ---- preview ----
  async function showPreview(e) {
    showPane(true);
    preview.innerHTML = `<div class="fb-loading"><div class="fb-skel"></div></div>`;
    try {
      const resp = await fetch(api(wsName, root, `?path=${encodeURIComponent(e.path)}&preview=1`));
      const d = await resp.json();
      if (!resp.ok) throw new Error(d.error || "Could not read the file");
      if (d.kind === "table") {
        const head = `<tr>${d.columns.map((c) =>
          `<th>${String(c).replace(/[<&]/g, (x) => (x === "<" ? "&lt;" : "&amp;"))}</th>`).join("")}</tr>`;
        const esc = (v) => String(v).replace(/[<&]/g, (c) => (c === "<" ? "&lt;" : "&amp;"));
        const body = d.rows.map((r) =>
          `<tr>${r.map((c) => `<td title="${esc(c).replace(/"/g, "&quot;")}">${esc(c)}</td>`).join("")}</tr>`
        ).join("");
        preview.innerHTML =
          `<div class="fb-pv-head"><span class="fb-pv-name">${e.name}</span>` +
          `<span class="fb-pv-meta">${d.total ?? d.rows.length} row${(d.total ?? d.rows.length) === 1 ? "" : "s"}` +
          `${d.truncated ? ` · showing first ${d.rows.length}` : ""}` +
          `${d.note ? ` · ${d.note}` : ""}</span></div>` +
          `<div class="fb-pv-scroll"><table class="fb-table">${head}${body}</table></div>`;
      } else if (d.kind === "text") {
        preview.innerHTML =
          `<div class="fb-pv-head"><span class="fb-pv-name">${e.name}</span></div>` +
          `<div class="fb-pv-scroll"><pre class="fb-pre">${
            d.text.replace(/[<&]/g, (c) => (c === "<" ? "&lt;" : "&amp;"))}</pre></div>`;
      } else {
        preview.innerHTML = `<div class="fb-error">${d.error || "Cannot preview this file"}</div>`;
      }
    } catch (err) {
      preview.innerHTML = `<div class="fb-error">${err.message}</div>`;
    }
  }

  // ---- actions ----
  // New folder / Delete go over the socket; the list updates from the
  // change the server then pushes — no reload.
  async function remove(p) {
    const e = entries.find((x) => x.path === p);
    if (!e) return;
    const ok = window.confirm(
      e.dir ? `Delete the folder “${e.name}”?` : `Delete “${e.name}”?\n\nThis cannot be undone.`);
    if (!ok) return;
    try {
      await sock.request("delete", { root, path: e.path });
    } catch (err) { toast(err.message, "bad"); }
  }

  q(".fb-mkdir").onclick = async () => {
    const name = window.prompt("New folder name");
    if (!name) return;
    try {
      await sock.request("mkdir", { root, path: path ? `${path}/${name}` : name });
    } catch (err) { toast(err.message, "bad"); }
  };

  // The folder on screen, as a zip. A throwaway <a download> so the
  // browser's own download UI shows progress for a big folder.
  q(".fb-zip").onclick = () => {
    const a = document.createElement("a");
    a.href = api(wsName, root, `?path=${encodeURIComponent(path)}&zip=1`);
    a.setAttribute("download", "");
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  // Upload: the file is the request body, streamed to disk by the
  // server as it arrives (fslive.UploadHandler) — never held in memory.
  // XHR, not fetch, for the upload progress.
  const upBtn = q(".fb-upload");
  upBtn.onclick = () => fileInput.click();
  fileInput.onchange = async () => {
    if (!fileInput.files.length) return;
    const f = fileInput.files[0];
    const label = upBtn.innerHTML;
    upBtn.disabled = true;
    try {
      await putFile(api(wsName, root, `/upload?${new URLSearchParams({ path, name: f.name })}`), f,
        (pct) => { upBtn.innerHTML = `<span class="fb-spin"></span> Uploading ${pct}%`; });
    } catch (err) {
      toast(err.message, "bad");
    } finally {
      upBtn.disabled = false;
      upBtn.innerHTML = label;
      fileInput.value = "";
    }
  };

  // ---- open / close ----
  function done(entry) {
    sock.close();                        // the server stops watching
    el.classList.remove("show");
    document.removeEventListener("keydown", onKey, true);
    const r = resolveFn; resolveFn = null;
    if (r) r(entry || null);
  }
  // Escape closes the panel, like every other modal. CAPTURE phase +
  // stopImmediatePropagation because this panel is usually opened from
  // INSIDE the parameters modal: the page's own global ESC handler is a
  // bubble-phase listener on document, so without taking the key first
  // one press would close the browser AND the modal underneath it.
  function onKey(ev) {
    if (ev.key !== "Escape" || !el.classList.contains("show")) return;
    ev.stopImmediatePropagation();
    ev.preventDefault();
    done(null);
  }
  q(".fb-close").onclick = () => done(null);
  q(".fb-cancel").onclick = () => done(null);
  useBtn.onclick = () => done(selected);
  // Backdrop click closes, same as the params and device modals. The
  // press must START on the backdrop too: this panel has a drag-to-
  // resize splitter, and releasing a drag outside the modal delivers a
  // click whose target is the overlay — a pick would be lost to it.
  let downOnBackdrop = false;
  el.onpointerdown = (ev) => { downOnBackdrop = (ev.target === el); };
  el.onclick = (ev) => { if (ev.target === el && downOnBackdrop) done(null); };

  select(null);
  load();
  el.classList.add("show");
  document.addEventListener("keydown", onKey, true);
  return new Promise((res) => { resolveFn = res; });
}
