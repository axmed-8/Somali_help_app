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

  function fmtReceived(iso) {
    if (!iso) return "—";
    try {
      var d = new Date(String(iso).replace(" ", "T"));
      if (isNaN(d.getTime())) return String(iso);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch (e) {
      return String(iso);
    }
  }

  function queuePriority(c, elapsedSec) {
    var st = (c && c.status) || "";
    if (st === "ringing") {
      if (elapsedSec >= 90) return "high";
      if (elapsedSec >= 45) return "med";
      return "high";
    }
    if (st === "accepted" || st === "connecting" || st === "connected" || st === "answered" || st === "in_progress") {
      return "med";
    }
    return "low";
  }

  function priorityLabel(level) {
    if (level === "high") return "Urgent";
    if (level === "med") return "Active";
    return "Tracked";
  }

  function renderStats(stats, online) {
    var onlineN = (stats && stats.operators_online) || (online && online.length) || 0;
    var incoming = (stats && stats.incoming_calls) || 0;
    var waiting = (stats && stats.calls_waiting) || 0;
    var progress = (stats && stats.calls_in_progress) || 0;
    var resolved = (stats && stats.resolved_today) || 0;
    var avg = (stats && stats.avg_response_minutes) || 0;

    var set = function (id, val) {
      var el = document.getElementById(id);
      if (el) el.textContent = val;
    };
    set("st-online", onlineN);
    set("st-incoming", incoming);
    set("st-waiting", waiting);
    set("st-progress", progress);
    set("st-resolved", resolved);
    set("st-avg", avg);
    var op = document.getElementById("op-online");
    if (op) op.textContent = "Operators: " + onlineN;

    var strip = document.getElementById("cc-stats");
    if (strip) {
      strip.classList.toggle("cc-kpi-hot", incoming > 0 || waiting > 0);
      var waitCard = document.getElementById("kpi-waiting");
      if (waitCard) waitCard.classList.toggle("is-alert", waiting > 0);
      var inCard = document.getElementById("kpi-incoming");
      if (inCard) inCard.classList.toggle("is-alert", incoming > 0);
    }
  }

  function renderIncoming(calls) {
    var box = document.getElementById("incoming-list");
    if (!box) return;
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
    // Ringing first (longest wait first); then active; then dispatched
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
      var ta = a.start_time || "";
      var tb = b.start_time || "";
      if (ta !== tb) return ta < tb ? -1 : 1;
      return (a.id || 0) - (b.id || 0);
    });
    if (!ringing.length) {
      box.innerHTML =
        '<div class="empty-alerts cc-empty">' +
        "<strong>No active emergency calls</strong>" +
        "<p>New emergency calls will appear here.</p>" +
        "</div>";
      return;
    }
    box.innerHTML = "";
    ringing.forEach(function (c) {
      var card = document.createElement("article");
      var start = c.start_time ? new Date(String(c.start_time).replace(" ", "T")) : new Date();
      var elapsed = Math.floor((Date.now() - start.getTime()) / 1000);
      if (isNaN(elapsed) || elapsed < 0) elapsed = 0;
      var isRinging = c.status === "ringing";
      var isVoice = !!c.voice_mode || isRinging;
      var eids = (c.emergency_ids || []).join(", ") || "—";
      var prio = queuePriority(c, elapsed);
      var prioClass = prio === "high" ? "cc-prio-high" : prio === "med" ? "cc-prio-med" : "cc-prio-low";
      var selected = activeCallId != null && String(activeCallId) === String(c.id);
      card.className =
        "cc-card cc-q-card " +
        (c.status || "") +
        (isRinging ? " cc-q-ringing" : "") +
        (selected ? " is-selected" : "");
      card.setAttribute("data-call-id", c.id);
      var callerName = c.caller_name || "Unknown caller";
      var phone = c.phone || "No phone";
      var citizenId = c.user_id != null ? "GN-" + c.user_id : "—";
      var gps =
        c.latitude != null && c.longitude != null
          ? Number(c.latitude).toFixed(5) + ", " + Number(c.longitude).toFixed(5)
          : "—";
      var addr = c.address || "Address unknown";

      card.innerHTML =
        '<div class="cc-q-top">' +
          '<div class="cc-card-title">' +
            (isRinging ? '<span class="cc-pulse" aria-hidden="true"></span>' : "") +
            "<strong>Call #" +
            esc(c.id) +
            "</strong>" +
            (isVoice ? ' <span class="cc-voice-tag">Voice</span>' : "") +
          "</div>" +
          '<span class="cc-badge ' +
          esc(c.status || "") +
          '">' +
          esc((c.status || "live").replace(/_/g, " ")) +
          "</span>" +
        "</div>" +
        '<div class="cc-q-fields">' +
          '<div class="cc-q-field"><span>Priority</span><strong class="' +
          prioClass +
          '">' +
          esc(priorityLabel(prio)) +
          "</strong></div>" +
          '<div class="cc-q-field"><span>Received</span><strong>' +
          esc(fmtReceived(c.start_time)) +
          "</strong></div>" +
          '<div class="cc-q-field"><span>Wait</span><strong class="cc-wait">' +
          esc(fmtDuration(elapsed)) +
          "</strong></div>" +
          '<div class="cc-q-field"><span>Case</span><strong>' +
          esc(eids) +
          "</strong></div>" +
        "</div>" +
        '<div class="cc-q-citizen">' +
          "<strong>" +
          esc(callerName) +
          "</strong>" +
          "<em>" +
          esc(phone) +
          " · " +
          esc(citizenId) +
          "</em>" +
        "</div>" +
        '<div class="cc-q-loc">' +
          '<span class="cc-loc-label">📍 Location</span>' +
          '<strong class="cc-gps">' +
          esc(gps) +
          "</strong>" +
          "<em class=\"cc-addr\">" +
          esc(addr) +
          "</em>" +
        "</div>" +
        '<div class="cc-card-actions cc-q-actions">' +
          (isRinging
            ? '<button type="button" class="btn-action btn-accept" data-voice-accept="' +
              c.id +
              '">Accept</button>' +
              '<button type="button" class="btn-action btn-reject" data-voice-reject="' +
              c.id +
              '">Reject</button>'
            : '<button type="button" class="btn-action btn-view" data-open="' +
              c.id +
              '">Open</button>') +
          '<button type="button" class="btn-action btn-reject" data-cancel="' +
          c.id +
          '">Cancel</button>' +
        "</div>";
      box.appendChild(card);
    });
    bindIncoming();
  }

  function markSelectedQueueCard(callId) {
    document.querySelectorAll("#incoming-list .cc-q-card, #incoming-list .cc-card").forEach(function (card) {
      var id = card.getAttribute("data-call-id");
      card.classList.toggle("is-selected", callId != null && String(id) === String(callId));
    });
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
    var ending = voiceCall;
    voiceCall = null;
    voiceActiveId = null;
    if (ending) {
      try { ending.hangup(false); } catch (e) {}
      // Keep shared ops socket alive — destroy() would only disconnect if it owns the socket.
      ending.socket = null;
      try { ending.destroy(); } catch (e2) {}
    }
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

  function acceptDeskOnly(callId) {
    callId = Number(callId);
    hideIncomingModal(callId);
    setVoiceConsole({ id: callId }, "Opening desk session...");
    return api("/api/call-center/calls/" + callId + "/answer", { method: "POST", body: {} })
      .then(function (d) {
        if (d && d.success) {
          try {
            openSession(d.call, d);
          } catch (e) {
            console.warn("openSession", e);
          }
          setVoiceConsole(d.call || { id: callId }, "Desk session active (no voice)");
        } else {
          alert((d && d.message) || "Could not answer call");
          clearVoiceConsole("Answer failed");
        }
        loadLive();
      })
      .catch(function () {
        alert("Could not answer call");
        clearVoiceConsole("Answer failed");
        loadLive();
      });
  }

  function acceptVoiceCall(callId, meta) {
    callId = Number(callId);
    var insecure =
      window.GurmadVoiceCall &&
      typeof GurmadVoiceCall.isSecureContext === "function" &&
      !GurmadVoiceCall.isSecureContext();
    if (insecure || !window.GurmadVoiceCall) {
      // Presentation / LAN HTTP: open Operator Workspace without WebRTC
      return acceptDeskOnly(callId);
    }
    if (voiceActiveId && Number(voiceActiveId) !== callId) {
      alert("You are already on another call.");
      return;
    }
    hideIncomingModal(callId);
    var call = (meta && meta.call) || { id: callId };
    setVoiceConsole(call, "Accepting...");
    voiceActiveId = callId;
    var webrtcAccepted = false;

    if (voiceCall) {
      try { voiceCall.hangup(false); } catch (e) {}
      voiceCall.socket = null;
      try { voiceCall.destroy(); } catch (e2) {}
      voiceCall = null;
    }

    // Reuse the dashboard Socket.IO connection (avoid dual-socket hangups).
    ensureOpsSocket()
      .then(function (sock) {
        voiceCall = new GurmadVoiceCall({ role: "operator", socket: sock });
        voiceCall.setCallId(callId, (meta && meta.ice_servers) || []);
        // Accept is a user gesture — unlock remote audio playback now.
        voiceCall.unlockAudio();

        voiceCall.on("connected", function () {
          if (Number(voiceActiveId) !== callId) return;
          setVoiceConsole(call, "Connected");
          startVoiceMediaTimer();
          api("/api/call-center/calls/" + callId).then(function (d) {
            if (d.success) {
              try { openSession(d.call, d); } catch (e) { console.warn(e); }
            }
          }).catch(function () {});
        });
        voiceCall.on("media", function (p) {
          if (Number(voiceActiveId) !== callId) return;
          if (p && p.state === "connected" && !voiceMediaStartedAt) {
            setVoiceConsole(call, "Connected");
            startVoiceMediaTimer();
          }
        });
        voiceCall.on("reconnecting", function () {
          if (Number(voiceActiveId) !== callId) return;
          var el = document.getElementById("cc-vc-status");
          if (el) el.textContent = "Reconnecting...";
        });
        voiceCall.on("ended", function () {
          if (Number(voiceActiveId) !== callId && voiceActiveId != null) return;
          clearVoiceConsole("Call Ended");
          loadLive();
        });
        voiceCall.on("rejected", function () {
          if (Number(voiceActiveId) !== callId && voiceActiveId != null) return;
          clearVoiceConsole("Rejected");
          loadLive();
        });
        voiceCall.on("failed", function (p) {
          if (Number(voiceActiveId) !== callId && voiceActiveId != null) return;
          var msg = (p && p.message) || "Connection Failed";
          if (p && p.hint) msg += " — " + p.hint;
          clearVoiceConsole(msg);
          loadLive();
        });
        voiceCall.on("busy", function (p) {
          if (Number(voiceActiveId) !== callId && voiceActiveId != null) return;
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
        // WebRTC accept first — conversation must not depend on REST/AI UI.
        voiceCall.accept();
        webrtcAccepted = true;
        setVoiceConsole(call, "Connecting audio...");
        // Session/AI UI is best-effort and must NEVER reject/hang up the live call.
        return api("/api/call-center/calls/" + callId + "/answer", { method: "POST", body: {} })
          .then(function (d) {
            if (d && d.success) {
              try { openSession(d.call, d); } catch (e) { console.warn("openSession", e); }
            }
            loadLive();
          })
          .catch(function (err) {
            console.warn("Call session UI update failed (voice call still active)", err);
            loadLive();
          });
      })
      .catch(function (err) {
        // Only setup failures (mic/join) before WebRTC accept should reject the call.
        if (webrtcAccepted) {
          console.warn("Post-accept setup warning (call kept alive)", err);
          return;
        }
        try {
          console.error("[CallCenterVoice] accept setup failed", err && err.name, err && err.message, err);
        } catch (e) {}
        var msg =
          (window.GurmadVoiceCall && GurmadVoiceCall.describeMicError
            ? GurmadVoiceCall.describeMicError(err)
            : null) ||
          (err && err.message) ||
          "Voice call failed";
        alert(msg);
        clearVoiceConsole(msg);
        try {
          ensureOpsSocket().then(function (sock) {
            sock.emit("call:reject", { call_id: callId });
          });
        } catch (e2) {}
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
      // GurmadVoiceCall also handles call:end → ended → clearVoiceConsole.
      // Only clear here if voice helper is already gone (avoid double destroy races).
      if (Number(voiceActiveId) === Number(payload.call_id) && !voiceCall) {
        clearVoiceConsole("Call Ended");
      } else if (Number(voiceActiveId) === Number(payload.call_id) && voiceCall) {
        // Let voice helper emit ended; still refresh queue.
      }
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
        var ending = voiceCall;
        if (ending) {
          try { ending.end(); } catch (e) {}
        } else {
          clearVoiceConsole("Call Ended");
        }
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
      '<ul class="cc-history-list cc-ws-history">' +
      history.slice(0, 5).map(function (h) {
        return (
          '<li class="cc-ws-history-item">' +
          '<strong>#' +
          esc(h.id) +
          "</strong>" +
          '<span class="cc-ws-history-type">' +
          esc((h.type || "—").toString().replace(/_/g, " ")) +
          "</span>" +
          '<span class="cc-badge ' +
          esc(h.status || "") +
          '">' +
          esc((h.status || "—").toString().replace(/_/g, " ")) +
          "</span>" +
          (h.timestamp
            ? '<em class="cc-ws-history-time">' + esc(h.timestamp) + "</em>"
            : "") +
          "</li>"
        );
      }).join("") +
      "</ul>"
    );
  }

  function wsField(label, valueHtml, wide) {
    return (
      '<div class="cc-ws-field' +
      (wide ? " cc-ws-field-wide" : "") +
      '"><span>' +
      esc(label) +
      "</span><strong>" +
      valueHtml +
      "</strong></div>"
    );
  }

  function openSession(call, payload) {
    activeCallId = call.id;
    markSelectedQueueCard(activeCallId);
    selectedTypes = {};
    latestAiPanel = null;
    callStartedAt = call.start_time ? new Date(call.start_time.replace(" ", "T")) : new Date();
    showPanel("active");
    var history = (payload && payload.emergency_history) || call.emergency_history || [];
    var ai = (payload && payload.ai) || {};
    var el = document.getElementById("active-session");
    el.classList.remove("empty");
    el.classList.add("cc-ws");
    var phoneRaw = String(call.phone || "").replace(/\s/g, "");
    var phoneHtml = phoneRaw
      ? '<a href="tel:' + esc(phoneRaw) + '">' + esc(call.phone) + "</a>"
      : esc(call.phone || "—");
    var gpsTxt =
      call.latitude != null && call.longitude != null
        ? Number(call.latitude).toFixed(5) + ", " + Number(call.longitude).toFixed(5)
        : "—";
    var statusTxt = (call.status || "live").toString().replace(/_/g, " ");

    el.innerHTML =
      '<header class="cc-sess-top cc-ws-header">' +
        '<div class="cc-ws-header-main">' +
          '<p class="cc-sess-kicker">Call Information</p>' +
          "<h2>Call #" +
          esc(call.id) +
          " — " +
          esc(call.caller_name || "Citizen") +
          "</h2>" +
          '<p class="cc-sess-ask">Ask only: <strong>“What happened?”</strong> · Location is already on screen</p>' +
        "</div>" +
        '<div class="cc-sess-top-right cc-ws-header-meta">' +
          '<span class="cc-badge ' +
          esc(call.status || "") +
          '" id="session-status">' +
          esc(statusTxt) +
          "</span>" +
          '<span class="cc-sess-timer" id="session-duration" title="Call duration">00:00</span>' +
          '<button type="button" class="btn-action btn-view" id="btn-back-live">← Live queue</button>' +
        "</div>" +
      "</header>" +

      '<div class="cc-sess-grid cc-ws-grid">' +
        '<section class="cc-sess-card cc-ws-card cc-ws-caller">' +
          '<h3 class="cc-ws-section-title">Caller</h3>' +
          '<div class="cc-caller-grid cc-ws-fields">' +
            wsField("Name", esc(call.caller_name || "—")) +
            wsField("Phone", phoneHtml) +
            wsField("Citizen ID", "GN-" + esc(call.user_id != null ? call.user_id : "—")) +
            wsField("GPS", '<span id="session-gps">' + esc(gpsTxt) + "</span>", true) +
            wsField("Address", '<span id="session-address">' + esc(call.address || "—") + "</span>", true) +
          "</div>" +
          '<div class="cc-ws-notes">' +
            '<label class="cc-notes-label" for="session-notes">Operator Notes — What happened?</label>' +
            '<textarea id="session-notes" rows="3" placeholder="Type what the citizen describes…">' +
            esc(call.notes || "") +
            "</textarea>" +
          "</div>" +
          '<details class="cc-history-fold cc-ws-history-fold">' +
            "<summary>Previous emergencies</summary>" +
            '<div id="session-history">' +
            historyHtml(history) +
            "</div>" +
          "</details>" +
        "</section>" +

        '<section class="cc-sess-card cc-ws-card cc-ws-location">' +
          '<h3 class="cc-ws-section-title">Location</h3>' +
          '<p class="cc-loc-hint">Click or drag the pin if they moved / will wait elsewhere.</p>' +
          '<div id="session-map" class="cc-map" title="Click map to set caller location"></div>' +
          '<div class="cc-loc-grid cc-ws-loc-grid">' +
            '<label>Latitude <input id="loc-lat" type="number" step="any" value="' +
            esc(call.latitude) +
            '"></label>' +
            '<label>Longitude <input id="loc-lng" type="number" step="any" value="' +
            esc(call.longitude) +
            '"></label>' +
            '<label class="cc-loc-addr">Address <input id="loc-address" type="text" value="' +
            esc(call.address || "") +
            '"></label>' +
          "</div>" +
          '<div class="cc-loc-actions">' +
            '<button type="button" class="btn-action btn-view" id="btn-save-location">Save location</button>' +
            '<span id="loc-msg" class="cc-loc-msg"></span>' +
          "</div>" +
        "</section>" +

        '<section class="cc-sess-card cc-sess-span cc-ws-card">' +
          '<h3 class="cc-ws-section-title">Emergency Details · Nearest Help</h3>' +
          '<div id="session-nearest">' +
          nearestHtml(call.nearest) +
          "</div>" +
        "</section>" +

        '<section class="cc-sess-card cc-sess-span cc-ai-panel cc-ws-card" id="ai-panel">' +
          '<div class="cc-ai-panel-head">' +
            "<h3>AI Assistance</h3>" +
            '<span class="cc-ai-rec-badge">AI Recommendation</span>' +
            '<span id="ai-panel-status" class="cc-ai-muted">Human approval required before dispatch</span>' +
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
          "<h3>Dispatch</h3>" +
          '<p class="cc-dispatch-help">Select team(s), then Dispatch. Multi-select OK.</p>' +
          '<div class="cc-type-grid" id="type-grid">' +
          typeButtonsHtml() +
          "</div>" +
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

  function responderLine(label, obj, fallbackEta) {
    if (!obj) {
      return '<div class="cc-ai-responder"><strong>' + esc(label) + ":</strong> —</div>";
    }
    var name = obj.name || obj.station_name || "Unit";
    var eta = obj.eta_minutes != null ? obj.eta_minutes : fallbackEta;
    var meta = [];
    if (obj.distance_km != null) meta.push(obj.distance_km + " km");
    if (eta != null) meta.push("ETA ~" + eta + " min");
    if (obj.emergency_capacity != null) meta.push("capacity " + obj.emergency_capacity);
    if (obj.active_load != null) meta.push("load " + obj.active_load);
    if (obj.ambulance_available === true) meta.push("ambulance available");
    else if (obj.ambulance_available === false && label === "Hospital") meta.push("no ambulance flag");
    return (
      '<div class="cc-ai-responder"><strong>' +
      esc(label) +
      ":</strong> " +
      esc(name) +
      (meta.length ? " · " + esc(meta.join(" · ")) : "") +
      "</div>"
    );
  }

  function renderAiPanel(panel) {
    var box = document.getElementById("ai-panel-body");
    if (!box) return;
    latestAiPanel = panel || null;
    if (!panel || (!panel.recommendation_id && !panel.category && !panel.emergency_type)) {
      box.innerHTML =
        '<p class="cc-ai-muted">Waiting for AI analysis… Enter notes (“What happened?”) then Analyze.</p>';
      return;
    }
    var conf =
      panel.confidence_pct != null
        ? panel.confidence_pct
        : Math.round((panel.confidence || 0) * 100);
    var banners = "";
    if (panel.insufficient_data) {
      banners +=
        '<p class="cc-ai-banner cc-ai-banner-warn" data-cc-ai="insufficient">Insufficient data / Human review required</p>';
    } else if (panel.low_confidence) {
      banners +=
        '<p class="cc-ai-banner cc-ai-banner-caution" data-cc-ai="low-confidence">Low confidence — Human review required</p>';
    }
    var suggested = panel.suggested_dispatch_types || [];
    box.innerHTML =
      banners +
      '<div class="cc-ai-grid">' +
        "<div><span>Type</span><strong>" +
        esc(panel.emergency_type || panel.category || "—") +
        "</strong></div>" +
        "<div><span>Severity</span><strong class='cc-ai-pri-" +
        esc(panel.severity || panel.priority || "medium") +
        "'>" +
        esc(panel.severity || panel.priority || "—") +
        "</strong></div>" +
        "<div><span>Priority</span><strong class='cc-ai-pri-" +
        esc(panel.priority || "medium") +
        "'>" +
        esc(panel.priority || "—") +
        "</strong></div>" +
        "<div><span>Risk</span><strong>" +
        esc(panel.risk || "—") +
        "</strong></div>" +
        "<div><span>Confidence</span><strong>" +
        esc(conf) +
        "%</strong></div>" +
      "</div>" +
      "<p><strong>Services:</strong> " +
      esc(
        typeof panel.required_services === "string"
          ? panel.required_services
          : (panel.required_services || []).join(", ") || "—"
      ) +
      "</p>" +
      "<p><strong>Suggested dispatch:</strong> " +
      esc(suggested.length ? suggested.join(", ") : "—") +
      "</p>" +
      "<p><strong>Summary:</strong> " +
      esc(panel.summary || "—") +
      "</p>" +
      '<div class="cc-ai-responders">' +
      responderLine("Hospital", panel.recommended_hospital, panel.eta_minutes) +
      responderLine("Police", panel.recommended_police, null) +
      responderLine("Fire", panel.recommended_fire, null) +
      "</div>" +
      (panel.ranking_why
        ? '<p class="cc-ai-rank"><strong>Why ranked #1:</strong> ' +
          esc(panel.ranking_why) +
          "</p>"
        : "") +
      "<p><strong>Reason:</strong> " +
      esc(panel.reason || panel.dispatch_reason || "—") +
      "</p>" +
      (panel.history_note
        ? '<p class="cc-ai-muted">' + esc(panel.history_note) + "</p>"
        : "") +
      '<p class="cc-ai-status">Status: <strong id="ai-rec-status">' +
      esc(panel.status || "pending") +
      "</strong></p>" +
      (String(panel.status || "").toLowerCase().indexOf("approv") === 0
        ? '<p class="cc-ai-banner cc-ai-banner-ok" data-cc-ai="approve-lock">Recommendation accepted — press Dispatch now to send</p>'
        : "");
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
        var warn =
          (latestAiPanel.low_confidence || latestAiPanel.insufficient_data)
            ? " Recommendation confidence is low — review carefully before Dispatch now."
            : "";
        if (
          !confirm(
            "Accept AI recommendation and preselect suggested teams?" +
              warn +
              "\n\nAI does not dispatch — you must press Dispatch now to send."
          )
        ) {
          return;
        }
        var notes = document.getElementById("session-notes").value;
        api("/api/call-center/calls/" + activeCallId + "/ai/decision", {
          method: "POST",
          body: {
            decision: "approve",
            recommendation_id: latestAiPanel.recommendation_id,
            notes: notes
          }
        }).then(function (d) {
          document.getElementById("session-msg").textContent =
            d.message ||
            "Recommendation accepted — press Dispatch now to send.";
          if (d.panel) renderAiPanel(d.panel);
          if (d.success) {
            applySuggestedTypes(d.panel || { suggested_dispatch_types: d.suggested_dispatch_types });
            var grid = document.getElementById("type-grid");
            if (grid) grid.scrollIntoView({ behavior: "smooth", block: "center" });
          } else {
            alert(d.message || "Approve failed");
          }
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
        var msg = d.message || (d.success ? "Dispatched." : d.message);
        if (d.success && Array.isArray(d.emergencies) && d.emergencies.length) {
          var bits = d.emergencies.map(function (e) {
            var who = e.assigned_hospital_name || e.assigned_station_name || e.assigned_to || e.type || "unit";
            return "#" + e.id + " → " + who;
          });
          msg = (msg || "Dispatched") + " · " + bits.join("; ");
        }
        document.getElementById("session-msg").textContent = msg;
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
