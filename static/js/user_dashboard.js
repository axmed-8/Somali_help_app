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
  var lastTracking = null;
  var lastMapsDestination = null;

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

  function notifTitle(type) {
    var titles = {
      request_received: "Request received",
      request_accepted: "Request accepted",
      team_assigned: "Team assigned",
      team_dispatched: "Team dispatched",
      team_arrived: "Team arrived",
      emergency_completed: "Emergency completed",
      system_alert: "Important alert",
      announcement: "Announcement"
    };
    return titles[type] || "Update";
  }

  function parseCommDate(value) {
    if (!value) return null;
    var d = new Date(value);
    if (!isNaN(d.getTime())) return d;
    var m = String(value).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
    if (m) {
      d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
      if (!isNaN(d.getTime())) return d;
    }
    return null;
  }

  function formatCommTime(value) {
    var d = parseCommDate(value);
    if (!d) return value ? String(value) : "";
    try {
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (e) {
      return String(value);
    }
  }

  function formatCommDayTime(value) {
    var d = parseCommDate(value);
    if (!d) return value ? String(value) : "—";
    var now = new Date();
    var startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var startThat = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var diffDays = Math.round((startToday - startThat) / 86400000);
    var time = formatCommTime(d.toISOString());
    if (diffDays === 0) return "Today · " + time;
    if (diffDays === 1) return "Yesterday · " + time;
    try {
      return d.toLocaleDateString([], { month: "short", day: "numeric" }) + " · " + time;
    } catch (e) {
      return String(value);
    }
  }

  function notifDayBucket(value) {
    var d = parseCommDate(value);
    if (!d) return "earlier";
    var now = new Date();
    var startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var startThat = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var diffDays = Math.round((startToday - startThat) / 86400000);
    if (diffDays === 0) return "today";
    if (diffDays === 1) return "yesterday";
    return "earlier";
  }

  function histTypeMeta(type) {
    var t = String(type || "").toLowerCase();
    if (t === "medical" || t === "family_help") return { icon: "🏥", label: "Medical Emergency" };
    if (t === "fire") return { icon: "🔥", label: "Fire Emergency" };
    if (t === "security" || t === "accident") return { icon: "👮", label: "Police Emergency" };
    return { icon: "🚨", label: type ? String(type) : "Emergency" };
  }

  function histBadgeLabel(cls) {
    if (cls === "completed") return "Completed";
    if (cls === "cancelled") return "Cancelled";
    return "Active";
  }

  function isSystemChatMessage(m) {
    var role = String((m && m.sender_role) || "").toLowerCase();
    var type = String((m && m.msg_type) || "").toLowerCase();
    return role === "system" || role === "admin" || type === "system" || type === "alert";
  }

  function chatSenderLabel(m) {
    if (isSystemChatMessage(m)) return "Somali Help App";
    var role = String((m && m.sender_role) || "").toLowerCase();
    if (role === "citizen") return "You";
    if (role === "police") return "Police";
    if (role === "fire") return "Fire team";
    if (role === "hospital") return "Hospital team";
    return "Response team";
  }

  function setDashHomeMode(mode) {
    var idle = document.getElementById("ers-idle");
    var activeWrap = document.getElementById("ers-active-wrap");
    var summaryWrap = document.getElementById("ers-summary-wrap");
    var chatActive = document.getElementById("ers-chat-active");
    var chatIdle = document.getElementById("ers-chat-idle");
    var tracking = mode === "tracking";
    var summary = mode === "summary";
    if (idle) idle.classList.toggle("hidden", tracking || summary);
    if (activeWrap) activeWrap.classList.toggle("hidden", !tracking);
    if (summaryWrap) summaryWrap.classList.toggle("hidden", !summary);
    if (chatActive) chatActive.classList.toggle("hidden", !tracking);
    if (chatIdle) chatIdle.classList.toggle("hidden", tracking);
    var chip = document.getElementById("header-status-chip");
    if (chip) {
      if (tracking) {
        chip.textContent = "Live tracking";
        chip.className = "gn-status-chip live hidden";
      } else {
        chip.textContent = "All clear";
        chip.className = "gn-status-chip idle hidden";
      }
    }
    if (!tracking && EmergencyLocation && EmergencyLocation.stopEmergencyTracking) {
      EmergencyLocation.stopEmergencyTracking();
    }
    if (!tracking) {
      lastTracking = null;
      lastMapsDestination = null;
      updateGoogleMapsLink(lastEmergency, null);
    }
  }

  function setActiveMode(active) {
    setDashHomeMode(active ? "tracking" : "idle");
  }

  function emergencyTypeLabel(em) {
    var t = String((em && em.type) || "").toLowerCase();
    if (t === "medical" || t === "family_help") return "Medical Emergency";
    if (t === "fire") return "Fire Emergency";
    if (t === "security" || t === "accident") return "Police Emergency";
    return "Emergency";
  }

  function bilingualStatus(em) {
    var stage = String((em && em.display_stage) || "").toLowerCase();
    var st = String((em && em.status) || "").toLowerCase();
    var map = {
      submitted: ["Codsiga waa la helay", "We received your request"],
      finding_nearest: ["Waxaan raadineynaa gargaar", "Finding help near you"],
      assigned: ["Xarun ayaa loo xilsaaray", "A facility was assigned"],
      accepted: ["Kooxdu waa aqbashay", "A team accepted your case"],
      responder_dispatched: ["Gargaarku waa socdaa", "Help is on the way"],
      on_the_way: ["Ambalaas ayaa ku soo socota", "Ambulance is coming to you"],
      arrived: ["Kooxdu waa gaadhay", "Your team has arrived"],
      completed: ["Waad nabadqabtaa", "You are safe"],
      cancelled: ["Codsiga waa la joojiyay", "This emergency was cancelled"],
      no_facility: ["Xarun lama helin", "No facility is available right now"]
    };
    if (map[stage]) return { so: map[stage][0], en: map[stage][1] };
    if (st === "completed" || st === "resolved") return { so: map.completed[0], en: map.completed[1] };
    if (st === "cancelled") return { so: map.cancelled[0], en: map.cancelled[1] };
    if (st === "accepted" || st === "dispatched") return { so: map.accepted[0], en: map.accepted[1] };
    return {
      so: (em && em.display_stage_label) || "Xaaladda degdegga",
      en: (em && em.display_stage_label) || "Emergency status"
    };
  }

  function statusTipText(em) {
    var stage = String((em && em.display_stage) || "").toLowerCase();
    var st = String((em && em.status) || "").toLowerCase();
    if (st === "cancelled" || stage === "cancelled") return "This request is closed. You can report a new emergency if needed.";
    if (st === "completed" || st === "resolved" || stage === "completed") return "Stay safe. Thank you for using Somali Help App.";
    if (stage === "arrived") return "Your responders are with you. Follow their guidance.";
    if (stage === "on_the_way" || stage === "responder_dispatched" || st === "dispatched") {
      return "Stay where you are if it is safe. Keep your phone nearby.";
    }
    if (stage === "accepted" || stage === "assigned" || st === "accepted") {
      return "A team is preparing. Stay calm and keep this screen open.";
    }
    return "Stay where you are. Help is being arranged.";
  }

  function facilityOrg(em, tracking) {
    var t = String((em && em.type) || "").toLowerCase();
    var station = (tracking && tracking.station) || (em && em.station) || null;
    var stype = station && String(station.type || "").toLowerCase();
    if (stype === "police" || t === "security" || t === "accident") {
      return { key: "police", label: "Police Station", icon: "👮" };
    }
    if (stype === "fire" || t === "fire") {
      return { key: "fire", label: "Fire Station", icon: "🔥" };
    }
    return { key: "hospital", label: "Hospital", icon: "🏥" };
  }

  function friendlyTimelineLabel(label) {
    var raw = String(label || "").trim();
    var key = raw.toLowerCase().replace(/[_-]+/g, " ");
    var map = {
      "request received": "We received your request",
      "request submitted": "We received your request",
      submitted: "We received your request",
      "finding nearest": "Finding help near you",
      "finding nearest hospital": "Finding help near you",
      assigned: "Facility assigned",
      "facility assigned": "Facility assigned",
      accepted: "Team accepted your case",
      "responders assigned": "Team accepted your case",
      dispatched: "Help is on the way",
      "responder dispatched": "Help is on the way",
      "on the way": "Responders are coming",
      "ambulance en route": "Ambulance is coming",
      arrived: "Team arrived",
      "team arrived": "Team arrived",
      completed: "You are safe",
      resolved: "You are safe",
      cancelled: "Request cancelled"
    };
    if (map[key]) return map[key];
    if (map[raw.toLowerCase()]) return map[raw.toLowerCase()];
    return raw || "Update";
  }

  function friendlyTimelineTime(step) {
    if (!step) return "Pending";
    if (step.timestamp) {
      var t = String(step.timestamp);
      try {
        var d = new Date(t);
        if (!isNaN(d.getTime())) {
          return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        }
      } catch (e) {}
      return t;
    }
    return step.completed ? "Done" : "Up next";
  }

  function updateFacilityPresentation(em, tracking) {
    var org = facilityOrg(em, tracking);
    var card = document.getElementById("gn-hosp-card");
    var icon = document.getElementById("facility-org-icon");
    var label = document.getElementById("facility-org-label");
    var responderLine = document.getElementById("facility-responder-line");
    if (card) card.setAttribute("data-org", org.key);
    if (icon) icon.textContent = org.icon;
    if (label) label.textContent = org.label;
    if (responderLine) {
      var u = (em && em.dispatch_unit) || (tracking && tracking.dispatch_unit) || {};
      var team = u.team_name || (em && em.team) || (tracking && tracking.team_label) || "";
      var vehicle = u.vehicle_number || "";
      if (vehicle && team) responderLine.textContent = "Responder: " + vehicle + " · " + team;
      else if (team) responderLine.textContent = "Responder: " + team;
      else if (org.key === "hospital") responderLine.textContent = "Looking for an ambulance…";
      else if (org.key === "police") responderLine.textContent = "Looking for a police unit…";
      else responderLine.textContent = "Looking for a fire unit…";
    }
  }

  function statusHeroTone(em) {
    var stage = String((em && em.display_stage) || "").toLowerCase();
    var st = String((em && em.status) || "").toLowerCase();
    if (st === "cancelled" || stage === "cancelled" || stage === "no_facility") return "critical";
    if (st === "completed" || st === "resolved" || stage === "completed") return "safe";
    if (
      st === "accepted" ||
      st === "dispatched" ||
      st === "in_progress" ||
      stage === "accepted" ||
      stage === "responder_dispatched" ||
      stage === "on_the_way" ||
      stage === "arrived" ||
      stage === "assigned"
    ) {
      return "assigned";
    }
    return "processing";
  }

  function looksLikeCoords(text) {
    return /^\s*-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?\s*$/.test(String(text || ""));
  }

  function formatGpsTime(d) {
    try {
      return (d || new Date()).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch (e) {
      return "—";
    }
  }

  function readableAddressLabel(fix, fallback) {
    if (!fix) return fallback || "Detecting…";
    var label = fix.district || fallback || "";
    if (label && !looksLikeCoords(label)) return label;
    if (fix.lat != null && fix.lng != null && EmergencyLocation && EmergencyLocation.getDistrictName) {
      var sync = EmergencyLocation.getDistrictName(fix.lat, fix.lng);
      if (sync && !looksLikeCoords(sync)) return sync;
    }
    return label || fallback || "Resolving address…";
  }

  function refreshAddressAsync(fix, onReady) {
    if (!fix || fix.lat == null || fix.lng == null || !EmergencyLocation || !EmergencyLocation.reverseGeocode) return;
    EmergencyLocation.reverseGeocode(fix.lat, fix.lng).then(function (label) {
      if (!label || looksLikeCoords(label)) return;
      fix.district = label;
      if (currentUserLoc && currentUserLoc.lat === fix.lat && currentUserLoc.lng === fix.lng) {
        currentUserLoc.district = label;
      }
      if (typeof onReady === "function") onReady(label);
    }).catch(function () {});
  }

  function facilityPoint(obj) {
    if (!obj) return null;
    var lat = obj.latitude != null ? obj.latitude : obj.lat;
    var lng = obj.longitude != null ? obj.longitude : obj.lng;
    if (lat == null || lng == null) return null;
    lat = Number(lat);
    lng = Number(lng);
    if (isNaN(lat) || isNaN(lng)) return null;
    if (Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
    return { lat: lat, lng: lng };
  }

  function resolveNavDestination(em, tracking) {
    // Destination must be the currently assigned hospital / police / fire only.
    // Never use emergency-site coords, cached searches, or unrelated places.
    var t = tracking || lastTracking || null;
    var hospital = (t && t.hospital) || null;
    var station = (t && t.station) || null;
    if (!hospital && em && em.hospital && (em.assigned_hospital_id || (em.hospital && em.hospital.id))) {
      hospital = em.hospital;
    }
    if (!station && em && em.station) {
      station = em.station;
    }
    var org = facilityOrg(em, t);
    if (org.key === "police" || org.key === "fire") {
      var stationPt = facilityPoint(station);
      if (stationPt) return stationPt;
    }
    var hospitalPt = facilityPoint(hospital);
    if (hospitalPt) return hospitalPt;
    stationPt = facilityPoint(station);
    if (stationPt) return stationPt;
    return null;
  }

  function updateGoogleMapsLink(em, tracking) {
    var btn = document.getElementById("btn-open-google-maps");
    if (!btn) return;
    var origin = currentUserLoc && currentUserLoc.lat != null && currentUserLoc.lng != null
      ? { lat: Number(currentUserLoc.lat), lng: Number(currentUserLoc.lng) }
      : null;
    var dest = resolveNavDestination(em || lastEmergency, tracking);
    lastMapsDestination = dest;
    if (!origin || !dest || isNaN(origin.lat) || isNaN(origin.lng) || isNaN(dest.lat) || isNaN(dest.lng)) {
      btn.removeAttribute("href");
      btn.setAttribute("aria-disabled", "true");
      btn.classList.add("is-disabled");
      btn.title = !dest
        ? "Waiting for assigned hospital, police, or fire station"
        : "Waiting for your GPS location";
      return;
    }
    var url =
      "https://www.google.com/maps/dir/?api=1" +
      "&origin=" + encodeURIComponent(origin.lat.toFixed(6) + "," + origin.lng.toFixed(6)) +
      "&destination=" + encodeURIComponent(dest.lat.toFixed(6) + "," + dest.lng.toFixed(6)) +
      "&travelmode=driving";
    btn.href = url;
    btn.removeAttribute("aria-disabled");
    btn.classList.remove("is-disabled");
    btn.title = "Open navigation to assigned facility";
  }

  function updateGpsPresentation(fix) {
    var locEl = document.getElementById("gps-current-location");
    var timeEl = document.getElementById("gps-last-update");
    var accEl = document.getElementById("gps-accuracy");
    var mapYou = document.getElementById("map-detail-you");
    var label = readableAddressLabel(fix, "Live GPS");
    if (locEl) locEl.textContent = label;
    if (mapYou) mapYou.textContent = label;
    if (timeEl) timeEl.textContent = formatGpsTime(new Date());
    if (accEl) {
      accEl.textContent =
        fix && fix.accuracy != null && !isNaN(fix.accuracy)
          ? "±" + Math.round(fix.accuracy) + " m"
          : "—";
    }
    updateGoogleMapsLink(lastEmergency, lastTracking);
  }

  function renderStatusHeader(em) {
    var title = document.getElementById("ers-status-title");
    var ref = document.getElementById("ers-ref-num");
    var updated = document.getElementById("ers-last-update");
    var typeEl = document.getElementById("ers-em-type");
    var facilityEl = document.getElementById("ers-facility");
    var soEl = document.getElementById("ers-status-so");
    var enEl = document.getElementById("ers-status-en");
    var hero = document.getElementById("ers-status-hero");
    var bi = bilingualStatus(em);
    if (title) title.textContent = bi.en || em.display_stage_label || "Request received";
    if (ref) ref.textContent = "#" + em.id;
    if (updated) updated.textContent = em.last_update || em.timestamp || "—";
    if (typeEl) typeEl.textContent = emergencyTypeLabel(em);
    if (facilityEl) {
      facilityEl.textContent =
        detailHospitalName(em) !== "—"
          ? detailHospitalName(em)
          : ((em.dispatch_unit && em.dispatch_unit.team_name) || em.team || "Assigning…");
    }
    if (soEl) soEl.textContent = bi.so;
    if (enEl) enEl.textContent = bi.en;
    var tipEl = document.getElementById("ers-status-tip");
    if (tipEl) tipEl.textContent = statusTipText(em);
    if (hero) hero.setAttribute("data-tone", statusHeroTone(em));
    renderTeamPanel(em);
    updateFacilityPresentation(em, null);
    updateGoogleMapsLink(em, lastTracking);
    syncLiveTimeline(em);
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
    var etaEl = document.getElementById("info-eta");
    var distEl = document.getElementById("info-distance");
    if (etaEl) etaEl.textContent = em.eta_minutes != null ? "~" + em.eta_minutes + " min" : "—";
    if (distEl) distEl.textContent = em.distance_km != null ? em.distance_km + " km" : "—";
    updateEtaRing(em.eta_minutes);

    var mapYou = document.getElementById("map-detail-you");
    var mapEm = document.getElementById("map-detail-em");
    var mapTeam = document.getElementById("map-detail-team");
    if (mapYou && currentUserLoc) mapYou.textContent = readableAddressLabel(currentUserLoc, "Live GPS");
    if (mapEm) {
      var emLoc = em.location || "";
      mapEm.textContent = looksLikeCoords(emLoc) ? "Emergency site" : (emLoc || "Emergency site");
    }
    var hospName = (em.hospital && em.hospital.name) || teamName || "No data available";
    var mapHospital = document.getElementById("map-detail-hospital");
    if (mapHospital) mapHospital.textContent = hospName;
    var mapHospitalLeg = document.getElementById("map-detail-hospital-leg");
    if (mapHospitalLeg) mapHospitalLeg.textContent = hospName;
    if (mapTeam) mapTeam.textContent = vehicle ? vehicle + " · " + teamName : teamName;
    var mapStatus = document.getElementById("map-detail-status");
    if (mapStatus) mapStatus.textContent = em.display_stage_label || em.status || "—";
    updateFacilityPresentation(em, null);
    updateGoogleMapsLink(em, lastTracking);
    syncLiveTimeline(em);

    var phoneEl = document.getElementById("info-hospital-phone");
    if (phoneEl) {
      if (teamContact && teamContact !== "—") {
        phoneEl.textContent = teamContact;
        phoneEl.href = "tel:" + String(teamContact).replace(/\s/g, "");
      } else {
        phoneEl.textContent = "—";
        phoneEl.removeAttribute("href");
      }
    }

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

  function detailUiState(em) {
    if (!em) return "empty";
    var st = String(em.status || "").toLowerCase();
    var stage = String(em.display_stage || "").toLowerCase();
    if (st === "cancelled" || stage === "cancelled") return "cancelled";
    if (st === "completed" || st === "resolved" || stage === "completed") return "completed";
    if (
      st === "no_hospital_available" ||
      st === "no_responder_available" ||
      st === "timeout" ||
      st === "rejected" ||
      st === "rejected_by_hospital" ||
      st.indexOf("reject") >= 0 ||
      stage === "no_facility" ||
      stage === "no_responder" ||
      stage === "timeout" ||
      stage === "rejected"
    ) {
      return "no_hospital";
    }
    return "active";
  }

  function detailHospitalName(em) {
    if (!em) return "—";
    if (em.hospital && em.hospital.name) return em.hospital.name;
    if (em.assigned_hospital_name) return em.assigned_hospital_name;
    if (em.team) return em.team;
    return "—";
  }

  function detailResponseTime(em) {
    if (!em || !em.timestamp) return "—";
    var end = em.last_update || em.timestamp;
    try {
      var ms = new Date(end).getTime() - new Date(em.timestamp).getTime();
      if (!(ms >= 0)) return "—";
      var mins = Math.round(ms / 60000);
      if (mins < 1) return "< 1 min";
      if (mins < 60) return mins + " min";
      return Math.floor(mins / 60) + "h " + (mins % 60) + "m";
    } catch (e) {
      return "—";
    }
  }

  function renderTrackingTimeline(timeline) {
    if (!timeline || !timeline.length) {
      return "<p class='gn-detail-muted'>Timeline loading…</p>";
    }
    var firstPending = timeline.findIndex(function (s) { return !s.completed; });
    return timeline.map(function (step, i) {
      var cls = "gn-tl-step";
      if (step.completed) cls += " done";
      else cls += " pending";
      if (i === firstPending && firstPending >= 0) cls += " current";
      var icon = step.completed ? "✓" : (i === firstPending ? "●" : String(i + 1));
      return "<div class='" + cls + "'>" +
        "<div class='gn-tl-icon' aria-hidden='true'>" + icon + "</div>" +
        "<div class='gn-tl-body'>" +
        "<div class='gn-tl-label'>" + escapeHtml(friendlyTimelineLabel(step.label)) + "</div>" +
        "<div class='gn-tl-time'>" + escapeHtml(friendlyTimelineTime(step)) + "</div>" +
        "</div></div>";
    }).join("");
  }

  function syncLiveTimeline(em) {
    var live = document.getElementById("ers-timeline-live");
    if (!live) return;
    if (!em || !em.timeline) {
      live.innerHTML = "<p class='gn-detail-muted'>Progress updates will appear here.</p>";
      return;
    }
    live.innerHTML = renderTrackingTimeline(em.timeline);
  }

  function sosBaseHref() {
    var sosLink = document.querySelector(".gn-bnav-sos, .gn-sidebar-sos");
    if (sosLink && sosLink.getAttribute("href")) {
      return String(sosLink.getAttribute("href")).split("#")[0] || "/";
    }
    return "/";
  }

  function bindSummaryActions(root) {
    if (!root) return;
    root.querySelectorAll("[data-detail-nav]").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        loadDashboard();
        showPanel(btn.getAttribute("data-detail-nav") || "overview");
      });
    });
    root.querySelectorAll("[data-rate-star]").forEach(function (star) {
      star.addEventListener("click", function () {
        var n = parseInt(star.getAttribute("data-rate-star"), 10) || 0;
        root.querySelectorAll("[data-rate-star]").forEach(function (s) {
          var v = parseInt(s.getAttribute("data-rate-star"), 10) || 0;
          s.classList.toggle("is-on", v <= n);
        });
      });
    });
    var rateBtn = root.querySelector("[data-status-action='rate']");
    if (rateBtn) {
      rateBtn.addEventListener("click", function () {
        var box = root.querySelector(".cit-rate-box, .gn-detail-rate");
        if (box) box.innerHTML = "<p class='gn-detail-note'>Thank you for your feedback.</p>";
      });
    }
  }

  function summaryHtml(em) {
    var state = detailUiState(em);
    var base = sosBaseHref();
    var sosHref = base + "#type";
    var callHref = base + "#call-center";

    if (state === "completed") {
      return (
        "<div class='gn-detail-state gn-detail-completed'>" +
          "<div class='gn-detail-icon ok' aria-hidden='true'>✓</div>" +
          "<h3>You are Safe</h3>" +
          "<p>Emergency Completed</p>" +
          "<div class='gn-detail-row'><span>Hospital</span><strong>" + escapeHtml(detailHospitalName(em)) + "</strong></div>" +
          "<div class='gn-detail-row'><span>Completion time</span><strong>" + escapeHtml(em.last_update || em.timestamp || "—") + "</strong></div>" +
          "<div class='gn-detail-row'><span>Response duration</span><strong>" + escapeHtml(detailResponseTime(em)) + "</strong></div>" +
          "<p class='gn-detail-note'>Thank you for using Somali Help App.</p>" +
          "<div class='gn-detail-rate'>" +
            "<p class='gn-detail-live-label'>Rate Experience</p>" +
            "<div class='cit-rate-stars' style='display:flex;justify-content:center;gap:6px;margin:10px 0;'>" +
              [1, 2, 3, 4, 5].map(function (n) {
                return "<button type='button' class='cit-rate-star' data-rate-star='" + n + "' style='border:0;background:transparent;font-size:1.5rem;color:#D6CBDE;cursor:pointer;'>★</button>";
              }).join("") +
            "</div>" +
            "<button type='button' class='gn-btn-primary' data-status-action='rate' style='width:100%;'>Submit rating</button>" +
          "</div>" +
          "<a class='gn-btn-outline' href='#' data-detail-nav='overview' style='display:block;text-align:center;margin-top:12px;text-decoration:none;padding:12px;'>Back Home</a>" +
        "</div>"
      );
    }
    if (state === "cancelled") {
      return (
        "<div class='gn-detail-state gn-detail-cancelled'>" +
          "<div class='gn-detail-icon cancel' aria-hidden='true'>×</div>" +
          "<h3>Emergency Cancelled</h3>" +
          "<p>This request was cancelled. Live tracking is no longer available.</p>" +
          "<div class='gn-detail-row'><span>Reference</span><strong>#" + escapeHtml(em.id) + "</strong></div>" +
          "<div class='gn-detail-actions'>" +
            "<a class='gn-btn-primary' href='" + escapeHtml(sosHref) + "'>Report New Emergency</a>" +
            "<a class='gn-btn-outline' href='#' data-detail-nav='overview'>Back Home</a>" +
          "</div>" +
        "</div>"
      );
    }
    /* no_hospital / rejected */
    return (
      "<div class='gn-detail-state gn-detail-nohosp'>" +
        "<div class='gn-detail-icon warn' aria-hidden='true'>!</div>" +
        "<h3>Hospital unavailable</h3>" +
        "<p>Hospital could not accept your request, or no hospital is available right now.</p>" +
        "<div class='gn-detail-row'><span>Reference</span><strong>#" + escapeHtml(em.id) + "</strong></div>" +
        "<div class='gn-detail-row'><span>Status</span><strong>" + escapeHtml(em.display_stage_label || em.status || "Unavailable") + "</strong></div>" +
        "<div class='gn-detail-actions'>" +
          "<a class='gn-btn-primary' href='" + escapeHtml(callHref) + "'>Call Center</a>" +
          "<a class='gn-btn-outline' href='" + escapeHtml(sosHref) + "'>Retry</a>" +
        "</div>" +
      "</div>"
    );
  }

  function renderHomeSummary(em) {
    var panel = document.getElementById("ers-summary-panel");
    if (!panel || !em) return;
    lastEmergency = em;
    panel.innerHTML = summaryHtml(em);
    bindSummaryActions(panel);
  }

  function showLiveTrackingDashboard(em) {
    if (!em) return;
    lastEmergency = em;
    activeRequestId = em.id;
    setDashHomeMode("tracking");
    renderStatusHeader(em);
    renderEmergencyDetails(em);
    showPanel("overview");
    if (mapsReady) {
      EmergencyLocation.startEmergencyTracking(em.id);
      setTimeout(function () {
        if (dashMap) dashMap.resize();
        refreshMapTracking();
      }, 200);
    }
    loadChat();
  }

  function showSummaryDashboard(em) {
    if (!em) return;
    lastEmergency = em;
    if (EmergencyLocation && EmergencyLocation.stopEmergencyTracking) {
      EmergencyLocation.stopEmergencyTracking();
    }
    /* Do not keep live-tracking session for finished cases */
    if (!activeRequestId || String(activeRequestId) === String(em.id)) {
      activeRequestId = null;
    }
    setDashHomeMode("summary");
    renderHomeSummary(em);
    renderEmergencyDetails(em);
    showPanel("overview");
  }

  function renderEmergencyDetails(em) {
    var el = document.getElementById("ers-timeline");
    var title = document.getElementById("ers-details-title");
    var contact = document.getElementById("btn-contact-team");
    if (!el) return;

    if (!em) {
      if (title) title.textContent = "Emergency Details";
      if (contact) contact.classList.add("hidden");
      el.innerHTML = "<p class='gn-detail-muted'>No emergency selected.</p>";
      return;
    }

    lastEmergency = em;
    var state = detailUiState(em);
    if (state === "active") {
      if (title) title.textContent = "Emergency progress";
      if (contact) contact.classList.remove("hidden");
      var bi = bilingualStatus(em);
      el.innerHTML =
        "<p class='gn-detail-live-label'>Active emergency · Ref #" + escapeHtml(em.id) + "</p>" +
        "<p class='gn-detail-live-status'>" + escapeHtml(bi.en || em.display_stage_label || "In progress") + "</p>" +
        "<p class='gn-detail-live-tip'>" + escapeHtml(statusTipText(em)) + "</p>" +
        renderTrackingTimeline(em.timeline);
      syncLiveTimeline(em);
      return;
    }

    if (title) {
      title.textContent =
        state === "completed" ? "Emergency Completed" :
        state === "cancelled" ? "Emergency Cancelled" :
        "Hospital Unavailable";
    }
    if (contact) contact.classList.add("hidden");
    el.innerHTML = summaryHtml(em);
    bindSummaryActions(el);
  }

  function normalizeStatusPayload(req) {
    return {
      id: req.id,
      type: req.type,
      status: req.status,
      display_stage: req.display_stage,
      display_stage_label: req.display_stage_label,
      timeline: req.timeline,
      hospital: req.hospital || req.recommended_hospital,
      assigned_hospital_name: req.assigned_hospital_name,
      team: req.team_label || req.team,
      timestamp: req.timestamp,
      last_update: req.last_location_update || req.last_update || req.timestamp,
      eta_minutes: req.eta_minutes,
      distance_km: req.distance_km,
      dispatch_unit: req.dispatch_unit
    };
  }

  function openEmergencyDetails(emOrId) {
    function route(em) {
      if (!em) return;
      var state = detailUiState(em);
      if (state === "active") {
        showLiveTrackingDashboard(em);
      } else {
        showSummaryDashboard(em);
      }
    }
    if (emOrId && typeof emOrId === "object") {
      route(emOrId);
      return;
    }
    var id = emOrId || activeRequestId;
    if (!id) {
      route(lastEmergency);
      return;
    }
    api("/api/patient/request/status?id=" + encodeURIComponent(id)).then(function (data) {
      if (data && data.request) {
        route(normalizeStatusPayload(data.request));
      } else if (lastEmergency) {
        route(lastEmergency);
      }
    });
  }

  function notifHtml(n) {
    var unreadCls = n.read ? "" : " unread";
    var title = notifTitle(n.type);
    return "<article class='gn-notif-item" + unreadCls + "'>" +
      "<div class='gn-notif-icon-wrap' aria-hidden='true'>" + notifIcon(n.type) + "</div>" +
      "<div class='gn-notif-body'>" +
        "<div class='gn-notif-top'>" +
          "<strong class='gn-notif-title'>" + escapeHtml(title) + "</strong>" +
          (n.read ? "" : "<span class='gn-notif-unread-pill'>New</span>") +
        "</div>" +
        "<p>" + escapeHtml(n.message) + "</p>" +
        "<time>" + escapeHtml(formatCommDayTime(n.timestamp)) + "</time>" +
      "</div>" +
    "</article>";
  }

  function renderGroupedNotifications(list) {
    if (!list || !list.length) {
      return "<div class='gn-empty-state gn-empty-state-sm'>" +
        "<span class='gn-empty-ico' aria-hidden='true'>🔔</span>" +
        "<strong>No alerts yet</strong>" +
        "<p>Emergency updates will appear here.</p>" +
      "</div>";
    }
    var groups = { today: [], yesterday: [], earlier: [] };
    list.forEach(function (n) {
      groups[notifDayBucket(n.timestamp)].push(n);
    });
    var order = [
      ["today", "Today"],
      ["yesterday", "Yesterday"],
      ["earlier", "Earlier"]
    ];
    var html = "";
    order.forEach(function (pair) {
      var key = pair[0];
      var label = pair[1];
      if (!groups[key].length) return;
      html += "<div class='gn-notif-group'>" +
        "<h3 class='gn-notif-group-label'>" + label + "</h3>" +
        groups[key].map(notifHtml).join("") +
        "</div>";
    });
    return html;
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
    if (el) el.innerHTML = renderGroupedNotifications(filtered);
    var prev = document.getElementById("ers-notifications-preview");
    if (prev) {
      prev.innerHTML = allNotifications.slice(0, 3).map(notifHtml).join("") ||
        "<div class='gn-empty-state gn-empty-state-sm'><p>No alerts yet</p></div>";
    }
  }

  function renderHistory(recent) {
    var el = document.getElementById("ers-history-list");
    if (!el) return;
    if (!recent || !recent.length) {
      el.innerHTML = "<div class='gn-empty-state'>" +
        "<span class='gn-empty-ico' aria-hidden='true'>📋</span>" +
        "<strong>No history yet</strong>" +
        "<p>Your past emergencies will show up here.</p>" +
      "</div>";
      return;
    }
    var completed = [
      "resolved",
      "completed",
      "cancelled",
      "no_hospital_available",
      "no_responder_available",
      "timeout",
      "rejected"
    ];
    el.innerHTML = recent.map(function (e) {
      var badgeCls = completed.indexOf(e.status) >= 0
        ? (e.status === "cancelled" ? "cancelled" : "completed")
        : "active";
      var meta = histTypeMeta(e.type);
      var when = formatCommDayTime(e.last_update || e.timestamp || e.created_at);
      return "<button type='button' class='gn-hist-card' data-history-id='" + escapeHtml(e.id) + "'>" +
        "<span class='gn-hist-ico' aria-hidden='true'>" + meta.icon + "</span>" +
        "<div class='gn-hist-body'>" +
          "<div class='gn-hist-top'>" +
            "<strong>#GN-" + escapeHtml(e.id) + "</strong>" +
            "<span class='gn-hist-badge " + badgeCls + "'>" + histBadgeLabel(badgeCls) + "</span>" +
          "</div>" +
          "<p class='gn-hist-type'>" + escapeHtml(meta.label) + "</p>" +
          "<p class='gn-hist-when'>" + escapeHtml(when) + "</p>" +
        "</div>" +
        "<span class='gn-hist-cta'>View details</span>" +
      "</button>";
    }).join("");
    el.querySelectorAll("[data-history-id]").forEach(function (btn) {
      btn.onclick = function () {
        openEmergencyDetails(btn.getAttribute("data-history-id"));
      };
    });
  }

  function timeGreeting() {
    var h = new Date().getHours();
    if (h < 12) return "Good Morning,";
    if (h < 17) return "Good Afternoon,";
    return "Good Evening,";
  }

  function firstName(name) {
    var n = String(name || "").trim();
    if (!n) return "Citizen";
    return n.split(/\s+/)[0];
  }

  function displayOrDash(value) {
    var v = String(value || "").trim();
    return v || "—";
  }

  function medCell(label, value) {
    var v = String(value || "").trim();
    if (!v) {
      return "<div class='gn-med-cell is-empty'>" +
        "<em>" + escapeHtml(label) + "</em>" +
        "<strong>Not provided</strong>" +
        "</div>";
    }
    return "<div class='gn-med-cell'>" +
      "<em>" + escapeHtml(label) + "</em>" +
      "<strong>" + escapeHtml(v) + "</strong>" +
      "</div>";
  }

  function renderEmergencyContactCard(profile) {
    var el = document.getElementById("profile-ec-list");
    if (!el) return;
    var name = String((profile && profile.emergency_contact_name) || "").trim();
    var phone = String((profile && profile.emergency_contact_phone) || "").trim();
    var relation = String((profile && profile.emergency_contact_relation) || "").trim();
    if (!name && !phone) {
      el.innerHTML =
        "<div class='gn-empty-state gn-empty-state-sm'>" +
          "<span class='gn-empty-ico' aria-hidden='true'>👤</span>" +
          "<strong>No emergency contact yet</strong>" +
          "<p>Add someone we can reach in a crisis.</p>" +
        "</div>";
      return;
    }
    var initial = (name || "C").charAt(0).toUpperCase();
    var tel = phone ? "tel:" + phone.replace(/\s/g, "") : "#";
    el.innerHTML =
      "<article class='gn-ec-card'>" +
        "<div class='gn-ec-avatar' aria-hidden='true'>" + escapeHtml(initial) + "</div>" +
        "<div class='gn-ec-body'>" +
          "<strong>" + escapeHtml(name || "Emergency contact") + "</strong>" +
          "<em>" + escapeHtml(relation || "Emergency contact") + "</em>" +
          "<span>" + escapeHtml(phone || "No phone on file") + "</span>" +
        "</div>" +
        (phone
          ? "<a class='gn-ec-call' href='" + escapeHtml(tel) + "' aria-label='Call " + escapeHtml(name || "contact") + "'>Call</a>"
          : "<span class='gn-ec-call is-disabled' aria-disabled='true'>Call</span>") +
      "</article>";
  }

  function renderMedicalPresentation(profile) {
    var el = document.getElementById("profile-medical-grid");
    if (!el) return;
    var p = profile || {};
    el.innerHTML =
      medCell("Blood group", p.blood_type) +
      medCell("Allergies", p.allergies) +
      medCell("Chronic diseases", "") +
      medCell("Medications", "") +
      medCell("Notes", p.medical_notes);
  }

  function fillProfilePresentation(profile) {
    var p = profile || {};
    var greet = document.getElementById("profile-greeting");
    if (greet) greet.textContent = timeGreeting();
    var nameEl = document.getElementById("pv-name");
    var phoneEl = document.getElementById("pv-phone");
    var emailEl = document.getElementById("pv-email");
    var addressEl = document.getElementById("pv-address");
    var nidEl = document.getElementById("pv-national-id");
    if (nameEl) nameEl.textContent = displayOrDash(p.name);
    if (phoneEl) phoneEl.textContent = displayOrDash(p.phone);
    if (emailEl) emailEl.textContent = displayOrDash(p.email);
    if (addressEl) {
      var addr = [p.address, p.city].filter(function (x) { return String(x || "").trim(); }).join(", ");
      addressEl.textContent = displayOrDash(addr);
    }
    if (nidEl) {
      var last4 = String(p.national_id_last4 || "").trim();
      nidEl.textContent = last4 ? ("•••• " + last4) : "Not provided";
    }
    renderEmergencyContactCard(p);
    renderMedicalPresentation(p);
  }

  function renderProfileSummary(ps, data) {
    if (!ps) return;
    var greetEl = document.getElementById("welcome-greeting");
    if (greetEl) greetEl.textContent = timeGreeting();
    var welcomeName = document.getElementById("welcome-name");
    if (welcomeName) welcomeName.textContent = firstName(ps.name);
    document.getElementById("profile-display-name").textContent = ps.name || "User";
    document.getElementById("profile-display-phone").textContent = ps.phone || ps.email || "";
    var profileGreet = document.getElementById("profile-greeting");
    if (profileGreet) profileGreet.textContent = timeGreeting();
    var statusEl = document.getElementById("profile-account-status");
    var st = (ps.account_status || "active");
    statusEl.textContent = st.charAt(0).toUpperCase() + st.slice(1);
    statusEl.className = "gn-status-chip " + (st === "active" ? "live" : "idle");

    ["top-avatar", "profile-avatar"].forEach(function (id) {
      setAvatarElement(document.getElementById(id), ps.profile_photo, ps.name);
    });

    fillProfilePresentation({
      name: ps.name,
      phone: ps.phone,
      email: ps.email,
      emergency_contact_name: ps.emergency_contact_name,
      emergency_contact_phone: ps.emergency_contact_phone
    });

    if (data) {
      var recent = data.recent_emergencies || [];
      var cancelled = recent.filter(function (e) {
        return e.status === "cancelled" ||
          e.status === "no_hospital_available" ||
          e.status === "no_responder_available" ||
          e.status === "timeout" ||
          e.status === "rejected";
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
    lastTracking = tracking || null;
    if (!dashMap) return;
    if (tracking) {
      dashMap.updateTracking(tracking);
      if (tracking.display_stage_label) {
        var titleEl = document.getElementById("ers-status-title");
        if (titleEl) titleEl.textContent = tracking.display_stage_label;
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
      if (mapEm) {
        var tLoc = tracking.location || "";
        mapEm.textContent = looksLikeCoords(tLoc) ? "Emergency site" : (tLoc || "Emergency site");
      }
      var hospLabel = (tracking.hospital && tracking.hospital.name)
        || (tracking.station && tracking.station.name) || "—";
      var mapHospital = document.getElementById("map-detail-hospital");
      if (mapHospital) mapHospital.textContent = hospLabel;
      var mapHospitalLeg = document.getElementById("map-detail-hospital-leg");
      if (mapHospitalLeg) mapHospitalLeg.textContent = hospLabel;
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
      updateGoogleMapsLink(lastEmergency, tracking);
      if (lastEmergency) {
        updateFacilityPresentation(lastEmergency, tracking);
        if (tracking.display_stage || tracking.display_stage_label) {
          var tipEl = document.getElementById("ers-status-tip");
          var tipEm = {};
          Object.keys(lastEmergency).forEach(function (k) { tipEm[k] = lastEmergency[k]; });
          tipEm.display_stage = tracking.display_stage || lastEmergency.display_stage;
          tipEm.status = tracking.status || lastEmergency.status;
          if (tipEl) tipEl.textContent = statusTipText(tipEm);
        }
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
      updateGpsPresentation(fix);
      refreshAddressAsync(fix, function (label) {
        var locEl = document.getElementById("gps-current-location");
        var mapYou = document.getElementById("map-detail-you");
        if (locEl) locEl.textContent = label;
        if (mapYou) mapYou.textContent = label;
      });
      if (activeRequestId) EmergencyLocation.pushEmergencyLocation(activeRequestId, fix);
    });
    document.getElementById("dash-map-permission").textContent =
      "Live GPS · Map updates every 5 seconds";
  }

  function initDashboardMap() {
    EmergencyLocation.resolveUserLocation().then(function (loc) {
      currentUserLoc = loc;
      updateGpsPresentation(loc);
      refreshAddressAsync(loc, function (label) {
        var locEl = document.getElementById("gps-current-location");
        var mapYou = document.getElementById("map-detail-you");
        if (locEl) locEl.textContent = label;
        if (mapYou) mapYou.textContent = label;
      });
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
      var messages = data.messages || [];
      if (!messages.length) {
        box.innerHTML =
          "<div class='gn-chat-empty'>" +
            "<span class='gn-empty-ico' aria-hidden='true'>💬</span>" +
            "<strong>Say hello to your team</strong>" +
            "<p>Share your location or ask for updates. Help is connected.</p>" +
          "</div>";
      } else {
        messages.forEach(function (m) {
          var div = document.createElement("div");
          var system = isSystemChatMessage(m);
          var role = m.sender_role === "citizen" ? "citizen" : (m.sender_role || "hospital");
          div.className = "gn-bubble " + (system ? "system" : role);
          var tick = m.status === "seen" ? " ✓✓" : m.status === "delivered" ? " ✓" : "";
          var meta = formatCommTime(m.timestamp) + tick;
          var sender = "<span class='gn-bubble-sender'>" + escapeHtml(chatSenderLabel(m)) + "</span>";
          if (m.msg_type === "voice" && m.text && m.text.indexOf("data:audio") === 0) {
            div.innerHTML = sender + "<audio controls src='" + m.text + "'></audio>" +
              "<span class='gn-bubble-meta'>" + escapeHtml(meta) + "</span>";
          } else {
            div.innerHTML = sender +
              "<span class='gn-bubble-text'>" + escapeHtml(m.text) + "</span>" +
              "<span class='gn-bubble-meta'>" + escapeHtml(meta) + "</span>";
          }
          box.appendChild(div);
        });
      }
      box.scrollTop = box.scrollHeight;
      var typingEl = document.getElementById("ers-typing");
      if (typingEl) {
        if (data.typing) {
          typingEl.innerHTML =
            "<span class='gn-typing-dots' aria-hidden='true'><i></i><i></i><i></i></span>" +
            "<span>Response team is typing…</span>";
          typingEl.classList.add("is-on");
        } else {
          typingEl.innerHTML = "";
          typingEl.classList.remove("is-on");
        }
      }
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
        var state = detailUiState(em);
        if (state === "active") {
          activeRequestId = em.id;
          setDashHomeMode("tracking");
          renderStatusHeader(em);
          renderEmergencyDetails(em);
          if (prevActive !== activeRequestId && mapsReady) {
            EmergencyLocation.startEmergencyTracking(activeRequestId);
          }
          loadChat();
        } else {
          /* Rejected / unavailable must never keep Live Tracking on screen */
          if (prevActive) EmergencyLocation.stopEmergencyTracking();
          activeRequestId = null;
          showSummaryDashboard(em);
        }
      } else {
        activeRequestId = null;
        if (prevActive) EmergencyLocation.stopEmergencyTracking();
        if (lastEmergency && detailUiState(lastEmergency) !== "active") {
          /* Keep summary visible after case finishes; do not fall back to tracking */
          setDashHomeMode("summary");
          renderHomeSummary(lastEmergency);
          renderEmergencyDetails(lastEmergency);
        } else {
          setDashHomeMode("idle");
        }
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

  document.querySelectorAll(".gn-idle-chip[data-panel]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var p = btn.getAttribute("data-panel");
      if (p) showPanel(p);
    });
  });

  document.getElementById("btn-scroll-notif").onclick = function () { showPanel("notifications"); };
  var headerProfile = document.getElementById("btn-header-profile");
  if (headerProfile) {
    headerProfile.onclick = function () { showPanel("profile"); };
  }
  var greetBoot = document.getElementById("welcome-greeting");
  if (greetBoot) greetBoot.textContent = timeGreeting();

  document.getElementById("btn-open-chat").onclick = function () { showPanel("chat"); };
  document.getElementById("btn-open-chat-2").onclick = function () { showPanel("chat"); };
  document.getElementById("btn-open-history").onclick = function () { showPanel("history"); };
  function openTimelinePanel() {
    if (lastEmergency && detailUiState(lastEmergency) === "active") {
      showPanel("timeline");
      renderEmergencyDetails(lastEmergency);
      return;
    }
    openEmergencyDetails(lastEmergency || activeRequestId);
  }
  document.getElementById("btn-view-timeline").onclick = openTimelinePanel;
  var btnTimeline2 = document.getElementById("btn-view-timeline-2");
  if (btnTimeline2) btnTimeline2.onclick = openTimelinePanel;

  document.querySelectorAll(".gn-notif-tab").forEach(function (tab) {
    tab.onclick = function () {
      document.querySelectorAll(".gn-notif-tab").forEach(function (t) { t.classList.remove("active"); });
      tab.classList.add("active");
      notifFilter = tab.getAttribute("data-filter");
      renderNotifications(allNotifications, document.getElementById("notif-badge").classList.contains("hidden") ? 0 : 1);
    };
  });

  function bindProfileNav(btn) {
    if (!btn || btn._gnProfileNavBound) return;
    btn._gnProfileNavBound = true;
    btn.addEventListener("click", function (e) {
      var gotoPanel = btn.getAttribute("data-goto");
      var focusId = btn.getAttribute("data-focus");
      if (gotoPanel) {
        e.preventDefault();
        showPanel(gotoPanel);
      }
      if (focusId) {
        setTimeout(function () {
          var el = document.getElementById(focusId);
          if (el && el.focus) el.focus();
          if (el && el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "center" });
        }, 60);
      }
    });
  }
  document.querySelectorAll(".gn-menu-item[data-goto], .gn-profile-link[data-goto], .gn-settings-row[data-goto], .gn-settings-row[data-focus]").forEach(bindProfileNav);

  document.getElementById("btn-share-loc").onclick = function () {
    var lat = currentUserLoc && currentUserLoc.lat;
    var lng = currentUserLoc && currentUserLoc.lng;
    var text = lat != null
      ? "My emergency location: " + lat.toFixed(5) + ", " + lng.toFixed(5)
      : "Emergency — location sharing";
    if (navigator.share) {
      navigator.share({ title: "Emergency Location", text: text }).catch(function () {});
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
  var mapsBtn = document.getElementById("btn-open-google-maps");
  if (mapsBtn) {
    mapsBtn.addEventListener("click", function (e) {
      updateGoogleMapsLink(lastEmergency, lastTracking);
      if (mapsBtn.classList.contains("is-disabled") || !mapsBtn.getAttribute("href")) {
        e.preventDefault();
      }
    });
  }

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
      if (d.success) {
        if (d.profile) fillProfilePresentation(d.profile);
        loadDashboard();
        alert("Profile saved.");
      } else alert(d.message || "Save failed");
    });
  };

  var passwordForm = document.getElementById("password-form");
  if (passwordForm) {
    passwordForm.onsubmit = function (e) {
      e.preventDefault();
      api("/api/user/password", {
        method: "POST",
        body: {
          current_password: document.getElementById("pw-current").value,
          new_password: document.getElementById("pw-new").value,
          confirm_password: document.getElementById("pw-confirm").value
        }
      }).then(function (d) {
        if (d.success) {
          passwordForm.reset();
          alert(d.message || "Password updated.");
        } else alert(d.message || "Could not update password");
      });
    };
  }

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
      fillProfilePresentation(d.profile);
    }
  });

  pollTimer = setInterval(function () {
    loadDashboard();
    if (mapsReady) refreshMapTracking();
    if (activeRequestId) loadChat();
  }, 5000);
})();
