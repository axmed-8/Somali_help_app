/**
 * Citizen registration: multi-step wizard (login-style card).
 * Only the current step is visible; Next / Back move between steps.
 * Custom Date of Birth picker (DD/MM/YYYY display, YYYY-MM-DD submit).
 */
(function () {
  "use strict";

  var TOTAL_STEPS = 4;
  var EMAIL_RE = /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/;
  var currentStep = 1;
  var STEP_HINT = "Complete your details to continue.";
  var MIN_AGE = 13;
  var MAX_AGE = 120;
  var MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];
  var MONTHS_SHORT = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];

  function $(id) {
    return document.getElementById(id);
  }

  function phoneOk(value) {
    return String(value || "").replace(/\D+/g, "").length >= 7;
  }

  function showClientError(msg) {
    var alerts = $("signup-client-alerts");
    if (!alerts) return;
    alerts.hidden = false;
    alerts.innerHTML =
      '<div class="flash flash-error" role="alert">' + msg + "</div>";
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function clearClientError() {
    var alerts = $("signup-client-alerts");
    if (!alerts) return;
    alerts.hidden = true;
    alerts.innerHTML = "";
  }

  function pad2(n) {
    return n < 10 ? "0" + n : String(n);
  }

  function todayDate() {
    var n = new Date();
    return new Date(n.getFullYear(), n.getMonth(), n.getDate());
  }

  function toISO(d) {
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate());
  }

  function toDisplay(d) {
    return pad2(d.getDate()) + "/" + pad2(d.getMonth() + 1) + "/" + d.getFullYear();
  }

  function parseISO(value) {
    if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    var p = value.split("-");
    var d = new Date(+p[0], +p[1] - 1, +p[2]);
    if (
      d.getFullYear() !== +p[0] ||
      d.getMonth() !== +p[1] - 1 ||
      d.getDate() !== +p[2]
    ) {
      return null;
    }
    return d;
  }

  function calcAge(dob) {
    var t = todayDate();
    var age = t.getFullYear() - dob.getFullYear();
    var m = t.getMonth() - dob.getMonth();
    if (m < 0 || (m === 0 && t.getDate() < dob.getDate())) age -= 1;
    return age;
  }

  function validateDobValue(iso) {
    if (!iso) return "Date of birth is required.";
    var dob = parseISO(iso);
    if (!dob) return "Enter a valid date of birth.";
    if (dob > todayDate()) return "Date of birth cannot be in the future.";
    var age = calcAge(dob);
    if (age < MIN_AGE) return "You must be at least " + MIN_AGE + " years old.";
    if (age > MAX_AGE) return "Enter a valid date of birth.";
    if (dob.getFullYear() < 1900) return "Enter a valid date of birth.";
    return "";
  }

  function bindPasswordToggle(btnId, inputId) {
    var btn = $(btnId);
    var input = $(inputId);
    if (!btn || !input) return;

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

    setVisible(false);
    btn.addEventListener("click", function () {
      setVisible(input.type !== "text");
    });
  }

  /* ---------- Custom DOB picker ---------- */
  function initDobPicker() {
    var root = $("dob-picker");
    var display = $("dob_display");
    var hidden = $("date_of_birth");
    var popover = $("dob-popover");
    var backdrop = $("dob-backdrop");
    var daysEl = $("dob-days");
    var yearPanel = $("dob-year-panel");
    var yearList = $("dob-year-list");
    var monthPanel = $("dob-month-panel");
    var monthGrid = $("dob-month-grid");
    var monthBtn = $("dob-month-btn");
    var yearBtn = $("dob-year-btn");
    if (!root || !display || !hidden || !popover || !daysEl) return;

    var now = todayDate();
    var minYear = now.getFullYear() - MAX_AGE;
    var maxYear = now.getFullYear() - MIN_AGE;
    var viewYear = now.getFullYear() - 20;
    var viewMonth = now.getMonth();
    var committed = null; // applied to inputs
    var draft = null; // in-progress selection while popover is open

    function cloneDate(d) {
      return d ? new Date(d.getFullYear(), d.getMonth(), d.getDate()) : null;
    }

    function isFuture(y, m, d) {
      return new Date(y, m, d) > now;
    }

    function syncInputsFromCommitted() {
      if (committed) {
        hidden.value = toISO(committed);
        display.value = toDisplay(committed);
      } else {
        hidden.value = "";
        display.value = "";
      }
    }

    function setOpen(open) {
      popover.hidden = !open;
      if (backdrop) backdrop.hidden = !open;
      display.setAttribute("aria-expanded", open ? "true" : "false");
      root.classList.toggle("is-open", open);
      if (open) {
        yearPanel.hidden = true;
        monthPanel.hidden = true;
        render();
      }
    }

    function cancelAndClose() {
      // Discard draft; leave input as previously committed value
      draft = cloneDate(committed);
      setOpen(false);
    }

    function applyAndClose() {
      committed = cloneDate(draft);
      syncInputsFromCommitted();
      setOpen(false);
      clearClientError();
    }

    function renderHead() {
      monthBtn.textContent = MONTHS[viewMonth];
      yearBtn.textContent = String(viewYear);
      var atMaxMonth =
        viewYear > now.getFullYear() ||
        (viewYear === now.getFullYear() && viewMonth >= now.getMonth());
      $("dob-next").disabled = atMaxMonth;
      $("dob-prev").disabled = viewYear < minYear || (viewYear === minYear && viewMonth <= 0);
    }

    function renderDays() {
      daysEl.innerHTML = "";
      var firstDow = new Date(viewYear, viewMonth, 1).getDay();
      var daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
      var i;
      for (i = 0; i < firstDow; i++) {
        var empty = document.createElement("span");
        empty.className = "dob-day is-empty";
        daysEl.appendChild(empty);
      }
      for (i = 1; i <= daysInMonth; i++) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "dob-day";
        btn.textContent = String(i);
        var disabled = isFuture(viewYear, viewMonth, i);
        if (disabled) {
          btn.disabled = true;
          btn.classList.add("is-disabled");
        }
        if (
          draft &&
          draft.getFullYear() === viewYear &&
          draft.getMonth() === viewMonth &&
          draft.getDate() === i
        ) {
          btn.classList.add("is-selected");
        }
        (function (day) {
          btn.addEventListener("click", function (e) {
            e.preventDefault();
            e.stopPropagation();
            if (isFuture(viewYear, viewMonth, day)) return;
            draft = new Date(viewYear, viewMonth, day);
            renderDays();
          });
        })(i);
        daysEl.appendChild(btn);
      }
    }

    function renderYears() {
      yearList.innerHTML = "";
      var y;
      for (y = maxYear; y >= minYear; y--) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "dob-year-item";
        btn.textContent = String(y);
        if (y === viewYear) btn.classList.add("is-active");
        (function (year) {
          btn.addEventListener("click", function (e) {
            e.preventDefault();
            e.stopPropagation();
            viewYear = year;
            if (viewYear === now.getFullYear() && viewMonth > now.getMonth()) {
              viewMonth = now.getMonth();
            }
            // Keep draft day if still valid in new year/month
            if (draft) {
              var dim = new Date(viewYear, viewMonth + 1, 0).getDate();
              var day = Math.min(draft.getDate(), dim);
              if (!isFuture(viewYear, viewMonth, day)) {
                draft = new Date(viewYear, viewMonth, day);
              } else {
                draft = null;
              }
            }
            yearPanel.hidden = true;
            render();
          });
        })(y);
        yearList.appendChild(btn);
      }
    }

    function scrollYearIntoView() {
      var active = yearList.querySelector(".is-active");
      if (active && active.scrollIntoView) {
        active.scrollIntoView({ block: "center" });
      }
    }

    function renderMonths() {
      monthGrid.innerHTML = "";
      var m;
      for (m = 0; m < 12; m++) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "dob-month-item";
        btn.textContent = MONTHS_SHORT[m];
        var disabled =
          viewYear > now.getFullYear() ||
          (viewYear === now.getFullYear() && m > now.getMonth());
        if (disabled) {
          btn.disabled = true;
          btn.classList.add("is-disabled");
        }
        if (m === viewMonth) btn.classList.add("is-active");
        (function (month) {
          btn.addEventListener("click", function (e) {
            e.preventDefault();
            e.stopPropagation();
            if (
              viewYear > now.getFullYear() ||
              (viewYear === now.getFullYear() && month > now.getMonth())
            ) {
              return;
            }
            viewMonth = month;
            if (draft) {
              var dim = new Date(viewYear, viewMonth + 1, 0).getDate();
              var day = Math.min(draft.getDate(), dim);
              if (!isFuture(viewYear, viewMonth, day)) {
                draft = new Date(viewYear, viewMonth, day);
              } else {
                draft = null;
              }
            }
            monthPanel.hidden = true;
            render();
          });
        })(m);
        monthGrid.appendChild(btn);
      }
    }

    function render() {
      renderHead();
      renderDays();
      renderYears();
      renderMonths();
    }

    function openPicker() {
      draft = cloneDate(committed);
      if (draft) {
        viewYear = draft.getFullYear();
        viewMonth = draft.getMonth();
      } else {
        viewYear = now.getFullYear() - 20;
        viewMonth = now.getMonth();
      }
      setOpen(true);
    }

    display.addEventListener("click", function (e) {
      e.stopPropagation();
      if (popover.hidden) openPicker();
    });
    display.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (popover.hidden) openPicker();
      }
    });
    $("dob-open-btn").addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (popover.hidden) openPicker();
      else cancelAndClose();
    });

    $("dob-prev").addEventListener("click", function (e) {
      e.stopPropagation();
      viewMonth -= 1;
      if (viewMonth < 0) {
        viewMonth = 11;
        viewYear -= 1;
      }
      if (viewYear < minYear) {
        viewYear = minYear;
        viewMonth = 0;
      }
      render();
    });

    $("dob-next").addEventListener("click", function (e) {
      e.stopPropagation();
      viewMonth += 1;
      if (viewMonth > 11) {
        viewMonth = 0;
        viewYear += 1;
      }
      if (
        viewYear > now.getFullYear() ||
        (viewYear === now.getFullYear() && viewMonth > now.getMonth())
      ) {
        viewYear = now.getFullYear();
        viewMonth = now.getMonth();
      }
      render();
    });

    yearBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      monthPanel.hidden = true;
      yearPanel.hidden = !yearPanel.hidden;
      if (!yearPanel.hidden) {
        renderYears();
        setTimeout(scrollYearIntoView, 0);
      }
    });

    monthBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      yearPanel.hidden = true;
      monthPanel.hidden = !monthPanel.hidden;
      if (!monthPanel.hidden) renderMonths();
    });

    $("dob-clear").addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      draft = null;
      renderDays();
    });

    $("dob-done").addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      applyAndClose();
    });

    if (backdrop) {
      backdrop.addEventListener("click", function () {
        cancelAndClose();
      });
    }

    document.addEventListener("click", function (e) {
      if (!popover.hidden && !root.contains(e.target)) {
        cancelAndClose();
      }
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !popover.hidden) cancelAndClose();
    });

    // Default view year ~20 years ago (no pre-selected value)
    viewYear = now.getFullYear() - 20;
    viewMonth = now.getMonth();
    syncInputsFromCommitted();
    render();
  }

  function validateStep(step) {
    if (step === 1) {
      var first = ($("first_name").value || "").trim();
      var last = ($("last_name").value || "").trim();
      var gender = $("gender").value;
      var dob = $("date_of_birth").value;
      if (!first) return "First name is required.";
      if (!last) return "Last name is required.";
      if (gender !== "male" && gender !== "female") return "Please select a gender.";
      var dobErr = validateDobValue(dob);
      if (dobErr) return dobErr;
      return "";
    }
    if (step === 2) {
      var email = ($("email").value || "").trim();
      var phone = $("phone").value;
      if (!EMAIL_RE.test(email)) return "Enter a valid email address.";
      if (!phoneOk(phone)) return "Enter a valid phone number.";
      return "";
    }
    if (step === 3) {
      var cName = ($("emergency_contact_name").value || "").trim();
      var relation = $("emergency_contact_relation").value;
      var cPhone = $("emergency_contact_phone").value;
      var cEmail = ($("emergency_contact_email").value || "").trim();
      if (!cName) return "Emergency contact name is required.";
      if (!relation) return "Please select a relationship.";
      if (!phoneOk(cPhone)) return "Enter a valid emergency contact phone number.";
      if (cEmail && !EMAIL_RE.test(cEmail)) {
        return "Enter a valid emergency contact email, or leave it blank.";
      }
      return "";
    }
    if (step === 4) {
      var pw = $("password").value || "";
      var cp = $("confirm_password").value || "";
      var terms = $("agree_terms");
      if (pw.length < 6) return "Password must be at least 6 characters.";
      if (pw !== cp) return "Passwords do not match.";
      if (!terms || !terms.checked) return "Please agree to the Terms & Privacy Policy.";
      return "";
    }
    return "";
  }

  function goToStep(step) {
    currentStep = step;
    var panels = document.querySelectorAll(".signup-step");
    for (var i = 0; i < panels.length; i++) {
      var panel = panels[i];
      var n = parseInt(panel.getAttribute("data-step"), 10);
      if (n === step) {
        panel.hidden = false;
      } else {
        panel.hidden = true;
      }
    }

    var back = $("btn-back");
    var next = $("btn-next");
    var submit = $("btn-signup");
    if (back) back.hidden = step === 1;
    if (next) next.hidden = step === TOTAL_STEPS;
    if (submit) submit.hidden = step !== TOTAL_STEPS;

    var label = $("signup-step-label");
    if (label) label.textContent = "Step " + step + " of " + TOTAL_STEPS;

    var bar = $("signup-progress-bar");
    if (bar) bar.style.width = (step / TOTAL_STEPS) * 100 + "%";

    var hint = $("signup-step-hint");
    if (hint) hint.textContent = STEP_HINT;

    clearClientError();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var form = $("signup-form");
    if (!form) return;

    bindPasswordToggle("password-toggle", "password");
    bindPasswordToggle("confirm-toggle", "confirm_password");
    initDobPicker();

    goToStep(1);

    var btnNext = $("btn-next");
    var btnBack = $("btn-back");

    if (btnNext) {
      btnNext.addEventListener("click", function () {
        var err = validateStep(currentStep);
        if (err) {
          showClientError(err);
          return;
        }
        if (currentStep < TOTAL_STEPS) goToStep(currentStep + 1);
      });
    }

    if (btnBack) {
      btnBack.addEventListener("click", function () {
        if (currentStep > 1) goToStep(currentStep - 1);
      });
    }

    form.addEventListener("submit", function (e) {
      if (currentStep !== TOTAL_STEPS) {
        e.preventDefault();
        goToStep(TOTAL_STEPS);
        return;
      }
      var err = validateStep(4);
      if (err) {
        e.preventDefault();
        showClientError(err);
        return;
      }
      var btn = $("btn-signup");
      if (btn) {
        btn.disabled = true;
        btn.classList.add("is-loading");
        btn.textContent = "Creating account…";
      }
    });
  });
})();
