/**
 * Emergency Help App - Location system (Somalia / Mogadishu)
 * Uses Geolocation API, district lookup, IP fallback, and Leaflet maps.
 */
var EmergencyLocation = (function () {
  "use strict";

  var MOGADISHU_CENTER = { lat: 2.0469, lng: 45.3182 };
  var SOMALIA_BOUNDS = { latMin: -1.7, latMax: 12.0, lngMin: 40.9, lngMax: 51.6 };

  var DISTRICTS = [
    { lat: 2.03, lng: 45.33, name: "Wadajir District, Mogadishu" },
    { lat: 2.04, lng: 45.34, name: "KM4 Junction, Mogadishu" },
    { lat: 2.02, lng: 45.32, name: "Bakaro Market, Mogadishu" },
    { lat: 2.05, lng: 45.35, name: "Howlwadaag, Mogadishu" },
    { lat: 2.01, lng: 45.31, name: "Boondheere, Mogadishu" },
    { lat: 2.045, lng: 45.325, name: "Hodan District, Mogadishu" },
    { lat: 2.055, lng: 45.34, name: "Yaaqshiid, Mogadishu" },
    { lat: 2.06, lng: 45.36, name: "Dayniile, Mogadishu" }
  ];

  var mapInstances = {};
  var liveMarkers = {};
  var livePolylines = {};
  var activeTracking = { watchStop: null, emergencyId: null, pushTimer: null };

  /**
   * Request browser/mobile GPS permission explicitly (triggers permission prompt).
   */
  function requestLocationPermission(options) {
    options = options || {};
    return getUserLocation({
      enableHighAccuracy: true,
      timeout: options.timeout || 20000,
      maximumAge: 0
    }).then(function (coords) {
      return {
        lat: coords.lat,
        lng: coords.lng,
        accuracy: coords.accuracy,
        district: getDistrictName(coords.lat, coords.lng),
        source: "gps",
        permission: "granted"
      };
    }).catch(function (err) {
      var msg = "Location permission denied. Enable GPS in your browser settings.";
      if (err && err.code === 2) msg = "GPS unavailable. Try moving outdoors.";
      if (err && err.code === 3) msg = "GPS timeout. Please try again.";
      var error = new Error(msg);
      error.code = err && err.code;
      throw error;
    });
  }

  function confidenceFromAccuracy(accuracy) {
    if (accuracy == null || isNaN(accuracy)) return 70;
    if (accuracy <= 10) return 95;
    if (accuracy <= 30) return 85;
    if (accuracy <= 100) return 70;
    return 50;
  }

  function locationPayloadFromFix(fix) {
    return {
      latitude: fix.lat,
      longitude: fix.lng,
      district: fix.district || getDistrictName(fix.lat, fix.lng),
      accuracy_m: fix.accuracy,
      speed_mps: fix.speed,
      heading: fix.heading,
      method: fix.source === "gps_live" ? "gps_live" : "gps",
      confidence: confidenceFromAccuracy(fix.accuracy),
      location: (fix.district || getDistrictName(fix.lat, fix.lng)) +
        " (" + fix.lat.toFixed(5) + ", " + fix.lng.toFixed(5) + ")"
    };
  }

  /**
   * Push live GPS fix to server for an active emergency.
   */
  function pushEmergencyLocation(emergencyId, fix) {
    if (!emergencyId || fix.lat == null) return Promise.resolve();
    if (!isInSomalia(fix.lat, fix.lng)) return Promise.resolve();
    var body = locationPayloadFromFix(fix);
    return fetch("/api/emergencies/" + emergencyId + "/location", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); });
  }

  /**
   * Start real-time GPS tracking for an active emergency (watchPosition + server sync).
   */
  function startEmergencyTracking(emergencyId, onUpdate) {
    stopEmergencyTracking();
    if (!emergencyId || !navigator.geolocation) return;
    activeTracking.emergencyId = emergencyId;
    var lastPush = 0;
    var lastFix = null;

    function handleFix(fix) {
      fix.source = "gps_live";
      lastFix = fix;
      if (onUpdate) onUpdate(fix);
      var now = Date.now();
      if (now - lastPush >= 8000) {
        lastPush = now;
        pushEmergencyLocation(emergencyId, fix);
      }
    }

    activeTracking.watchStop = watchUserLocation(handleFix, { maximumAge: 3000, timeout: 25000 });
    activeTracking.pushTimer = setInterval(function () {
      if (lastFix) pushEmergencyLocation(emergencyId, lastFix);
    }, 15000);
  }

  function stopEmergencyTracking() {
    if (activeTracking.watchStop) {
      activeTracking.watchStop();
      activeTracking.watchStop = null;
    }
    if (activeTracking.pushTimer) {
      clearInterval(activeTracking.pushTimer);
      activeTracking.pushTimer = null;
    }
    if (activeTracking.emergencyId) {
      fetch("/api/emergencies/" + activeTracking.emergencyId + "/location/stop", {
        method: "POST",
        credentials: "same-origin"
      }).catch(function () {});
    }
    activeTracking.emergencyId = null;
  }

  /**
   * Initialize map using Google Maps when API key loaded, else Leaflet/OSM.
   */
  function initMapProvider(containerId, lat, lng, options) {
    options = options || {};
    if (window.google && window.google.maps) {
      return initGoogleMap(containerId, lat, lng, options);
    }
    return initMap(containerId, lat, lng, options);
  }

  function initGoogleMap(containerId, lat, lng, options) {
    var el = document.getElementById(containerId);
    if (!el || !window.google) return null;
    el.innerHTML = "";
    var map = new google.maps.Map(el, {
      center: { lat: lat, lng: lng },
      zoom: options.zoom || 16,
      mapTypeControl: true,
      streetViewControl: false
    });
    var marker = new google.maps.Marker({
      position: { lat: lat, lng: lng },
      map: map,
      title: options.label || "Location"
    });
    mapInstances[containerId] = { type: "google", map: map, marker: marker };
    return map;
  }

  function updateLiveMarker(containerId, lat, lng, label) {
    var inst = mapInstances[containerId];
    if (!inst) return;
    if (inst.type === "google" && inst.marker) {
      inst.marker.setPosition({ lat: lat, lng: lng });
      inst.map.panTo({ lat: lat, lng: lng });
    } else if (inst.marker && inst.marker.setLatLng) {
      inst.marker.setLatLng([lat, lng]);
      inst.map.panTo([lat, lng]);
    }
  }

  /**
   * Live dispatch map: patient markers + GPS trail polylines (hospital dashboard).
   */
  function initLiveDispatchMap(containerId, emergencies, options) {
    options = options || {};
    if (typeof L === "undefined") return null;
    var el = document.getElementById(containerId);
    if (!el) return null;

    var mapKey = containerId;
    var existing = mapInstances[mapKey];
    var map;

    if (existing && existing.type === "live-dispatch") {
      map = existing.map;
    } else {
      if (existing && existing.remove) existing.remove();
      map = L.map(containerId).setView([MOGADISHU_CENTER.lat, MOGADISHU_CENTER.lng], 13);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap",
        maxZoom: 19
      }).addTo(map);
      mapInstances[mapKey] = { type: "live-dispatch", map: map };
      liveMarkers[mapKey] = {};
      livePolylines[mapKey] = {};
    }

    var bounds = [];
    (emergencies || []).forEach(function (em) {
      var c = parseCoordinates(em);
      if (c.lat == null || isNaN(c.lat)) return;
      bounds.push([c.lat, c.lng]);
      var id = String(em.id);
      var isLive = em.tracking_active;
      var popup = "<strong>#" + em.id + "</strong> " + (em.caller_name || "") +
        "<br>" + (em.phone || "") +
        "<br><em>" + (em.status || "").replace(/_/g, " ") + "</em>" +
        (isLive ? "<br><span style='color:#c62828'>● LIVE GPS</span>" : "");

      if (!liveMarkers[mapKey][id]) {
        var icon = L.divIcon({
          className: "map-pin-patient",
          html: "<div class='pin-dot " + (isLive ? "pin-live" : "pin-blue") + "'></div>",
          iconSize: [22, 22],
          iconAnchor: [11, 11]
        });
        liveMarkers[mapKey][id] = L.marker([c.lat, c.lng], { icon: icon }).addTo(map);
        liveMarkers[mapKey][id].bindPopup(popup);
      } else {
        liveMarkers[mapKey][id].setLatLng([c.lat, c.lng]);
        liveMarkers[mapKey][id].setPopupContent(popup);
      }

      var trail = (em.location_history || em.location_trail || []).filter(function (p) {
        return p.latitude != null && p.longitude != null;
      });
      if (trail.length >= 2) {
        var pts = trail.map(function (p) { return [p.latitude, p.longitude]; });
        if (livePolylines[mapKey][id]) {
          livePolylines[mapKey][id].setLatLngs(pts);
        } else {
          livePolylines[mapKey][id] = L.polyline(pts, {
            color: "#1565c0",
            weight: 3,
            opacity: 0.7,
            dashArray: "6, 4"
          }).addTo(map);
        }
      }
    });

    if (bounds.length > 1) {
      map.fitBounds(bounds, { padding: [40, 40] });
    } else if (bounds.length === 1) {
      map.setView(bounds[0], 15);
    }
    setTimeout(function () { map.invalidateSize(); }, 200);
    return map;
  }

  function toRad(deg) {
    return (deg * Math.PI) / 180;
  }

  function isInSomalia(lat, lng) {
    if (lat == null || lng == null || isNaN(lat) || isNaN(lng)) return false;
    return lat >= SOMALIA_BOUNDS.latMin && lat <= SOMALIA_BOUNDS.latMax
      && lng >= SOMALIA_BOUNDS.lngMin && lng <= SOMALIA_BOUNDS.lngMax;
  }

  function clampToSomalia(lat, lng) {
    if (isInSomalia(lat, lng)) return { lat: lat, lng: lng };
    return { lat: MOGADISHU_CENTER.lat, lng: MOGADISHU_CENTER.lng };
  }

  function distanceKm(lat1, lng1, lat2, lng2) {
    var R = 6371;
    var dLat = toRad(lat2 - lat1);
    var dLng = toRad(lng2 - lng1);
    var a =
      Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  /**
   * Get district name from coordinates (nearest Mogadishu district).
   */
  function getDistrictName(lat, lng) {
    if (lat == null || lng == null || isNaN(lat) || isNaN(lng)) {
      return "Mogadishu, Somalia";
    }
    if (!isInSomalia(lat, lng)) {
      return "Mogadishu, Somalia";
    }
    var nearest = DISTRICTS[0];
    var minDist = distanceKm(lat, lng, nearest.lat, nearest.lng);
    for (var i = 1; i < DISTRICTS.length; i++) {
      var d = DISTRICTS[i];
      var dist = distanceKm(lat, lng, d.lat, d.lng);
      if (dist < minDist) {
        minDist = dist;
        nearest = d;
      }
    }
    return nearest.name;
  }

  /**
   * Promise wrapper for navigator.geolocation.getCurrentPosition
   */
  function getUserLocation(options) {
    options = options || {};
    return new Promise(function (resolve, reject) {
      if (!navigator.geolocation) {
        reject(new Error("Geolocation not supported"));
        return;
      }
      navigator.geolocation.getCurrentPosition(
        function (position) {
          resolve({
            lat: position.coords.latitude,
            lng: position.coords.longitude,
            accuracy: position.coords.accuracy,
            source: "gps"
          });
        },
        function (err) {
          reject(err || new Error("Location denied"));
        },
        {
          enableHighAccuracy: options.enableHighAccuracy !== false,
          timeout: options.timeout || 15000,
          maximumAge: options.maximumAge || 0
        }
      );
    });
  }

  /**
   * IP-based approximate location via backend (avoids CORS).
   */
  function getApproxLocationByIP() {
    return fetch("/api/location/ip")
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        return {
          lat: data.lat,
          lng: data.lng,
          district: data.district || getDistrictName(data.lat, data.lng),
          source: data.source || "ip"
        };
      })
      .catch(function () {
        return {
          lat: MOGADISHU_CENTER.lat,
          lng: MOGADISHU_CENTER.lng,
          district: getDistrictName(MOGADISHU_CENTER.lat, MOGADISHU_CENTER.lng),
          source: "default"
        };
      });
  }

  /**
   * Full flow: GPS -> IP fallback -> Mogadishu default
   */
  function resolveUserLocation(options) {
    return getUserLocation(options)
      .then(function (coords) {
        var clamped = clampToSomalia(coords.lat, coords.lng);
        return {
          lat: clamped.lat,
          lng: clamped.lng,
          district: getDistrictName(clamped.lat, clamped.lng),
          accuracy: coords.accuracy,
          source: isInSomalia(coords.lat, coords.lng) ? "gps" : "gps_clamped"
        };
      })
      .catch(function () {
        return getApproxLocationByIP().then(function (loc) {
          var clamped = clampToSomalia(loc.lat, loc.lng);
          return {
            lat: clamped.lat,
            lng: clamped.lng,
            district: getDistrictName(clamped.lat, clamped.lng),
            source: loc.source || "default"
          };
        });
      });
  }

  /**
   * Parse lat/lng from emergency location string or object fields.
   */
  function parseCoordinates(emergencyOrString) {
    if (typeof emergencyOrString === "object" && emergencyOrString !== null) {
      if (emergencyOrString.latitude != null && emergencyOrString.longitude != null) {
        var lat = parseFloat(emergencyOrString.latitude);
        var lng = parseFloat(emergencyOrString.longitude);
        if (!isInSomalia(lat, lng)) {
          return {
            lat: MOGADISHU_CENTER.lat,
            lng: MOGADISHU_CENTER.lng,
            label: emergencyOrString.district || "Mogadishu, Somalia",
            invalid: true
          };
        }
        return {
          lat: lat,
          lng: lng,
          label: emergencyOrString.district || emergencyOrString.location || ""
        };
      }
      emergencyOrString = emergencyOrString.location || "";
    }
    var str = String(emergencyOrString || "");
    var match = str.match(/\((-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\)/);
    if (match) {
      var mlat = parseFloat(match[1]);
      var mlng = parseFloat(match[2]);
      if (!isInSomalia(mlat, mlng)) {
        return {
          lat: MOGADISHU_CENTER.lat,
          lng: MOGADISHU_CENTER.lng,
          label: str.split("(")[0].trim() || "Mogadishu, Somalia",
          invalid: true
        };
      }
      return {
        lat: mlat,
        lng: mlng,
        label: str.split("(")[0].trim()
      };
    }
    return {
      lat: MOGADISHU_CENTER.lat,
      lng: MOGADISHU_CENTER.lng,
      label: str || "Mogadishu, Somalia"
    };
  }

  /**
   * Initialize or update a Leaflet map in a container.
   */
  function initMap(containerId, lat, lng, options) {
    options = options || {};
    if (typeof L === "undefined") {
      console.warn("Leaflet not loaded");
      return null;
    }
    var el = document.getElementById(containerId);
    if (!el) return null;

    var mapKey = containerId;
    if (mapInstances[mapKey]) {
      mapInstances[mapKey].remove();
      delete mapInstances[mapKey];
    }

    var map = L.map(containerId, {
      scrollWheelZoom: options.scrollWheelZoom !== false,
      zoomControl: true
    }).setView([lat, lng], options.zoom || 15);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19
    }).addTo(map);

    var marker = L.marker([lat, lng]).addTo(map);
    if (options.label) {
      marker.bindPopup(options.label).openPopup();
    }

    mapInstances[mapKey] = { type: "leaflet", map: map, marker: marker };
    setTimeout(function () {
      map.invalidateSize();
    }, 200);

    return map;
  }

  function ensureMapModal() {
    if (document.getElementById("emergency-map-modal")) return;
    var modal = document.createElement("div");
    modal.id = "emergency-map-modal";
    modal.className = "map-modal hidden";
    modal.innerHTML =
      '<div class="map-modal-backdrop"></div>' +
      '<div class="map-modal-content">' +
      '<div class="map-modal-header">' +
      '<h3 id="map-modal-title">Location</h3>' +
      '<button type="button" class="map-modal-close" aria-label="Close">&times;</button>' +
      '</div>' +
      '<p id="map-modal-address" class="map-modal-address"></p>' +
      '<div id="map-modal-container" class="leaflet-map-container"></div>' +
      '</div>';
    document.body.appendChild(modal);

    modal.querySelector(".map-modal-backdrop").onclick = closeMapModal;
    modal.querySelector(".map-modal-close").onclick = closeMapModal;
  }

  function showMapModal(lat, lng, title, address) {
    ensureMapModal();
    var modal = document.getElementById("emergency-map-modal");
    document.getElementById("map-modal-title").textContent = title || "Emergency location";
    document.getElementById("map-modal-address").textContent =
      address || getDistrictName(lat, lng) + " (" + lat.toFixed(5) + ", " + lng.toFixed(5) + ")";
    modal.classList.remove("hidden");
    initMap("map-modal-container", lat, lng, {
      label: address || getDistrictName(lat, lng),
      zoom: 16
    });
  }

  function closeMapModal() {
    var modal = document.getElementById("emergency-map-modal");
    if (modal) modal.classList.add("hidden");
    if (mapInstances["map-modal-container"]) {
      var old = mapInstances["map-modal-container"];
      if (old.map && old.map.remove) old.map.remove();
      delete mapInstances["map-modal-container"];
    }
  }

  function showEmergencyOnMap(emergency) {
    var coords = parseCoordinates(emergency);
    showMapModal(
      coords.lat,
      coords.lng,
      "Emergency #" + (emergency.id || ""),
      coords.label || emergency.location
    );
  }

  /**
   * Dashboard overview map with multiple emergency markers.
   */
  /**
   * Continuous GPS updates while citizen is on location page.
   * Returns stop() function.
   */
  function watchUserLocation(onUpdate, options) {
    options = options || {};
    if (!navigator.geolocation) return function () {};
    var watchId = navigator.geolocation.watchPosition(
      function (position) {
        var lat = position.coords.latitude;
        var lng = position.coords.longitude;
        onUpdate({
          lat: lat,
          lng: lng,
          accuracy: position.coords.accuracy,
          speed: position.coords.speed,
          heading: position.coords.heading,
          district: getDistrictName(lat, lng),
          source: "gps"
        });
      },
      function () {},
      {
        enableHighAccuracy: true,
        maximumAge: options.maximumAge || 5000,
        timeout: options.timeout || 20000
      }
    );
    return function () {
      navigator.geolocation.clearWatch(watchId);
    };
  }

  function initOverviewMap(containerId, emergencies) {
    if (typeof L === "undefined") return null;
    var el = document.getElementById(containerId);
    if (!el) return null;

    var mapKey = containerId;
    if (mapInstances[mapKey]) {
      mapInstances[mapKey].remove();
      delete mapInstances[mapKey];
    }

    var map = L.map(containerId).setView(
      [MOGADISHU_CENTER.lat, MOGADISHU_CENTER.lng],
      13
    );
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; OpenStreetMap',
      maxZoom: 19
    }).addTo(map);

    var bounds = [];
    (emergencies || []).forEach(function (em) {
      var c = parseCoordinates(em);
      var marker = L.marker([c.lat, c.lng]).addTo(map);
      marker.bindPopup(
        "<strong>#" + (em.id || "") + "</strong> " + (em.type || "") +
        "<br>" + (c.label || em.location || "")
      );
      bounds.push([c.lat, c.lng]);
    });

    if (bounds.length > 1) {
      map.fitBounds(bounds, { padding: [30, 30] });
    } else if (bounds.length === 1) {
      map.setView(bounds[0], 15);
    }

    mapInstances[mapKey] = map;
    setTimeout(function () {
      map.invalidateSize();
    }, 300);
    return map;
  }

  /**
   * Patient + nearest hospital on one map (route line, both markers).
   */
  function initPatientHospitalMap(containerId, patientLat, patientLng, hospitalLat, hospitalLng, options) {
    options = options || {};
    if (typeof L === "undefined") return null;
    if (hospitalLat == null || hospitalLng == null) {
      return initMap(containerId, patientLat, patientLng, options);
    }

    var mapKey = containerId;
    if (mapInstances[mapKey]) {
      mapInstances[mapKey].remove();
      delete mapInstances[mapKey];
    }

    var map = L.map(containerId, { scrollWheelZoom: true, zoomControl: true });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; OpenStreetMap',
      maxZoom: 19
    }).addTo(map);

    var patientIcon = L.divIcon({
      className: "map-pin-patient",
      html: '<div class="pin-dot pin-blue"></div>',
      iconSize: [20, 20],
      iconAnchor: [10, 10]
    });
    var hospitalIcon = L.divIcon({
      className: "map-pin-hospital",
      html: '<div class="pin-dot pin-red">+</div>',
      iconSize: [24, 24],
      iconAnchor: [12, 12]
    });

    var pMarker = L.marker([patientLat, patientLng], { icon: patientIcon }).addTo(map);
    pMarker.bindPopup(options.patientLabel || "Your location");

    var hMarker = L.marker([hospitalLat, hospitalLng], { icon: hospitalIcon }).addTo(map);
    hMarker.bindPopup(options.hospitalLabel || "Nearest hospital");

    L.polyline(
      [[patientLat, patientLng], [hospitalLat, hospitalLng]],
      { color: "#e53935", weight: 4, opacity: 0.75, dashArray: "8, 8" }
    ).addTo(map);

    map.fitBounds(
      L.latLngBounds([patientLat, patientLng], [hospitalLat, hospitalLng]),
      { padding: [40, 40] }
    );

    mapInstances[mapKey] = map;
    setTimeout(function () { map.invalidateSize(); }, 200);
    return map;
  }

  function pinIcon(color, label) {
    if (!(window.google && window.google.maps)) return null;
    return {
      path: google.maps.SymbolPath.CIRCLE,
      fillColor: color,
      fillOpacity: 1,
      strokeColor: "#fff",
      strokeWeight: 2,
      scale: label ? 11 : 9,
      label: label ? { text: label, color: "#fff", fontSize: "10px", fontWeight: "700" } : undefined
    };
  }

  /**
   * Integrated dashboard map: Somalia-only emergency view with OSRM routing.
   */
  function createDashboardMap(containerId, options) {
    options = options || {};
    var MARKER_KEYS = ["user", "emergency", "hospital", "ambulance", "police", "fire"];
    var state = {
      type: null,
      map: null,
      markers: {},
      directionsService: null,
      directionsRenderers: {},
      routeLines: {},
      trailLine: null,
      initialized: false,
      lastPatient: null
    };

    function validPoint(lat, lng) {
      return isInSomalia(lat, lng);
    }

    function markerKeysForFit() {
      return MARKER_KEYS.filter(function (key) { return state.markers[key]; });
    }

    function fitAll() {
      if (!state.map) return;
      if (state.type === "google") {
        var bounds = new google.maps.LatLngBounds();
        var has = false;
        markerKeysForFit().forEach(function (key) {
          var m = state.markers[key];
          if (m && m.getPosition) {
            bounds.extend(m.getPosition());
            has = true;
          }
        });
        if (has) state.map.fitBounds(bounds, 48);
      } else if (state.type === "leaflet-dash") {
        var pts = [];
        markerKeysForFit().forEach(function (key) {
          var m = state.markers[key];
          if (m && m.getLatLng) pts.push(m.getLatLng());
        });
        if (pts.length > 1) {
          state.map.fitBounds(L.latLngBounds(pts), { padding: [40, 40], maxZoom: 16 });
        } else if (pts.length === 1) {
          state.map.setView(pts[0], 15);
        } else {
          state.map.setView([MOGADISHU_CENTER.lat, MOGADISHU_CENTER.lng], 14);
        }
      }
    }

    function fetchOsrmRoute(from, to) {
      return fetch(
        "/api/route/osrm?from=" + from.lat + "," + from.lng + "&to=" + to.lat + "," + to.lng,
        { credentials: "same-origin" }
      ).then(function (r) { return r.json(); });
    }

    function clearRoute(key) {
      if (state.directionsRenderers[key]) {
        state.directionsRenderers[key].setMap(null);
        delete state.directionsRenderers[key];
      }
      if (state.routeLines[key] && state.map) {
        state.map.removeLayer(state.routeLines[key]);
        delete state.routeLines[key];
      }
    }

    function clearAllRoutes() {
      Object.keys(state.directionsRenderers).forEach(clearRoute);
      Object.keys(state.routeLines).forEach(clearRoute);
    }

    function drawRoute(key, origin, dest, color) {
      if (!origin || !dest || !validPoint(origin.lat, origin.lng) || !validPoint(dest.lat, dest.lng)) {
        clearRoute(key);
        return;
      }
      color = color || "#1565c0";
      if (state.type === "google" && state.directionsService) {
        clearRoute(key);
        var renderer = new google.maps.DirectionsRenderer({
          map: state.map,
          suppressMarkers: true,
          preserveViewport: true,
          polylineOptions: { strokeColor: color, strokeWeight: 5, strokeOpacity: 0.85 }
        });
        state.directionsRenderers[key] = renderer;
        state.directionsService.route({
          origin: { lat: origin.lat, lng: origin.lng },
          destination: { lat: dest.lat, lng: dest.lng },
          travelMode: google.maps.TravelMode.DRIVING
        }, function (result, status) {
          if (status === "OK" && state.directionsRenderers[key]) {
            state.directionsRenderers[key].setDirections(result);
          }
        });
      } else if (state.type === "leaflet-dash" && state.map) {
        fetchOsrmRoute(origin, dest).then(function (data) {
          if (!data.success || !data.coordinates || !state.map) {
            clearRoute(key);
            return;
          }
          var latlngs = data.coordinates.map(function (c) { return [c[1], c[0]]; });
          if (state.routeLines[key]) {
            state.routeLines[key].setLatLngs(latlngs);
          } else {
            state.routeLines[key] = L.polyline(latlngs, {
              color: color,
              weight: 5,
              opacity: 0.85
            }).addTo(state.map);
          }
        }).catch(function () { clearRoute(key); });
      }
    }

    function removeMarker(key) {
      if (!state.markers[key]) return;
      if (state.type === "google") state.markers[key].setMap(null);
      else if (state.map) state.map.removeLayer(state.markers[key]);
      delete state.markers[key];
    }

    function setGoogleMarker(key, lat, lng, icon, title) {
      if (!state.map || !validPoint(lat, lng)) return;
      if (!state.markers[key]) {
        state.markers[key] = new google.maps.Marker({
          map: state.map,
          position: { lat: lat, lng: lng },
          icon: icon,
          title: title || key,
          zIndex: key === "emergency" ? 5 : key === "ambulance" ? 4 : 3
        });
      } else {
        state.markers[key].setPosition({ lat: lat, lng: lng });
        if (title) state.markers[key].setTitle(title);
      }
    }

    function setLeafletMarker(key, lat, lng, className, title) {
      if (!state.map || typeof L === "undefined" || !validPoint(lat, lng)) return;
      var label = "";
      if (className === "pin-hospital") label = "+";
      if (className === "pin-ambulance") label = "A";
      if (className === "pin-police") label = "P";
      if (className === "pin-fire") label = "F";
      var html = "<div class='pin-dot " + className + "'>" + label + "</div>";
      var icon = L.divIcon({ className: "map-pin-emergency", html: html, iconSize: [24, 24], iconAnchor: [12, 12] });
      if (!state.markers[key]) {
        state.markers[key] = L.marker([lat, lng], { icon: icon }).addTo(state.map);
        state.markers[key].bindPopup(title || key);
      } else {
        state.markers[key].setLatLng([lat, lng]);
        state.markers[key].setPopupContent(title || key);
      }
    }

    function setMarker(key, lat, lng, leafletClass, googleColor, googleLabel, title) {
      if (!state.initialized) return;
      if (state.type === "google") {
        setGoogleMarker(key, lat, lng, pinIcon(googleColor, googleLabel), title);
      } else {
        setLeafletMarker(key, lat, lng, leafletClass, title);
      }
    }

    function initGoogle(lat, lng) {
      var el = document.getElementById(containerId);
      if (!el || !window.google) return false;
      var center = clampToSomalia(lat, lng);
      state.type = "google";
      state.map = new google.maps.Map(el, {
        center: { lat: center.lat, lng: center.lng },
        zoom: 14,
        mapTypeControl: true,
        zoomControl: true,
        streetViewControl: false,
        fullscreenControl: true,
        mapTypeId: "roadmap",
        gestureHandling: "greedy",
        restriction: {
          latLngBounds: {
            north: SOMALIA_BOUNDS.latMax,
            south: SOMALIA_BOUNDS.latMin,
            east: SOMALIA_BOUNDS.lngMax,
            west: SOMALIA_BOUNDS.lngMin
          },
          strictBounds: true
        },
        minZoom: 11
      });
      state.directionsService = new google.maps.DirectionsService();
      setGoogleMarker("user", center.lat, center.lng, pinIcon("#c62828"), "Your location");
      state.initialized = true;
      setTimeout(function () { google.maps.event.trigger(state.map, "resize"); }, 250);
      return true;
    }

    function initLeaflet(lat, lng) {
      if (typeof L === "undefined") return false;
      var el = document.getElementById(containerId);
      if (!el) return false;
      var center = clampToSomalia(lat, lng);
      var somaliaBounds = L.latLngBounds(
        [SOMALIA_BOUNDS.latMin, SOMALIA_BOUNDS.lngMin],
        [SOMALIA_BOUNDS.latMax, SOMALIA_BOUNDS.lngMax]
      );
      state.type = "leaflet-dash";
      state.map = L.map(containerId, {
        scrollWheelZoom: true,
        zoomControl: true,
        maxBounds: somaliaBounds,
        maxBoundsViscosity: 1.0,
        minZoom: 11
      }).setView([center.lat, center.lng], 14);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap",
        maxZoom: 19
      }).addTo(state.map);
      setLeafletMarker("user", center.lat, center.lng, "pin-user", "Your location");
      state.initialized = true;
      setTimeout(function () { state.map.invalidateSize(); }, 250);
      return true;
    }

    function updateMarkersAndRoutes(data) {
      var patient = { lat: data.latitude, lng: data.longitude };
      if (!validPoint(patient.lat, patient.lng)) return;
      state.lastPatient = patient;

      setMarker("emergency", patient.lat, patient.lng, "pin-emergency", "#ff6f00", "!", "Emergency #" + (data.emergency_id || ""));

      var hospital = data.hospital;
      var station = data.station;
      removeMarker("hospital");
      removeMarker("police");
      removeMarker("fire");

      if (hospital && validPoint(hospital.latitude, hospital.longitude)) {
        setMarker(
          "hospital",
          hospital.latitude,
          hospital.longitude,
          "pin-hospital",
          "#1565c0",
          "+",
          hospital.name || "Hospital"
        );
        drawRoute("hospital", { lat: hospital.latitude, lng: hospital.longitude }, patient, "#1565c0");
      } else if (station && validPoint(station.latitude, station.longitude)) {
        if (station.type === "police") {
          setMarker("police", station.latitude, station.longitude, "pin-police", "#0d47a1", "P", station.name);
          drawRoute("hospital", { lat: station.latitude, lng: station.longitude }, patient, "#0d47a1");
        } else if (station.type === "fire") {
          setMarker("fire", station.latitude, station.longitude, "pin-fire", "#e65100", "F", station.name);
          drawRoute("hospital", { lat: station.latitude, lng: station.longitude }, patient, "#e65100");
        }
      } else {
        clearRoute("hospital");
      }

      var resp = data.responder;
      if (resp && validPoint(resp.latitude, resp.longitude)) {
        var ambLabel = (data.dispatch_unit && data.dispatch_unit.vehicle_number) || "Ambulance";
        setMarker(
          "ambulance",
          resp.latitude,
          resp.longitude,
          "pin-ambulance",
          "#2e7d32",
          "A",
          ambLabel + " — " + (resp.name || "En route")
        );
        drawRoute("ambulance", { lat: resp.latitude, lng: resp.longitude }, patient, "#2e7d32");
      } else {
        removeMarker("ambulance");
        clearRoute("ambulance");
      }

      var trail = (data.trail || []).filter(function (p) {
        return validPoint(p.latitude, p.longitude);
      });
      if (trail.length >= 2 && state.type === "leaflet-dash" && state.map) {
        var pts = trail.map(function (p) { return [p.latitude, p.longitude]; });
        if (state.trailLine) state.trailLine.setLatLngs(pts);
        else {
          state.trailLine = L.polyline(pts, { color: "#c62828", weight: 3, opacity: 0.55, dashArray: "5, 5" }).addTo(state.map);
        }
      }
      fitAll();
    }

    return {
      init: function (lat, lng) {
        var c = clampToSomalia(lat, lng);
        if (initGoogle(c.lat, c.lng)) return;
        initLeaflet(c.lat, c.lng);
      },
      isGoogle: function () { return state.type === "google"; },
      setUserLocation: function (lat, lng) {
        if (!state.initialized) return;
        var c = clampToSomalia(lat, lng);
        setMarker("user", c.lat, c.lng, "pin-user", "#c62828", null, "Your location");
      },
      updateTracking: function (data) {
        if (!state.initialized || !data) return;
        updateMarkersAndRoutes(data);
        if (options.onTrackingUpdate) options.onTrackingUpdate(data);
      },
      recenter: function () {
        fitAll();
      },
      clearEmergency: function () {
        ["emergency", "hospital", "ambulance", "police", "fire"].forEach(removeMarker);
        clearAllRoutes();
        if (state.trailLine && state.map) {
          state.map.removeLayer(state.trailLine);
          state.trailLine = null;
        }
        state.lastPatient = null;
        fitAll();
        if (options.onTrackingUpdate) options.onTrackingUpdate(null);
      },
      resize: function () {
        if (state.type === "google" && state.map) {
          google.maps.event.trigger(state.map, "resize");
        } else if (state.type === "leaflet-dash" && state.map) {
          state.map.invalidateSize();
        }
      },
      destroy: function () {
        clearAllRoutes();
        state = {
          type: null, map: null, markers: {}, directionsService: null,
          directionsRenderers: {}, routeLines: {}, trailLine: null,
          initialized: false, lastPatient: null
        };
      }
    };
  }

  return {
    MOGADISHU_CENTER: MOGADISHU_CENTER,
    SOMALIA_BOUNDS: SOMALIA_BOUNDS,
    isInSomalia: isInSomalia,
    clampToSomalia: clampToSomalia,
    DISTRICTS: DISTRICTS,
    getUserLocation: getUserLocation,
    requestLocationPermission: requestLocationPermission,
    getDistrictName: getDistrictName,
    getApproxLocationByIP: getApproxLocationByIP,
    resolveUserLocation: resolveUserLocation,
    parseCoordinates: parseCoordinates,
    locationPayloadFromFix: locationPayloadFromFix,
    pushEmergencyLocation: pushEmergencyLocation,
    startEmergencyTracking: startEmergencyTracking,
    stopEmergencyTracking: stopEmergencyTracking,
    initMap: initMap,
    initMapProvider: initMapProvider,
    updateLiveMarker: updateLiveMarker,
    initLiveDispatchMap: initLiveDispatchMap,
    showMapModal: showMapModal,
    closeMapModal: closeMapModal,
    showEmergencyOnMap: showEmergencyOnMap,
    initOverviewMap: initOverviewMap,
    initPatientHospitalMap: initPatientHospitalMap,
    watchUserLocation: watchUserLocation,
    confidenceFromAccuracy: confidenceFromAccuracy,
    createDashboardMap: createDashboardMap
  };
})();
