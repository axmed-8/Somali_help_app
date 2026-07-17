/**
 * Login page UX: validation, loading states, show/hide password.
 */
(function () {
  "use strict";

  var EMAIL_RE = /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/;

  function $(id) {
    return document.getElementById(id);
  }

  function setFieldError(inputId, errorId, groupId, message) {
    var input = $(inputId);
    var err = $(errorId);
    var group = $(groupId);
    if (!input || !err) return;
    if (message) {
      err.textContent = message;
      err.hidden = false;
      input.setAttribute("aria-invalid", "true");
      if (group) group.classList.add("has-error");
    } else {
      err.textContent = "";
      err.hidden = true;
      input.setAttribute("aria-invalid", "false");
      if (group) group.classList.remove("has-error");
    }
  }

  function validateEmail(value) {
    var v = (value || "").trim();
    if (!v) return "Email is required.";
    if (!EMAIL_RE.test(v)) return "Enter a valid email address.";
    return "";
  }

  function validatePassword(value) {
    if (!value) return "Password is required.";
    if (value.length < 6) return "Password must be at least 6 characters.";
    return "";
  }

  function setLoading(btn, loading, loadingLabel) {
    if (!btn) return;
    var defaultLabel = btn.getAttribute("data-default-label") || btn.textContent;
    if (loading) {
      btn.disabled = true;
      btn.classList.add("is-loading");
      btn.setAttribute("aria-busy", "true");
      btn.textContent = loadingLabel || "Please wait…";
    } else {
      btn.disabled = false;
      btn.classList.remove("is-loading");
      btn.setAttribute("aria-busy", "false");
      btn.textContent = defaultLabel;
    }
  }

  function bindPasswordToggle() {
    var input = $("password");
    var btn = $("password-toggle");
    if (!input || !btn) return;

    function setVisible(showing) {
      input.type = showing ? "text" : "password";
      btn.setAttribute("aria-pressed", showing ? "true" : "false");
      btn.setAttribute("aria-label", showing ? "Hide password" : "Show password");
      var eye = btn.querySelector(".icon-eye");
      var eyeOff = btn.querySelector(".icon-eye-off");
      if (eye) {
        if (showing) eye.setAttribute("hidden", "");
        else eye.removeAttribute("hidden");
      }
      if (eyeOff) {
        if (showing) eyeOff.removeAttribute("hidden");
        else eyeOff.setAttribute("hidden", "");
      }
    }

    // Initial state: password hidden → only open-eye icon
    setVisible(false);

    btn.addEventListener("click", function () {
      setVisible(input.type !== "text");
      try {
        input.focus({ preventScroll: true });
      } catch (e) {
        input.focus();
      }
    });
  }

  function bindLoginForm() {
    var form = $("login-form");
    var btn = $("btn-login");
    if (!form) return;

    form.addEventListener("submit", function (e) {
      var emailErr = validateEmail($("username").value);
      var passErr = validatePassword($("password").value);
      setFieldError("username", "username-error", "group-username", emailErr);
      setFieldError("password", "password-error", "group-password", passErr);
      if (emailErr || passErr) {
        e.preventDefault();
        if (emailErr) $("username").focus();
        else $("password").focus();
        return;
      }
      setLoading(btn, true, "Signing in…");
    });

    ["username", "password"].forEach(function (id) {
      var el = $(id);
      if (!el) return;
      el.addEventListener("input", function () {
        if (id === "username") {
          setFieldError("username", "username-error", "group-username", "");
        } else {
          setFieldError("password", "password-error", "group-password", "");
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindLoginForm();
    bindPasswordToggle();
  });
})();
