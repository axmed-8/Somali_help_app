/**
 * Call Center Operator Dashboard
 */
(function () {
  "use strict";

  var selectedTypes = {};
  var activeCallId = null;
  var refreshMs = 5000;
  var sessionMap = null;
  var timerInterval = null;
  var callStartedAt = null;
  var latestAiPanel = null;
  var aiAnalyzeTimer = null;

  function api(url, opts) {
    opts = opts || {};
    opts.credentials = "same-origin";
    opts.headers = opts.headers || {};
    if (opts.body && typeof opts.body === "object") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    return fetch(url, opts).then(function (r) { return r.json(); });
  }

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function fmtDuration(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    var m = Math.floor(sec / 60);
    var s = sec % 60;
    return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }

  function renderStats(stats, online) {
    document.getElementById("st-online").textContent = (stats && stats.operators_online) || (online && online.length) || 0;
    document.getElementById("st-incoming").textContent = (stats && stats.incoming_calls) || 0;
    document.getElementById("st-waiting").textContent = (stats && stats.calls_waiting) || 0;
    document.getElementById("st-progress").textContent = (stats && stats.calls_in_progress) || 0;
    document.getElementById("st-resolved").textContent = (stats && stats.resolved_today) || 0;
    document.getElementById("st-avg").textContent = (stats && stats.avg_response_minutes) || 0;
    document.getElementById("op-online").textContent = "Operators: " + ((stats && stats.operators_online) || 0);
  }

  function renderIncoming(calls) {
    var box = document.getElementById("incoming-list");
    var ringing = (calls || []).filter(function (c) {
      return c.status === "ringing" || c.status === "answered" || c.status === "in_progress" || c.status === "dispatched";
    });
    if (!ringing.length) {
      box.innerHTML = '<div class="empty-alerts">No live calls. Waiting for citizens…</div>';
      return;
    }
    box.innerHTML = "";
    ringing.forEach(function (c) {
      var card = document.createElement("div");
      card.className = "cc-card " + (c.status || "");
      var start = c.start_time ? new Date(c.start_time.replace(" ", "T")) : new Date();
      var elapsed = Math.floor((Date.now() - start.getTime()) / 1000);
      card.innerHTML =
        '<div class="cc-card-head">' +
          "<strong>Incoming Call #" + c.id + "</strong>" +
          '<span class="cc-badge ' + esc(c.status) + '">' + esc(c.status) + "</span>" +
        "</div>" +
        "<dl>" +
          "<dt>Name</dt><dd>" + esc(c.caller_name) + "</dd>" +
          "<dt>Phone</dt><dd>" + esc(c.phone) + "</dd>" +
          "<dt>User ID</dt><dd>GN-" + esc(c.user_id) + "</dd>" +
          "<dt>GPS</dt><dd>" + esc(c.latitude) + ", " + esc(c.longitude) + "</dd>" +
          "<dt>Address</dt><dd>" + esc(c.address) + "</dd>" +
          "<dt>Duration</dt><dd>" + fmtDuration(elapsed) + "</dd>" +
          "<dt>Status</dt><dd>" + esc(c.status) + "</dd>" +
        "</dl>" +
        '<div class="cc-card-actions">' +
          (c.status === "ringing"
            ? '<button type="button" class="btn-action btn-accept" data-answer="' + c.id + '">Answer</button>'
            : '<button type="button" class="btn-action btn-view" data-open="' + c.id + '">Open</button>') +
          '<a class="btn-action btn-call" href="tel:' + esc(String(c.phone || "").replace(/\s/g, "")) + '">Call Citizen</a>' +
          '<button type="button" class="btn-action btn-reject" data-cancel="' + c.id + '">Cancel</button>' +
        "</div>";
      box.appendChild(card);
    });
    bindIncoming();
  }

  function bindIncoming() {
    document.querySelectorAll("[data-answer]").forEach(function (btn) {
      btn.onclick = function () {
        var id = parseInt(btn.getAttribute("data-answer"), 10);
        api("/api/call-center/calls/" + id + "/answer", { method: "POST", body: {} })
          .then(function (d) {
            if (d.success) openSession(d.call, d);
            else alert(d.message || "Could not answer");
          });
      };
    });
    document.querySelectorAll("[data-open]").forEach(function (btn) {
      btn.onclick = function () {
        var id = parseInt(btn.getAttribute("data-open"), 10);
        api("/api/call-center/calls/" + id).then(function (d) {
          if (d.success) openSession(d.call, d);
        });
      };
    });
    document.querySelectorAll("[data-cancel]").forEach(function (btn) {
      btn.onclick = function () {
        if (!confirm("Cancel this call?")) return;
        var id = parseInt(btn.getAttribute("data-cancel"), 10);
        api("/api/call-center/calls/" + id + "/cancel", { method: "POST", body: {} }).then(loadLive);
      };
    });
  }

  function nearestHtml(n) {
    if (!n) return "<p>Searching nearest responders…</p>";
    var parts = [];
    ["hospital", "police", "fire"].forEach(function (k) {
      var x = n[k];
      if (!x) return;
      parts.push(
        '<div class="cc-nearest-card">' +
          "<strong>" + (k === "hospital" ? "Nearest Hospital" : k === "police" ? "Nearest Police" : "Nearest Fire") + "</strong>" +
          esc(x.name) + "<br>" +
          (x.distance_km != null ? x.distance_km + " km · ETA ~" + x.eta_minutes + " min<br>" : "") +
          (x.phone ? '<a href="tel:' + esc(String(x.phone).replace(/\s/g, "")) + '">' + esc(x.phone) + "</a>" : "") +
        "</div>"
      );
    });
    return '<div class="cc-nearest">' + parts.join("") + "</div>";
  }

  function typeButtonsHtml() {
    var opts = window.CC_TYPE_OPTIONS || [
      ["medical", "Medical"],
      ["fire", "Fire"],
      ["security", "Police"],
      ["accident", "Accident"],
      ["family_help", "Family Emergency"],
      ["other", "Other"]
    ];
    return opts.map(function (o) {
      var key = o[0];
      var label = o[1];
      var active = selectedTypes[key] ? " active" : "";
      return '<button type="button" class="cc-type-btn' + active + '" data-type="' + key + '">' + label + "</button>";
    }).join("");
  }

  function historyHtml(history) {
    if (!history || !history.length) {
      return '<p class="cc-ai-muted">No previous emergencies on file.</p>';
    }
    return (
      '<ul class="cc-history-list">' +
      history.slice(0, 5).map(function (h) {
        return (
          "<li>#" + esc(h.id) + " · " + esc(h.type) + " · " + esc(h.status) +
          (h.timestamp ? " · " + esc(h.timestamp) : "") +
          "</li>"
        );
      }).join("") +
      "</ul>"
    );
  }

  function responderLine(label, obj, eta) {
    if (!obj) return "<div><strong>" + label + ":</strong> —</div>";
    var name = obj.name || obj.station_name || "Unit";
    var dist = obj.distance_km != null ? obj.distance_km + " km" : "";
    var why = obj.reason || obj.why || "";
    return (
      "<div><strong>" + label + ":</strong> " + esc(name) +
      (dist ? " (" + esc(dist) + ")" : "") +
      (eta != null ? " · ETA ~" + esc(eta) + " min" : "") +
      (why ? "<br><span class='cc-ai-muted'>" + esc(why) + "</span>" : "") +
      "</div>"
    );
  }

  function renderAiPanel(panel) {
    var box = document.getElementById("ai-panel-body");
    if (!box) return;
    latestAiPanel = panel || null;
    if (!panel || (!panel.recommendation_id && !panel.category && !panel.emergency_type)) {
      box.innerHTML = '<p class="cc-ai-muted">Waiting for AI analysis… Enter notes (“What happened?”) then Analyze.</p>';
      return;
    }
    var conf = panel.confidence_pct != null
      ? panel.confidence_pct
      : Math.round((panel.confidence || 0) * 100);
    box.innerHTML =
      '<div class="cc-ai-grid">' +
        "<div><span>Type</span><strong>" + esc(panel.emergency_type || panel.category || "—") + "</strong></div>" +
        "<div><span>Priority</span><strong class='cc-ai-pri-" + esc(panel.priority || "medium") + "'>" + esc(panel.priority || "—") + "</strong></div>" +
        "<div><span>Risk</span><strong>" + esc(panel.risk || "—") + "</strong></div>" +
        "<div><span>Confidence</span><strong>" + esc(conf) + "%</strong></div>" +
      "</div>" +
      "<p><strong>Services:</strong> " + esc(
        typeof panel.required_services === "string"
          ? panel.required_services
          : (panel.required_services || []).join(", ") || "—"
      ) + "</p>" +
      "<p><strong>Summary:</strong> " + esc(panel.summary || "—") + "</p>" +
      responderLine("Hospital", panel.recommended_hospital, panel.eta_minutes) +
      responderLine("Police", panel.recommended_police, null) +
      responderLine("Fire", panel.recommended_fire, null) +
      "<p><strong>Reason:</strong> " + esc(panel.reason || panel.dispatch_reason || "—") + "</p>" +
      '<p class="cc-ai-status">Status: <strong id="ai-rec-status">' + esc(panel.status || "pending") + "</strong></p>";
  }

  function applySuggestedTypes(panel) {
    selectedTypes = {};
    var types = (panel && panel.suggested_dispatch_types) || [];
    types.forEach(function (t) { selectedTypes[t] = true; });
    document.querySelectorAll("#type-grid .cc-type-btn").forEach(function (btn) {
      var t = btn.getAttribute("data-type");
      btn.classList.toggle("active", !!selectedTypes[t]);
    });
  }

  function runAiAnalyze() {
    if (!activeCallId) return;
    var notesEl = document.getElementById("session-notes");
    var notes = notesEl ? notesEl.value : "";
    var statusEl = document.getElementById("ai-panel-status");
    if (statusEl) statusEl.textContent = "Analyzing…";
    api("/api/call-center/calls/" + activeCallId + "/ai/analyze", {
      method: "POST",
      body: { notes: notes }
    }).then(function (d) {
      if (statusEl) statusEl.textContent = d.success ? "Updated" : (d.message || "Failed");
      if (d.success) {
        renderAiPanel(d.panel);
        applySuggestedTypes(d.panel);
        if (d.emergency_history) {
          var h = document.getElementById("session-history");
          if (h) h.innerHTML = historyHtml(d.emergency_history);
        }
      }
    });
  }

  function openSession(call, payload) {
    activeCallId = call.id;
    selectedTypes = {};
    latestAiPanel = null;
    callStartedAt = call.start_time ? new Date(call.start_time.replace(" ", "T")) : new Date();
    showPanel("active");
    var history = (payload && payload.emergency_history) || call.emergency_history || [];
    var ai = (payload && payload.ai) || {};
    var el = document.getElementById("active-session");
    el.classList.remove("empty");
    el.innerHTML =
      '<div class="cc-card-head"><h2 style="margin:0;">Call #' + call.id + " — " + esc(call.caller_name) + "</h2>" +
      '<span class="cc-badge ' + esc(call.status) + '" id="session-status">' + esc(call.status) + "</span></div>" +
      "<p><strong>Ask:</strong> “What happened?” · GPS is already available.</p>" +
      "<dl>" +
        "<dt>Citizen</dt><dd>" + esc(call.caller_name) + "</dd>" +
        "<dt>Phone</dt><dd>" + esc(call.phone) + "</dd>" +
        "<dt>User ID</dt><dd>GN-" + esc(call.user_id) + "</dd>" +
        "<dt>GPS</dt><dd>" + esc(call.latitude) + ", " + esc(call.longitude) + "</dd>" +
        "<dt>Address</dt><dd>" + esc(call.address) + "</dd>" +
        "<dt>Duration</dt><dd id=\"session-duration\">00:00</dd>" +
      "</dl>" +
      "<h3>Emergency history</h3>" +
      '<div id="session-history">' + historyHtml(history) + "</div>" +
      '<div id="session-map" class="cc-map"></div>' +
      "<h3>Nearest responders</h3>" +
      '<div id="session-nearest">' + nearestHtml(call.nearest) + "</div>" +
      '<section class="cc-ai-panel" id="ai-panel">' +
        '<div class="cc-ai-panel-head">' +
          "<h3>AI Assistant Panel</h3>" +
          '<span id="ai-panel-status" class="cc-ai-muted">Recommendation only — you approve dispatch</span>' +
        "</div>" +
        '<div id="ai-panel-body"></div>' +
        '<div class="cc-ai-actions">' +
          '<button type="button" class="btn-action btn-accept" id="btn-ai-approve">Approve Recommendation</button>' +
          '<button type="button" class="btn-action btn-reject" id="btn-ai-reject">Reject Recommendation</button>' +
          '<button type="button" class="btn-action btn-view" id="btn-ai-manual">Manual Selection</button>' +
          '<button type="button" class="btn-action" id="btn-ai-analyze">Analyze Notes</button>' +
        "</div>" +
      "</section>" +
      "<h3>Emergency type (multi-select OK)</h3>" +
      '<div class="cc-type-grid" id="type-grid">' + typeButtonsHtml() + "</div>" +
      '<label style="font-size:0.85rem;font-weight:600;color:#64748B;">Operator notes (what happened)</label>' +
      '<textarea id="session-notes" rows="2" style="width:100%;margin-top:6px;padding:10px;border-radius:12px;border:1.5px solid rgba(13,71,161,0.15);">' +
        esc(call.notes || "") + "</textarea>" +
      '<div class="cc-dispatch-row">' +
        '<button type="button" class="btn-action btn-accept" id="btn-dispatch">Dispatch Request</button>' +
        '<button type="button" class="btn-action btn-view" id="btn-send-gps">Send GPS</button>' +
        '<a class="btn-action btn-call" id="btn-call-hospital" href="#">Call Hospital</a>' +
        '<a class="btn-action btn-call" id="btn-call-police" href="#">Call Police</a>' +
        '<a class="btn-action btn-call" id="btn-call-fire" href="#">Call Fire</a>' +
        '<button type="button" class="btn-action" id="btn-transfer">Transfer Call</button>' +
        '<button type="button" class="btn-action btn-reject" id="btn-cancel-session">Cancel Call</button>' +
        '<button type="button" class="btn-action btn-arrived" id="btn-complete">Complete</button>' +
      "</div>" +
      '<p id="session-msg" style="margin-top:12px;color:#2E7D32;"></p>';

    bindSession(call);
    initSessionMap(call);
    refreshNearest(call.id);
    startDurationTimer();
    renderAiPanel(ai.panel || (ai.success && ai.panel) || null);
    if (ai.panel) applySuggestedTypes(ai.panel);
    if (!ai.panel && !ai.analysis) {
      runAiAnalyze();
    }
  }

  function bindSession(call) {
    document.querySelectorAll("#type-grid .cc-type-btn").forEach(function (btn) {
      btn.onclick = function () {
        var t = btn.getAttribute("data-type");
        if (selectedTypes[t]) delete selectedTypes[t];
        else selectedTypes[t] = true;
        btn.classList.toggle("active", !!selectedTypes[t]);
      };
    });

    var n = call.nearest || {};
    function setTel(id, phone) {
      var a = document.getElementById(id);
      if (!a) return;
      if (phone) a.href = "tel:" + String(phone).replace(/\s/g, "");
      else a.onclick = function (e) { e.preventDefault(); alert("No phone on file for this unit."); };
    }
    setTel("btn-call-hospital", n.hospital && n.hospital.phone);
    setTel("btn-call-police", n.police && n.police.phone);
    setTel("btn-call-fire", n.fire && n.fire.phone);

    var notesEl = document.getElementById("session-notes");
    if (notesEl) {
      notesEl.oninput = function () {
        if (aiAnalyzeTimer) clearTimeout(aiAnalyzeTimer);
        aiAnalyzeTimer = setTimeout(runAiAnalyze, 900);
      };
    }

    var btnAnalyze = document.getElementById("btn-ai-analyze");
    if (btnAnalyze) btnAnalyze.onclick = runAiAnalyze;

    var btnApprove = document.getElementById("btn-ai-approve");
    if (btnApprove) {
      btnApprove.onclick = function () {
        if (!latestAiPanel || !latestAiPanel.recommendation_id) {
          alert("Run AI analysis first.");
          return;
        }
        if (!confirm("Approve AI recommendation and dispatch suggested responders?")) return;
        var notes = document.getElementById("session-notes").value;
        api("/api/call-center/calls/" + activeCallId + "/ai/decision", {
          method: "POST",
          body: {
            decision: "approve",
            recommendation_id: latestAiPanel.recommendation_id,
            notes: notes
          }
        }).then(function (d) {
          document.getElementById("session-msg").textContent = d.message || "";
          if (d.panel) renderAiPanel(d.panel);
          if (d.success && d.call) {
            document.getElementById("session-status").textContent = d.call.status;
            loadLive();
          } else if (!d.success) alert(d.message || "Approve failed");
        });
      };
    }

    var btnReject = document.getElementById("btn-ai-reject");
    if (btnReject) {
      btnReject.onclick = function () {
        if (!latestAiPanel || !latestAiPanel.recommendation_id) {
          alert("Run AI analysis first.");
          return;
        }
        api("/api/call-center/calls/" + activeCallId + "/ai/decision", {
          method: "POST",
          body: {
            decision: "reject",
            recommendation_id: latestAiPanel.recommendation_id,
            notes: document.getElementById("session-notes").value
          }
        }).then(function (d) {
          document.getElementById("session-msg").textContent = d.message || "Rejected.";
          if (d.panel) renderAiPanel(d.panel);
        });
      };
    }

    var btnManual = document.getElementById("btn-ai-manual");
    if (btnManual) {
      btnManual.onclick = function () {
        function focusManual() {
          document.getElementById("session-msg").textContent =
            "Manual selection — choose emergency types below, then Dispatch Request.";
          var grid = document.getElementById("type-grid");
          if (grid) grid.scrollIntoView({ behavior: "smooth", block: "center" });
        }
        if (!latestAiPanel || !latestAiPanel.recommendation_id) {
          focusManual();
          return;
        }
        api("/api/call-center/calls/" + activeCallId + "/ai/decision", {
          method: "POST",
          body: {
            decision: "manual",
            recommendation_id: latestAiPanel.recommendation_id,
            notes: document.getElementById("session-notes").value
          }
        }).then(function (d) {
          if (d.panel) renderAiPanel(d.panel);
          focusManual();
        });
      };
    }

    document.getElementById("btn-dispatch").onclick = function () {
      var types = Object.keys(selectedTypes);
      if (!types.length) { alert("Select at least one emergency type."); return; }
      var notes = document.getElementById("session-notes").value;
      api("/api/call-center/calls/" + activeCallId + "/dispatch", {
        method: "POST",
        body: { types: types, notes: notes }
      }).then(function (d) {
        document.getElementById("session-msg").textContent = d.message || (d.success ? "Dispatched." : d.message);
        if (d.success && d.call) {
          document.getElementById("session-status").textContent = d.call.status;
          loadLive();
        } else if (!d.success) alert(d.message || "Dispatch failed");
      });
    };

    document.getElementById("btn-send-gps").onclick = function () {
      var types = Object.keys(selectedTypes);
      var targets = [];
      if (types.indexOf("medical") >= 0 || types.indexOf("family_help") >= 0 || types.indexOf("other") >= 0) targets.push("hospital");
      if (types.indexOf("fire") >= 0) targets.push("fire");
      if (types.indexOf("security") >= 0 || types.indexOf("accident") >= 0) targets.push("police");
      if (!targets.length) {
        targets = ["hospital", "police", "fire"];
      }
      api("/api/call-center/calls/" + activeCallId + "/send-gps", {
        method: "POST",
        body: { targets: targets, notes: document.getElementById("session-notes").value }
      }).then(function (d) {
        document.getElementById("session-msg").textContent = d.message || "GPS sent.";
        if (!d.success) alert(d.message || "Failed");
        loadLive();
      });
    };

    document.getElementById("btn-transfer").onclick = function () {
      api("/api/call-center/live").then(function (d) {
        var ops = (d.operators_online || []).filter(function (o) {
          return true;
        });
        if (!ops.length) {
          alert("No other operators currently online to transfer to.");
          return;
        }
        var lines = ops.map(function (o, i) {
          return (i + 1) + ". " + o.name + " (ID " + o.id + ")";
        }).join("\n");
        var pick = prompt("Transfer call to operator — enter operator ID:\n\n" + lines);
        if (!pick) return;
        var oid = parseInt(pick, 10);
        if (!oid) { alert("Invalid operator ID"); return; }
        api("/api/call-center/calls/" + activeCallId + "/transfer", {
          method: "POST",
          body: { operator_id: oid, notes: document.getElementById("session-notes").value }
        }).then(function (res) {
          if (res.success) {
            document.getElementById("session-msg").textContent = res.message || "Transferred.";
            activeCallId = null;
            showPanel("live");
            loadLive();
          } else {
            alert(res.message || "Transfer failed");
          }
        });
      });
    };

    document.getElementById("btn-cancel-session").onclick = function () {
      if (!confirm("Cancel this call?")) return;
      api("/api/call-center/calls/" + activeCallId + "/cancel", { method: "POST", body: {} })
        .then(function () {
          activeCallId = null;
          document.getElementById("active-session").classList.add("empty");
          document.getElementById("active-session").innerHTML = "<p>Call cancelled.</p>";
          showPanel("live");
          loadLive();
        });
    };

    document.getElementById("btn-complete").onclick = function () {
      api("/api/call-center/calls/" + activeCallId + "/complete", { method: "POST", body: {} })
        .then(function (d) {
          document.getElementById("session-msg").textContent = "Call completed.";
          if (d.success) loadLive();
        });
    };
  }

  function refreshNearest(id) {
    api("/api/call-center/calls/" + id + "/nearest").then(function (d) {
      if (d.success) {
        document.getElementById("session-nearest").innerHTML = nearestHtml(d.nearest);
        var n = d.nearest || {};
        var bh = document.getElementById("btn-call-hospital");
        var bp = document.getElementById("btn-call-police");
        var bf = document.getElementById("btn-call-fire");
        if (bh && n.hospital && n.hospital.phone) bh.href = "tel:" + String(n.hospital.phone).replace(/\s/g, "");
        if (bp && n.police && n.police.phone) bp.href = "tel:" + String(n.police.phone).replace(/\s/g, "");
        if (bf && n.fire && n.fire.phone) bf.href = "tel:" + String(n.fire.phone).replace(/\s/g, "");
      }
    });
  }

  function initSessionMap(call) {
    if (typeof L === "undefined" || call.latitude == null) return;
    var el = document.getElementById("session-map");
    if (!el) return;
    el.innerHTML = "";
    if (sessionMap) {
      try { sessionMap.remove(); } catch (e) {}
      sessionMap = null;
    }
    sessionMap = L.map("session-map").setView([call.latitude, call.longitude], 15);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap"
    }).addTo(sessionMap);
    L.marker([call.latitude, call.longitude]).addTo(sessionMap)
      .bindPopup(esc(call.caller_name) + "<br>" + esc(call.address)).openPopup();
    setTimeout(function () { sessionMap.invalidateSize(); }, 200);
  }

  function startDurationTimer() {
    if (timerInterval) clearInterval(timerInterval);
    timerInterval = setInterval(function () {
      var el = document.getElementById("session-duration");
      if (!el || !callStartedAt) return;
      el.textContent = fmtDuration((Date.now() - callStartedAt.getTime()) / 1000);
    }, 1000);
  }

  function showPanel(name) {
    document.getElementById("panel-live").style.display = name === "live" ? "block" : "none";
    document.getElementById("panel-active").classList.toggle("hidden-panel", name !== "active");
    document.querySelectorAll(".admin-nav a[data-panel]").forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-panel") === name);
    });
  }

  var livePollTimer = null;

  function scheduleLivePoll() {
    if (livePollTimer) clearInterval(livePollTimer);
    livePollTimer = setInterval(loadLive, refreshMs);
  }

  function loadLive() {
    api("/api/call-center/live").then(function (d) {
      if (!d.success) return;
      var nextMs = d.refresh_interval ? d.refresh_interval * 1000 : refreshMs;
      if (nextMs !== refreshMs) {
        refreshMs = nextMs;
        scheduleLivePoll();
      }
      renderStats(d.stats, d.operators_online);
      renderIncoming(d.calls);
      document.getElementById("refresh-time").textContent =
        "Last updated " + new Date().toLocaleTimeString();
    });
  }

  document.querySelectorAll(".admin-nav a[data-panel]").forEach(function (a) {
    a.addEventListener("click", function (e) {
      e.preventDefault();
      showPanel(a.getAttribute("data-panel"));
    });
  });

  var toggle = document.getElementById("sidebar-toggle");
  if (toggle) {
    toggle.onclick = function () {
      document.getElementById("sidebar").classList.toggle("open");
    };
  }

  loadLive();
  scheduleLivePoll();
})();
