/* main.js - MODULE A: small progressive-enhancement behaviours (no framework needed) */
document.addEventListener("DOMContentLoaded", function () {

  /* ---- Sidebar toggle on small screens ---- */
  var toggle = document.getElementById("menu-toggle");
  var sidebar = document.getElementById("sidebar");
  if (toggle && sidebar) {
    toggle.addEventListener("click", function () {
      var isOpen = sidebar.classList.toggle("open");
      toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    });
    document.addEventListener("click", function (event) {
      if (sidebar.classList.contains("open") && !sidebar.contains(event.target) && event.target !== toggle) {
        sidebar.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  }

  /* ---- Dismiss flash messages ---- */
  document.querySelectorAll(".flash-close").forEach(function (button) {
    button.addEventListener("click", function () {
      var flash = button.closest(".flash");
      if (flash) { flash.style.display = "none"; }
    });
  });

  /* ---- Character counters for long text areas ---- */
  document.querySelectorAll("textarea[data-counter]").forEach(function (textarea) {
    var max = textarea.getAttribute("maxlength");
    if (!max) { return; }
    var counter = document.createElement("p");
    counter.className = "hint counter";
    textarea.insertAdjacentElement("afterend", counter);
    function update() {
      counter.textContent = textarea.value.length + " / " + max + " characters";
    }
    textarea.addEventListener("input", update);
    update();
  });

  /* ---- Stop a form (e.g. "Register case") being submitted twice ---- */
  document.querySelectorAll("form[data-confirm-once]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var button = form.querySelector('button[type="submit"]');
      if (button) {
        window.setTimeout(function () {
          button.disabled = true;
          button.textContent = "Please wait...";
        }, 0);
      }
    });
  });

  /* ---- Auto-hide success/info flash messages after a while ---- */
  document.querySelectorAll(".flash-success, .flash-info").forEach(function (flash) {
    window.setTimeout(function () {
      flash.style.transition = "opacity 0.4s ease";
      flash.style.opacity = "0";
      window.setTimeout(function () { flash.style.display = "none"; }, 400);
    }, 8000);
  });
});
