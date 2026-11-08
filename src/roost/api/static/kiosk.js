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

  // Streaming (NFR-01): show the answer as it is written, then swap in the finished
  // conversation from the server, which is the checked text that was recorded.
  function streamInto(conversation) {
    var bubble = conversation && conversation.querySelector("[data-stream-url]");
    if (!bubble || !window.EventSource) {
      return;
    }
    var out = bubble.querySelector("p");
    var text = "";
    var source = new EventSource(bubble.dataset.streamUrl);
    source.addEventListener("token", function (event) {
      text += JSON.parse(event.data);
      // Rough markdown clean-up while streaming; the final text is cleaned properly.
      out.textContent = text.replace(/\*\*|__/g, "").replace(/^#+\s*/gm, "");
      restart();
      scrollToLatest();
    });
    source.addEventListener("reset", function () {
      text = "";
      out.textContent = "";
    });
    source.addEventListener("done", function (event) {
      source.close();
      var html = JSON.parse(event.data);
      if (!html) {
        window.location.reload();
        return;
      }
      var template = document.createElement("template");
      template.innerHTML = html.trim();
      var fresh = template.content.firstElementChild;
      conversation.replaceWith(fresh);
      if (window.htmx) {
        window.htmx.process(fresh);
      }
      scrollToLatest();
      var input = document.querySelector("form[data-reset-after-send] input[name=text]");
      if (input) {
        input.focus();
      }
    });
    source.onerror = function () {
      // Lost the stream: the turn still finishes and is recorded; reload to show it.
      source.close();
      window.setTimeout(function () {
        window.location.reload();
      }, 1500);
    };
  }

  document.addEventListener("htmx:afterSwap", function () {
    scrollToLatest();
    streamInto(document.getElementById("conversation"));
  });
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
