// The Workspace assistant — a floating button that opens a side panel, on
// every page once this robot is logged in (hidden in offline mode).
// Talks to /ai/api (gui/ai/handlers.py): POST /chat streams NDJSON events
// (text / think / tool / tool_done / ui / error / done).
//
// A page tells the assistant what the user is looking at by setting
//   window.__aiContext = {page, project, tab, ...}   (or a function returning it)
// and can follow what the assistant changed with
//   window.addEventListener("ai-ui", (e) => e.detail)   // {type: reload|status, what?: scene|hmi|parameters|files, project}
//   window.addEventListener("ai-turn", ...)             // a turn finished
(() => {
  if (window.__aiChatLoaded) return;
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

  const CHAT_ICON = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a8 8 0 01-11.6 7.1L4 20l1-4.4A8 8 0 1121 12z"/><path d="M8.5 11h.01M12 11h.01M15.5 11h.01"/></svg>`;
  const SUGGESTIONS = [
    "Why did the last run stop?",
    "Which devices are in simulation in this project?",
    "Add a field for the batch size to the Parameters page.",
  ];

  // ── DOM ──────────────────────────────────────────────────────────
  const fab = document.createElement("button");
  fab.type = "button"; fab.className = "ai-fab"; fab.hidden = true;
  fab.title = "Assistant"; fab.setAttribute("aria-label", "Open the assistant");
  fab.innerHTML = CHAT_ICON;

  const root = document.createElement("aside");
  root.className = "ai-panel"; root.hidden = true;
  root.setAttribute("aria-label", "Assistant");
  root.innerHTML = `
    <div class="ai-resize" title="Drag to resize"></div>
    <div class="ai-head">
      <span class="ai-title">Assistant</span>
      <span class="ai-proj" hidden></span>
      <span class="spacer"></span>
      <button class="ai-iconbtn ai-new" type="button" title="Start a fresh conversation">New chat</button>
      <button class="ai-iconbtn ai-close" type="button" title="Close" aria-label="Close the assistant">✕</button>
    </div>
    <div class="ai-log" aria-live="polite"></div>
    <div class="ai-compose">
      <div class="ai-compose-row">
        <textarea rows="1" placeholder="Ask about this robot, a run, or a change to make…" aria-label="Message the assistant"></textarea>
        <button class="btn btn-primary ai-send" type="button" title="Send (Enter)">Send</button>
      </div>
      <div class="ai-bar"><span class="ai-hint">Enter to send · Shift+Enter for a new line</span><span class="spacer"></span>
        <button class="btn btn-ghost btn-sm ai-stop" type="button" hidden>Stop</button></div>
    </div>`;
  const $ = (s) => root.querySelector(s);
  const log = $(".ai-log"), input = $("textarea"), sendBtn = $(".ai-send"), stopBtn = $(".ai-stop"), projEl = $(".ai-proj");
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

  function setBusy(b) { busy = b; sendBtn.disabled = b; stopBtn.hidden = !b; }

  // ── rendering (the AI builder's chat look) ───────────────────────
  function emptyState() {
    log.innerHTML = "";
    const d = document.createElement("div");
    d.className = "ai-empty";
    d.innerHTML = `<h3>How can I help?</h3>
      <div>I know this Workspace from running a project to developing one. I can check a run, recover a device,
      change the scene, the workflow or the HMI, and answer questions.</div>
      <ul class="ai-examples">${SUGGESTIONS.map((s) => `<li><button type="button">${esc(s)}</button></li>`).join("")}</ul>`;
    d.querySelectorAll(".ai-examples button").forEach((b) => { b.onclick = () => send(b.textContent); });
    log.appendChild(d);
  }
  function userBubble(text) {
    log.querySelector(".ai-empty")?.remove();
    const d = document.createElement("div");
    d.className = "ai-msg user"; d.textContent = text;
    log.appendChild(d); scrollDown();
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
          if (!api.toolsEl) { api.textEl = null; api.toolsEl = document.createElement("div"); api.toolsEl.className = "ai-tools"; d.appendChild(api.toolsEl); }
          row = document.createElement("div");
          row.innerHTML = `<span class="dot"></span><span class="lbl"></span>`;
          api.toolsEl.appendChild(row);
          api.tools[id] = row;
        }
        row.className = "ai-tool " + state;
        if (label) row.querySelector(".lbl").textContent = label;
        row.title = error || "";
        scrollDown();
      },
      error(msg) { api.seal(); const e = document.createElement("div"); e.className = "ai-error"; e.textContent = msg; d.appendChild(e); api.textEl = null; api.toolsEl = null; scrollDown(); },
    };
    return api;
  }
  function renderTranscript(items, running) {
    if (!items || !items.length) { emptyState(); return null; }
    log.innerHTML = "";
    let b = null;
    items.forEach((it, n) => {
      if (it.role === "user") { userBubble(it.text); return; }
      b = assistantBubble();
      const live = running && n === items.length - 1;
      for (const p of it.blocks || []) {
        if (p.kind === "text") b.text(p.text);
        else if (p.kind === "tool") b.tool(p.id, p.label, p.ok === false ? "bad" : (p.ok == null && live ? "running" : "ok"), p.error);
        else if (p.kind === "error") b.error(p.text);
      }
      if (!live) b.seal();
    });
    scrollDown();
    return b;
  }

  // ── chat ─────────────────────────────────────────────────────────
  async function send(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    setBusy(true);
    userBubble(text);
    const bubble = assistantBubble();
    bubble.pending();
    let following = false;
    try {
      const res = await fetch(API + "/chat", {
        method: "POST", headers: hdrs(true),
        body: JSON.stringify({ session: sid, text, context: pageContext() }),
      });
      if (res.status === 401) { bubble.error("This robot isn't logged in any more — log in again (top right)."); window.dornaAccount?.refresh(true); return; }
      if (res.status === 409) {
        bubble.el.previousElementSibling?.remove(); bubble.el.remove();
        input.value = text;
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
    document.documentElement.classList.toggle("ai-open", open && enabled);
    ls.set(OPEN_KEY, open ? "1" : "0");
    if (open && enabled) {
      showProject();
      if (!loaded) loadSession().catch((e) => { log.innerHTML = ""; assistantBubble().error("Couldn't reach the assistant: " + e.message); });
      setTimeout(() => input.focus(), 50);
    }
  }
  function setEnabled(st) {
    const on = !!(st && st.logged_in);
    if (on === enabled) return;
    enabled = on;
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
  $(".ai-close").onclick = () => setOpen(false);
  $(".ai-new").onclick = async () => {
    if (busy) return;
    const r = await fetch(API + "/session", { method: "POST", headers: hdrs(true),
      body: JSON.stringify({ project: pageContext().project || null }) });
    if (!r.ok) return;
    const j = await r.json();
    sid = j.id; ls.set(SID_KEY, sid);
    emptyState(); input.focus();
  };
  $(".ai-resize").addEventListener("pointerdown", (e) => {
    const startX = e.clientX, startW = root.getBoundingClientRect().width;
    const move = (ev) => { const nw = Math.max(320, Math.min(window.innerWidth - 40, startW - (ev.clientX - startX))); root.style.setProperty("--aiw", nw + "px"); ls.set(W_KEY, String(Math.round(nw))); };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  });

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

  window.aiChat = { open: () => setOpen(true), close: () => setOpen(false), send, get sid() { return sid; }, get busy() { return busy; } };
})();
