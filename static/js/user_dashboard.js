(function () {
  "use strict";

  var activeRequestId = null;
  var pollTimer = null;
  var mapWatchStop = null;
  var dashMap = null;
  var dashMapFull = null;
  var mapsReady = false;
  var allNotifications = [];
  var notifFilter = "all";
  var lastNotifCount = 0;
  var typingTimer = null;
  var mediaRecorder = null;
  var voiceChunks = [];
  var currentUserLoc = null;
  var teamContact = "";
  var lastEmergency = null;

  function api(url, opts) {
    opts = opts || {};
    opts.credentials = "same-origin";
    opts.headers = opts.headers || {};
    if (opts.body) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    return fetch(url, opts).then(function (r) { return r.json(); });
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s || "";
    return d.innerHTML;
  }

  function setAvatarElement(av, photo, fallbackName) {
    if (!av) return;
    av.textContent = "";
    if (photo && String(photo).indexOf("data:image/") === 0) {
      var img = document.createElement("img");
      img.src = photo;
      img.alt = "";
      av.appendChild(img);
    } else {
      av.textContent = (fallbackName || "U").charAt(0);
    }
  }

  function notifIcon(type) {
    var icons = {
      request_received: "📥", request_accepted: "✅", team_assigned: "🚑",
      team_dispatched: "🚀", team_arrived: "📍", emergency_completed: "✓",
      system_alert: "⚠", announcement: "📢"
    };
    return icons[type] || "🔔";
  }

  function setActiveMode(active) {
    document.getElementById("ers-idle").classList.toggle("hidden", active);
    document.getElementById("ers-active-wrap").classList.toggle("hidden", !active);
    document.getElementById("ers-chat-active").classList.toggle("hidden", !active);
    document.getElementById("ers-chat-idle").classList.toggle("hidden", active);
    var chip = document.getElementById("header-status-chip");
    if (active) {
      chip.textContent = "Live tracking";
      chip.className = "gn-status-chip live";
    } else {
      chip.textContent = "All clear";
      chip.className = "gn-status-chip idle";
    }
  }

  function renderStatusHeader(em) {
    document.getElementById("ers-status-title").textContent = em.display_stage_label || "Request Received";
    document.getElementById("ers-ref-num").textContent = "#" + em.id;
    document.getElementById("ers-last-update").textContent = em.last_update || em.timestamp || "—";
    renderTeamPanel(em);
  }

  function updateEtaRing(eta) {
    var ring = document.getElementById("eta-ring-progress");
    var num = document.getElementById("eta-ring-num");
    if (!ring || !num) return;
    if (eta == null || isNaN(eta)) {
      num.textContent = "—";
      ring.style.strokeDashoffset = "264";
      return;
    }
    num.textContent = String(eta).padStart(2, "0");
    var max = 30;
    var pct = Math.max(0, Math.min(1, 1 - eta / max));
    ring.style.strokeDashoffset = String(264 * pct);
  }

  function renderTeamPanel(em) {
    var u = em.dispatch_unit || {};
    teamContact = u.contact_number || "";
    var teamName = u.team_name || em.team || "Assigning…";
    var vehicle = u.vehicle_number || null;
    var unitLabel = vehicle || teamName || "No data available";
    document.getElementById("info-eta").textContent = em.eta_minutes != null ? "~" + em.eta_minutes + " min" : "—";
    document.getElementById("info-distance").textContent = em.distance_km != null ? em.distance_km + " km" : "—";
    updateEtaRing(em.eta_minutes);

    var mapYou = document.getElementById("map-detail-you");
    var mapEm = document.getElementById("map-detail-em");
    var mapTeam = document.getElementById("map-detail-team");
    if (mapYou && currentUserLoc) mapYou.textContent = currentUserLoc.district || "Live GPS";
    if (mapEm) mapEm.textContent = em.location || "Emergency site";
    var mapHospital = document.getElementById("map-detail-hospital");
    if (mapHospital) mapHospital.textContent = (em.hospital && em.hospital.name) || teamName || "No data available";
    if (mapTeam) mapTeam.textContent = vehicle ? vehicle + " · " + teamName : teamName;
    var mapStatus = document.getElementById("map-detail-status");
    if (mapStatus) mapStatus.textContent = em.display_stage_label || em.status || "—";

    var sheetTeam = document.getElementById("sheet-team-name");
    var sheetEta = document.getElementById("sheet-eta");
    var sheetDist = document.getElementById("sheet-dist");
    if (sheetTeam) sheetTeam.textContent = unitLabel;
    if (sheetEta) sheetEta.textContent = em.eta_minutes != null ? em.eta_minutes + " min" : "—";
    if (sheetDist) sheetDist.textContent = em.distance_km != null ? em.distance_km + " km" : "—";

    var chatSub = document.getElementById("chat-team-sub");
    if (chatSub) chatSub.textContent = teamName;

    ["btn-call-team", "btn-call-chat", "btn-contact-team"].forEach(function (id) {
      var btn = document.getElementById(id);
      if (!btn) return;
      if (teamContact && teamContact !== "—") {
        btn.href = "tel:" + teamContact.replace(/\s/g, "");
      } else {
        btn.href = "#";
      }
    });
    lastEmergency = em;
  }

  function renderTimeline(timeline) {
    var el = document.getElementById("ers-timeline");
    if (!timeline || !timeline.length) {
      el.innerHTML = "<p style='color:var(--gn-text-muted);font-size:0.85rem;'>Timeline loading…</p>";
      return;
    }
    var firstPending = timeline.findIndex(function (s) { return !s.completed; });
    el.innerHTML = timeline.map(function (step, i) {
      var cls = "gn-tl-step";
      if (step.completed) cls += " done";
      else cls += " pending";
      if (i === firstPending && firstPending >= 0) cls += " current";
      var icon = step.completed ? "✓" : (i + 1);
      return "<div class='" + cls + "'>" +
        "<div class='gn-tl-icon'>" + icon + "</div>" +
        "<div class='gn-tl-body'>" +
        "<div class='gn-tl-label'>" + escapeHtml(step.label) + "</div>" +
        "<div class='gn-tl-time'>" + escapeHtml(step.timestamp || (step.completed ? "Done" : "Pending")) + "</div>" +
        "</div></div>";
    }).join("");
  }

  function notifHtml(n) {
    return "<div class='gn-notif-item" + (n.read ? "" : " unread") + "'>" +
      "<div class='gn-notif-icon-wrap'>" + notifIcon(n.type) + "</div>" +
      "<div><p>" + escapeHtml(n.message) + "</p><time>" + escapeHtml(n.timestamp) + "</time></div></div>";
  }

  function renderNotifications(notifications, unread) {
    allNotifications = notifications || [];
    var badge = document.getElementById("notif-badge");
    if (unread > 0) {
      badge.textContent = unread > 9 ? "9+" : unread;
      badge.classList.remove("hidden");
    } else badge.classList.add("hidden");

    var filtered = allNotifications;
    if (notifFilter === "unread") filtered = allNotifications.filter(function (n) { return !n.read; });

    var el = document.getElementById("ers-notifications");
    if (el) {
      el.innerHTML = filtered.length
        ? filtered.map(notifHtml).join("")
        : "<p style='color:var(--gn-text-muted);font-size:0.85rem;'>No data available</p>";
    }
    var prev = document.getElementById("ers-notifications-preview");
    if (prev) {
      prev.innerHTML = allNotifications.slice(0, 3).map(notifHtml).join("") ||
        "<p style='color:var(--gn-text-muted);font-size:0.82rem;'>No data available</p>";
    }
  }

  function renderHistory(recent) {
    var el = document.getElementById("ers-history-list");
    if (!el) return;
    if (!recent || !recent.length) {
      el.innerHTML = "<p style='color:var(--gn-text-muted);font-size:0.82rem;'>No data available</p>";
      return;
    }
    var completed = ["resolved", "completed", "cancelled", "no_hospital_available"];
    el.innerHTML = recent.map(function (e) {
      var st = (e.status || "").replace(/_/g, " ");
      var badgeCls = completed.indexOf(e.status) >= 0
        ? (e.status === "cancelled" ? "cancelled" : "completed")
        : "active";
      return "<div class='gn-hist-card'><div><strong>#GN-" + e.id + "</strong><br>" +
        escapeHtml(e.type) + " · " + escapeHtml(st) + "</div>" +
        "<span class='gn-hist-badge " + badgeCls + "'>" + badgeCls + "</span></div>";
    }).join("");
  }

  function renderProfileSummary(ps, data) {
    if (!ps) return;
    document.getElementById("welcome-name").textContent = ps.name || "User";
    document.getElementById("profile-display-name").textContent = ps.name || "User";
    document.getElementById("profile-display-phone").textContent = ps.phone || ps.email || "";
    var statusEl = document.getElementById("profile-account-status");
    var st = (ps.account_status || "active");
    statusEl.textContent = st.charAt(0).toUpperCase() + st.slice(1);
    statusEl.className = "gn-status-chip " + (st === "active" ? "live" : "idle");

    ["top-avatar", "profile-avatar"].forEach(function (id) {
      setAvatarElement(document.getElementById(id), ps.profile_photo, ps.name);
    });

    if (data) {
      var recent = data.recent_emergencies || [];
      var cancelled = recent.filter(function (e) {
        return e.status === "cancelled" || e.status === "no_hospital_available";
      }).length;
      document.getElementById("stat-active").textContent = data.active_count || 0;
      document.getElementById("stat-completed").textContent = data.completed_count || 0;
      document.getElementById("stat-total").textContent =
        (data.active_count || 0) + (data.completed_count || 0);
      var cancelEl = document.getElementById("stat-cancelled");
      if (cancelEl) cancelEl.textContent = cancelled;
    }
    renderSavedLocations(ps.saved_locations || []);
  }

  function renderSavedLocations(locs) {
    var el = document.getElementById("saved-locations-list");
    if (!locs.length) { el.textContent = "No saved locations."; return; }
    el.innerHTML = locs.map(function (l, i) {
      return "<div style='padding:6px 0;'>" + escapeHtml(l.label || "Location " + (i + 1)) +
        " — " + escapeHtml(l.district || "") + "</div>";
    }).join("");
  }

  function applyTracking(tracking) {
    if (!dashMap) return;
    if (tracking) {
      dashMap.updateTracking(tracking);
      if (tracking.display_stage_label) {
        document.getElementById("ers-status-title").textContent = tracking.display_stage_label;
      }
      if (tracking.eta_minutes != null) {
        document.getElementById("info-eta").textContent = "~" + tracking.eta_minutes + " min";
        updateEtaRing(tracking.eta_minutes);
      } else {
        document.getElementById("info-eta").textContent = "—";
        updateEtaRing(null);
      }
      document.getElementById("info-distance").textContent =
        tracking.distance_km != null ? tracking.distance_km + " km" : "—";
      var mapEm = document.getElementById("map-detail-em");
      if (mapEm) mapEm.textContent = tracking.location || "Emergency site";
      var mapHospital = document.getElementById("map-detail-hospital");
      if (mapHospital) {
        mapHospital.textContent = (tracking.hospital && tracking.hospital.name)
          || (tracking.station && tracking.station.name) || "—";
      }
      var u = tracking.dispatch_unit || {};
      var mapTeam = document.getElementById("map-detail-team");
      if (mapTeam) {
        mapTeam.textContent = u.vehicle_number
          ? u.vehicle_number + " · " + (u.team_name || tracking.team_label || "Team")
          : (u.team_name || tracking.team_label || "No data available");
      }
      var mapStatus = document.getElementById("map-detail-status");
      if (mapStatus) mapStatus.textContent = tracking.display_stage_label || tracking.status || "—";
      if (tracking.coords_corrected) {
        document.getElementById("dash-map-permission").textContent =
          "Location corrected to Mogadishu — previous coordinates were outside Somalia.";
      }
    } else {
      dashMap.clearEmergency();
    }
    if (dashMapFull) {
      if (tracking) dashMapFull.updateTracking(tracking);
      else dashMapFull.clearEmergency();
    }
  }

  function refreshMapTracking() {
    if (!activeRequestId) { applyTracking(null); return; }
    api("/api/emergencies/" + activeRequestId + "/tracking").then(function (data) {
      if (data.success) applyTracking(data);
    });
  }

  function beginLocationWatch() {
    if (mapWatchStop) mapWatchStop();
    if (!navigator.geolocation) {
      document.getElementById("dash-map-permission").textContent = "GPS not available.";
      return;
    }
    mapWatchStop = EmergencyLocation.watchUserLocation(function (fix) {
      var clamped = EmergencyLocation.clampToSomalia(fix.lat, fix.lng);
      fix.lat = clamped.lat;
      fix.lng = clamped.lng;
      fix.district = EmergencyLocation.getDistrictName(fix.lat, fix.lng);
      currentUserLoc = fix;
      if (dashMap) dashMap.setUserLocation(fix.lat, fix.lng);
      if (dashMapFull) dashMapFull.setUserLocation(fix.lat, fix.lng);
      var mapYou = document.getElementById("map-detail-you");
      if (mapYou) mapYou.textContent = fix.district || "Live GPS";
      if (activeRequestId) EmergencyLocation.pushEmergencyLocation(activeRequestId, fix);
    });
    document.getElementById("dash-map-permission").textContent =
      "Live GPS · Map updates every 5 seconds";
  }

  function initDashboardMap() {
    EmergencyLocation.resolveUserLocation().then(function (loc) {
      currentUserLoc = loc;
      dashMap = EmergencyLocation.createDashboardMap("dash-tracking-map");
      dashMapFull = EmergencyLocation.createDashboardMap("dash-tracking-map-full");
      dashMap.init(loc.lat, loc.lng);
      dashMapFull.init(loc.lat, loc.lng);
      mapsReady = true;
      beginLocationWatch();
      if (activeRequestId) {
        EmergencyLocation.startEmergencyTracking(activeRequestId);
        refreshMapTracking();
      }
    }).catch(function () {
      var perm = document.getElementById("dash-map-permission");
      if (perm) {
        perm.textContent =
          "Enable device GPS for live location. Map shows the default region until GPS is available.";
      }
      // Initial map viewport only — not treated as the user's GPS position
      var c = EmergencyLocation.DEFAULT_VIEW || EmergencyLocation.MOGADISHU_CENTER;
      dashMap = EmergencyLocation.createDashboardMap("dash-tracking-map");
      dashMapFull = EmergencyLocation.createDashboardMap("dash-tracking-map-full");
      dashMap.init(c.lat, c.lng);
      dashMapFull.init(c.lat, c.lng);
      mapsReady = true;
      beginLocationWatch();
    });
  }

  function renderChatPreview(messages) {
    var el = document.getElementById("chat-preview-msgs");
    if (!el) return;
    var msgs = (messages || []).slice(-2);
    if (!msgs.length) {
      el.innerHTML = "<p>No messages yet. Open chat to communicate.</p>";
      return;
    }
    el.innerHTML = msgs.map(function (m) {
      return "<p><strong>" + (m.sender_role === "citizen" ? "You" : "Team") + ":</strong> " +
        escapeHtml((m.text || "").substring(0, 60)) + "</p>";
    }).join("");
  }

  function loadChat() {
    if (!activeRequestId) return;
    api("/api/messages/" + activeRequestId).then(function (data) {
      var box = document.getElementById("dash-chat-messages");
      box.innerHTML = "";
      (data.messages || []).forEach(function (m) {
        var div = document.createElement("div");
        var role = m.sender_role === "citizen" ? "citizen" : (m.sender_role || "hospital");
        div.className = "gn-bubble " + role;
        var tick = m.status === "seen" ? " ✓✓" : m.status === "delivered" ? " ✓" : "";
        if (m.msg_type === "voice" && m.text && m.text.indexOf("data:audio") === 0) {
          div.innerHTML = "<audio controls src='" + m.text + "'></audio>" +
            "<span class='gn-bubble-meta'>" + m.timestamp + tick + "</span>";
        } else {
          div.innerHTML = escapeHtml(m.text) + "<span class='gn-bubble-meta'>" + m.timestamp + tick + "</span>";
        }
        box.appendChild(div);
      });
      box.scrollTop = box.scrollHeight;
      document.getElementById("ers-typing").textContent = data.typing ? "Response team is typing…" : "";
      renderChatPreview(data.messages);
      var chatBadge = document.getElementById("chat-nav-badge");
      if (chatBadge && data.messages && data.messages.length) chatBadge.classList.remove("hidden");
    });
  }

  function showPanel(name) {
    document.querySelectorAll(".gn-panel").forEach(function (p) { p.classList.remove("active"); });
    var panel = document.getElementById("panel-" + name);
    if (panel) panel.classList.add("active");
    document.querySelectorAll(".gn-nav-item, .gn-bnav-item").forEach(function (btn) {
      btn.classList.toggle("active", btn.getAttribute("data-panel") === name);
    });
    if (name === "map" || name === "overview") {
      setTimeout(function () {
        if (dashMap) dashMap.resize();
        if (dashMapFull) dashMapFull.resize();
        refreshMapTracking();
      }, 250);
    }
    if (name === "chat" && activeRequestId) loadChat();
  }

  function sendTyping(on) {
    if (!activeRequestId) return;
    api("/api/emergencies/" + activeRequestId + "/typing", { method: "POST", body: { typing: !!on } });
  }

  function sendDashChat(textOverride) {
    var text = (textOverride || document.getElementById("dash-chat-input").value).trim();
    if (!text || !activeRequestId) return;
    sendTyping(false);
    api("/api/messages/" + activeRequestId, { method: "POST", body: { text: text } })
      .then(function () {
        document.getElementById("dash-chat-input").value = "";
        loadChat();
      });
  }

  function sendVoiceMessage(audioData) {
    if (!activeRequestId || !audioData) return;
    api("/api/messages/" + activeRequestId, {
      method: "POST", body: { msg_type: "voice", audio: audioData, text: "[Voice]" }
    }).then(loadChat);
  }

  function loadDashboard() {
    api("/api/user/dashboard").then(function (data) {
      if (!data.success) return;
      renderProfileSummary(data.profile_summary, data);
      renderNotifications(data.notifications, data.unread_notifications);
      renderHistory(data.recent_emergencies);

      var prevActive = activeRequestId;
      if (data.active_emergency) {
        var em = data.active_emergency;
        activeRequestId = em.id;
        setActiveMode(true);
        renderStatusHeader(em);
        renderTimeline(em.timeline);
        if (prevActive !== activeRequestId && mapsReady) {
          EmergencyLocation.startEmergencyTracking(activeRequestId);
        }
        loadChat();
      } else {
        activeRequestId = null;
        setActiveMode(false);
        if (prevActive) EmergencyLocation.stopEmergencyTracking();
      }

      if (mapsReady) {
        refreshMapTracking();
        setTimeout(function () { if (dashMap) dashMap.resize(); }, 200);
      }
      lastNotifCount = data.unread_notifications || 0;
    });
  }

  document.querySelectorAll(".gn-nav-item, .gn-bnav-item").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var p = btn.getAttribute("data-panel");
      if (p) showPanel(p);
    });
  });

  document.getElementById("btn-scroll-notif").onclick = function () { showPanel("notifications"); };

  document.getElementById("btn-open-chat").onclick = function () { showPanel("chat"); };
  document.getElementById("btn-open-chat-2").onclick = function () { showPanel("chat"); };
  document.getElementById("btn-open-history").onclick = function () { showPanel("history"); };
  document.getElementById("btn-view-timeline").onclick = function () { showPanel("timeline"); };

  document.querySelectorAll(".gn-notif-tab").forEach(function (tab) {
    tab.onclick = function () {
      document.querySelectorAll(".gn-notif-tab").forEach(function (t) { t.classList.remove("active"); });
      tab.classList.add("active");
      notifFilter = tab.getAttribute("data-filter");
      renderNotifications(allNotifications, document.getElementById("notif-badge").classList.contains("hidden") ? 0 : 1);
    };
  });

  document.querySelectorAll(".gn-menu-item[data-goto]").forEach(function (btn) {
    btn.onclick = function () {
      showPanel(btn.getAttribute("data-goto"));
      var focusId = btn.getAttribute("data-focus");
      if (focusId) {
        setTimeout(function () {
          var el = document.getElementById(focusId);
          if (el && el.focus) el.focus();
          else if (el && el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "center" });
        }, 50);
      }
    };
  });

  document.getElementById("btn-share-loc").onclick = function () {
    var lat = currentUserLoc && currentUserLoc.lat;
    var lng = currentUserLoc && currentUserLoc.lng;
    var text = lat != null
      ? "My emergency location: " + lat.toFixed(5) + ", " + lng.toFixed(5)
      : "GurmadNet emergency — location sharing";
    if (navigator.share) {
      navigator.share({ title: "GurmadNet Location", text: text }).catch(function () {});
    } else if (lat != null) {
      navigator.clipboard.writeText(text).then(function () { alert("Location copied."); });
    }
  };

  document.getElementById("btn-call-team").onclick = function (e) {
    if (!teamContact || teamContact === "—") {
      e.preventDefault();
      alert("Response team contact will appear when a team is assigned.");
    }
  };

  document.getElementById("btn-map-recenter").onclick = function () {
    if (dashMap && currentUserLoc) dashMap.setUserLocation(currentUserLoc.lat, currentUserLoc.lng);
    if (dashMap && dashMap.recenter) dashMap.recenter();
    if (dashMapFull && dashMapFull.recenter) dashMapFull.recenter();
    refreshMapTracking();
  };

  document.getElementById("btn-mark-all-read").onclick = function () {
    api("/api/notifications/read", { method: "POST", body: {} }).then(loadDashboard);
  };

  document.getElementById("dash-chat-send").onclick = function () { sendDashChat(); };
  document.getElementById("dash-chat-input").addEventListener("input", function () {
    clearTimeout(typingTimer);
    sendTyping(true);
    typingTimer = setTimeout(function () { sendTyping(false); }, 2000);
  });
  document.getElementById("dash-chat-input").onkeypress = function (e) {
    if (e.key === "Enter") sendDashChat();
  };
  document.querySelectorAll(".gn-quick-chip[data-text]").forEach(function (btn) {
    btn.onclick = function () { sendDashChat(btn.getAttribute("data-text")); };
  });

  document.getElementById("ers-voice-btn").onclick = function () {
    var btn = document.getElementById("ers-voice-btn");
    if (mediaRecorder && mediaRecorder.state === "recording") {
      mediaRecorder.stop();
      btn.classList.remove("recording");
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      alert("Voice messages not supported."); return;
    }
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      voiceChunks = [];
      mediaRecorder = new MediaRecorder(stream);
      mediaRecorder.ondataavailable = function (e) { if (e.data.size) voiceChunks.push(e.data); };
      mediaRecorder.onstop = function () {
        var blob = new Blob(voiceChunks, { type: "audio/webm" });
        var reader = new FileReader();
        reader.onload = function (ev) { sendVoiceMessage(ev.target.result); };
        reader.readAsDataURL(blob);
        stream.getTracks().forEach(function (t) { t.stop(); });
      };
      mediaRecorder.start();
      btn.classList.add("recording");
      setTimeout(function () {
        if (mediaRecorder && mediaRecorder.state === "recording") {
          mediaRecorder.stop();
          btn.classList.remove("recording");
        }
      }, 15000);
    }).catch(function () { alert("Microphone permission required."); });
  };

  document.getElementById("btn-save-location").onclick = function () {
    if (!currentUserLoc) { alert("Waiting for GPS…"); return; }
    api("/api/user/profile").then(function (res) {
      var p = res.profile || {};
      var locs = p.saved_locations || [];
      locs.push({
        label: "Saved " + new Date().toLocaleDateString(),
        latitude: currentUserLoc.lat,
        longitude: currentUserLoc.lng,
        district: currentUserLoc.district || EmergencyLocation.getDistrictName(currentUserLoc.lat, currentUserLoc.lng)
      });
      if (locs.length > 5) locs = locs.slice(-5);
      return api("/api/user/profile", { method: "PUT", body: { saved_locations: locs } });
    }).then(function () { loadDashboard(); });
  };

  document.getElementById("profile-form").onsubmit = function (e) {
    e.preventDefault();
    api("/api/user/profile", {
      method: "PUT",
      body: {
        name: document.getElementById("pf-name").value,
        phone: document.getElementById("pf-phone").value,
        emergency_contact_name: document.getElementById("pf-ec-name").value,
        emergency_contact_phone: document.getElementById("pf-ec-phone").value,
        profile_photo: document.getElementById("profile-avatar").querySelector("img")
          ? document.getElementById("profile-avatar").querySelector("img").src : ""
      }
    }).then(function (d) {
      if (d.success) { loadDashboard(); alert("Profile saved."); }
      else alert(d.message || "Save failed");
    });
  };

  document.getElementById("profile-photo-input").onchange = function (e) {
    var file = e.target.files[0];
    if (!file || file.size > 100000) { alert("Image must be under 100KB"); return; }
    var reader = new FileReader();
    reader.onload = function (ev) {
      var dataUrl = ev.target.result;
      setAvatarElement(document.getElementById("profile-avatar"), dataUrl, "U");
      setAvatarElement(document.getElementById("top-avatar"), dataUrl, "U");
    };
    reader.readAsDataURL(file);
  };

  initDashboardMap();
  loadDashboard();
  api("/api/user/profile").then(function (d) {
    if (d.profile) {
      document.getElementById("pf-name").value = d.profile.name || "";
      document.getElementById("pf-phone").value = d.profile.phone || "";
      document.getElementById("pf-ec-name").value = d.profile.emergency_contact_name || "";
      document.getElementById("pf-ec-phone").value = d.profile.emergency_contact_phone || "";
    }
  });

  pollTimer = setInterval(function () {
    loadDashboard();
    if (mapsReady) refreshMapTracking();
    if (activeRequestId) loadChat();
  }, 5000);
})();
