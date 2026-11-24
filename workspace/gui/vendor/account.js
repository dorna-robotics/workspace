// The top-right account area, the same on every page.
//   offline   → a "Log in" button → Dorna's sign-in in a popup window (sign in,
//               tap Connect); the modal behind it polls until approved, and offers
//               the QR code + short code for a phone ("Use your phone instead",
//               or straight away when the browser blocks the popup)
//   logged in → an account icon → a menu: email, AI usage (a percent, never
//               money), Log out
// It mounts into every .topbar-actions on the page (desktop and pendant
// headers), or a fixed corner when a page has none. Other scripts read
// window.dornaAccount and listen for the "account-status" event.
(() => {
  if (window.dornaAccount) return;
  const API = "/account/api";

  function css(href) {
    if (document.querySelector(`link[href="${href}"]`)) return;
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = href;
    document.head.appendChild(l);
  }
  css("/vendor/account.css");

  let status = null;
  const listeners = [];
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const hdrs = () => {
    const h = { "Content-Type": "application/json" };
    try { const t = (localStorage.getItem("orch_token") || "").trim(); if (t) h["X-Orch-Token"] = t; } catch (_) {}
    return h;
  };
  const post = (path, body) => fetch(API + path, { method: "POST", headers: hdrs(),
    body: JSON.stringify(body || {}) }).then((r) => r.json());
  const toast = (msg, kind) => { try { window.showToast ? window.showToast(msg, kind || "") : console.log(msg); } catch (_) {} };

  const USER_ICON = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/></svg>`;

  // ── status ───────────────────────────────────────────────────────
  async function refresh(force) {
    try {
      const r = await fetch(API + "/status" + (force ? "?refresh=1" : ""), { cache: "no-store" });
      status = await r.json();
    } catch (_) {
      status = status || { logged_in: false };
    }
    render();
    for (const cb of listeners) { try { cb(status); } catch (_) {} }
    window.dispatchEvent(new CustomEvent("account-status", { detail: status }));
    return status;
  }

  // ── the top-right control ────────────────────────────────────────
  let menuOpen = null;
  function hosts() {
    let list = Array.from(document.querySelectorAll(".topbar-actions"));
    if (!list.length) {
      let fixed = document.getElementById("acctFixed");
      if (!fixed) {
        fixed = document.createElement("div");
        fixed.id = "acctFixed"; fixed.className = "acct-fixed";
        document.body.appendChild(fixed);
      }
      list = [fixed];
    }
    return list;
  }

  function render() {
    if (!document.body) return;
    for (const host of hosts()) {
      let el = host.querySelector(":scope > .acct");
      if (!el) {
        el = document.createElement("div");
        el.className = "acct";
        host.appendChild(el);
      }
      if (!status || !status.logged_in) {
        el.innerHTML = `<button type="button" class="btn btn-primary btn-sm acct-login">Log in</button>`;
        el.querySelector("button").onclick = openLogin;
      } else {
        const name = status.email || status.account || "Account";
        el.innerHTML = `<button type="button" class="btn btn-ghost btn-sm btn-icon acct-icon" title="${esc(name)}" aria-label="Account" aria-haspopup="true">${USER_ICON}${status.reachable === false ? '<span class="acct-offdot" title="Offline"></span>' : ""}</button>`;
        el.querySelector("button").onclick = (e) => { e.stopPropagation(); toggleMenu(el); };
      }
    }
  }

  function usageHtml(u) {
    if (!u) return `<div class="acct-usage-label">AI usage: not known while offline</div>`;
    if (u.unlimited) return `<div class="acct-usage-label">AI usage: unlimited</div>`;
    const p = Math.max(0, Math.min(100, Math.round(u.percent || 0)));
    const lvl = p >= 90 ? "bad" : p >= 70 ? "warn" : "ok";
    let extra = "";
    if (status.is_admin && u.credits_left != null) extra = `<div class="acct-usage-admin">${esc(u.credits_left)} of ${esc(u.credits_granted)} credits left</div>`;
    return `<div class="acct-usage-label">AI usage: ${p}% used</div>
      <div class="acct-bar ${lvl}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p}"><span style="width:${p}%"></span></div>${extra}`;
  }

  function closeMenu() { if (menuOpen) { menuOpen.remove(); menuOpen = null; } }
  function toggleMenu(anchor) {
    if (menuOpen) { closeMenu(); return; }
    const m = document.createElement("div");
    m.className = "acct-menu";
    m.innerHTML = `
      <div class="acct-who"><div class="acct-email">${esc(status.email || "")}</div>
        <div class="acct-acc">${esc(status.account || "")}${status.is_admin ? " · Dorna admin" : ""}</div></div>
      ${status.reachable === false ? `<div class="acct-offline">The Dorna server can't be reached — the assistant is unavailable until this robot is back online.</div>` : ""}
      <div class="acct-usage">${usageHtml(status.usage)}</div>
      <button type="button" class="acct-item acct-logout">Log out</button>`;
    document.body.appendChild(m);
    const r = anchor.getBoundingClientRect();
    m.style.top = Math.round(r.bottom + 6) + "px";
    m.style.right = Math.max(8, Math.round(window.innerWidth - r.right)) + "px";
    m.addEventListener("click", (e) => e.stopPropagation());
    m.querySelector(".acct-logout").onclick = async () => {
      closeMenu();
      await post("/logout").catch(() => null);
      await refresh();
      toast("Logged out — this robot runs in offline mode", "");
    };
    menuOpen = m;
    refresh(true).then(() => {                       // the latest usage
      if (menuOpen === m) m.querySelector(".acct-usage").innerHTML = usageHtml(status.usage);
    });
  }
  document.addEventListener("click", closeMenu);
  window.addEventListener("resize", closeMenu);

  // ── the login modal ──────────────────────────────────────────────
  let pollTimer = null;
  function loadQr() {
    if (window.QRCode) return Promise.resolve();
    return new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = "/vendor/qrcode.js"; s.onload = res; s.onerror = rej;
      document.head.appendChild(s);
    });
  }
  function qrSvg(text) {
    const q = new window.QRCode(-1, window.QRCode.ErrorCorrectLevel.M);
    q.addData(text); q.make();
    const n = q.getModuleCount(), pad = 4, size = n + pad * 2;
    let d = "";
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (q.isDark(r, c)) d += `M${c + pad} ${r + pad}h1v1h-1z`;
    return `<svg viewBox="0 0 ${size} ${size}" shape-rendering="crispEdges" role="img" aria-label="QR code for the login link"><rect width="${size}" height="${size}" class="qr-bg"/><path d="${d}" class="qr-fg"/></svg>`;
  }

  // The sign-in window: Dorna's own sign-in, then one Connect tap, in a popup over this page.
  // It must open inside the click (a popup opened after an await is blocked), so it opens
  // blank and is pointed at the sign-in once the robot has its code. Blocked (a kiosk
  // browser) → the phone way: the QR code and the short code.
  let popup = null, popupWatch = null;
  const POPUP_NAME = "dornaSignIn";
  function popupFeatures() {
    const w = 480, h = 720;
    const x = Math.max(0, Math.round((window.screenX || 0) + ((window.outerWidth || w) - w) / 2));
    const y = Math.max(0, Math.round((window.screenY || 0) + ((window.outerHeight || h) - h) / 2));
    return `popup=yes,width=${w},height=${h},left=${x},top=${y}`;
  }
  function openPopup(url) {
    let w = null;
    try { w = window.open(url || "about:blank", POPUP_NAME, popupFeatures()); } catch (_) { w = null; }
    if (!w) return null;
    if (!url) {
      try {
        w.document.title = "Dorna sign-in";
        w.document.body.style.cssText = "margin:0;display:grid;place-items:center;min-height:100vh;font:15px system-ui,sans-serif;color:GrayText;background:Canvas";
        w.document.body.textContent = "Opening Dorna sign-in…";
      } catch (_) {}
    }
    try { w.focus(); } catch (_) {}
    return w;
  }
  function closePopup() {
    clearInterval(popupWatch); popupWatch = null;
    try { if (popup && !popup.closed) popup.close(); } catch (_) {}
    popup = null;
  }

  function closeLogin() {
    clearTimeout(pollTimer); pollTimer = null;
    closePopup();
    document.getElementById("acctModal")?.remove();
  }

  function phoneHtml(j) {
    return `<div class="acct-grid">
        <div class="acct-qr" aria-hidden="false"></div>
        <div class="acct-steps">
          <div class="acct-code-label">Your code</div>
          <div class="acct-code">${esc(j.user_code)}</div>
          <a class="acct-link" href="${esc(j.verify_url)}" target="_blank" rel="noopener">${esc(j.verify_url)}</a>
        </div>
      </div>`;
  }

  async function openLogin() {
    closeLogin();
    popup = openPopup(null);                       // now, while the click still counts
    const ov = document.createElement("div");
    ov.id = "acctModal"; ov.className = "acct-overlay";
    ov.innerHTML = `<div class="acct-modal" role="dialog" aria-modal="true" aria-labelledby="acctTitle">
      <div class="acct-modal-head"><h2 id="acctTitle">Log in to your Dorna account</h2>
        <button type="button" class="btn btn-ghost btn-sm btn-icon acct-x" aria-label="Close">✕</button></div>
      <div class="acct-modal-body"><div class="acct-state"><span class="acct-spin"></span>Opening Dorna sign-in…</div></div></div>`;
    document.body.appendChild(ov);
    ov.addEventListener("click", (e) => { if (e.target === ov) closeLogin(); });
    ov.querySelector(".acct-x").onclick = closeLogin;
    const body = ov.querySelector(".acct-modal-body");
    let j;
    try {
      const r = await fetch(API + "/login/start", { method: "POST", headers: hdrs(), body: "{}" });
      j = await r.json();
      if (!r.ok) throw new Error(j.error || `login failed (${r.status})`);
    } catch (e) {
      closePopup();
      body.innerHTML = `<div class="acct-err">${esc(e.message)}</div>
        <button type="button" class="btn btn-primary acct-retry">Try again</button>`;
      body.querySelector(".acct-retry").onclick = openLogin;
      return;
    }
    if (!document.body.contains(ov)) { closePopup(); return; }
    const signInUrl = j.verify_url + (j.verify_url.includes("?") ? "&" : "?") + "from=robot";
    if (popup && !popup.closed) {
      try { popup.location.href = signInUrl; } catch (_) { closePopup(); }
    }
    const popped = !!(popup && !popup.closed);
    body.innerHTML = popped ? `
      <div class="acct-popup">
        <div class="acct-state acct-state-big"><span class="acct-spin"></span><span class="acct-popup-msg">Finish signing in in the window that opened.</span></div>
        <button type="button" class="acct-textbtn acct-reopen">Reopen sign-in window</button>
      </div>
      <details class="acct-phone">
        <summary>Use your phone instead</summary>
        <p class="acct-lead">On your phone or computer, open the link or scan the code, sign in, and approve this robot.</p>
        ${phoneHtml(j)}
      </details>
      <p class="acct-foot">Without logging in, this robot still runs its projects (offline mode). Logging in adds the assistant and Developer mode.</p>`
    : `
      <p class="acct-lead">On your phone or computer, open the link or scan the code, sign in, and approve this robot.</p>
      ${phoneHtml(j)}
      <div class="acct-state"><span class="acct-spin"></span>Waiting for approval…</div>
      <p class="acct-foot">Without logging in, this robot still runs its projects (offline mode). Logging in adds the assistant and Developer mode.</p>`;
    let qrDone = false;
    const drawQr = () => {
      if (qrDone) return; qrDone = true;
      loadQr().then(() => { const q = body.querySelector(".acct-qr"); if (q) q.innerHTML = qrSvg(j.verify_url); })
        .catch(() => { const q = body.querySelector(".acct-qr"); if (q) q.remove(); });
    };
    const phone = body.querySelector(".acct-phone");
    if (phone) phone.addEventListener("toggle", () => { if (phone.open) drawQr(); }); else drawQr();
    const msgEl = body.querySelector(".acct-popup-msg");
    const reopen = body.querySelector(".acct-reopen");
    if (reopen) reopen.onclick = () => {
      const w = openPopup(signInUrl);
      if (w) { popup = w; if (msgEl) msgEl.textContent = "Finish signing in in the window that opened."; }
      else if (phone) { phone.open = true; }
    };
    if (popped) {
      popupWatch = setInterval(() => {             // closed by hand: say so; the phone way still works
        if (popup && popup.closed && msgEl) msgEl.textContent = "The sign-in window was closed. Reopen it, or use your phone.";
      }, 700);
    }
    const interval = Math.max(1, Number(j.interval) || 3) * 1000;
    const stateEl = body.querySelector(".acct-state");
    const poll = async () => {
      if (!document.body.contains(ov)) return;
      let p;
      try { p = await post("/login/poll"); } catch (_) { p = { status: "pending" }; }
      if (p.status === "ok") {
        closeLogin();
        await refresh(true);
        toast(`Logged in${status && status.email ? " as " + status.email : ""}`, "ok");
        return;
      }
      if (p.status === "expired" || p.status === "denied" || p.status === "none") {
        closePopup();
        if (reopen) reopen.remove();
        if (phone) phone.remove();                 // that code is spent
        stateEl.innerHTML = `<span class="acct-err">${p.status === "denied" ? "The login was cancelled." : "The login expired."}</span>
          <button type="button" class="btn btn-primary btn-sm acct-retry">Try again</button>`;
        stateEl.querySelector(".acct-retry").onclick = openLogin;
        return;
      }
      pollTimer = setTimeout(poll, interval);
    };
    pollTimer = setTimeout(poll, interval);
  }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeLogin(); closeMenu(); } });

  window.dornaAccount = {
    get status() { return status; },
    get loggedIn() { return !!(status && status.logged_in); },
    refresh, login: openLogin,
    onChange(cb) { listeners.push(cb); if (status) cb(status); },
  };

  const start = () => { refresh(); setInterval(() => refresh(), 60000); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
