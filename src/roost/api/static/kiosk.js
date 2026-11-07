// Shared kiosk (FR-40, NFR-06): sign out after 2 idle minutes and never show a previous
// employee's page. The server enforces the same limit; this only clears the screen.
(function () {
  "use strict";
  var idleMs = (parseInt(document.body.dataset.idleSeconds, 10) || 120) * 1000;
  var timer;

  function expire() {
    var signOut = document.getElementById("idle-sign-out");
    if (signOut) {
      signOut.submit();
      return;
    }
    document.querySelectorAll("input:not([type=hidden])").forEach(function (input) {
      input.value = "";
    });
  }

  function restart() {
    clearTimeout(timer);
    timer = setTimeout(expire, idleMs);
  }

  function scrollToLatest() {
    var conversation = document.getElementById("conversation");
    if (conversation) {
      conversation.scrollTop = conversation.scrollHeight;
    }
  }

  ["keydown", "pointerdown", "touchstart", "input"].forEach(function (name) {
    document.addEventListener(name, restart, { passive: true });
  });
  restart();
  scrollToLatest();

  // A page restored by the back button could be another employee's: load it fresh.
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) {
      window.location.reload();
    }
  });

  document.addEventListener("htmx:afterSwap", scrollToLatest);
  document.addEventListener("htmx:afterRequest", function (event) {
    var form = event.target.closest && event.target.closest("form[data-reset-after-send]");
    if (form && event.detail.successful) {
      form.reset();
      var input = form.querySelector("input[name=text]");
      if (input) {
        input.focus();
      }
    }
  });
})();
