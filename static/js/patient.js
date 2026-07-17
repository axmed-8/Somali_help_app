(function () {
  "use strict";

  var userCoords = { lat: null, lng: null };
  var locationLabel = "";
  var activeRequestId = null;
  var statusTimer = null;
  var pageWatchStop = null;
  var selectedType = "medical";
  var lastAccuracy = null;

  var pages = {
    home: document.getElementById("page-home"),
    type: document.getElementById("page-type"),
    location: document.getElementById("page-location"),
    status: document.getElementById("page-status")
  };

  function showPage(name) {
    Object.keys(pages).forEach(function (k) {
      if (pages[k]) pages[k].classList.toggle("active", k === name);
    });
    document.querySelectorAll(".gn-user-nav .gn-bnav-item, .bottom-nav .nav-item").forEach(function (btn) {
      var nav = btn.getAttribute("data-nav");
      var active = (nav === "home" && name === "home") ||
        (nav === "type" && name === "type") ||
        (nav === "status" && name === "status");
      btn.classList.toggle("active", active);
    });
    if (name === "home") {
      if (pageWatchStop) { pageWatchStop(); pageWatchStop = null; }
      loadRecent();
      checkActiveBanner();
    }
  }

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

  function payloadBase() {
    var body = document.body;
    return {
      type: selectedType,
      name: body.getAttribute("data-user-name") || "",
      phone: body.getAttribute("data-user-phone") || "",
      district: locationLabel,
      latitude: userCoords.lat,
      longitude: userCoords.lng,
      accuracy_m: lastAccuracy,
      location: locationLabel + " (" + (userCoords.lat || 0).toFixed(5) + ", " + (userCoords.lng || 0).toFixed(5) + ")",
      method: "gps",
      confidence: EmergencyLocation.confidenceFromAccuracy(lastAccuracy),
      notes: (document.getElementById("emergency-notes") || {}).value || ""
    };
  }

  function applyLocation(result) {
    userCoords.lat = result.lat;
    userCoords.lng = result.lng;
    locationLabel = result.district || EmergencyLocation.getDistrictName(result.lat, result.lng);
    if (result.accuracy != null) {
      lastAccuracy = result.accuracy;
      var acc = document.getElementById("location-accuracy");
      if (acc) { acc.classList.remove("hidden"); acc.textContent = "GPS accuracy: ±" + Math.round(result.accuracy) + "m"; }
    }
    var statusEl = document.getElementById("location-status");
    var coordsEl = document.getElementById("location-coords");
    if (statusEl) { statusEl.textContent = "✓ Location captured: " + locationLabel; statusEl.classList.add("ready"); }
    if (coordsEl) coordsEl.textContent = userCoords.lat.toFixed(5) + ", " + userCoords.lng.toFixed(5);
    EmergencyLocation.initMapProvider("user-location-map", userCoords.lat, userCoords.lng, { label: locationLabel, zoom: 15 });
    var btn = document.getElementById("btn-send-alert");
    if (btn) btn.disabled = false;
  }

  function fetchLocation() {
    var statusEl = document.getElementById("location-status");
    if (statusEl) statusEl.textContent = "Requesting GPS permission...";
    return EmergencyLocation.requestLocationPermission({ timeout: 20000 })
      .then(function (r) {
        applyLocation(r);
        if (pageWatchStop) pageWatchStop();
        pageWatchStop = EmergencyLocation.watchUserLocation(applyLocation, { maximumAge: 5000 });
      })
      .catch(function (err) {
        if (statusEl) statusEl.textContent = err.message || "GPS denied";
        return EmergencyLocation.getApproxLocationByIP().then(applyLocation);
      });
  }

  function startTypeFlow(type) {
    selectedType = type || "medical";
    showPage("location");
    document.getElementById("location-heading").textContent =
      "Confirm location — " + selectedType.replace(/_/g, " ");
    fetchLocation();
  }

  function beginLiveTracking(eid) {
    activeRequestId = eid;
    var badge = document.getElementById("status-live-badge");
    if (badge) badge.classList.remove("hidden");
    EmergencyLocation.startEmergencyTracking(eid, function (fix) {
      EmergencyLocation.updateLiveMarker("status-map", fix.lat, fix.lng);
    });
  }

  function renderStatus(data) {
    var panel = document.getElementById("status-panel");
    if (!data.active || !data.request) {
      panel.innerHTML = "<p>No active emergency. Tap SOS to report.</p>";
      return;
    }
    var r = data.request;
    var statusText = (r.status || "").replace(/_/g, " ");
    panel.innerHTML =
      "<div class='status-badge-live'>" + statusText + "</div>" +
      "<p class='status-team'><strong>🚑 " + (r.team_label || "Emergency Response Team") + "</strong></p>" +
      "<p>Type: " + (r.type || "emergency") + "</p>" +
      "<p>Location: " + (r.location || "") + "</p>" +
      (r.tracking_active ? "<p class='live-gps-badge'>● Live location sharing active</p>" : "") +
      "<ul class='status-timeline'>" +
      (r.status_history || []).map(function (s) {
        return "<li>" + s.timestamp + " — " + s.status + "</li>";
      }).join("") + "</ul>";
    if (r.latitude && r.longitude) {
      EmergencyLocation.initMapProvider("status-map", r.latitude, r.longitude, { zoom: 15, label: "Your location" });
    }
    loadMessages();
  }

  function loadMessages() {
    if (!activeRequestId) return;
    api("/api/messages/" + activeRequestId).then(function (data) {
      var box = document.getElementById("chat-messages");
      if (!box) return;
      box.innerHTML = "";
      (data.messages || []).forEach(function (m) {
        var div = document.createElement("div");
        div.className = "chat-bubble " + m.sender_role;
        var statusIcon = m.status === "seen" ? " ✓✓" : m.status === "delivered" ? " ✓" : "";
        div.innerHTML = "<span class='chat-text'>" + escapeHtml(m.text) + "</span>" +
          "<span class='chat-meta'>" + formatTime(m.timestamp) + statusIcon + "</span>";
        box.appendChild(div);
      });
      box.scrollTop = box.scrollHeight;
    });
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function formatTime(ts) {
    try { return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
    catch (e) { return ts; }
  }

  function startStatusPolling() {
    if (statusTimer) clearInterval(statusTimer);
    function poll() {
      var url = "/api/patient/request/status" + (activeRequestId ? "?id=" + activeRequestId : "");
      api(url).then(renderStatus);
    }
    poll();
    statusTimer = setInterval(poll, 4000);
  }

  function loadRecent() {
    api("/api/user/dashboard").then(function (data) {
      var el = document.getElementById("recent-list");
      if (!el) return;
      var list = data.recent_emergencies || [];
      if (!list.length) {
        el.innerHTML = "<p class='recent-empty'>No recent requests yet.</p>";
        return;
      }
      el.innerHTML = list.slice(0, 5).map(function (e) {
        return "<div class='recent-item'><span>" + e.type + " · " + e.status + "</span><span>#" + e.id + "</span></div>";
      }).join("");
    }).catch(function () {});
  }

  function checkActiveBanner() {
    api("/api/patient/request/status").then(function (data) {
      var banner = document.getElementById("home-active-banner");
      if (!banner) return;
      if (data.active && data.request) {
        activeRequestId = data.request.id;
        banner.classList.remove("hidden");
        banner.innerHTML = "<strong>Active emergency #" + data.request.id + "</strong> — " +
          (data.request.status || "").replace(/_/g, " ") +
          " <button type='button' id='btn-view-active'>View status</button>";
        document.getElementById("btn-view-active").onclick = function () {
          showPage("status");
          startStatusPolling();
          beginLiveTracking(activeRequestId);
        };
      } else {
        banner.classList.add("hidden");
      }
    });
  }

  document.getElementById("btn-sos").onclick = function () { showPage("type"); };

  var callCenterBtn = document.getElementById("btn-call-center");
  if (callCenterBtn) {
    callCenterBtn.onclick = function () {
      var statusEl = document.getElementById("call-center-status");
      callCenterBtn.disabled = true;
      if (statusEl) {
        statusEl.classList.remove("hidden");
        statusEl.textContent = "Getting your location…";
      }
      function sendCall(coords) {
        var body = document.body;
        var district = (coords && coords.district) ||
          (window.EmergencyLocation && EmergencyLocation.getDistrictName
            ? EmergencyLocation.getDistrictName(coords.lat, coords.lng) : "");
        api("/api/call-center/initiate", {
          method: "POST",
          body: {
            name: body.getAttribute("data-user-name") || "",
            phone: body.getAttribute("data-user-phone") || "",
            latitude: coords.lat,
            longitude: coords.lng,
            address: district,
            district: district,
            accuracy_m: coords.accuracy,
            device_info: {
              user_agent: navigator.userAgent.slice(0, 300),
              platform: navigator.platform || "",
              language: navigator.language || ""
            }
          }
        }).then(function (data) {
          if (data.success) {
            if (statusEl) statusEl.textContent = data.message || "Connecting… Location shared with operator.";
            if (data.tel_href) {
              window.location.href = data.tel_href;
            }
          } else {
            alert(data.message || "Could not reach Call Center");
            if (statusEl) statusEl.textContent = data.message || "Failed";
          }
        }).finally(function () {
          callCenterBtn.disabled = false;
        });
      }
      if (window.EmergencyLocation && EmergencyLocation.requestLocationPermission) {
        EmergencyLocation.requestLocationPermission({ timeout: 15000 })
          .then(sendCall)
          .catch(function () {
            if (EmergencyLocation.getApproxLocationByIP) {
              EmergencyLocation.getApproxLocationByIP().then(sendCall).catch(function () {
                alert("GPS required to call Emergency Center so operators know your location.");
                callCenterBtn.disabled = false;
              });
            } else {
              alert("GPS required to call Emergency Center.");
              callCenterBtn.disabled = false;
            }
          });
      } else {
        alert("Location services unavailable.");
        callCenterBtn.disabled = false;
      }
    };
  }

  document.querySelectorAll(".service-btn, #page-type .type-btn").forEach(function (btn) {
    btn.onclick = function () { startTypeFlow(btn.getAttribute("data-type")); };
  });
  document.getElementById("btn-back-home-1").onclick = function () { showPage("home"); };
  document.getElementById("btn-back-type").onclick = function () { showPage("type"); };
  document.getElementById("btn-back-home-4").onclick = function () {
    if (statusTimer) clearInterval(statusTimer);
    showPage("home");
  };

  document.getElementById("btn-send-alert").onclick = function () {
    if (!userCoords.lat) { alert("Waiting for GPS location."); return; }
    var btn = document.getElementById("btn-send-alert");
    btn.disabled = true;
    api("/api/send_alert", { method: "POST", body: payloadBase() })
      .then(function (data) {
        if (data.success) {
          beginLiveTracking(data.id);
          showPage("status");
          startStatusPolling();
        } else alert(data.message || "Failed");
      })
      .finally(function () { btn.disabled = false; });
  };

  document.getElementById("btn-send-chat").onclick = sendChat;
  document.getElementById("chat-text").onkeypress = function (e) { if (e.key === "Enter") sendChat(); };
  document.querySelectorAll("#chat-quick .quick-reply").forEach(function (btn) {
    btn.onclick = function () {
      document.getElementById("chat-text").value = btn.getAttribute("data-text");
      sendChat();
    };
  });

  function sendChat() {
    var text = document.getElementById("chat-text").value.trim();
    if (!text || !activeRequestId) return;
    api("/api/messages/" + activeRequestId, { method: "POST", body: { text: text } })
      .then(function () {
        document.getElementById("chat-text").value = "";
        loadMessages();
      });
  }

  document.querySelectorAll(".gn-user-nav .gn-bnav-item, .gn-user-nav .gn-bnav-sos, .bottom-nav .nav-item").forEach(function (btn) {
    btn.onclick = function () {
      var nav = btn.getAttribute("data-nav");
      if (nav === "home") showPage("home");
      else if (nav === "type") { selectedType = "medical"; startTypeFlow("medical"); }
      else if (nav === "dashboard") window.location.href = "/dashboard";
      else if (nav === "status") {
        showPage("status");
        if (!activeRequestId) api("/api/patient/request/status").then(function (d) {
          if (d.active) { activeRequestId = d.request.id; startStatusPolling(); beginLiveTracking(activeRequestId); }
        });
        else startStatusPolling();
      }
    };
  });

  checkActiveBanner();
  loadRecent();

  // Deep-link from dashboard: /#call-center
  if (location.hash === "#call-center") {
    var ccBtn = document.getElementById("btn-call-center");
    var ccWrap = document.getElementById("call-center");
    if (ccWrap && ccWrap.scrollIntoView) ccWrap.scrollIntoView({ behavior: "smooth", block: "center" });
    if (ccBtn) {
      setTimeout(function () { ccBtn.focus(); }, 200);
    }
  }
})();
