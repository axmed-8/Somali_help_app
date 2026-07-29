(function () {
  "use strict";

  var userCoords = { lat: null, lng: null };
  var locationLabel = "";
  var activeRequestId = null;
  var statusTimer = null;
  var pageWatchStop = null;
  var selectedType = "medical";
  var lastAccuracy = null;
  var pendingPhotoDataUrl = "";
  var hospitalCache = [];
  var mapMode = "map";
  var mapCategory = "hospital";
  var MAP_CAT_LABELS = {
    hospital: "Isbitaallada ugu dhow",
    police: "Saldhigyada ugu dhow",
    fire: "Dab-demiska ugu dhow"
  };
  var MAP_CAT_EMPTY = {
    hospital: "Isbitaal lama helin.",
    police: "Saldhig lama helin.",
    fire: "Xarun dab-demis lama helin."
  };

  var TYPE_LABELS = {
    medical: "Medical / Ambulance",
    accident: "Accident",
    fire: "Fire",
    security: "Police / Security",
    family_help: "Family help"
  };

  var pages = {
    home: document.getElementById("page-home"),
    type: document.getElementById("page-type"),
    location: document.getElementById("page-location"),
    map: document.getElementById("page-map"),
    hospitals: document.getElementById("page-hospitals"),
    status: document.getElementById("page-status"),
    profile: document.getElementById("page-profile")
  };

  function showPage(name) {
    Object.keys(pages).forEach(function (k) {
      if (pages[k]) pages[k].classList.toggle("active", k === name);
    });
    document.querySelectorAll(".gn-user-nav .gn-bnav-item").forEach(function (btn) {
      var nav = btn.getAttribute("data-nav");
      var active =
        (nav === "home" && name === "home") ||
        (nav === "map" && (name === "map" || name === "hospitals")) ||
        (nav === "status" && name === "status") ||
        (nav === "profile" && name === "profile") ||
        (nav === "type" && (name === "type" || name === "location"));
      btn.classList.toggle("active", !!active);
    });
    if (name === "home") {
      if (pageWatchStop) { pageWatchStop(); pageWatchStop = null; }
      loadRecent();
      checkActiveBanner();
      refreshHomeLocationCard();
    } else if (name === "map") {
      loadMapPage();
    } else if (name === "hospitals") {
      loadHospitalsPage();
    } else if (name === "profile") {
      loadProfile();
    } else if (name === "status") {
      ensureStatusView();
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

  function setHomeLocState(state, title, detail) {
    var card = document.getElementById("home-loc-card");
    if (!card) return;
    card.classList.remove("is-active", "is-searching", "is-denied", "is-off");
    card.classList.add("is-" + state);
    var t = document.getElementById("home-loc-title");
    var d = document.getElementById("home-loc-detail");
    if (t) t.textContent = title;
    if (d) d.textContent = detail;
  }

  function refreshHomeLocationCard() {
    setHomeLocState("searching", "Goobta…", "GPS waa la hubinayaa");
    if (!navigator.geolocation) {
      setHomeLocState("off", "GPS ma shaqeynayo", "Taleefankan ma taageero");
      return;
    }
    if (userCoords.lat != null && userCoords.lng != null) {
      setHomeLocState(
        "active",
        "Goobtu waa diyaar",
        locationLabel || (userCoords.lat.toFixed(4) + ", " + userCoords.lng.toFixed(4))
      );
      return;
    }
    EmergencyLocation.requestLocationPermission({ timeout: 12000 })
      .then(function (r) {
        userCoords.lat = r.lat;
        userCoords.lng = r.lng;
        locationLabel = r.district || EmergencyLocation.getDistrictName(r.lat, r.lng);
        if (r.accuracy != null) lastAccuracy = r.accuracy;
        setHomeLocState("active", "Goobtu waa diyaar", locationLabel || "GPS ready");
      })
      .catch(function () {
        setHomeLocState("denied", "Oggolow goobta", "Fur GPS si SOS sax ah");
      });
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
  if (statusEl) { statusEl.textContent = "✓ Goobtu waa diyaar: " + locationLabel; statusEl.classList.add("ready"); }
    if (coordsEl) coordsEl.textContent = userCoords.lat.toFixed(5) + ", " + userCoords.lng.toFixed(5);
    EmergencyLocation.initMapProvider("user-location-map", userCoords.lat, userCoords.lng, { label: locationLabel, zoom: 15 });
    var btn = document.getElementById("btn-send-alert");
    if (btn) btn.disabled = false;
    setHomeLocState("active", "Location active", locationLabel || "GPS ready");
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
        if (statusEl) statusEl.textContent = err.message || "GPS denied — trying network location…";
        return EmergencyLocation.getApproxLocationByIP()
          .then(applyLocation)
          .catch(function (ipErr) {
            if (statusEl) {
              statusEl.textContent =
                (ipErr && ipErr.message) ||
                "Location unavailable. Enable GPS for accurate emergency positioning.";
            }
            setHomeLocState("denied", "Permission required", "Enable location for accurate SOS");
          });
      });
  }

  function startTypeFlow(type) {
    selectedType = type || "medical";
    showPage("location");
    var label = TYPE_LABELS[selectedType] || selectedType.replace(/_/g, " ");
    var heading = document.getElementById("location-heading");
    if (heading) heading.textContent = "Xaqiiji goobtaada — " + label;
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

  function calmStatusLabel(raw) {
    var s = String(raw || "").toLowerCase();
    if (s.indexOf("complete") >= 0 || s.indexOf("resolved") >= 0) return "Resolved";
    if (s.indexOf("arriv") >= 0 || s.indexOf("reached") >= 0) return "Team arrived";
    if (s.indexOf("way") >= 0 || s.indexOf("dispatched") >= 0 || s.indexOf("accepted") >= 0) return "Help is on the way";
    if (s.indexOf("pending") >= 0 || s.indexOf("assign") >= 0) return "Team notified";
    if (s.indexOf("received") >= 0) return "Request received";
    return (raw || "Updating…").toString().replace(/_/g, " ");
  }

  function renderStatus(data) {
    var panel = document.getElementById("status-panel");
    if (!panel) return;
    if (!data.active || !data.request) {
      panel.innerHTML =
        "<div class='cit-status-empty'>" +
          "<strong>No active emergency</strong>" +
          "<p>If you need help, tap SOS and choose the emergency type.</p>" +
          "<button type='button' class='btn-primary' id='btn-status-report'>Report Emergency</button>" +
        "</div>";
      var go = document.getElementById("btn-status-report");
      if (go) go.onclick = function () { showPage("type"); };
      return;
    }
    var r = data.request;
    var calm = calmStatusLabel(r.display_stage_label || r.status);
    var team = r.team_label || r.assigned_hospital_name || "Emergency Response Team";
    panel.innerHTML =
      "<div class='cit-status-card'>" +
        "<div class='status-badge-live cit-status-badge'>" + escapeHtml(calm) + "</div>" +
        "<p class='cit-status-ref'>Ref #" + escapeHtml(r.id) + " · " + escapeHtml(TYPE_LABELS[r.type] || r.type || "emergency") + "</p>" +
        "<p class='status-team'><strong>" + escapeHtml(team) + "</strong></p>" +
        "<p class='cit-status-loc'>" + escapeHtml(r.location || "Your shared GPS") + "</p>" +
        (r.tracking_active ? "<p class='live-gps-badge'>● Live location sharing active</p>" : "") +
        "<p class='cit-status-breathe'>Breathe. Keep your phone nearby. Help is being coordinated.</p>" +
      "</div>" +
      "<ul class='status-timeline'>" +
      (r.status_history || []).slice(-8).map(function (s) {
        return "<li>" + escapeHtml(s.timestamp) + " — " + escapeHtml(String(s.status || "").replace(/_/g, " ")) +
          (s.note ? " · " + escapeHtml(s.note) : "") + "</li>";
      }).join("") + "</ul>";
    if (r.latitude && r.longitude) {
      EmergencyLocation.initMapProvider("status-map", r.latitude, r.longitude, { zoom: 15, label: "Your location" });
    }
    loadMessages();
  }

  function ensureStatusView() {
    if (!activeRequestId) {
      api("/api/patient/request/status").then(function (d) {
        if (d.active && d.request) {
          activeRequestId = d.request.id;
          startStatusPolling();
          beginLiveTracking(activeRequestId);
        } else {
          renderStatus(d);
        }
      });
    } else {
      startStatusPolling();
    }
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
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function formatTime(ts) {
    try { return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
    catch (e) { return ts; }
  }

  function formatDist(km) {
    if (km == null || km === "" || isNaN(Number(km))) return "—";
    var n = Number(km);
    if (n < 1) return Math.round(n * 1000) + " m";
    return n.toFixed(1) + " km";
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
      var nameEl = document.getElementById("home-user-name");
      if (nameEl && data.profile_summary && data.profile_summary.name) {
        var first = String(data.profile_summary.name).split(/\s+/)[0];
        if (first) nameEl.textContent = first;
      }
      var list = data.recent_emergencies || [];
      if (!list.length) {
        el.innerHTML = "<p class='recent-empty'>Weli ma jiraan codsiyo dhowaan.</p>";
        return;
      }
      el.innerHTML = list.slice(0, 5).map(function (e) {
        return "<button type='button' class='recent-item cit-list-item' data-recent-id='" + escapeHtml(e.id) + "'>" +
          "<span>" + escapeHtml(TYPE_LABELS[e.type] || e.type) + " · " + escapeHtml(String(e.status || "").replace(/_/g, " ")) + "</span>" +
          "<span>#" + escapeHtml(e.id) + "</span></button>";
      }).join("");
      el.querySelectorAll("[data-recent-id]").forEach(function (btn) {
        btn.onclick = function () {
          activeRequestId = btn.getAttribute("data-recent-id");
          showPage("status");
          startStatusPolling();
          beginLiveTracking(activeRequestId);
        };
      });
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

  function hospitalsUrl() {
    var q = "/api/hospitals";
    if (userCoords.lat != null && userCoords.lng != null) {
      q += "?lat=" + encodeURIComponent(userCoords.lat) + "&lng=" + encodeURIComponent(userCoords.lng);
    }
    return q;
  }

  function stationsUrl(kind) {
    var q = "/api/stations?kind=" + encodeURIComponent(kind);
    if (userCoords.lat != null && userCoords.lng != null) {
      q += "&lat=" + encodeURIComponent(userCoords.lat) + "&lng=" + encodeURIComponent(userCoords.lng);
    }
    return q;
  }

  function renderPlaceItems(targetId, places, opts) {
    opts = opts || {};
    var el = document.getElementById(targetId);
    if (!el) return;
    if (!places || !places.length) {
      el.innerHTML = "<p class='cit-empty'>" + escapeHtml(opts.emptyText || "Lama helin.") + "</p>";
      return;
    }
    el.innerHTML = places.slice(0, opts.limit || 20).map(function (h) {
      var dist = formatDist(h.distance_km);
      var city = h.city || h.district || h.region || "";
      return "<div class='cit-hospital-card'>" +
        "<div>" +
          "<strong>" + escapeHtml(h.name || "Xarun") + "</strong>" +
          "<em>" + escapeHtml(city || "Somalia") + (h.phone ? " · " + escapeHtml(h.phone) : "") + "</em>" +
          (opts.showMapLink
            ? "<button type='button' class='cit-link-btn' data-focus-hospital='" + escapeHtml(h.id) + "'>Ku arag khariidadda</button>"
            : "") +
        "</div>" +
        "<div class='cit-hospital-meta'>" + dist + "</div>" +
      "</div>";
    }).join("");
    el.querySelectorAll("[data-focus-hospital]").forEach(function (btn) {
      btn.onclick = function () {
        showPage("map");
        var id = btn.getAttribute("data-focus-hospital");
        setTimeout(function () { focusHospitalOnMap(id); }, 200);
      };
    });
  }

  function renderHospitalItems(targetId, hospitals, opts) {
    opts = opts || {};
    opts.emptyText = opts.emptyText || MAP_CAT_EMPTY.hospital;
    renderPlaceItems(targetId, hospitals, opts);
  }

  function focusHospitalOnMap(id) {
    var h = hospitalCache.find(function (x) { return String(x.id) === String(id); });
    if (!h || h.latitude == null || h.longitude == null) return;
    EmergencyLocation.initMapProvider("citizen-map", h.latitude, h.longitude, {
      zoom: 14,
      label: h.name || "Xarun"
    });
  }

  function plotHospitalsOnMap(hospitals) {
    var nearest = (hospitals || []).find(function (h) {
      return h.latitude != null && h.longitude != null;
    });
    var centerLat = userCoords.lat;
    var centerLng = userCoords.lng;
    var label = locationLabel || "Adiga";
    if (centerLat == null || centerLng == null) {
      if (nearest) {
        centerLat = nearest.latitude;
        centerLng = nearest.longitude;
        label = nearest.name || "Xarun";
      } else {
        centerLat = 2.0469;
        centerLng = 45.3182;
        label = "Muqdisho";
      }
    }
    EmergencyLocation.initMapProvider("citizen-map", centerLat, centerLng, {
      zoom: nearest && userCoords.lat != null ? 12 : 13,
      label: label
    });
  }

  function updateMapListTitle() {
    var title = document.getElementById("map-list-title");
    if (title) title.textContent = MAP_CAT_LABELS[mapCategory] || MAP_CAT_LABELS.hospital;
  }

  function fetchMapCategoryData() {
    if (mapCategory === "police" || mapCategory === "fire") {
      return api(stationsUrl(mapCategory)).then(function (data) {
        hospitalCache = data.stations || [];
        return hospitalCache;
      });
    }
    return api(hospitalsUrl()).then(function (data) {
      hospitalCache = data.hospitals || [];
      return hospitalCache;
    });
  }

  function withCoordsThen(fn) {
    if (userCoords.lat != null && userCoords.lng != null) {
      fn();
      return;
    }
    EmergencyLocation.requestLocationPermission({ timeout: 10000 })
      .then(function (r) {
        userCoords.lat = r.lat;
        userCoords.lng = r.lng;
        locationLabel = r.district || EmergencyLocation.getDistrictName(r.lat, r.lng);
        fn();
      })
      .catch(function () {
        EmergencyLocation.getApproxLocationByIP().then(function (r) {
          userCoords.lat = r.lat;
          userCoords.lng = r.lng;
          locationLabel = r.district || "";
          fn();
        }).catch(fn);
      });
  }

  function loadMapPage() {
    updateMapListTitle();
    withCoordsThen(function () {
      fetchMapCategoryData().then(function (list) {
        renderPlaceItems("map-hospital-items", list, {
          limit: 10,
          emptyText: MAP_CAT_EMPTY[mapCategory]
        });
        if (mapMode === "map") {
          var mapEl = document.getElementById("citizen-map");
          if (mapEl) mapEl.classList.remove("hidden");
          plotHospitalsOnMap(list);
        } else {
          var mapHide = document.getElementById("citizen-map");
          if (mapHide) mapHide.classList.add("hidden");
        }
        if (activeRequestId) {
          api("/api/emergencies/" + activeRequestId + "/tracking").then(function (t) {
            var lat = (t.citizen && t.citizen.latitude) || (t.location && t.location.latitude) || userCoords.lat;
            var lng = (t.citizen && t.citizen.longitude) || (t.location && t.location.longitude) || userCoords.lng;
            if (lat && lng && mapMode === "map") {
              EmergencyLocation.initMapProvider("citizen-map", lat, lng, { zoom: 14, label: "Active emergency" });
            }
          }).catch(function () {});
        }
      }).catch(function () {
        var el = document.getElementById("map-hospital-items");
        if (el) el.innerHTML = "<p class='cit-empty'>Waa la soo rarayn waayay.</p>";
      });
    });
  }

  function loadHospitalsPage() {
    withCoordsThen(function () {
      api(hospitalsUrl()).then(function (data) {
        hospitalCache = data.hospitals || [];
        renderPlaceItems("hospitals-list", hospitalCache, {
          limit: 30,
          showMapLink: true,
          emptyText: MAP_CAT_EMPTY.hospital
        });
      }).catch(function () {
        var el = document.getElementById("hospitals-list");
        if (el) el.innerHTML = "<p class='cit-empty'>Waa la soo rarayn waayay.</p>";
      });
    });
  }

  function setAvatar(el, photo, name) {
    if (!el) return;
    if (photo) {
      el.innerHTML = "<img src=\"" + photo + "\" alt=\"\">";
    } else {
      el.textContent = (name || "U").charAt(0).toUpperCase();
    }
  }

  function fillProfileForms(p) {
    p = p || {};
    setAvatar(document.getElementById("cit-profile-avatar"), p.profile_photo, p.name);
    var n = document.getElementById("cit-profile-name");
    var ph = document.getElementById("cit-profile-phone");
    if (n) n.textContent = p.name || "Citizen";
    if (ph) ph.textContent = p.phone || p.email || "";
    var set = function (id, val) {
      var el = document.getElementById(id);
      if (el) el.value = val || "";
    };
    set("cit-pf-name", p.name);
    set("cit-pf-phone", p.phone);
    set("cit-pf-email", p.email);
    set("cit-pf-address", p.address);
    set("cit-pf-city", p.city);
    set("cit-pf-ec-name", p.emergency_contact_name);
    set("cit-pf-ec-phone", p.emergency_contact_phone);
    set("cit-pf-ec-relation", p.emergency_contact_relation);
    set("cit-pf-blood", p.blood_type);
    set("cit-pf-notes", p.medical_notes);
    pendingPhotoDataUrl = p.profile_photo || "";
  }

  function loadProfile() {
    api("/api/user/profile").then(function (d) {
      if (d.profile) fillProfileForms(d.profile);
    }).catch(function () {});
  }

  function showProfilePanel(name) {
    ["personal", "contacts", "medical", "password", "help"].forEach(function (k) {
      var el = document.getElementById("cit-profile-" + k);
      if (el) el.classList.toggle("hidden", k !== name);
    });
  }

  var voiceCall = null;
  var voiceTimer = null;
  var voiceConnectedAt = null;

  function fmtCallTimer(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    var m = Math.floor(sec / 60);
    var s = sec % 60;
    return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }

  function setVoiceUi(statusText, opts) {
    opts = opts || {};
    var overlay = document.getElementById("cit-voice-overlay");
    var statusEl = document.getElementById("cit-voice-status");
    var connEl = document.getElementById("cit-voice-conn");
    var timerEl = document.getElementById("cit-voice-timer");
    if (overlay) overlay.classList.add("is-open");
    if (statusEl) statusEl.textContent = statusText || "";
    if (connEl) connEl.textContent = opts.connection || "";
    if (timerEl) {
      timerEl.classList.toggle("hidden", !opts.showTimer);
      if (opts.timerText) timerEl.textContent = opts.timerText;
    }
  }

  function closeVoiceUi(delayMs) {
    var overlay = document.getElementById("cit-voice-overlay");
    function hide() {
      if (overlay) overlay.classList.remove("is-open");
      if (voiceTimer) {
        clearInterval(voiceTimer);
        voiceTimer = null;
      }
      voiceConnectedAt = null;
    }
    if (delayMs) setTimeout(hide, delayMs);
    else hide();
  }

  function startVoiceTimer() {
    voiceConnectedAt = Date.now();
    if (voiceTimer) clearInterval(voiceTimer);
    voiceTimer = setInterval(function () {
      var sec = Math.floor((Date.now() - voiceConnectedAt) / 1000);
      setVoiceUi("Connected", { connection: "Connected", showTimer: true, timerText: fmtCallTimer(sec) });
    }, 1000);
  }

  function teardownVoice(notify) {
    if (voiceCall) {
      if (notify) voiceCall.end();
      else voiceCall.hangup(false);
      voiceCall.destroy();
      voiceCall = null;
    }
    if (voiceTimer) {
      clearInterval(voiceTimer);
      voiceTimer = null;
    }
  }

  function bindVoiceControls() {
    var muteBtn = document.getElementById("cit-voice-mute");
    var speakerBtn = document.getElementById("cit-voice-speaker");
    var endBtn = document.getElementById("cit-voice-end");
    if (muteBtn) {
      muteBtn.onclick = function () {
        if (!voiceCall) return;
        var muted = voiceCall.toggleMute();
        muteBtn.classList.toggle("is-active", muted);
        muteBtn.setAttribute("aria-pressed", muted ? "true" : "false");
      };
    }
    if (speakerBtn) {
      speakerBtn.onclick = function () {
        if (!voiceCall) return;
        var on = voiceCall.toggleSpeaker();
        speakerBtn.classList.toggle("is-active", on);
        speakerBtn.setAttribute("aria-pressed", on ? "true" : "false");
      };
    }
    if (endBtn) {
      endBtn.onclick = function () {
        setVoiceUi("Call Ended", { connection: "Call Ended", showTimer: false });
        teardownVoice(true);
        closeVoiceUi(1200);
      };
    }
  }

  function startInAppVoiceCall(initiateData) {
    if (!window.GurmadVoiceCall) {
      alert("Voice calling is unavailable. Please refresh the page.");
      return;
    }
    if (typeof GurmadVoiceCall.isSecureContext === "function" && !GurmadVoiceCall.isSecureContext()) {
      var httpsMsg = GurmadVoiceCall.HTTPS_REQUIRED_MSG || "Voice calls require HTTPS or localhost.";
      try {
        console.error("[CitizenVoice] WebRTC not started — insecure HTTP", location.href);
      } catch (e) {}
      setVoiceUi(httpsMsg, { connection: httpsMsg, showTimer: false });
      closeVoiceUi(5000);
      return;
    }
    teardownVoice(false);
    bindVoiceControls();
    setVoiceUi("Calling...", { connection: "Connecting...", showTimer: false });

    voiceCall = new GurmadVoiceCall({ role: "citizen" });
    voiceCall.setCallId(initiateData.call_id, initiateData.ice_servers || []);
    voiceCall.unlockAudio();

    voiceCall.on("ringing", function () {
      setVoiceUi("Calling...", { connection: "Ringing Call Center...", showTimer: false });
    });
    voiceCall.on("accepted", function () {
      voiceCall.unlockAudio();
      setVoiceUi("Connecting...", { connection: "Operator answered — connecting audio...", showTimer: false });
    });
    voiceCall.on("connected", function () {
      voiceCall.unlockAudio();
      startVoiceTimer();
      setVoiceUi("Connected", { connection: "Connected", showTimer: true, timerText: "00:00" });
    });
    voiceCall.on("media", function (p) {
      if (p && p.state === "connected" && !voiceConnectedAt) {
        startVoiceTimer();
      }
    });
    voiceCall.on("reconnecting", function () {
      setVoiceUi("Reconnecting...", { connection: "Reconnecting...", showTimer: !!voiceConnectedAt });
    });
    voiceCall.on("rejected", function () {
      setVoiceUi("Call Ended", { connection: "Call rejected", showTimer: false });
      teardownVoice(false);
      closeVoiceUi(1500);
    });
    voiceCall.on("ended", function () {
      setVoiceUi("Call Ended", { connection: "Call Ended", showTimer: false });
      teardownVoice(false);
      closeVoiceUi(1200);
    });
    voiceCall.on("failed", function (p) {
      var msg = (p && p.message) || "Connection Failed";
      if (p && p.hint) msg += " — " + p.hint;
      setVoiceUi("Connection Failed", {
        connection: msg,
        showTimer: false
      });
      teardownVoice(false);
      closeVoiceUi(2500);
    });
    voiceCall.on("busy", function (p) {
      setVoiceUi("Call Ended", {
        connection: (p && p.message) || "Operator busy",
        showTimer: false
      });
      teardownVoice(false);
      closeVoiceUi(1800);
    });

    // Mic + socket in parallel so operator Accept is not lost while waiting on mic prompt.
    Promise.all([voiceCall.requestMic(), voiceCall.connectSocket()])
      .then(function () {
        setVoiceUi("Calling...", { connection: "Microphone ready — connecting...", showTimer: false });
        return voiceCall.join();
      })
      .then(function () {
        voiceCall.signalStart();
        setVoiceUi("Calling...", { connection: "Calling Call Center...", showTimer: false });
      })
      .catch(function (err) {
        try {
          console.error("[CitizenVoice] call setup failed", err && err.name, err && err.message, err);
        } catch (e) {}
        var msg =
          (window.GurmadVoiceCall && GurmadVoiceCall.describeMicError
            ? GurmadVoiceCall.describeMicError(err)
            : null) ||
          (err && err.message) ||
          "Voice call failed";
        setVoiceUi(msg, { connection: msg, showTimer: false });
        teardownVoice(true);
        closeVoiceUi(msg.length > 80 ? 8000 : 4000);
      });
  }

  window.addEventListener("beforeunload", function () {
    if (voiceCall) {
      try { voiceCall.end(); } catch (e) {}
    }
  });

  function initiateCallCenter() {
    var callCenterBtn = document.getElementById("btn-call-center");
    var statusEl = document.getElementById("call-center-status");
    if (
      window.GurmadVoiceCall &&
      typeof GurmadVoiceCall.isSecureContext === "function" &&
      !GurmadVoiceCall.isSecureContext()
    ) {
      var httpsMsg = GurmadVoiceCall.HTTPS_REQUIRED_MSG || "Voice calls require HTTPS or localhost.";
      try {
        console.error("[CitizenVoice] Call blocked — insecure HTTP", location.href);
      } catch (e) {}
      if (statusEl) {
        statusEl.classList.remove("hidden");
        statusEl.textContent = httpsMsg;
      }
      setVoiceUi(httpsMsg, { connection: httpsMsg, showTimer: false });
      closeVoiceUi(5000);
      if (callCenterBtn) callCenterBtn.disabled = false;
      return;
    }
    if (callCenterBtn) callCenterBtn.disabled = true;
    if (statusEl) {
      statusEl.classList.remove("hidden");
      statusEl.textContent = "Getting your location…";
    }
    setVoiceUi("Calling...", { connection: "Preparing call...", showTimer: false });

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
          voice_mode: true,
          device_info: {
            user_agent: navigator.userAgent.slice(0, 300),
            platform: navigator.platform || "",
            language: navigator.language || ""
          }
        }
      }).then(function (data) {
        if (data.success) {
          if (statusEl) statusEl.textContent = data.message || "Connecting in-app voice…";
          // Never open tel: / device dialer — stay inside GurmadNet WebRTC
          startInAppVoiceCall(data);
        } else {
          alert(data.message || "Could not reach Call Center");
          if (statusEl) statusEl.textContent = data.message || "Failed";
          setVoiceUi("Connection Failed", { connection: data.message || "Failed", showTimer: false });
          closeVoiceUi(1600);
        }
      }).catch(function () {
        setVoiceUi("Connection Failed", { connection: "Network error", showTimer: false });
        closeVoiceUi(1600);
      }).finally(function () {
        if (callCenterBtn) callCenterBtn.disabled = false;
      });
    }
    if (window.EmergencyLocation && EmergencyLocation.requestLocationPermission) {
      EmergencyLocation.requestLocationPermission({ timeout: 15000 })
        .then(sendCall)
        .catch(function () {
          if (EmergencyLocation.getApproxLocationByIP) {
            EmergencyLocation.getApproxLocationByIP().then(sendCall).catch(function () {
              alert("GPS required to call Emergency Center so operators know your location.");
              if (callCenterBtn) callCenterBtn.disabled = false;
              closeVoiceUi(0);
            });
          } else {
            alert("GPS required to call Emergency Center.");
            if (callCenterBtn) callCenterBtn.disabled = false;
            closeVoiceUi(0);
          }
        });
    } else {
      alert("Location services unavailable.");
      if (callCenterBtn) callCenterBtn.disabled = false;
      closeVoiceUi(0);
    }
  }

  /* ── Event wiring ── */
  var sosBtn = document.getElementById("btn-sos");
  if (sosBtn) sosBtn.onclick = function () { showPage("type"); };

  var topHome = document.getElementById("btn-top-home");
  if (topHome) {
    topHome.onclick = function () {
      showPage("home");
      try { history.replaceState(null, "", location.pathname); } catch (e) {}
    };
  }

  var callCenterBtn = document.getElementById("btn-call-center");
  if (callCenterBtn) callCenterBtn.onclick = initiateCallCenter;

  document.querySelectorAll(".cit-service-tile, .cit-chip, #page-type .type-btn").forEach(function (btn) {
    btn.onclick = function () {
      var navPage = btn.getAttribute("data-nav-page");
      if (navPage) { showPage(navPage); return; }
      if (btn.getAttribute("data-action") === "call-center") {
        initiateCallCenter();
        return;
      }
      var t = btn.getAttribute("data-type");
      if (t) startTypeFlow(t);
    };
  });

  var backHome1 = document.getElementById("btn-back-home-1");
  if (backHome1) backHome1.onclick = function () { showPage("home"); };
  var backType = document.getElementById("btn-back-type");
  if (backType) backType.onclick = function () { showPage("type"); };
  var backHome4 = document.getElementById("btn-back-home-4");
  if (backHome4) {
    backHome4.onclick = function () {
      if (statusTimer) clearInterval(statusTimer);
      showPage("home");
    };
  }

  var sendAlert = document.getElementById("btn-send-alert");
  if (sendAlert) {
    sendAlert.onclick = function () {
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
  }

  var sendChatBtn = document.getElementById("btn-send-chat");
  if (sendChatBtn) sendChatBtn.onclick = sendChat;
  var chatText = document.getElementById("chat-text");
  if (chatText) {
    chatText.onkeypress = function (e) { if (e.key === "Enter") sendChat(); };
  }
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

  document.querySelectorAll(".gn-user-nav .gn-bnav-item, .gn-user-nav .gn-bnav-sos").forEach(function (btn) {
    btn.onclick = function () {
      var nav = btn.getAttribute("data-nav");
      if (nav === "home") showPage("home");
      else if (nav === "map") showPage("map");
      else if (nav === "type") showPage("type");
      else if (nav === "profile") showPage("profile");
      else if (nav === "status") showPage("status");
    };
  });

  document.querySelectorAll("[data-map-mode]").forEach(function (btn) {
    btn.onclick = function () {
      mapMode = btn.getAttribute("data-map-mode");
      document.querySelectorAll("[data-map-mode]").forEach(function (b) {
        b.classList.toggle("active", b === btn);
      });
      var mapEl = document.getElementById("citizen-map");
      if (mapMode === "list") {
        if (mapEl) mapEl.classList.add("hidden");
      } else {
        if (mapEl) mapEl.classList.remove("hidden");
        plotHospitalsOnMap(hospitalCache);
      }
    };
  });

  document.querySelectorAll("[data-map-cat]").forEach(function (btn) {
    btn.onclick = function () {
      mapCategory = btn.getAttribute("data-map-cat") || "hospital";
      document.querySelectorAll("[data-map-cat]").forEach(function (b) {
        b.classList.toggle("active", b === btn);
      });
      loadMapPage();
    };
  });

  var hospToMap = document.getElementById("btn-hospitals-to-map");
  if (hospToMap) {
    hospToMap.onclick = function () {
      mapCategory = "hospital";
      document.querySelectorAll("[data-map-cat]").forEach(function (b) {
        b.classList.toggle("active", b.getAttribute("data-map-cat") === "hospital");
      });
      showPage("map");
    };
  }

  document.querySelectorAll("[data-profile-panel]").forEach(function (btn) {
    btn.onclick = function () {
      showProfilePanel(btn.getAttribute("data-profile-panel"));
    };
  });

  var profileForm = document.getElementById("cit-profile-form");
  if (profileForm) {
    profileForm.onsubmit = function (e) {
      e.preventDefault();
      var body = {
        name: document.getElementById("cit-pf-name").value,
        phone: document.getElementById("cit-pf-phone").value,
        address: document.getElementById("cit-pf-address").value,
        city: document.getElementById("cit-pf-city").value
      };
      if (pendingPhotoDataUrl) body.profile_photo = pendingPhotoDataUrl;
      api("/api/user/profile", { method: "PUT", body: body }).then(function (d) {
        if (d.success) {
          fillProfileForms(d.profile);
          alert("Profile saved.");
        } else alert(d.message || "Could not save profile");
      });
    };
  }

  var contactsForm = document.getElementById("cit-contacts-form");
  if (contactsForm) {
    contactsForm.onsubmit = function (e) {
      e.preventDefault();
      api("/api/user/profile", {
        method: "PUT",
        body: {
          emergency_contact_name: document.getElementById("cit-pf-ec-name").value,
          emergency_contact_phone: document.getElementById("cit-pf-ec-phone").value,
          emergency_contact_relation: document.getElementById("cit-pf-ec-relation").value
        }
      }).then(function (d) {
        if (d.success) {
          fillProfileForms(d.profile);
          alert("Contacts saved.");
        } else alert(d.message || "Could not save contacts");
      });
    };
  }

  var medicalForm = document.getElementById("cit-medical-form");
  if (medicalForm) {
    medicalForm.onsubmit = function (e) {
      e.preventDefault();
      api("/api/user/profile", {
        method: "PUT",
        body: {
          blood_type: document.getElementById("cit-pf-blood").value,
          medical_notes: document.getElementById("cit-pf-notes").value
        }
      }).then(function (d) {
        if (d.success) {
          fillProfileForms(d.profile);
          alert("Medical info saved.");
        } else alert(d.message || "Could not save");
      });
    };
  }

  var passwordForm = document.getElementById("cit-password-form");
  if (passwordForm) {
    passwordForm.onsubmit = function (e) {
      e.preventDefault();
      api("/api/user/password", {
        method: "POST",
        body: {
          current_password: document.getElementById("cit-pw-current").value,
          new_password: document.getElementById("cit-pw-new").value,
          confirm_password: document.getElementById("cit-pw-confirm").value
        }
      }).then(function (d) {
        if (d.success) {
          passwordForm.reset();
          alert(d.message || "Password updated.");
        } else alert(d.message || "Could not update password");
      });
    };
  }

  var photoInput = document.getElementById("cit-profile-photo-input");
  if (photoInput) {
    photoInput.onchange = function (e) {
      var file = e.target.files && e.target.files[0];
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function () {
        var dataUrl = reader.result;
        if (String(dataUrl).length > 120000) {
          alert("Photo too large. Choose a smaller image.");
          return;
        }
        pendingPhotoDataUrl = dataUrl;
        setAvatar(document.getElementById("cit-profile-avatar"), dataUrl, "U");
      };
      reader.readAsDataURL(file);
    };
  }

  checkActiveBanner();
  loadRecent();
  refreshHomeLocationCard();

  function applyHashRoute() {
    var hash = (location.hash || "").toLowerCase();
    if (hash === "#type" || hash === "#emergency" || hash === "#report" || hash === "#sos") {
      showPage("type");
      return;
    }
    if (hash === "#map") { showPage("map"); return; }
    if (hash === "#hospitals") { showPage("hospitals"); return; }
    if (hash === "#profile") { showPage("profile"); return; }
    if (hash === "#status" || hash === "#alerts") { showPage("status"); return; }
    if (hash === "#call-center") {
      showPage("home");
      var ccBtn = document.getElementById("btn-call-center");
      var ccWrap = document.getElementById("call-center");
      if (ccWrap && ccWrap.scrollIntoView) ccWrap.scrollIntoView({ behavior: "smooth", block: "center" });
      if (ccBtn) setTimeout(function () { ccBtn.focus(); }, 200);
    }
  }
  applyHashRoute();
  window.addEventListener("hashchange", applyHashRoute);
})();
