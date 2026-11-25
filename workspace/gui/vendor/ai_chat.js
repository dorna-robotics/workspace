// The Workspace assistant — a floating button that opens a side panel, on
// every page once this robot is logged in (hidden in offline mode).
// Talks to /ai/api (gui/ai/handlers.py): POST /chat streams NDJSON events
// (text / think / tool / tool_done / image / excerpt / approve / approval /
// ui / error / done). A picture a tool showed is drawn inline (click to
// enlarge); an excerpt is a log / file excerpt with its lines highlighted;
// an approve card holds a hardware action until the user presses Approve
// or Decline (POST /approve). The look and the
// message rendering are the AI builder's chat (dorna_ai_builder
// builder/web/builder/ai_chat.js); files go up through POST /upload
// (attach button, drag and drop, paste) and ride the next message.
//
// A page tells the assistant what the user is looking at by setting
//   window.__aiContext = {page, project, tab, ...}   (or a function returning it)
// and can follow what the assistant changed with
//   window.addEventListener("ai-ui", (e) => e.detail)   // {type: reload|status, what?: scene|hmi|parameters|files, project}
//   window.addEventListener("ai-turn", ...)             // a turn finished
// Any page brings a message to the assistant with
//   window.dispatchEvent(new CustomEvent("ask-ai", {detail: {level, label, title, text}}))
// (vendor/messages.js's "Ask Dorna AI" on an error / warning card); <html>
// has the class "ai-ready" while the assistant can take it.
(() => {
  if (window.__aiChatLoaded) return;
  // a page embedded in another (the Scene builder inside Developer mode):
  // the outer page has the assistant — never a second one inside the frame
  try { if (window.self !== window.top) return; } catch (_) { return; }
  window.__aiChatLoaded = true;
  const API = "/ai/api";
  // the orchestrator token (when ORCH_TOKEN is on) — the assistant's routes need it too
  const hdrs = (json) => {
    const h = json ? { "Content-Type": "application/json" } : {};
    try { const t = (localStorage.getItem("orch_token") || "").trim(); if (t) h["X-Orch-Token"] = t; } catch (_) {}
    return h;
  };
  const OPEN_KEY = "ws_ai_open", SID_KEY = "ws_ai_session", W_KEY = "ws_ai_width";

  function css(href) {
    if (document.querySelector(`link[href="${href}"]`)) return;
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = href;
    document.head.appendChild(l);
  }
  css("/vendor/ai_chat.css");
  // the account area comes with it (one owner: account.js)
  if (!window.dornaAccount && !document.querySelector('script[src="/vendor/account.js"]')) {
    const s = document.createElement("script"); s.src = "/vendor/account.js"; document.head.appendChild(s);
  }

  const ls = { get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
               set(k, v) { try { localStorage.setItem(k, v); } catch (_) {} } };
  let sid = ls.get(SID_KEY);
  let busy = false, loaded = false, enabled = false;
  let atts = [], attKey = 0;      // the files for the next message: {key, file, state: up|ok|bad, pct, rec, error, preview, xhr}

  const MAX_UPLOAD = 50 * 1024 * 1024;           // gui/ai/uploads.py MAX_BYTES
  const CLIP_ICON = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21.4 11.1l-8.5 8.5a5.5 5.5 0 01-7.8-7.8l8.5-8.5a3.7 3.7 0 015.2 5.2l-8.5 8.5a1.8 1.8 0 01-2.6-2.6l7.8-7.8"/></svg>`;
  const FILE_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
  const CUBE_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 16V8a2 2 0 00-1-1.7l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.7l7 4a2 2 0 002 0l7-4a2 2 0 001-1.7z"/><polyline points="3.3 7 12 12 20.7 7"/><line x1="12" y1="22" x2="12" y2="12"/></svg>`;
  const EYE_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`;
  const KIND_ICON = { cad: CUBE_ICON, vision_model: EYE_ICON };
  const KIND_WORD = { image: "Image", pdf: "PDF", cad: "3D model", vision_model: "Vision model", archive: "Archive", text: "Text", other: "File" };
  const SUGGESTIONS = [
    "Why did the last run stop?",
    "Which devices are in simulation in this project?",
    "Add a field for the batch size to the Parameters page.",
  ];

  const LOGO = `<svg viewBox="97.377763787 267.344743133 34.999991232 34.999995952" aria-hidden="true"><path d="m 115.01371,267.34517 c -2.29532,-0.0158 -4.53492,0.40708 -6.65544,1.25775 -2.19489,0.88047 -4.16317,2.18192 -5.85032,3.86819 -2.35312,2.35191 -3.979789,5.30207 -4.703869,8.53143 -0.70508,3.14428 -0.53303,6.41655 0.49727,9.46331 0.48483,1.4339 1.38431,2.57081 2.601019,3.28767 1.10797,0.65278 2.45406,0.93696 3.79026,0.80031 2.19193,-0.22429 6.15511,-1.56389 6.58127,-5.04159 0.48278,-3.93968 -2.17383,-8.42404 -2.63533,-9.84628 -0.0589,-0.18152 -0.10541,-0.32499 -0.13722,-0.43224 -0.42269,-1.42634 -0.23659,-2.7638 0.51077,-3.66951 0.52607,-0.63754 1.57248,-1.35704 3.57034,-1.16398 2.00419,0.19361 5.30108,1.78893 7.53799,2.98554 -1.01837,-0.0805 -2.06121,-0.0226 -3.02265,0.15239 -3.04696,0.55447 -4.70375,3.06534 -3.82983,5.71262 0.118,0.54878 0.2842,1.16531 0.47997,1.87022 0.54764,1.97214 1.38583,4.27011 1.28244,7.2277 -0.11004,3.1469 -1.23383,6.42864 -3.10793,9.67983 2.84216,0.5536 5.66007,0.38004 8.39122,-0.51869 3.18536,-1.04828 5.44222,-2.80977 6.77449,-4.10292 1.68759,-1.63817 3.00417,-3.56451 3.91339,-5.72523 0.87828,-2.08713 1.34074,-4.30153 1.37425,-6.5815 0.0335,-2.28031 -0.36367,-4.50665 -1.18044,-6.61755 -0.84579,-2.18579 -2.10613,-4.14869 -3.74598,-5.83394 -3.29609,-3.38728 -7.71247,-5.27066 -12.43567,-5.30353 z" fill="currentColor"/></svg>`;
  const SPARK_ICON = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9z"/><path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z"/></svg>`;

  const HISTORY_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 103-6.7L3 8"/><polyline points="3 3 3 8 8 8"/><polyline points="12 7 12 12 15 14"/></svg>`;

  const NEW_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/></svg>`;
  const CLOSE_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12"/></svg>`;

  // ── DOM ──────────────────────────────────────────────────────────
  const fab = document.createElement("button");
  fab.type = "button"; fab.className = "ai-fab"; fab.hidden = true;
  fab.title = "Dorna AI"; fab.setAttribute("aria-label", "Open Dorna AI");
  fab.innerHTML = `${SPARK_ICON}<span>Dorna AI</span>`;

  const root = document.createElement("aside");
  root.className = "ai-panel"; root.hidden = true;
  root.setAttribute("aria-label", "Dorna AI");
  root.innerHTML = `
    <div class="ai-resize" title="Drag to resize"></div>
    <div class="ai-head">
      <span class="ai-logo">${LOGO}</span><span class="ai-title">Dorna AI</span>
      <span class="ai-proj" hidden></span>
      <span class="spacer"></span>
      <button class="ai-iconbtn ai-hist-btn" type="button" title="Recent chats" aria-label="Recent chats" aria-expanded="false">${HISTORY_ICON}</button>
      <button class="ai-iconbtn ai-new" type="button" title="New chat" aria-label="New chat">${NEW_ICON}</button>
      <span class="ai-head-sep" aria-hidden="true"></span><button class="ai-iconbtn ai-close" type="button" title="Close" aria-label="Close Dorna AI">${CLOSE_ICON}</button>
    </div>
    <div class="ai-hist" role="menu" aria-label="Recent chats" hidden></div>
    <div class="ai-log" aria-live="polite"></div>
    <div class="ai-compose">
      <div class="ai-out" role="status" hidden><b>Out of AI credits</b>Contact the Dorna team to add more. Your projects keep running as usual.</div>
      <div class="ai-atts" hidden></div>
      <div class="ai-compose-row">
        <button class="ai-iconbtn ai-attach" type="button" title="Attach files — images, PDFs, 3D models, vision models (up to 50 MB each)" aria-label="Attach files">${CLIP_ICON}</button>
        <textarea rows="1" placeholder="Ask Dorna AI…" aria-label="Message Dorna AI"></textarea>
        <button class="btn btn-primary btn-sm ai-send" type="button" title="Send (Enter)" aria-label="Send">Send</button>
      </div>
      <div class="ai-bar"><span class="spacer"></span><span class="ai-hint">Enter to send</span>
        <button class="btn btn-ghost btn-sm ai-stop" type="button" hidden>Stop</button></div>
      <input class="ai-file" type="file" multiple hidden/>
    </div>
    <div class="ai-drop" hidden><div>Drop files to attach</div></div>`;
  const $ = (s) => root.querySelector(s);
  const log = $(".ai-log"), input = $("textarea"), sendBtn = $(".ai-send"), stopBtn = $(".ai-stop"), projEl = $(".ai-proj");
  const attsEl = $(".ai-atts"), fileIn = $(".ai-file"), dropEl = $(".ai-drop");
  const w = parseInt(ls.get(W_KEY) || "", 10);
  if (isFinite(w)) root.style.setProperty("--aiw", Math.max(320, Math.min(900, w)) + "px");

  // ── helpers ──────────────────────────────────────────────────────
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function inline(s) {
    return esc(s)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>");
  }
  function md(text) {
    const out = []; let list = null, para = [], code = null;
    const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
    const flush = () => { if (para.length) { out.push(`<p>${para.map(inline).join("<br>")}</p>`); para = []; } };
    for (const raw of String(text).split("\n")) {
      const line = raw.trimEnd();
      let m;
      if (/^\s*```/.test(line)) {
        if (code) { out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`); code = null; }
        else { flush(); close(); code = []; }
        continue;
      }
      if (code) { code.push(raw); continue; }
      if (!line.trim()) { flush(); close(); continue; }
      if ((m = line.match(/^\s*[-*•]\s+(.*)/))) { flush(); if (list !== "ul") { close(); out.push("<ul>"); list = "ul"; } out.push(`<li>${inline(m[1])}</li>`); continue; }
      if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { flush(); if (list !== "ol") { close(); out.push("<ol>"); list = "ol"; } out.push(`<li>${inline(m[1])}</li>`); continue; }
      if ((m = line.match(/^#{1,4}\s+(.*)/))) { flush(); close(); out.push(`<h4>${inline(m[1])}</h4>`); continue; }
      close(); para.push(line);
    }
    if (code) out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
    flush(); close();
    return out.join("");
  }
  const scrollDown = () => { log.scrollTop = log.scrollHeight; };
  // an image from /ai/api: a plain src, or — when the orchestrator token is
  // on (an <img> can't send its header) — fetched with it into a blob URL
  function authImg(img, url) {
    const h = hdrs(false);
    if (!h["X-Orch-Token"]) { img.src = url; return; }
    fetch(url, { headers: h }).then((r) => r.ok ? r.blob() : null).then((b) => { if (b) img.src = URL.createObjectURL(b); }).catch(() => {});
  }
  const shotUrl = (shot) => `${API}/shot?session=${encodeURIComponent(sid || "")}&id=${encodeURIComponent(shot)}`;
  let lightbox = null;
  function enlarge(src, caption) {
    if (!lightbox) {
      lightbox = document.createElement("div");
      lightbox.className = "ai-lightbox"; lightbox.hidden = true;
      lightbox.setAttribute("role", "dialog"); lightbox.setAttribute("aria-label", "Picture");
      lightbox.innerHTML = `<figure><img alt=""/><figcaption></figcaption></figure><button type="button" class="ai-lb-x" aria-label="Close">${CLOSE_ICON}</button>`;
      lightbox.addEventListener("click", () => { lightbox.hidden = true; });
      document.body.appendChild(lightbox);
    }
    lightbox.querySelector("img").src = src;
    lightbox.querySelector("figcaption").textContent = caption || "";
    lightbox.hidden = false;
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && lightbox && !lightbox.hidden) { e.stopImmediatePropagation(); lightbox.hidden = true; }
  }, true);
  const fmtSize = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;
  function kindOf(name) {
    const e = (String(name).match(/\.[^.]+$/) || [""])[0].toLowerCase();
    if ([".png", ".jpg", ".jpeg", ".webp", ".gif"].includes(e)) return "image";
    if (e === ".pdf") return "pdf";
    if ([".glb", ".gltf", ".step", ".stp", ".stl", ".obj", ".3mf", ".iges", ".igs"].includes(e)) return "cad";
    if ([".pkl", ".onnx", ".pt", ".pth", ".engine", ".blob", ".tflite"].includes(e)) return "vision_model";
    if ([".zip", ".tar", ".gz", ".tgz", ".7z"].includes(e)) return "archive";
    if ([".csv", ".json", ".yaml", ".yml", ".py", ".txt", ".md", ".j2"].includes(e)) return "text";
    return "other";
  }

  // a tool's label in plain words (the AI builder's toolText): the server
  // names most; a bare id ("add_component_from_file x") reads as a verb
  function toolText(label) {
    const t = String(label || "").trim();
    const m = t.match(/^([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?:\s+(.*))?$/);
    if (!m) return t;
    const words = (x) => x.replace(/_+/g, " ").trim();
    const verb = words(m[1]).charAt(0).toUpperCase() + words(m[1]).slice(1);
    return m[2] ? `${verb} ${words(m[2])}` : verb;
  }

  function pageContext() {
    let c = window.__aiContext;
    if (typeof c === "function") { try { c = c(); } catch (_) { c = null; } }
    const q = new URLSearchParams(location.search);
    const base = { page: location.pathname.replace(/\/index\.html$/, "/") };
    const proj = q.get("project") || q.get("name");
    if (proj) base.project = proj;
    if (q.get("pendant")) base.view = "pendant";
    return Object.assign(base, c && typeof c === "object" ? c : {});
  }
  function showProject() {
    const p = pageContext().project;
    projEl.hidden = !p;
    projEl.textContent = (p || "").replace(/_+/g, " ");
    projEl.title = p ? `Project: ${p}` : "";
  }

  function setBusy(b) { busy = b; sendBtn.disabled = b || atts.some((a) => a.state === "up"); stopBtn.hidden = !b; }

  // ── rendering (the AI builder's chat look) ───────────────────────
  function emptyState() {
    log.innerHTML = "";
    const d = document.createElement("div");
    d.className = "ai-empty";
    d.innerHTML = `<h3>How can I help?</h3>
      <div>I know this Workspace from running a project to developing one. I can check a run, recover a device,
      change the scene, the workflow or the HMI, and answer questions.</div>
      <div>For example:</div>
      <ul class="ai-examples">${SUGGESTIONS.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>`;
    log.appendChild(d);
  }
  // one file, as a chip: a thumbnail for an image, else the kind's icon
  function fileChip(f, opts = {}) {
    const c = document.createElement("div");
    c.className = "ai-file-chip" + (opts.cls ? " " + opts.cls : "");
    const kind = f.kind || kindOf(f.name);
    const thumb = kind === "image" && (opts.preview || (f.id && sid))
      ? `<img alt="" src="${esc(opts.preview || `${API}/upload_file?session=${encodeURIComponent(sid)}&id=${encodeURIComponent(f.id)}`)}"/>`
      : `<span class="ic">${KIND_ICON[kind] || FILE_ICON}</span>`;
    c.innerHTML = `<span class="thumb">${thumb}</span><span class="meta"><span class="nm"></span><span class="sub"></span></span>`;
    c.querySelector(".nm").textContent = f.name;
    c.querySelector(".sub").textContent = [KIND_WORD[kind] || "File", f.size != null ? fmtSize(f.size) : ""].filter(Boolean).join(" · ");
    c.title = f.project_path ? `${f.name} — also saved in the project as ${f.project_path}` : f.name;
    return c;
  }
  function userBubble(text, files) {
    log.querySelector(".ai-empty")?.remove();
    const d = document.createElement("div");
    d.className = "ai-msg user";
    if (files && files.length) {
      const row = document.createElement("div");
      row.className = "ai-msg-files";
      files.forEach((f) => row.appendChild(fileChip(f, { cls: "in-bubble", preview: f.preview })));
      d.appendChild(row);
    }
    const t = document.createElement("div");
    t.className = "ai-msg-text"; t.textContent = text;
    d.appendChild(t);
    log.appendChild(d); scrollDown();
  }
  // a local line in the log (not sent, not saved)
  function note(msg, bad) {
    log.querySelector(".ai-empty")?.remove();
    const d = document.createElement("div");
    d.className = "ai-note" + (bad ? " bad" : "");
    d.textContent = msg;
    log.appendChild(d); scrollDown();
    return d;
  }
  function sealTools(el) {
    if (!el || el.querySelector(".ai-tools-sum")) return;
    const rows = el.querySelectorAll(".ai-tool");
    if (rows.length < 2 || el.querySelector(".ai-tool.running")) return;
    const b = document.createElement("button");
    b.type = "button"; b.className = "ai-tools-sum"; b.setAttribute("aria-expanded", "false");
    b.innerHTML = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg><span>${rows.length} actions</span>`;
    b.onclick = () => { const on = el.classList.toggle("open"); b.setAttribute("aria-expanded", String(on)); };
    el.classList.add("sealed");
    el.prepend(b);
  }
  // the last group of actions in the log when nothing said has come after it
  function lastBareTools() {
    const all = log.querySelectorAll(".ai-tools");
    const last = all[all.length - 1];
    if (!last) return null;
    for (const el of log.querySelectorAll(".ai-text, .ai-msg.user, .ai-error")) {
      if (last.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING && el.textContent.trim()) return null;
    }
    return last;
  }
  function assistantBubble() {
    const d = document.createElement("div");
    d.className = "ai-msg assistant";
    log.appendChild(d);
    const api = {
      el: d, textEl: null, textBuf: "", toolsEl: null, tools: {}, thinkEl: null,
      seal() { sealTools(api.toolsEl); },
      text(delta) {
        api.thinkDone();
        if (!api.textEl) { api.seal(); api.toolsEl = null; api.textEl = document.createElement("div"); api.textEl.className = "ai-text"; d.appendChild(api.textEl); api.textBuf = ""; }
        api.textBuf += delta;
        api.textEl.innerHTML = md(api.textBuf);
        scrollDown();
      },
      think(delta) {
        if (!api.thinkEl) {
          api.thinkEl = document.createElement("details");
          api.thinkEl.className = "ai-think";
          api.thinkEl.innerHTML = "<summary>Thinking…</summary><div></div>";
          d.appendChild(api.thinkEl);
          api.seal(); api.textEl = null; api.toolsEl = null;
        }
        api.thinkEl.querySelector("div").textContent += delta;
        scrollDown();
      },
      thinkDone() { if (api.thinkEl) { api.thinkEl.querySelector("summary").textContent = "Thought process"; api.thinkEl = null; } },
      pending() {
        if (d.querySelector(".ai-pending")) return;
        const p = document.createElement("div");
        p.className = "ai-pending"; p.setAttribute("aria-label", "Working");
        p.innerHTML = "<span></span><span></span><span></span>";
        d.appendChild(p); scrollDown();
      },
      unpend() { d.querySelector(".ai-pending")?.remove(); },
      tool(id, label, state, error) {
        api.thinkDone();
        let row = api.tools[id];
        if (!row) {
          if (!api.toolsEl) {
            api.textEl = null;
            // actions straight after actions (only thinking between): one group, one count
            const prev = lastBareTools();
            if (prev) { prev.querySelector(".ai-tools-sum")?.remove(); prev.classList.remove("sealed", "open"); api.toolsEl = prev; }
            else { api.toolsEl = document.createElement("div"); api.toolsEl.className = "ai-tools"; d.appendChild(api.toolsEl); }
          }
          row = document.createElement("div");
          row.innerHTML = `<span class="dot"></span><span class="lbl"></span>`;
          api.toolsEl.appendChild(row);
          api.tools[id] = row;
        }
        row.className = "ai-tool " + state;
        if (label) row.querySelector(".lbl").textContent = toolText(label);
        row.title = error || "";
        scrollDown();
      },
      error(msg) { api.seal(); const e = document.createElement("div"); e.className = "ai-error"; e.textContent = msg; d.appendChild(e); api.textEl = null; api.toolsEl = null; scrollDown(); },
      // something shown between the actions: the group closes, the next starts anew
      block(el) { api.thinkDone(); api.seal(); api.textEl = null; api.toolsEl = null; d.appendChild(el); scrollDown(); return el; },
      image(shot, caption) {
        const f = document.createElement("figure");
        f.className = "ai-shot";
        f.innerHTML = `<button type="button" class="ai-shot-img" title="Click to enlarge"><img alt=""/></button>${caption ? "<figcaption></figcaption>" : ""}`;
        const img = f.querySelector("img");
        img.alt = caption || "A picture from the assistant";
        img.addEventListener("load", scrollDown, { once: true });
        authImg(img, shotUrl(shot));
        if (caption) f.querySelector("figcaption").textContent = caption;
        f.querySelector("button").onclick = () => enlarge(img.src, caption);
        return api.block(f);
      },
      excerpt(x) {
        const el = document.createElement("div");
        el.className = "ai-excerpt";
        el.innerHTML = `<div class="ai-ex-head"><span class="t"></span><span class="s"></span></div><div class="ai-ex-body" role="log"></div>`;
        el.querySelector(".t").textContent = x.title || "Excerpt";
        el.querySelector(".s").textContent = x.source && x.source !== x.title ? x.source : "";
        const body = el.querySelector(".ai-ex-body");
        for (const l of x.lines || []) {
          const r = document.createElement("div");
          r.className = "ln" + (l.hit ? " hit" : "") + (l.gap ? " gap" : "");
          r.innerHTML = `<span class="no"></span><span class="tx"></span>`;
          r.querySelector(".no").textContent = l.n != null ? l.n : "";
          r.querySelector(".tx").textContent = l.text;
          body.appendChild(r);
        }
        return api.block(el);
      },
      // a hardware action waiting for the user: Approve / Decline
      approve(id, label, text, state, live) {
        let el = d.querySelector(`.ai-approve[data-id="${CSS.escape(id)}"]`);
        if (!el) {
          el = document.createElement("div");
          el.className = "ai-approve"; el.dataset.id = id;
          el.setAttribute("role", "group"); el.setAttribute("aria-label", "Approve this action");
          el.innerHTML = `<div class="ai-ap-head"><span class="ai-ap-mark" aria-hidden="true">!</span><span class="ai-ap-q">Approve this action?</span></div>
            <div class="ai-ap-text"></div><div class="ai-ap-foot"></div>`;
          el.querySelector(".ai-ap-text").textContent = text || toolText(label);
          api.block(el);
        }
        api.approvalState(id, state, live);
        return el;
      },
      approvalState(id, state, live, why) {
        const el = d.querySelector(`.ai-approve[data-id="${CSS.escape(id)}"]`);
        if (!el) return;
        el.dataset.state = state;
        el.querySelector(".ai-ap-mark").textContent = state === "approved" ? "✓" : state === "pending" && live ? "!" : "×";
        el.querySelector(".ai-ap-q").textContent = state === "pending" && live ? "Approve this action?" : "Dorna AI asked to do this";
        const foot = el.querySelector(".ai-ap-foot");
        if (state === "pending" && live) {
          foot.innerHTML = `<button type="button" class="btn btn-sm ai-ap-no">Decline</button><button type="button" class="btn btn-primary btn-sm ai-ap-ok">Approve</button>`;
          const answer = async (ok) => {
            foot.querySelectorAll("button").forEach((b) => { b.disabled = true; });
            try {
              const r = await fetch(API + "/approve", { method: "POST", headers: hdrs(true), body: JSON.stringify({ session: sid, id, ok }) });
              if (!r.ok) { foot.innerHTML = `<span class="ai-ap-st">No longer waiting — not run</span>`; el.dataset.state = "declined"; }
            } catch (e) { foot.querySelectorAll("button").forEach((b) => { b.disabled = false; }); }
          };
          foot.querySelector(".ai-ap-ok").onclick = () => answer(true);
          foot.querySelector(".ai-ap-no").onclick = () => answer(false);
          setTimeout(() => foot.querySelector(".ai-ap-ok")?.focus({ preventScroll: true }), 30);
        } else {
          const word = state === "approved" ? "Approved" : state === "pending" ? "Not answered — not run"
            : why === "timeout" ? "No answer — not run" : "Declined — not run";
          foot.innerHTML = `<span class="ai-ap-st"></span>`;
          foot.querySelector(".ai-ap-st").textContent = word;
          if (state === "pending") el.dataset.state = "declined";
        }
        scrollDown();
      },
    };
    return api;
  }
  function renderTranscript(items, running) {
    if (!items || !items.length) { emptyState(); return null; }
    log.innerHTML = "";
    let b = null;
    items.forEach((it, n) => {
      if (it.role === "user") { userBubble(it.text, it.files); return; }
      b = assistantBubble();
      const live = running && n === items.length - 1;
      for (const p of it.blocks || []) {
        if (p.kind === "text") b.text(p.text);
        else if (p.kind === "tool") b.tool(p.id, p.label, p.ok === false ? "bad" : (p.ok == null && live ? "running" : "ok"), p.error);
        else if (p.kind === "error") b.error(p.text);
        else if (p.kind === "image") b.image(p.shot, p.caption);
        else if (p.kind === "excerpt") b.excerpt(p);
        else if (p.kind === "approve") b.approve(p.id, p.label, p.text, p.state, live);
      }
      if (!live) b.seal();
    });
    scrollDown();
    return b;
  }

  // ── chat ─────────────────────────────────────────────────────────
  async function send(text, extra) {
    text = (text || "").trim();
    if (busy || outOfCredits) return;
    if (atts.some((a) => a.state === "up")) { note("Wait for the files to finish uploading."); input.value = text; return; }
    const ready = atts.filter((a) => a.state === "ok");
    if (!text && !ready.length) return;
    setBusy(true);
    atts = atts.filter((a) => a.state === "bad");   // a failed one stays in view, with why
    renderAtts();
    userBubble(text || "(attached files)", ready.map((a) => ({ ...a.rec, preview: a.preview })));
    const bubble = assistantBubble();
    bubble.pending();
    let following = false;
    try {
      const res = await fetch(API + "/chat", {
        method: "POST", headers: hdrs(true),
        body: JSON.stringify({ session: sid, text, context: Object.assign(pageContext(), extra || {}), attachments: ready.map((a) => a.rec.id) }),
      });
      if (res.status === 401) { bubble.error("This robot isn't logged in any more — log in again (top right)."); window.dornaAccount?.refresh(true); return; }
      if (res.status === 409) {
        bubble.el.previousElementSibling?.remove(); bubble.el.remove();
        input.value = text;
        ready.forEach((a) => { atts.push(a); });
        renderAtts();
        following = true;
        await loadSession();
        return;
      }
      if (!res.ok || !res.body) {
        let msg = `Request failed (${res.status})`;
        try { msg = (await res.json()).error || msg; } catch (_) {}
        bubble.error(msg);
        return;
      }
      await readEvents(res, bubble);
    } catch (e) {
      bubble.error("Connection lost: " + e.message);
    } finally {
      if (!following) {
        bubble.unpend(); bubble.thinkDone(); bubble.seal();
        setBusy(false);
        window.dispatchEvent(new CustomEvent("ai-turn"));
        window.dornaAccount?.refresh(true);           // the usage bar moves after a turn
      }
    }
  }

  async function readEvents(res, bubble) {
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
        if (!line) continue;
        let ev; try { ev = JSON.parse(line); } catch (_) { continue; }
        if (ev.t !== "session" && ev.t !== "tool_pending") bubble.unpend();
        handle(ev, bubble);
      }
    }
  }

  function handle(ev, bubble) {
    switch (ev.t) {
      case "session": if (ev.id && ev.id !== sid) { sid = ev.id; ls.set(SID_KEY, sid); } break;
      case "text": bubble.text(ev.d); break;
      case "think": bubble.think(ev.d); break;
      case "tool": bubble.tool(ev.id, ev.label, "running"); break;
      case "tool_done": bubble.tool(ev.id, null, ev.ok ? "ok" : "bad", ev.error); break;
      case "error": bubble.error(ev.message); break;
      case "image": bubble.image(ev.shot, ev.caption); break;
      case "excerpt": bubble.excerpt(ev); break;
      case "approve": bubble.approve(ev.id, ev.label, ev.text, "pending", true); break;
      case "approval": bubble.approvalState(ev.id, ev.ok ? "approved" : "declined", false, ev.why); break;
      case "ui": onUi(ev); break;
    }
  }

  // what the assistant changed, for the page to follow
  function onUi(ev) {
    window.dispatchEvent(new CustomEvent("ai-ui", { detail: ev }));
    if (ev.type === "reload" && ev.what === "scene" && typeof window.__sbReloadProject === "function") {
      try { window.__sbReloadProject(); } catch (_) {}
    }
  }

  async function follow(from, bubble) {
    setBusy(true);
    try {
      const res = await fetch(API + "/attach?id=" + encodeURIComponent(sid) + "&from=" + from, { headers: hdrs(false) });
      if (res.ok && res.body) await readEvents(res, bubble);
    } catch (e) {
      bubble.error("Connection lost: " + e.message);
    } finally {
      bubble.thinkDone(); bubble.seal();
      setBusy(false);
      window.dispatchEvent(new CustomEvent("ai-turn"));
    }
  }

  async function loadSession() {
    const r = await fetch(API + "/session" + (sid ? "?id=" + encodeURIComponent(sid) : ""), { headers: hdrs(false) });
    if (r.status === 401) { window.dornaAccount?.refresh(true); return; }
    if (r.status === 403) { loaded = true; log.innerHTML = ""; assistantBubble().error(((await r.json().catch(() => ({}))).error || "Not allowed") + " — set the orchestrator token in the admin page."); return; }
    const j = await r.json();
    sid = j.id; ls.set(SID_KEY, sid);
    loaded = true;
    const last = renderTranscript(j.transcript, j.busy);
    if (j.busy) follow(j.live_seq || 0, last || assistantBubble());
  }

  // ── open / close ─────────────────────────────────────────────────
  function setOpen(open) {
    root.hidden = !open || !enabled;
    fab.hidden = !enabled || open;
    fab.setAttribute("aria-expanded", open && enabled ? "true" : "false");
    document.documentElement.classList.toggle("ai-open", open && enabled);
    ls.set(OPEN_KEY, open ? "1" : "0");
    if (open && enabled) {
      showProject();
      if (!loaded) loadSession().catch((e) => { log.innerHTML = ""; assistantBubble().error("Couldn't reach Dorna AI: " + e.message); });
      setTimeout(() => input.focus(), 50);
    }
  }
  // out of credits: the gateway refuses every request, so say it up front
  // and turn the composer off rather than let a message fail
  let outOfCredits = false;
  function setOut(st) {
    const u = st && st.usage;
    outOfCredits = !!(st && st.logged_in && u && !u.unlimited && Number(u.percent) >= 100);
    root.classList.toggle("out", outOfCredits);
    $(".ai-out").hidden = !outOfCredits;
    input.disabled = outOfCredits;
  }
  function setEnabled(st) {
    setOut(st);
    const on = !!(st && st.logged_in);
    if (on === enabled) return;
    enabled = on;
    document.documentElement.classList.toggle("ai-ready", on);
    if (!on) { loaded = false; log.innerHTML = ""; }
    setOpen(on && ls.get(OPEN_KEY) === "1");
  }

  // ── wiring ───────────────────────────────────────────────────────
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (busy) return;
      const t = input.value; input.value = ""; send(t);
    }
  });
  sendBtn.onclick = () => { const t = input.value; input.value = ""; send(t); };
  stopBtn.onclick = () => fetch(API + "/cancel", { method: "POST", headers: hdrs(true), body: JSON.stringify({ session: sid }) });
  fab.onclick = () => setOpen(true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !root.hidden) setOpen(false); });
  $(".ai-close").onclick = () => setOpen(false);
  // ── recent chats: the account's last 10 on this robot ────────────
  const histBtn = $(".ai-hist-btn"), hist = $(".ai-hist");
  function ago(t) {
    const s = Math.max(0, Date.now() / 1000 - (t || 0));
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    if (s < 7 * 86400) return `${Math.floor(s / 86400)} d ago`;
    return new Date(t * 1000).toLocaleDateString();
  }
  function closeHist() { hist.hidden = true; histBtn.setAttribute("aria-expanded", "false"); }
  async function openHist() {
    hist.hidden = false; histBtn.setAttribute("aria-expanded", "true");
    hist.innerHTML = `<div class="ai-hist-head">Recent chats</div><div class="ai-hist-msg">Loading…</div>`;
    let rows = [];
    try {
      const r = await fetch(API + "/sessions", { headers: hdrs(false) });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
      rows = (await r.json()).sessions || [];
    } catch (e) {
      hist.innerHTML = `<div class="ai-hist-head">Recent chats</div><div class="ai-hist-msg bad">Couldn't load them: ${esc(e.message)}</div>`;
      return;
    }
    hist.innerHTML = `<div class="ai-hist-head">Recent chats</div>` + (rows.length ? "" : `<div class="ai-hist-msg">No chats yet.</div>`);
    rows.forEach((x) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "ai-hist-item" + (x.id === sid ? " on" : ""); b.setAttribute("role", "menuitem");
      b.innerHTML = `<span class="t"></span><span class="m"></span>`;
      b.querySelector(".t").textContent = x.title;
      b.querySelector(".m").textContent = (x.project ? x.project + " · " : "") + ago(x.saved);
      b.onclick = async () => {
        closeHist();
        if (busy || x.id === sid) return;
        sid = x.id; ls.set(SID_KEY, sid);
        clearAtts(true);
        await loadSession().catch((e) => { log.innerHTML = ""; assistantBubble().error("Couldn't open that chat: " + e.message); });
      };
      hist.appendChild(b);
    });
  }
  histBtn.onclick = () => (hist.hidden ? openHist() : closeHist());
  document.addEventListener("pointerdown", (e) => { if (!hist.hidden && !hist.contains(e.target) && !histBtn.contains(e.target)) closeHist(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !hist.hidden) { e.stopImmediatePropagation(); closeHist(); } }, true);

  $(".ai-new").onclick = async () => {
    closeHist();
    if (busy) return;
    const r = await fetch(API + "/session", { method: "POST", headers: hdrs(true),
      body: JSON.stringify({ project: pageContext().project || null }) });
    if (!r.ok) return;
    const j = await r.json();
    sid = j.id; ls.set(SID_KEY, sid);
    clearAtts(true);
    emptyState(); input.focus();
  };
  $(".ai-resize").addEventListener("pointerdown", (e) => {
    const startX = e.clientX, startW = root.getBoundingClientRect().width;
    const move = (ev) => { const nw = Math.max(320, Math.min(window.innerWidth - 40, startW - (ev.clientX - startX))); root.style.setProperty("--aiw", nw + "px"); ls.set(W_KEY, String(Math.round(nw))); };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  });

  // ── attachments ──────────────────────────────────────────────────
  // Each file goes up the moment it is picked, dropped or pasted (so a big
  // model is on the robot by the time the message is written); the chips
  // above the box show its progress, and the ready ones ride the next message.
  function renderAtts() {
    attsEl.hidden = !atts.length;
    attsEl.innerHTML = "";
    for (const a of atts) {
      const c = fileChip(a.rec || { name: a.file.name, size: a.file.size, kind: kindOf(a.file.name) }, { cls: "pending " + a.state, preview: a.preview });
      if (a.state === "up") c.querySelector(".sub").textContent = `Uploading… ${a.pct || 0}%`;
      if (a.state === "bad") c.querySelector(".sub").textContent = a.error || "Upload failed";
      if (a.state === "up") { const bar = document.createElement("span"); bar.className = "bar"; bar.style.setProperty("--pct", (a.pct || 0) + "%"); c.appendChild(bar); }
      const x = document.createElement("button");
      x.type = "button"; x.className = "rm"; x.setAttribute("aria-label", `Remove ${a.file.name}`); x.title = "Remove";
      x.textContent = "✕";
      x.onclick = () => { a.xhr?.abort(); atts = atts.filter((b) => b !== a); renderAtts(); };
      c.appendChild(x);
      attsEl.appendChild(c);
    }
    sendBtn.disabled = busy || atts.some((a) => a.state === "up");
  }
  function clearAtts(abort) {
    if (abort) atts.forEach((a) => a.xhr?.abort());
    atts = []; renderAtts();
  }
  function addFiles(list) {
    if (!enabled) return;
    for (const file of Array.from(list || [])) {
      const a = { key: ++attKey, file, state: "up", pct: 0 };
      if (file.size > MAX_UPLOAD) { a.state = "bad"; a.error = `Larger than ${MAX_UPLOAD / 1048576} MB`; }
      else if (!file.size) { a.state = "bad"; a.error = "The file is empty"; }
      if (kindOf(file.name) === "image" || /^image\//.test(file.type)) { try { a.preview = URL.createObjectURL(file); } catch (_) {} }
      atts.push(a);
      if (a.state === "up") upload(a);
    }
    renderAtts();
  }
  function upload(a) {
    const name = a.file.name || (a.file.type === "image/png" ? "pasted.png" : "file");
    const q = new URLSearchParams({ name });
    if (sid) q.set("session", sid);
    const proj = pageContext().project;
    if (proj) q.set("project", proj);
    const x = new XMLHttpRequest();
    a.xhr = x;
    x.open("POST", `${API}/upload?${q}`);
    const h = hdrs(false);
    for (const k in h) x.setRequestHeader(k, h[k]);
    x.setRequestHeader("Content-Type", "application/octet-stream");
    x.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      a.pct = Math.round(100 * e.loaded / e.total);
      const chip = attsEl.children[atts.indexOf(a)];
      chip?.querySelector(".bar")?.style.setProperty("--pct", a.pct + "%");
      const sub = chip?.querySelector(".sub");
      if (sub) sub.textContent = `Uploading… ${a.pct}%`;
    };
    x.onload = () => {
      let j = {}; try { j = JSON.parse(x.responseText || "{}"); } catch (_) {}
      if (x.status === 200 && j.ok) {
        a.state = "ok"; a.rec = j.upload;
        if (j.session && j.session !== sid) { sid = j.session; ls.set(SID_KEY, sid); }
      } else {
        a.state = "bad"; a.error = j.error || `Upload failed (${x.status})`;
        if (x.status === 401) window.dornaAccount?.refresh(true);
      }
      a.xhr = null; renderAtts();
    };
    x.onerror = () => { a.state = "bad"; a.error = "Upload failed — connection lost"; a.xhr = null; renderAtts(); };
    x.send(a.file);
  }
  $(".ai-attach").onclick = () => fileIn.click();
  fileIn.onchange = () => { addFiles(fileIn.files); fileIn.value = ""; input.focus(); };
  input.addEventListener("paste", (e) => {
    const files = Array.from(e.clipboardData?.files || []);
    if (!files.length) return;
    e.preventDefault();
    addFiles(files.map((f, i) => f.name ? f : new File([f], `pasted_${Date.now()}_${i}.${(f.type.split("/")[1] || "bin")}`, { type: f.type })));
  });
  let dragDepth = 0;
  const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes("Files");
  root.addEventListener("dragenter", (e) => { if (!hasFiles(e)) return; e.preventDefault(); dragDepth++; dropEl.hidden = false; });
  root.addEventListener("dragover", (e) => { if (!hasFiles(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = "copy"; });
  root.addEventListener("dragleave", (e) => { if (!hasFiles(e)) return; if (--dragDepth <= 0) { dragDepth = 0; dropEl.hidden = true; } });
  root.addEventListener("drop", (e) => { if (!hasFiles(e)) return; e.preventDefault(); dragDepth = 0; dropEl.hidden = true; addFiles(e.dataTransfer.files); });

  function mount() {
    document.body.appendChild(fab);
    document.body.appendChild(root);
    const hook = () => {
      if (window.dornaAccount) { window.dornaAccount.onChange(setEnabled); return; }
      setTimeout(hook, 100);
    };
    hook();
    window.addEventListener("popstate", showProject);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();

  // "Ask Dorna AI" on a message card (messages.js): the chat opens with the
  // message and the page in context, and the assistant looks into it
  const LEVEL_WORD = { bad: "Error", warn: "Warning", info: "Notice", ok: "Done" };
  async function ask(m) {
    if (!enabled || !m) return;
    setOpen(true);
    const word = (LEVEL_WORD[m.level] || "message").toLowerCase();
    const what = [m.label && m.label.toLowerCase() !== word ? `(${m.label})` : "",
                  [m.title, m.text].filter(Boolean).join(" — ")].filter(Boolean).join(" ");
    const text = `Help me with this ${word}${what ? ": " + what : ""}`;
    const message = { level: m.level, label: m.label || "", title: m.title || "", text: m.text || "" };
    if (busy || outOfCredits) { input.value = text; input.focus(); return; }
    if (!loaded) { try { await loadSession(); } catch (_) {} }
    if (busy) { input.value = text; input.focus(); return; }
    send(text, { message });
  }
  window.addEventListener("ask-ai", (e) => ask(e.detail));

  window.aiChat = { open: () => setOpen(true), close: () => setOpen(false), send, ask, attach: addFiles, get sid() { return sid; }, get busy() { return busy; } };
})();
