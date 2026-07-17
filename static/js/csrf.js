/**
 * Attach CSRF token to mutating fetch() calls (Flask-WTF expects X-CSRFToken).
 */
(function () {
  "use strict";

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute("content")) {
      return meta.getAttribute("content");
    }
    var input = document.querySelector('input[name="csrf_token"]');
    return input ? input.value : "";
  }

  if (typeof window.fetch !== "function") {
    return;
  }

  var originalFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    init = init || {};
    var method = (init.method || "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD" && method !== "OPTIONS" && method !== "TRACE") {
      var token = csrfToken();
      if (token) {
        var headers = init.headers;
        if (!headers) {
          init.headers = { "X-CSRFToken": token };
        } else if (typeof Headers !== "undefined" && headers instanceof Headers) {
          if (!headers.has("X-CSRFToken") && !headers.has("X-CSRF-Token")) {
            headers.set("X-CSRFToken", token);
          }
        } else {
          var copy = {};
          Object.keys(headers).forEach(function (k) {
            copy[k] = headers[k];
          });
          if (!copy["X-CSRFToken"] && !copy["X-CSRF-Token"]) {
            copy["X-CSRFToken"] = token;
          }
          init.headers = copy;
        }
      }
    }
    if (!init.credentials) {
      init.credentials = "same-origin";
    }
    return originalFetch(input, init);
  };

  window.GurmadNetCSRF = { token: csrfToken };
})();
