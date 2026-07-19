/**
 * Hospital registration — Somalia-only map picker with geocoding.
 */
var HospitalApp = (function () {
  "use strict";

  var SOMALIA = { latMin: -1.7, latMax: 12.0, lngMin: 40.9, lngMax: 51.6 };
  var MOGADISHU = { lat: 2.0469, lng: 45.3182 };

  var regMap = null;
  var regMarker = null;
  var previewMap = null;
  var confirmMap = null;
  var searchTimer = null;
  var state = {
    lat: null,
    lng: null,
    placeName: "",
    searchQuery: "",
    searchAnchor: null,
    userConfirmed: false,
    lastResults: []
  };

  function el(id) {
    return document.getElementById(id);
  }

  function setVal(id, value) {
    var node = el(id);
    if (node && value != null && value !== "") node.value = value;
  }

  function setText(id, text) {
    var node = el(id);
    if (node) node.textContent = text;
  }

  function showError(msg) {
    var box = el("location-error");
    if (!box) return;
    if (msg) {
      box.textContent = msg;
      box.classList.remove("hidden");
    } else {
      box.textContent = "";
      box.classList.add("hidden");
    }
  }

  function showDistanceWarning(msg) {
    var box = el("location-distance-warning");
    if (!box) return;
    if (msg) {
      box.textContent = msg;
      box.classList.remove("hidden");
    } else {
      box.textContent = "";
      box.classList.add("hidden");
    }
  }

  function isInSomalia(lat, lng) {
    return (
      lat >= SOMALIA.latMin && lat <= SOMALIA.latMax &&
      lng >= SOMALIA.lngMin && lng <= SOMALIA.lngMax
    );
  }

  function haversineKm(lat1, lng1, lat2, lng2) {
    var r = 6371;
    var p1 = lat1 * Math.PI / 180;
    var p2 = lat2 * Math.PI / 180;
    var dp = (lat2 - lat1) * Math.PI / 180;
    var dl = (lng2 - lng1) * Math.PI / 180;
    var a = Math.sin(dp / 2) * Math.sin(dp / 2) +
      Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) * Math.sin(dl / 2);
    return 2 * r * Math.asin(Math.sqrt(a));
  }

  function validateSomaliaCoords(lat, lng) {
    if (!isInSomalia(lat, lng)) {
      showError("Location must be in Somalia.");
      return false;
    }
    showError("");
    return true;
  }

  function checkDistanceWarning() {
    if (!state.searchAnchor || state.lat == null) {
      showDistanceWarning("");
      return;
    }
    var dist = haversineKm(
      state.searchAnchor.lat, state.searchAnchor.lng,
      state.lat, state.lng
    );
    if (dist > 20) {
      showDistanceWarning(
        "Warning: Selected pin is " + dist.toFixed(1) + " km from your search. Please verify this is correct."
      );
    } else {
      showDistanceWarning("");
    }
  }

  function setCoords(lat, lng) {
    if (!validateSomaliaCoords(lat, lng)) return false;
    state.lat = lat;
    state.lng = lng;
    setVal("latitude", lat.toFixed(6));
    setVal("longitude", lng.toFixed(6));
    updatePreview();
    checkDistanceWarning();
    return true;
  }

  function moveMarker(lat, lng) {
    if (!regMap || !regMarker) return;
    if (window.google && window.google.maps && regMarker.setPosition) {
      regMarker.setPosition({ lat: lat, lng: lng });
      regMap.panTo({ lat: lat, lng: lng });
      return;
    }
    if (regMarker.setLatLng) {
      regMarker.setLatLng([lat, lng]);
      regMap.panTo([lat, lng]);
    }
  }

  function fetchJson(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      return r.json();
    });
  }

  function applyAddressFields(result) {
    if (!result) return;
    if (result.address) setVal("address", result.address);
    if (result.city) setVal("city", result.city);
    if (result.district) setVal("district", result.district);
    if (result.region) {
      var regionSel = el("region");
      if (regionSel) {
        var found = false;
        for (var i = 0; i < regionSel.options.length; i++) {
          if (regionSel.options[i].value.toLowerCase() === String(result.region).toLowerCase()) {
            regionSel.value = regionSel.options[i].value;
            found = true;
            break;
          }
        }
        if (!found && String(result.region).toLowerCase().indexOf("banadir") >= 0) {
          regionSel.value = "Banadir";
        }
      }
    }
    state.placeName = result.name || result.display_name || state.placeName;
  }

  function reverseGeocode(lat, lng) {
    if (!validateSomaliaCoords(lat, lng)) return Promise.resolve(null);
    return fetchJson("/api/geocode/reverse?lat=" + lat + "&lng=" + lng)
      .then(function (data) {
        if (!data.success) {
          showError(data.message || "Location must be in Somalia.");
          return null;
        }
        if (data.result) {
          applyAddressFields(data.result);
          return data.result;
        }
        return null;
      })
      .catch(function () { return null; });
  }

  function onLocationPicked(lat, lng, options) {
    options = options || {};
    if (!setCoords(lat, lng)) return false;
    moveMarker(lat, lng);
    if (!options.skipReverse) {
      reverseGeocode(lat, lng);
    } else if (options.result) {
      applyAddressFields(options.result);
    }
    state.userConfirmed = !!options.userConfirmed;
    var preview = el("location-preview");
    if (preview) preview.classList.remove("hidden");
    return true;
  }

  function initLeafletMap(containerId, lat, lng, interactive) {
    if (typeof L === "undefined") return null;
    var mapEl = el(containerId);
    if (!mapEl) return null;
    mapEl.innerHTML = "";
    var map = L.map(containerId, {
      scrollWheelZoom: !!interactive,
      dragging: !!interactive,
      zoomControl: !!interactive
    }).setView([lat, lng], interactive ? 15 : 16);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap"
    }).addTo(map);
    L.marker([lat, lng], { draggable: false }).addTo(map);
    setTimeout(function () { map.invalidateSize(); }, 200);
    return map;
  }

  function initMainMap(lat, lng) {
    var elMap = el("register-map");
    if (!elMap) return;
    if (regMap) {
      try {
        if (regMap.remove) regMap.remove();
      } catch (e) {}
      regMap = null;
      regMarker = null;
    }

    if (window.google && window.google.maps) {
      elMap.innerHTML = "";
      regMap = new google.maps.Map(elMap, {
        center: { lat: lat, lng: lng },
        zoom: 14,
        mapTypeControl: true,
        streetViewControl: false,
        mapTypeId: "roadmap",
        gestureHandling: "greedy",
      });
      regMarker = new google.maps.Marker({
        position: { lat: lat, lng: lng },
        map: regMap,
        draggable: true,
        title: "Hospital location",
      });
      regMarker.addListener("dragend", function () {
        var p = regMarker.getPosition();
        state.userConfirmed = true;
        onLocationPicked(p.lat(), p.lng(), { skipReverse: false, userConfirmed: true });
      });
      regMap.addListener("click", function (e) {
        state.userConfirmed = true;
        onLocationPicked(e.latLng.lat(), e.latLng.lng(), {
          skipReverse: false,
          userConfirmed: true,
        });
      });
      // Compatibility helpers used elsewhere (Leaflet-style)
      regMap.setView = function (coords, zoom) {
        regMap.setCenter({ lat: coords[0], lng: coords[1] });
        if (zoom) regMap.setZoom(zoom);
        if (regMarker) regMarker.setPosition({ lat: coords[0], lng: coords[1] });
      };
      setTimeout(function () {
        google.maps.event.trigger(regMap, "resize");
      }, 300);
      return;
    }

    if (typeof L === "undefined") return;
    regMap = L.map("register-map").setView([lat, lng], 14);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap",
    }).addTo(regMap);
    regMarker = L.marker([lat, lng], { draggable: true }).addTo(regMap);
    regMarker.on("dragend", function () {
      var p = regMarker.getLatLng();
      state.userConfirmed = true;
      onLocationPicked(p.lat, p.lng, { skipReverse: false, userConfirmed: true });
    });
    regMap.on("click", function (e) {
      state.userConfirmed = true;
      onLocationPicked(e.latlng.lat, e.latlng.lng, { skipReverse: false, userConfirmed: true });
    });
    setTimeout(function () {
      regMap.invalidateSize();
    }, 300);
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s || "";
    return d.innerHTML;
  }

  function renderSuggestions(results, requireSelection) {
    var list = el("location-suggestions");
    if (!list) return;
    list.innerHTML = "";
    state.lastResults = results || [];

    if (!results || !results.length) {
      list.classList.add("hidden");
      return;
    }

    if (requireSelection && results.length > 1) {
      var hint = document.createElement("li");
      hint.className = "reg-suggestions-hint";
      hint.textContent = results.length + " matches found — tap the correct hospital or address:";
      list.appendChild(hint);
    }

    results.forEach(function (item, idx) {
      if (!isInSomalia(item.lat, item.lng)) return;
      var li = document.createElement("li");
      li.setAttribute("role", "option");
      li.tabIndex = 0;
      var badge =
        item.source === "google"
          ? " · Google Maps"
          : item.source === "known_hospital"
            ? " ✓ Verified"
            : "";
      li.innerHTML =
        "<strong>" + escapeHtml(item.name) + badge + "</strong>" +
        "<span>" + escapeHtml(item.display_name || item.address || "") + "</span>" +
        "<small>" + item.lat.toFixed(5) + ", " + item.lng.toFixed(5) + " · " +
        escapeHtml([item.city, item.district].filter(Boolean).join(", ")) + "</small>";
      li.onclick = function () { selectSearchResult(item); };
      li.onkeydown = function (e) {
        if (e.key === "Enter") selectSearchResult(item);
      };
      list.appendChild(li);
    });
    list.classList.remove("hidden");
  }

  function selectSearchResult(item) {
    if (!item || !isInSomalia(item.lat, item.lng)) {
      showError("Location must be in Somalia.");
      return;
    }
    showError("");
    var list = el("location-suggestions");
    if (list) list.classList.add("hidden");
    var search = el("location-search");
    if (search) search.value = item.name || item.display_name || "";
    state.searchAnchor = { lat: item.lat, lng: item.lng };
    state.userConfirmed = true;
    onLocationPicked(item.lat, item.lng, {
      skipReverse: true,
      userConfirmed: true,
      result: item
    });
    if (regMap) regMap.setView([item.lat, item.lng], 16);
  }

  function searchLocations(query) {
    state.searchQuery = query;
    if (!query || query.length < 2) {
      renderSuggestions([]);
      showError("");
      return;
    }
    fetchJson("/api/geocode/search?q=" + encodeURIComponent(query))
      .then(function (data) {
        if (!data.success) {
          showError(data.message || "Location must be in Somalia.");
          renderSuggestions([]);
          return;
        }
        var valid = (data.results || []).filter(function (r) {
          return isInSomalia(r.lat, r.lng);
        });
        if (!valid.length) {
          showError("Location must be in Somalia.");
          renderSuggestions([]);
          return;
        }
        showError("");
        if (data.rejected > 0) {
          showError(valid.length ? "" : "Location must be in Somalia.");
        }
        renderSuggestions(valid, data.require_selection !== false && valid.length > 1);
        if (valid.length === 1) {
          state.searchAnchor = { lat: valid[0].lat, lng: valid[0].lng };
        }
      })
      .catch(function () {
        showError("Search failed. Try again or click the map.");
        renderSuggestions([]);
      });
  }

  function updatePreview() {
    if (state.lat == null || state.lng == null) return;
    var name = (el("name") && el("name").value) || state.placeName || "Your hospital";
    var address = (el("address") && el("address").value) || "—";
    var city = (el("city") && el("city").value) || "";
    var district = (el("district") && el("district").value) || "";
    var coords = state.lat.toFixed(6) + ", " + state.lng.toFixed(6);

    setText("preview-name", name);
    setText("preview-address", address);
    setText("preview-city", [city, district].filter(Boolean).join(" · ") || "—");
    setText("preview-coords", coords);

    if (previewMap) {
      previewMap.remove();
      previewMap = null;
    }
    previewMap = initLeafletMap("preview-map", state.lat, state.lng, false);
  }

  function showConfirmModal() {
    var name = (el("name") && el("name").value) || "—";
    var address = (el("address") && el("address").value) || "—";
    var coords = state.lat.toFixed(6) + ", " + state.lng.toFixed(6);
    setText("confirm-name", name);
    setText("confirm-address", address);
    setText("confirm-coords", coords);
    if (confirmMap) {
      confirmMap.remove();
      confirmMap = null;
    }
    confirmMap = initLeafletMap("confirm-map", state.lat, state.lng, false);
    var modal = el("reg-confirm-modal");
    if (modal) modal.classList.remove("hidden");
  }

  function hideConfirmModal() {
    var modal = el("reg-confirm-modal");
    if (modal) modal.classList.add("hidden");
  }

  function validateForm(form) {
    var checks = form.querySelectorAll('input[name="services"]:checked');
    if (!checks.length) {
      alert("Select at least one medical service.");
      return false;
    }
    if (state.lat == null || state.lng == null) {
      alert("Select your hospital location from the search results or map.");
      return false;
    }
    if (!isInSomalia(state.lat, state.lng)) {
      showError("Location must be in Somalia.");
      return false;
    }
    if (!state.userConfirmed) {
      alert("Please select a location from the search results or place the pin on the map.");
      return false;
    }
    if (state.lastResults.length > 1 && !state.userConfirmed) {
      alert("Multiple locations found. Please select the correct one from the list.");
      return false;
    }
    return true;
  }

  function initRegistration(opts) {
    opts = opts || {};
    var lat = opts.defaultLat || MOGADISHU.lat;
    var lng = opts.defaultLng || MOGADISHU.lng;

    initMainMap(lat, lng);
    setCoords(lat, lng);
    state.userConfirmed = false;

    var searchInput = el("location-search");
    if (searchInput) {
      searchInput.addEventListener("input", function () {
        clearTimeout(searchTimer);
        state.userConfirmed = false;
        var q = searchInput.value.trim();
        searchTimer = setTimeout(function () { searchLocations(q); }, 400);
      });
      searchInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          searchLocations(searchInput.value.trim());
        }
      });
      searchInput.addEventListener("blur", function () {
        setTimeout(function () {
          var list = el("location-suggestions");
          if (list && !list.matches(":hover")) list.classList.add("hidden");
        }, 250);
      });
    }

    var gpsBtn = el("btn-use-gps");
    if (gpsBtn && window.EmergencyLocation) {
      gpsBtn.onclick = function () {
        EmergencyLocation.resolveUserLocation({ enableHighAccuracy: true })
          .then(function (r) {
            if (!isInSomalia(r.lat, r.lng)) {
              showError("Location must be in Somalia.");
              return;
            }
            state.userConfirmed = true;
            onLocationPicked(r.lat, r.lng, { skipReverse: false, userConfirmed: true });
            if (regMap) regMap.setView([r.lat, r.lng], 16);
          })
          .catch(function () {
            alert("Could not get GPS. Search for your hospital or click the map.");
          });
      };
    }

    var nameInput = el("name");
    if (nameInput) {
      nameInput.addEventListener("input", updatePreview);
      nameInput.addEventListener("blur", function () {
        var n = nameInput.value.trim();
        if (n.length >= 3 && el("location-search") && !el("location-search").value) {
          el("location-search").value = n;
          searchLocations(n);
        }
      });
    }

    var form = el("hospital-register-form");
    var pendingSubmit = false;

    if (form) {
      form.addEventListener("submit", function (e) {
        if (!validateForm(form)) {
          e.preventDefault();
          return;
        }
        if (!pendingSubmit) {
          e.preventDefault();
          updatePreview();
          showConfirmModal();
        }
      });
    }

    var btnConfirm = el("btn-confirm-submit");
    if (btnConfirm && form) {
      btnConfirm.onclick = function () {
        if (!validateSomaliaCoords(state.lat, state.lng)) return;
        pendingSubmit = true;
        hideConfirmModal();
        form.submit();
      };
    }

    var btnCancel = el("btn-confirm-cancel");
    if (btnCancel) btnCancel.onclick = hideConfirmModal;

    var backdrop = document.querySelector(".reg-modal-backdrop");
    if (backdrop) backdrop.onclick = hideConfirmModal;
  }

  function initProfileForm(hospital) {
    var form = el("hospital-profile-form");
    if (!form || !hospital) return;

    function val(id, v) {
      var node = el(id);
      if (node && v != null) node.value = v;
    }
    val("pf-name", hospital.name);
    val("pf-region", hospital.region);
    val("pf-district", hospital.district);
    val("pf-city", hospital.city);
    val("pf-address", hospital.address);
    val("pf-phone", hospital.phone);
    val("pf-emergency", (hospital.emergency_contacts || []).join("\n"));
    val("pf-capacity", hospital.emergency_capacity);
    val("pf-amb-count", hospital.ambulance_count);
    val("pf-status", hospital.operating_status);
    val("pf-lat", hospital.latitude);
    val("pf-lng", hospital.longitude);

    var amb = el("pf-ambulance");
    if (amb) amb.checked = !!hospital.ambulance_available;

    form.querySelectorAll('input[name="pf-services"]').forEach(function (cb) {
      cb.checked = (hospital.services || []).indexOf(cb.value) >= 0;
    });

    if (hospital.latitude && hospital.longitude && typeof L !== "undefined") {
      var pLat = hospital.latitude;
      var pLng = hospital.longitude;
      var pMap = L.map("profile-map").setView([pLat, pLng], 15);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap"
      }).addTo(pMap);
      var pMarker = L.marker([pLat, pLng], { draggable: true }).addTo(pMap);
      pMarker.on("dragend", function () {
        var p = pMarker.getLatLng();
        if (!isInSomalia(p.lat, p.lng)) {
          alert("Location must be in Somalia.");
          return;
        }
        val("pf-lat", p.lat.toFixed(6));
        val("pf-lng", p.lng.toFixed(6));
      });
      pMap.on("click", function (e) {
        if (!isInSomalia(e.latlng.lat, e.latlng.lng)) {
          alert("Location must be in Somalia.");
          return;
        }
        pMarker.setLatLng(e.latlng);
        val("pf-lat", e.latlng.lat.toFixed(6));
        val("pf-lng", e.latlng.lng.toFixed(6));
      });
      setTimeout(function () { pMap.invalidateSize(); }, 300);
    }

    form.onsubmit = function (e) {
      e.preventDefault();
      var services = [];
      form.querySelectorAll('input[name="pf-services"]:checked').forEach(function (cb) {
        services.push(cb.value);
      });
      fetch("/api/hospital/profile", {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: el("pf-name").value,
          region: el("pf-region").value,
          district: el("pf-district").value,
          city: el("pf-city").value,
          address: el("pf-address").value,
          phone: el("pf-phone").value,
          emergency_contacts: el("pf-emergency").value,
          services: services,
          ambulance_available: el("pf-ambulance").checked,
          ambulance_count: el("pf-amb-count").value,
          emergency_capacity: el("pf-capacity").value,
          operating_status: el("pf-status").value,
          latitude: el("pf-lat").value,
          longitude: el("pf-lng").value
        })
      })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.success) {
            alert("Hospital profile saved.");
            location.reload();
          } else {
            alert(d.message || "Save failed");
          }
        });
    };
  }

  return {
    initRegistration: initRegistration,
    initProfileForm: initProfileForm
  };
})();
