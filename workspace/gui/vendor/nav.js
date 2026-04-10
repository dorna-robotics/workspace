// App Navigation — toggle sidebar on click
// Include via: <script src="/vendor/nav.js"></script>

(function() {
  const nav = document.querySelector(".app-nav");
  const overlay = document.querySelector(".app-nav-overlay");
  const toggle = document.querySelector(".app-nav-toggle");
  const burger = document.getElementById("btnBurger");

  function open()  { nav?.classList.add("open"); overlay?.classList.add("show"); }
  function close() { nav?.classList.remove("open"); overlay?.classList.remove("show"); }
  function flip()  { nav?.classList.contains("open") ? close() : open(); }

  toggle?.addEventListener("click", flip);
  burger?.addEventListener("click", flip);
  overlay?.addEventListener("click", close);

  // Close on nav link click (after navigation)
  nav?.querySelectorAll(".app-nav-link").forEach(link => {
    link.addEventListener("click", close);
  });

  // Close on Escape
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
  });
})();
