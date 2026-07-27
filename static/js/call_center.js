/**
 * Call Center Operator Dashboard
 */
(function () {
  "use strict";

  var selectedTypes = {};
  var activeCallId = null;
  var refreshMs = 5000;
  var sessionMap = null;
  var sessionMarker = null;
  var sessionMapKind = null; // "google" | "leaflet"
  var timerInterval = null;
  var callStartedAt = null;
  var latestAiPanel = null;
  var aiAnalyzeTimer = null;
  var voiceCall = null;
  var voiceActiveId = null;
  var pendingIncoming = {};
  var voiceMediaTimer = null;
  var voiceMediaStartedAt = null;

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
      return (
        c.status === "ringing" ||
        c.status === "accepted" ||
        c.status === "connecting" ||
        c.status === "connected" ||
        c.status === "answered" ||
        c.status === "in_progress" ||
        c.status === "dispatched"
      );
    });
    // Newest first; ringing before answered/dispatched (safety if API order drifts)
    var rank = {
      ringing: 0,
      accepted: 1,
      connecting: 1,
      connected: 1,
      answered: 1,
      in_progress: 1,
      dispatched: 2
    };
    ringing.sort(function (a, b) {
      var ra = rank[a.status] != null ? rank[a.status] : 9;
      var rb = rank[b.status] != null ? rank[b.status] : 9;
      if (ra !== rb) return ra - rb;
      return (b.id || 0) - (a.id || 0);
    });
    if (!ringing.length) {
      box.innerHTML = '<div class="empty-alerts cc-empty">No live calls right now.</div>';
      return;
    }
    box.innerHTML = "";
    ringing.forEach(function (c) {
      var card = document.createElement("article");
      card.className = "cc-card " + (c.status || "");
      card.setAttribute("data-call-id", c.id);
      var start = c.start_time ? new Date(c.start_time.replace(" ", "T")) : new Date();
      var elapsed = Math.floor((Date.now() - start.getTime()) / 1000);
      var isRinging = c.status === "ringing";
      var isVoice = !!c.voice_mode || isRinging;
      var eids = (c.emergency_ids || []).join(", ") || "—";
      card.innerHTML =
        '<div class="cc-card-head">' +
          '<div class="cc-card-title">' +
            (isRinging ? '<span class="cc-pulse" aria-hidden="true"></span>' : "") +
            "<strong>Call #" + c.id + "</strong>" +
            (isVoice ? ' <span class="cc-voice-tag">Voice</span>' : "") +
          "</div>" +
          '<span class="cc-badge ' + esc(c.status) + '">' + esc(c.status) + "</span>" +
        "</div>" +
        '<p class="cc-caller">' + esc(c.caller_name) + "</p>" +
        '<div class="cc-meta">' +
          '<div><span>Phone</span><strong>' + esc(c.phone) + "</strong></div>" +
          '<div><span>Citizen</span><strong>GN-' + esc(c.user_id) + "</strong></div>" +
          '<div><span>Case</span><strong>' + esc(eids) + "</strong></div>" +
          '<div><span>Wait</span><strong class="cc-wait">' + fmtDuration(elapsed) + "</strong></div>" +
        "</div>" +
        '<div class="cc-loc-block">' +
          '<span class="cc-loc-label">Location</span>' +
          '<strong class="cc-gps">' + esc(c.latitude) + ", " + esc(c.longitude) + "</strong>" +
          '<em class="cc-addr">' + esc(c.address || "—") + "</em>" +
        "</div>" +
        '<div class="cc-card-actions">' +
          (isRinging
            ? '<button type="button" class="btn-action btn-accept" data-voice-accept="' + c.id + '">Accept</button>' +
              '<button type="button" class="btn-action btn-reject" data-voice-reject="' + c.id + '">Reject</button>'
            : '<button type="button" class="btn-action btn-view" data-open="' + c.id + '">Open</button>') +
          '<button type="button" class="btn-action btn-reject" data-cancel="' + c.id + '">Cancel</button>' +
        "</div>";
      box.appendChild(card);
    });
    bindIncoming();
  }

  function bindIncoming() {
    document.querySelectorAll("[data-voice-accept]").forEach(function (btn) {
      btn.onclick = function () {
        var id = parseInt(btn.getAttribute("data-voice-accept"), 10);
        acceptVoiceCall(id, pendingIncoming[id] || null);
      };
    });
    document.querySelectorAll("[data-voice-reject]").forEach(function (btn) {
      btn.onclick = function () {
        var id = parseInt(btn.getAttribute("data-voice-reject"), 10);
        rejectVoiceCall(id);
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
        if (voiceActiveId === id && voiceCall) {
          voiceCall.end();
          clearVoiceConsole("Call Ended");
        }
        api("/api/call-center/calls/" + id + "/cancel", { method: "POST", body: {} }).then(loadLive);
      };
    });
  }

  function showIncomingModal(call, iceServers) {
    if (!call || !call.id) return;
    if (voiceActiveId) return; // already in a call — queue via live list only
    pendingIncoming[call.id] = { call: call, ice_servers: iceServers || [] };
    var modal = document.getElementById("cc-incoming-modal");
    if (!modal) return;
    document.getElementById("cc-in-name").textContent = call.caller_name || "Citizen";
    document.getElementById("cc-in-id").textContent = call.user_id != null ? "GN-" + call.user_id : "—";
    document.getElementById("cc-in-phone").textContent = call.phone || "—";
    document.getElementById("cc-in-loc").textContent =
      (call.latitude != null ? call.latitude + ", " + call.longitude : "—") +
      (call.address ? " · " + call.address : "");
    var eids = (call.emergency_ids || []).join(", ");
    document.getElementById("cc-in-case").textContent = eids || "—";
    document.getElementById("cc-in-status").textContent = "Incoming voice call";
    modal.classList.add("is-open");
    modal.setAttribute("data-call-id", String(call.id));
  }

  function hideIncomingModal(callId) {
    var modal = document.getElementById("cc-incoming-modal");
    if (!modal) return;
    if (callId && modal.getAttribute("data-call-id") !== String(callId)) return;
    modal.classList.remove("is-open");
    modal.removeAttribute("data-call-id");
  }

  function setVoiceConsole(call, statusText) {
    var panel = document.getElementById("cc-voice-console");
    if (!panel) return;
    panel.classList.add("is-open");
    document.getElementById("cc-vc-name").textContent = (call && call.caller_name) || "Citizen";
    document.getElementById("cc-vc-meta").textContent =
      "GN-" + ((call && call.user_id) != null ? call.user_id : "—") +
      " · " + ((call && call.phone) || "—");
    document.getElementById("cc-vc-status").textContent = statusText || "Connecting...";
    document.getElementById("cc-vc-timer").textContent = "00:00";
  }

  function clearVoiceConsole(finalStatus) {
    var panel = document.getElementById("cc-voice-console");
    var statusEl = document.getElementById("cc-vc-status");
    if (statusEl && finalStatus) statusEl.textContent = finalStatus;
    if (voiceMediaTimer) {
      clearInterval(voiceMediaTimer);
      voiceMediaTimer = null;
    }
    voiceMediaStartedAt = null;
    if (voiceCall) {
      voiceCall.destroy();
      voiceCall = null;
    }
    voiceActiveId = null;
    setTimeout(function () {
      if (panel) panel.classList.remove("is-open");
    }, finalStatus ? 900 : 0);
  }

  function startVoiceMediaTimer() {
    voiceMediaStartedAt = Date.now();
    if (voiceMediaTimer) clearInterval(voiceMediaTimer);
    voiceMediaTimer = setInterval(function () {
      var el = document.getElementById("cc-vc-timer");
      if (!el || !voiceMediaStartedAt) return;
      el.textContent = fmtDuration((Date.now() - voiceMediaStartedAt) / 1000);
    }, 1000);
  }

  function acceptVoiceCall(callId, meta) {
    callId = Number(callId);
    if (voiceActiveId && Number(voiceActiveId) !== callId) {
      alert("You are already on another call.");
      return;
    }
    if (!window.GurmadVoiceCall) {
      alert("Voice client unavailable. Refresh the page.");
      return;
    }
    hideIncomingModal(callId);
    var call = (meta && meta.call) || { id: callId };
    setVoiceConsole(call, "Accepting...");
    voiceActiveId = callId;

    if (voiceCall) {
      voiceCall.destroy();
      voiceCall = null;
    }

    // Reuse the dashboard Socket.IO connection (avoid dual-socket hangups).
    ensureOpsSocket()
      .then(function (sock) {
        voiceCall = new GurmadVoiceCall({ role: "operator", socket: sock });
        voiceCall.setCallId(callId, (meta && meta.ice_servers) || []);

        voiceCall.on("connected", function () {
          setVoiceConsole(call, "Connected");
          startVoiceMediaTimer();
          api("/api/call-center/calls/" + callId).then(function (d) {
            if (d.success) openSession(d.call, d);
          });
        });
        voiceCall.on("media", function (p) {
          if (p && p.state === "connected" && !voiceMediaStartedAt) {
            setVoiceConsole(call, "Connected");
            startVoiceMediaTimer();
          }
        });
        voiceCall.on("reconnecting", function () {
          var el = document.getElementById("cc-vc-status");
          if (el) el.textContent = "Reconnecting...";
        });
        voiceCall.on("ended", function () {
          clearVoiceConsole("Call Ended");
          loadLive();
        });
        voiceCall.on("rejected", function () {
          clearVoiceConsole("Rejected");
          loadLive();
        });
        voiceCall.on("failed", function (p) {
          clearVoiceConsole((p && p.message) || "Connection Failed");
          loadLive();
        });
        voiceCall.on("busy", function (p) {
          clearVoiceConsole((p && p.message) || "Call taken");
          loadLive();
        });

        setVoiceConsole(call, "Requesting microphone...");
        return voiceCall.requestMic();
      })
      .then(function () {
        setVoiceConsole(call, "Connecting...");
        return voiceCall.join();
      })
      .then(function () {
        voiceCall.accept();
        setVoiceConsole(call, "Connecting audio...");
        return api("/api/call-center/calls/" + callId + "/answer", { method: "POST", body: {} });
      })
      .then(function (d) {
        if (d && d.success) openSession(d.call, d);
        loadLive();
      })
      .catch(function (err) {
        var msg = (err && err.message) || "Microphone Permission Required";
        if (/NotAllowed|Permission|denied/i.test(String(msg))) msg = "Microphone Permission Required";
        alert(msg);
        clearVoiceConsole(msg);
        try {
          ensureOpsSocket().then(function (sock) {
            sock.emit("call:reject", { call_id: callId });
          });
        } catch (e) {}
      });
  }

  function rejectVoiceCall(callId) {
    hideIncomingModal(callId);
    delete pendingIncoming[callId];
    if (voiceCall && voiceActiveId === callId) {
      voiceCall.reject();
      clearVoiceConsole("Rejected");
    } else if (window.io) {
      // Ensure socket rejects even without active peer session
      ensureOpsSocket().then(function (sock) {
        sock.emit("call:reject", { call_id: callId });
      }).catch(function () {
        api("/api/call-center/calls/" + callId + "/cancel", { method: "POST", body: {} });
      });
    }
    loadLive();
  }

  var opsSocket = null;
  function ensureOpsSocket() {
    if (opsSocket && opsSocket.connected) return Promise.resolve(opsSocket);
    if (typeof io === "undefined") return Promise.reject(new Error("Socket.IO missing"));
    return new Promise(function (resolve, reject) {
      opsSocket = io({ path: "/socket.io", transports: ["websocket", "polling"], withCredentials: true });
      var done = false;
      opsSocket.on("connect", function () {
        if (done) return;
        done = true;
        bindOpsSocket(opsSocket);
        resolve(opsSocket);
      });
      opsSocket.on("connect_error", function (err) {
        if (done) return;
        done = true;
        reject(err);
      });
    });
  }

  function bindOpsSocket(sock) {
    if (sock._ccBound) return;
    sock._ccBound = true;
    sock.on("call:incoming", function (payload) {
      var call = (payload && payload.call) || {};
      if (!call.id && payload && payload.call_id) call.id = payload.call_id;
      pendingIncoming[call.id] = { call: call, ice_servers: (payload && payload.ice_servers) || [] };
      showIncomingModal(call, payload && payload.ice_servers);
      loadLive();
    });
    sock.on("call:taken", function (payload) {
      if (!payload) return;
      hideIncomingModal(payload.call_id);
      delete pendingIncoming[payload.call_id];
      if (voiceActiveId !== payload.call_id) loadLive();
    });
    sock.on("call:reject", function (payload) {
      if (!payload) return;
      hideIncomingModal(payload.call_id);
      delete pendingIncoming[payload.call_id];
      if (voiceActiveId === payload.call_id) clearVoiceConsole("Rejected");
      loadLive();
    });
    sock.on("call:end", function (payload) {
      if (!payload) return;
      hideIncomingModal(payload.call_id);
      delete pendingIncoming[payload.call_id];
      if (voiceActiveId === payload.call_id) clearVoiceConsole("Call Ended");
      loadLive();
    });
  }

  function initVoiceUi() {
    var acceptBtn = document.getElementById("cc-in-accept");
    var rejectBtn = document.getElementById("cc-in-reject");
    var endBtn = document.getElementById("cc-vc-end");
    var muteBtn = document.getElementById("cc-vc-mute");
    if (acceptBtn) {
      acceptBtn.onclick = function () {
        var modal = document.getElementById("cc-incoming-modal");
        var id = modal ? parseInt(modal.getAttribute("data-call-id"), 10) : 0;
        if (id) acceptVoiceCall(id, pendingIncoming[id]);
      };
    }
    if (rejectBtn) {
      rejectBtn.onclick = function () {
        var modal = document.getElementById("cc-incoming-modal");
        var id = modal ? parseInt(modal.getAttribute("data-call-id"), 10) : 0;
        if (id) rejectVoiceCall(id);
      };
    }
    if (endBtn) {
      endBtn.onclick = function () {
        if (voiceCall) voiceCall.end();
        clearVoiceConsole("Call Ended");
        loadLive();
      };
    }
    if (muteBtn) {
      muteBtn.onclick = function () {
        if (!voiceCall) return;
        var muted = voiceCall.toggleMute();
        muteBtn.classList.toggle("is-muted", muted);
        muteBtn.textContent = muted ? "Unmute" : "Mute";
      };
    }
    ensureOpsSocket().catch(function () {
      console.warn("Call Center voice socket unavailable — falling back to live poll only");
    });
  }

  function nearestHtml(n) {
    if (!n) return '<p class="cc-ai-muted">Searching nearest responders…</p>';
    var parts = [];
    var labels = { hospital: "Hospital", police: "Police", fire: "Fire" };
    ["hospital", "police", "fire"].forEach(function (k) {
      var x = n[k];
      if (!x) {
        parts.push(
          '<div class="cc-nearest-card cc-nearest-empty">' +
            "<strong>" + labels[k] + "</strong>" +
            "<span>No station nearby</span>" +
          "</div>"
        );
        return;
      }
      var phone = x.phone ? String(x.phone).replace(/\s/g, "") : "";
      var pref = x.id != null ? String(x.id) : "";
      parts.push(
        '<div class="cc-nearest-card cc-nearest-' + k + '">' +
          "<strong>" + labels[k] + "</strong>" +
          '<span class="cc-nearest-name">' + esc(x.name) + "</span>" +
          (x.distance_km != null
            ? '<span class="cc-nearest-meta">' + x.distance_km + " km · ~" + x.eta_minutes + " min</span>"
            : "") +
          '<div class="cc-nearest-actions">' +
            (phone
              ? '<a class="cc-nearest-call" href="tel:' + esc(phone) + '">Call</a>'
              : '<span class="cc-ai-muted">No phone</span>') +
            '<button type="button" class="cc-nearest-alert" data-alert="' + k + '"' +
              (pref ? ' data-preferred-id="' + esc(pref) + '"' : "") +
              ">Alert / Friin</button>" +
          "</div>" +
        "</div>"
      );
    });
    return '<div class="cc-nearest">' + parts.join("") + "</div>";
  }

  function bindNearestActions() {
    document.querySelectorAll("[data-alert]").forEach(function (btn) {
      btn.onclick = function () {
        if (!activeCallId) return;
        var target = btn.getAttribute("data-alert");
        var preferredId = btn.getAttribute("data-preferred-id");
        var notesEl = document.getElementById("session-notes");
        var latEl = document.getElementById("loc-lat");
        var lngEl = document.getElementById("loc-lng");
        var addressEl = document.getElementById("loc-address");
        var lat = latEl ? parseFloat(latEl.value) : NaN;
        var lng = lngEl ? parseFloat(lngEl.value) : NaN;
        btn.disabled = true;
        btn.textContent = "Sending…";

        function sendAlert() {
          api("/api/call-center/calls/" + activeCallId + "/alert", {
            method: "POST",
            body: {
              target: target,
              preferred_id: preferredId ? parseInt(preferredId, 10) : undefined,
              notes: notesEl ? notesEl.value : ""
            }
          }).then(function (d) {
            btn.disabled = false;
            btn.textContent = "Alert / Friin";
            var msg = document.getElementById("session-msg");
            if (msg) msg.textContent = d.message || (d.success ? "Alert sent." : "Failed");
            if (!d.success) {
              alert(d.message || "Alert failed");
              return;
            }
            if (d.nearest) {
              var box = document.getElementById("session-nearest");
              if (box) {
                box.innerHTML = nearestHtml(d.nearest);
                bindNearestActions();
              }
            }
            if (d.call) {
              applyLocationToSessionUi(d.call, d.nearest || d.call.nearest, d.message);
            }
            loadLive();
          }).catch(function () {
            btn.disabled = false;
            btn.textContent = "Alert / Friin";
          });
        }

        // Always persist the pin on screen before friin so responders get the corrected GPS
        if (isFinite(lat) && isFinite(lng)) {
          api("/api/call-center/calls/" + activeCallId + "/location", {
            method: "POST",
            body: {
              latitude: lat,
              longitude: lng,
              address: addressEl ? addressEl.value : ""
            }
          }).then(function (locRes) {
            if (locRes && locRes.success && locRes.call) {
              applyLocationToSessionUi(locRes.call, locRes.nearest, "Location saved — sending alert…");
            }
            sendAlert();
          }).catch(function () {
            sendAlert();
          });
        } else {
          sendAlert();
        }
      };
    });
  }

  function typeButtonsHtml() {
    var opts = window.CC_TYPE_OPTIONS || [
      ["medical", "Medical"],
      ["fire", "Fire"],
      ["security", "Police"],
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
      '<header class="cc-sess-top">' +
        '<div>' +
          '<p class="cc-sess-kicker">Active call</p>' +
          "<h2>Call #" + call.id + " — " + esc(call.caller_name) + "</h2>" +
          '<p class="cc-sess-ask">Ask only: <strong>“What happened?”</strong> · Location is already on screen</p>' +
        "</div>" +
        '<div class="cc-sess-top-right">' +
          '<span class="cc-badge ' + esc(call.status) + '" id="session-status">' + esc(call.status) + "</span>" +
          '<span class="cc-sess-timer" id="session-duration">00:00</span>' +
          '<button type="button" class="btn-action btn-view" id="btn-back-live">← Live queue</button>' +
        "</div>" +
      "</header>" +

      '<div class="cc-sess-grid">' +
        '<section class="cc-sess-card">' +
          "<h3>1 · Caller</h3>" +
          '<div class="cc-caller-grid">' +
            '<div><span>Name</span><strong>' + esc(call.caller_name) + "</strong></div>" +
            '<div><span>Phone</span><strong><a href="tel:' + esc(String(call.phone || "").replace(/\s/g, "")) + '">' + esc(call.phone) + "</a></strong></div>" +
            '<div><span>Citizen ID</span><strong>GN-' + esc(call.user_id) + "</strong></div>" +
            '<div class="cc-caller-wide"><span>GPS</span><strong id="session-gps">' + esc(call.latitude) + ", " + esc(call.longitude) + "</strong></div>" +
            '<div class="cc-caller-wide"><span>Address</span><strong id="session-address">' + esc(call.address || "—") + "</strong></div>" +
          "</div>" +
          '<label class="cc-notes-label" for="session-notes">What happened? (operator notes)</label>' +
          '<textarea id="session-notes" rows="3" placeholder="Type what the citizen describes…">' +
            esc(call.notes || "") + "</textarea>" +
          '<details class="cc-history-fold">' +
            "<summary>Previous emergencies</summary>" +
            '<div id="session-history">' + historyHtml(history) + "</div>" +
          "</details>" +
        "</section>" +

        '<section class="cc-sess-card">' +
          "<h3>2 · Location &amp; map</h3>" +
          '<p class="cc-loc-hint">Click or drag the pin if they moved / will wait elsewhere.</p>' +
          '<div id="session-map" class="cc-map" title="Click map to set caller location"></div>' +
          '<div class="cc-loc-grid">' +
            '<label>Latitude <input id="loc-lat" type="number" step="any" value="' + esc(call.latitude) + '"></label>' +
            '<label>Longitude <input id="loc-lng" type="number" step="any" value="' + esc(call.longitude) + '"></label>' +
            '<label class="cc-loc-addr">Address <input id="loc-address" type="text" value="' + esc(call.address || "") + '"></label>' +
          "</div>" +
          '<div class="cc-loc-actions">' +
            '<button type="button" class="btn-action btn-view" id="btn-save-location">Save location</button>' +
            '<span id="loc-msg" class="cc-loc-msg"></span>' +
          "</div>" +
        "</section>" +

        '<section class="cc-sess-card cc-sess-span">' +
          "<h3>3 · Nearest help</h3>" +
          '<div id="session-nearest">' + nearestHtml(call.nearest) + "</div>" +
        "</section>" +

        '<section class="cc-sess-card cc-sess-span cc-ai-panel" id="ai-panel">' +
          '<div class="cc-ai-panel-head">' +
            "<h3>AI suggestion (optional)</h3>" +
            '<span id="ai-panel-status" class="cc-ai-muted">You still approve before dispatch</span>' +
          "</div>" +
          '<div id="ai-panel-body"></div>' +
          '<div class="cc-ai-actions">' +
            '<button type="button" class="btn-action" id="btn-ai-analyze">Analyze notes</button>' +
            '<button type="button" class="btn-action btn-accept" id="btn-ai-approve">Approve AI</button>' +
            '<button type="button" class="btn-action btn-reject" id="btn-ai-reject">Reject</button>' +
            '<button type="button" class="btn-action btn-view" id="btn-ai-manual">Manual</button>' +
          "</div>" +
        "</section>" +
      "</div>" +

      '<footer class="cc-dispatch-bar">' +
        '<div class="cc-dispatch-bar-inner">' +
          "<h3>4 · Dispatch</h3>" +
          '<p class="cc-dispatch-help">Select team(s), then Dispatch. Multi-select OK.</p>' +
          '<div class="cc-type-grid" id="type-grid">' + typeButtonsHtml() + "</div>" +
          '<div class="cc-dispatch-row">' +
            '<button type="button" class="btn-action btn-accept cc-btn-primary" id="btn-dispatch">Dispatch now</button>' +
            '<button type="button" class="btn-action btn-view" id="btn-send-gps">Send GPS only</button>' +
            '<a class="btn-action btn-call" id="btn-call-hospital" href="#">Call Hospital</a>' +
            '<a class="btn-action btn-call" id="btn-call-police" href="#">Call Police</a>' +
            '<a class="btn-action btn-call" id="btn-call-fire" href="#">Call Fire</a>' +
            '<button type="button" class="btn-action" id="btn-transfer">Transfer</button>' +
            '<button type="button" class="btn-action btn-arrived" id="btn-complete">Complete</button>' +
            '<button type="button" class="btn-action btn-reject" id="btn-cancel-session">Cancel</button>' +
          "</div>" +
          '<p id="session-msg" class="cc-session-msg"></p>' +
        "</div>" +
      "</footer>";

    bindSession(call);
    initSessionMap(call);
    refreshNearest(call.id);
    bindNearestActions();
    startDurationTimer();
    renderAiPanel(ai.panel || (ai.success && ai.panel) || null);
    if (ai.panel) applySuggestedTypes(ai.panel);
    if (!ai.panel && !ai.analysis) {
      runAiAnalyze();
    }
  }

  function bindSession(call) {
    var backBtn = document.getElementById("btn-back-live");
    if (backBtn) {
      backBtn.onclick = function () {
        showPanel("live");
      };
    }

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

    var btnLoc = document.getElementById("btn-save-location");
    if (btnLoc) {
      btnLoc.onclick = function () {
        saveSessionLocation();
      };
    }
  }

  function applyLocationToSessionUi(call, nearest, message) {
    var gpsEl = document.getElementById("session-gps");
    var addrEl = document.getElementById("session-address");
    var latEl = document.getElementById("loc-lat");
    var lngEl = document.getElementById("loc-lng");
    var addressEl = document.getElementById("loc-address");
    var notesEl = document.getElementById("session-notes");
    var msgEl = document.getElementById("loc-msg");
    if (gpsEl) gpsEl.textContent = call.latitude + ", " + call.longitude;
    if (addrEl) addrEl.textContent = call.address || "";
    if (latEl) latEl.value = call.latitude;
    if (lngEl) lngEl.value = call.longitude;
    if (addressEl && call.address != null) addressEl.value = call.address;
    if (notesEl && call.notes != null) notesEl.value = call.notes;
    if (msgEl) msgEl.textContent = message || "Location updated — nearest refreshed";
    var n = nearest || call.nearest || {};
    var nearestBox = document.getElementById("session-nearest");
    if (nearestBox) nearestBox.innerHTML = nearestHtml(n);
    var bh = document.getElementById("btn-call-hospital");
    var bp = document.getElementById("btn-call-police");
    var bf = document.getElementById("btn-call-fire");
    if (bh && n.hospital && n.hospital.phone) bh.href = "tel:" + String(n.hospital.phone).replace(/\s/g, "");
    if (bp && n.police && n.police.phone) bp.href = "tel:" + String(n.police.phone).replace(/\s/g, "");
    if (bf && n.fire && n.fire.phone) bf.href = "tel:" + String(n.fire.phone).replace(/\s/g, "");
    updateSessionMapPin(call.latitude, call.longitude, call.address, call.caller_name);
  }

  function saveSessionLocation(overrideLat, overrideLng) {
    if (!activeCallId) return;
    var latEl = document.getElementById("loc-lat");
    var lngEl = document.getElementById("loc-lng");
    var addressEl = document.getElementById("loc-address");
    var msgEl = document.getElementById("loc-msg");
    var lat = overrideLat != null ? overrideLat : (latEl ? parseFloat(latEl.value) : NaN);
    var lng = overrideLng != null ? overrideLng : (lngEl ? parseFloat(lngEl.value) : NaN);
    if (!isFinite(lat) || !isFinite(lng)) {
      alert("Enter valid latitude and longitude.");
      return;
    }
    if (msgEl) msgEl.textContent = "Saving…";
    api("/api/call-center/calls/" + activeCallId + "/location", {
      method: "POST",
      body: {
        latitude: lat,
        longitude: lng,
        address: addressEl ? addressEl.value : ""
      }
    }).then(function (d) {
      if (!d.success) {
        if (msgEl) msgEl.textContent = "";
        alert(d.message || "Failed to update location");
        return;
      }
      applyLocationToSessionUi(d.call, d.nearest, d.message);
      loadLive();
    });
  }

  function fillLocInputsFromMap(lat, lng) {
    var latEl = document.getElementById("loc-lat");
    var lngEl = document.getElementById("loc-lng");
    if (latEl) latEl.value = Number(lat).toFixed(6);
    if (lngEl) lngEl.value = Number(lng).toFixed(6);
  }

  function refreshNearest(id) {
    api("/api/call-center/calls/" + id + "/nearest").then(function (d) {
      if (d.success) {
        document.getElementById("session-nearest").innerHTML = nearestHtml(d.nearest);
        bindNearestActions();
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

  function updateSessionMapPin(lat, lng, address, name) {
    if (lat == null || lng == null) return;
    if (sessionMapKind === "google" && sessionMap && sessionMarker) {
      var pos = { lat: Number(lat), lng: Number(lng) };
      sessionMarker.setPosition(pos);
      sessionMap.panTo(pos);
      return;
    }
    if (sessionMapKind === "leaflet" && sessionMap && sessionMarker) {
      sessionMarker.setLatLng([lat, lng]);
      sessionMap.panTo([lat, lng]);
      if (sessionMarker.setPopupContent) {
        sessionMarker.setPopupContent(esc(name || "Caller") + "<br>" + esc(address || ""));
      }
    }
  }

  function initSessionMap(call) {
    if (call.latitude == null || call.longitude == null) return;
    var el = document.getElementById("session-map");
    if (!el) return;
    el.innerHTML = "";
    sessionMarker = null;
    sessionMapKind = null;
    if (sessionMap) {
      try {
        if (sessionMap.remove) sessionMap.remove();
      } catch (e) {}
      sessionMap = null;
    }

    if (window.google && window.google.maps) {
      sessionMapKind = "google";
      sessionMap = new google.maps.Map(el, {
        center: { lat: call.latitude, lng: call.longitude },
        zoom: 15,
        mapTypeControl: true,
        streetViewControl: false,
        mapTypeId: "roadmap",
        gestureHandling: "greedy",
      });
      sessionMarker = new google.maps.Marker({
        position: { lat: call.latitude, lng: call.longitude },
        map: sessionMap,
        title: call.caller_name || "Caller",
        draggable: true,
      });
      var info = new google.maps.InfoWindow({
        content: esc(call.caller_name) + "<br>" + esc(call.address),
      });
      info.open(sessionMap, sessionMarker);
      sessionMap.addListener("click", function (ev) {
        if (!ev || !ev.latLng) return;
        var lat = ev.latLng.lat();
        var lng = ev.latLng.lng();
        fillLocInputsFromMap(lat, lng);
        sessionMarker.setPosition(ev.latLng);
      });
      sessionMarker.addListener("dragend", function (ev) {
        if (!ev || !ev.latLng) return;
        fillLocInputsFromMap(ev.latLng.lat(), ev.latLng.lng());
      });
    setTimeout(function () {
      google.maps.event.trigger(sessionMap, "resize");
      if (sessionMarker) sessionMap.panTo(sessionMarker.getPosition());
    }, 280);
      return;
    }

    if (typeof L === "undefined") return;
    sessionMapKind = "leaflet";
    sessionMap = L.map("session-map").setView([call.latitude, call.longitude], 15);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap",
    }).addTo(sessionMap);
    sessionMarker = L.marker([call.latitude, call.longitude], { draggable: true })
      .addTo(sessionMap)
      .bindPopup(esc(call.caller_name) + "<br>" + esc(call.address))
      .openPopup();
    sessionMap.on("click", function (ev) {
      fillLocInputsFromMap(ev.latlng.lat, ev.latlng.lng);
      sessionMarker.setLatLng(ev.latlng);
    });
    sessionMarker.on("dragend", function () {
      var ll = sessionMarker.getLatLng();
      fillLocInputsFromMap(ll.lat, ll.lng);
    });
    setTimeout(function () {
      sessionMap.invalidateSize();
    }, 280);
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

  window.addEventListener("beforeunload", function () {
    if (voiceCall) {
      try { voiceCall.end(); } catch (e) {}
    }
  });

  initVoiceUi();
  loadLive();
  scheduleLivePoll();
})();
