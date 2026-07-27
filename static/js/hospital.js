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

  function applyHospitalLogo(url, name) {
    var preview = el("pf-logo-preview");
    var brand = el("hcc-brand-mark");
    var removeBtn = el("pf-logo-remove");
    var initial = (name || (window.HOSPITAL_DATA && HOSPITAL_DATA.name) || "H").toString().charAt(0).toUpperCase();
    url = (url || "").trim();

    function setBox(node, withInitial) {
      if (!node) return;
      if (url) {
        node.classList.add("has-logo");
        node.style.backgroundImage = "url('" + url.replace(/'/g, "%27") + "')";
        node.textContent = "";
      } else {
        node.classList.remove("has-logo");
        node.style.backgroundImage = "";
        if (withInitial) node.textContent = initial;
      }
    }
    setBox(preview, true);
    setBox(brand, true);
    if (removeBtn) removeBtn.hidden = !url;
    if (window.HOSPITAL_DATA) HOSPITAL_DATA.logo_url = url;
  }

  function setLogoMsg(text, isErr) {
    var msg = el("pf-logo-msg");
    if (!msg) return;
    if (!text) {
      msg.hidden = true;
      msg.textContent = "";
      return;
    }
    msg.hidden = false;
    msg.textContent = text;
    msg.style.color = isErr ? "#fca5a5" : "#86efac";
  }

  function initLogoControls(hospital) {
    applyHospitalLogo(hospital && hospital.logo_url, hospital && hospital.name);
    var input = el("pf-logo-input");
    var removeBtn = el("pf-logo-remove");
    if (input && !input._hccLogoBound) {
      input._hccLogoBound = true;
      input.addEventListener("change", function () {
        var file = input.files && input.files[0];
        input.value = "";
        if (!file) return;
        if (file.size > 5 * 1024 * 1024) {
          setLogoMsg("Logo must be 5 MB or smaller.", true);
          return;
        }
        setLogoMsg("Uploading…");
        var fd = new FormData();
        fd.append("file", file);
        fetch("/api/hospital/logo", {
          method: "POST",
          credentials: "same-origin",
          body: fd,
        })
          .then(function (r) {
            return r.json();
          })
          .then(function (d) {
            if (!d || !d.success) {
              setLogoMsg((d && d.message) || "Upload failed", true);
              return;
            }
            applyHospitalLogo(d.logo_url, hospital && hospital.name);
            setLogoMsg("Logo saved.");
          })
          .catch(function () {
            setLogoMsg("Upload failed", true);
          });
      });
    }
    if (removeBtn && !removeBtn._hccLogoBound) {
      removeBtn._hccLogoBound = true;
      removeBtn.addEventListener("click", function () {
        if (!confirm("Remove hospital logo?")) return;
        setLogoMsg("Removing…");
        fetch("/api/hospital/logo", {
          method: "DELETE",
          credentials: "same-origin",
        })
          .then(function (r) {
            return r.json();
          })
          .then(function (d) {
            if (!d || !d.success) {
              setLogoMsg((d && d.message) || "Could not remove logo", true);
              return;
            }
            applyHospitalLogo("", hospital && hospital.name);
            setLogoMsg("Logo removed.");
          })
          .catch(function () {
            setLogoMsg("Could not remove logo", true);
          });
      });
    }
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
    val("pf-amb-ready", (hospital.ambulance_count || 0) + " available (from your units)");
    val("pf-status", hospital.operating_status);
    val("pf-lat", hospital.latitude);
    val("pf-lng", hospital.longitude);
    initLogoControls(hospital);

    form.querySelectorAll('input[name="pf-services"]').forEach(function (cb) {
      cb.checked = (hospital.services || []).indexOf(cb.value) >= 0;
    });

    var mapEl = el("profile-map");
    if (
      hospital.latitude &&
      hospital.longitude &&
      typeof L !== "undefined" &&
      mapEl &&
      !mapEl._hccProfileMap
    ) {
      var pLat = hospital.latitude;
      var pLng = hospital.longitude;
      var pMap = L.map("profile-map").setView([pLat, pLng], 15);
      mapEl._hccProfileMap = pMap;
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
    } else if (mapEl && mapEl._hccProfileMap) {
      setTimeout(function () { mapEl._hccProfileMap.invalidateSize(); }, 200);
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

  function apiAmb(url, opts) {
    opts = opts || {};
    return fetch(url, {
      method: opts.method || "GET",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    }).then(function (r) {
      return r.json().then(function (d) {
        d = d || {};
        d._http = r.status;
        return d;
      });
    });
  }

  var gpsLinkAid = null;

  function setGpsLinkError(msg) {
    var err = el("gps-link-error");
    if (!err) return;
    if (!msg) {
      err.hidden = true;
      err.textContent = "";
      return;
    }
    err.hidden = false;
    err.textContent = msg;
  }

  function fillGpsLinkModal(url, unitLabel) {
    var input = el("gps-link-url");
    var unitEl = el("gps-link-unit");
    var wa = el("gps-link-whatsapp");
    if (input) input.value = url || "";
    if (unitEl) {
      unitEl.textContent = unitLabel
        ? "Unit " + unitLabel + " — send this link to the driver's phone. No hospital login needed."
        : "Send this link to the driver's phone. No hospital login needed.";
    }
    if (wa) {
      var text = encodeURIComponent(
        "GurmadNet Driver GPS link for " + (unitLabel || "ambulance") + ":\n" + (url || "")
      );
      wa.href = url ? "https://wa.me/?text=" + text : "#";
    }
  }

  function closeGpsLinkModal() {
    var modal = el("gps-link-modal");
    if (modal) modal.classList.add("hidden");
    gpsLinkAid = null;
    setGpsLinkError("");
  }

  function openGpsLinkModal(aid, list) {
    gpsLinkAid = aid;
    var existing = (list || []).filter(function (a) {
      return a.id === aid;
    })[0];
    var unitLabel = existing ? existing.call_sign : "#" + aid;
    var modal = el("gps-link-modal");
    if (modal) modal.classList.remove("hidden");
    setGpsLinkError("");
    fillGpsLinkModal("", unitLabel);
    var input = el("gps-link-url");
    if (input) input.value = "Creating link…";

    apiAmb("/api/hospital/ambulances/" + aid + "/gps-link", {
      method: "POST",
      body: { rotate: false },
    }).then(function (r) {
      if (r && r.success === false) {
        setGpsLinkError(r.message || "Could not create GPS link");
        if (input) input.value = "";
        return;
      }
      fillGpsLinkModal((r && r.url) || "", (r.ambulance && r.ambulance.call_sign) || unitLabel);
    });
  }

  function copyGpsLink() {
    var input = el("gps-link-url");
    var url = input ? input.value : "";
    if (!url || url.indexOf("http") !== 0) {
      setGpsLinkError("No link yet");
      return;
    }
    function ok() {
      setGpsLinkError("");
      var hint = el("gps-link-hint");
      if (hint) hint.textContent = "Copied — paste to driver WhatsApp / SMS.";
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(ok).catch(function () {
        if (input) {
          input.select();
          try {
            document.execCommand("copy");
            ok();
          } catch (e) {
            setGpsLinkError("Copy failed — select and copy manually");
          }
        }
      });
    } else if (input) {
      input.select();
      try {
        document.execCommand("copy");
        ok();
      } catch (e) {
        setGpsLinkError("Copy failed — select and copy manually");
      }
    }
  }

  function rotateGpsLink() {
    if (!gpsLinkAid) return;
    if (!confirm("Create a new link? The old driver link will stop working.")) return;
    setGpsLinkError("");
    apiAmb("/api/hospital/ambulances/" + gpsLinkAid + "/gps-link", {
      method: "POST",
      body: { rotate: true },
    }).then(function (r) {
      if (r && r.success === false) {
        setGpsLinkError(r.message || "Could not rotate link");
        return;
      }
      var unitLabel = (r.ambulance && r.ambulance.call_sign) || "";
      fillGpsLinkModal((r && r.url) || "", unitLabel);
      var hint = el("gps-link-hint");
      if (hint) hint.textContent = "New link ready — send to driver again.";
    });
  }

  function revokeGpsLink() {
    if (!gpsLinkAid) return;
    if (!confirm("Revoke driver GPS link? Sharing will stop immediately.")) return;
    apiAmb("/api/hospital/ambulances/" + gpsLinkAid + "/gps-link", { method: "DELETE" }).then(
      function (r) {
        if (r && r.success === false) {
          setGpsLinkError(r.message || "Could not revoke");
          return;
        }
        fillGpsLinkModal("", "");
        closeGpsLinkModal();
      }
    );
  }

  // Poll must not re-bind this panel — that wiped pending photo uploads mid-edit
  var ambPanelInited = false;
  var ambPanelLoadList = null;
  var ambPanelRenderList = null;

  function initAmbulancesPanel() {
    var table = el("hospital-amb-table");
    if (!table) return;
    if (ambPanelInited) {
      if (typeof ambPanelLoadList === "function") ambPanelLoadList();
      return;
    }
    ambPanelInited = true;
    var pendingPhotoFile = null;
    var removePhotoOnSave = false;
    var pendingVehicleFile = null;
    var removeVehicleOnSave = false;

    function gpsText(a) {
      if (a.latitude == null || a.longitude == null) return "—";
      return Number(a.latitude).toFixed(4) + ", " + Number(a.longitude).toFixed(4);
    }

    function escAttr(s) {
      return String(s == null ? "" : s)
        .replace(/&/g, "&amp;")
        .replace(/"/g, "&quot;")
        .replace(/</g, "&lt;");
    }

    function setKindStatus(kind, msg, isErr) {
      var st = el(kind === "vehicle" ? "amb-vehicle-status" : "amb-photo-status");
      if (!st) return;
      if (!msg) {
        st.hidden = true;
        st.textContent = "";
        return;
      }
      st.hidden = false;
      st.textContent = msg;
      st.style.color = isErr ? "#fca5a5" : "#86efac";
    }

    function setPhotoStatus(msg, isErr) {
      setKindStatus("driver", msg, isErr);
    }

    function setKindPreview(kind, url) {
      var preview = el(kind === "vehicle" ? "amb-vehicle-preview" : "amb-photo-preview");
      var urlInput = el(kind === "vehicle" ? "amb-vehicle-url" : "amb-photo-url");
      var removeBtn = el(kind === "vehicle" ? "amb-vehicle-remove" : "amb-photo-remove");
      var pending = kind === "vehicle" ? pendingVehicleFile : pendingPhotoFile;
      url = (url || "").trim();
      if (urlInput) urlInput.value = url;
      if (preview) {
        if (url) {
          preview.classList.add("has-photo");
          preview.style.backgroundImage = "url('" + url.replace(/'/g, "%27") + "')";
        } else {
          preview.classList.remove("has-photo");
          preview.style.backgroundImage = "";
        }
      }
      if (removeBtn) removeBtn.hidden = !(url || pending);
    }

    function setPhotoPreview(url) {
      setKindPreview("driver", url);
    }

    function clearPendingPhoto() {
      pendingPhotoFile = null;
      removePhotoOnSave = false;
      pendingVehicleFile = null;
      removeVehicleOnSave = false;
      var input = el("amb-photo-input");
      if (input) input.value = "";
      var vInput = el("amb-vehicle-input");
      if (vInput) vInput.value = "";
      setKindStatus("driver", "");
      setKindStatus("vehicle", "");
    }

    function csrfHeaders() {
      var token = "";
      if (window.GurmadNetCSRF && typeof window.GurmadNetCSRF.token === "function") {
        token = window.GurmadNetCSRF.token() || "";
      }
      if (!token) {
        var meta = document.querySelector('meta[name="csrf-token"]');
        token = meta ? meta.getAttribute("content") || "" : "";
      }
      return token ? { "X-CSRFToken": token } : {};
    }

    function uploadPhoto(aid, file, kind) {
      kind = kind || "driver";
      var fd = new FormData();
      fd.append("file", file);
      fd.append("kind", kind);
      return fetch("/api/hospital/ambulances/" + aid + "/photo?kind=" + encodeURIComponent(kind), {
        method: "POST",
        credentials: "same-origin",
        headers: csrfHeaders(),
        body: fd,
      }).then(function (r) {
        return r.json().then(function (d) {
          d = d || {};
          d._http = r.status;
          return d;
        });
      });
    }

    function deletePhoto(aid, kind) {
      kind = kind || "driver";
      return fetch("/api/hospital/ambulances/" + aid + "/photo?kind=" + encodeURIComponent(kind), {
        method: "DELETE",
        credentials: "same-origin",
        headers: csrfHeaders(),
      }).then(function (r) {
        return r.json();
      });
    }

    function loadList() {
      apiAmb("/api/hospital/ambulances").then(function (d) {
        renderAmbRows((d && d.ambulances) || [], d);
      });
    }

    function renderAmbRows(list, d) {
        var tbody = table.querySelector("tbody");
        if (!tbody) return;
        tbody.innerHTML = "";
        list = list || [];
        d = d || {};
        var ready = el("stat-ambulance");
        if (ready) {
          if (d.available_count != null) {
            ready.textContent = String(d.available_count);
          } else {
            ready.textContent = String(
              list.filter(function (a) {
                return (a.status || "") === "available";
              }).length
            );
          }
        }
        var pfReady = el("pf-amb-ready");
        if (pfReady) {
          var availN =
            d.available_count != null
              ? d.available_count
              : list.filter(function (a) {
                  return (a.status || "") === "available";
                }).length;
          pfReady.value = availN + " available (from your units)";
        }
        if (!list.length) {
          tbody.innerHTML =
            '<tr><td colspan="7" style="text-align:center;padding:20px;color:#94a3b8;">No ambulances yet — add a unit for dispatch</td></tr>';
          return;
        }
        list.forEach(function (a) {
          var tr = document.createElement("tr");
          var bust = a.updated_at ? ("?v=" + encodeURIComponent(String(a.updated_at))) : "";
          var driverThumb = a.driver_photo_url
            ? '<img class="hcc-amb-thumb" src="' +
              escAttr(a.driver_photo_url) +
              bust +
              '" alt="">'
            : '<span class="hcc-amb-thumb-empty" title="No driver photo">' +
              escAttr(((a.driver_name || a.call_sign || "?") + "").charAt(0).toUpperCase()) +
              "</span>";
          var vehicleThumb = a.vehicle_photo_url
            ? '<img class="hcc-amb-thumb hcc-amb-thumb-car" src="' +
              escAttr(a.vehicle_photo_url) +
              bust +
              '" alt="car" title="Vehicle photo">'
            : '<span class="hcc-amb-thumb-empty hcc-amb-thumb-car" title="No vehicle photo">C</span>';
          var photoCell =
            '<div class="hcc-amb-thumbs">' + driverThumb + vehicleThumb + "</div>";
          tr.innerHTML =
            "<td>" +
            photoCell +
            "</td><td><strong>" +
            escAttr(a.call_sign || "") +
            "</strong></td><td>" +
            escAttr(a.status || "") +
            "</td><td>" +
            escAttr(a.driver_name || "—") +
            "</td><td>" +
            escAttr(a.driver_phone || "—") +
            "</td><td>" +
            gpsText(a) +
            '</td><td><button type="button" class="hcc-btn hcc-btn-ghost hcc-btn-sm amb-edit" data-id="' +
            a.id +
            '">Update</button> <button type="button" class="hcc-btn hcc-btn-primary hcc-btn-sm amb-gps-link" data-id="' +
            a.id +
            '">Driver GPS link</button> <button type="button" class="hcc-btn hcc-btn-ghost hcc-btn-sm amb-gps" data-id="' +
            a.id +
            '">Desk GPS once</button> <button type="button" class="hcc-btn hcc-btn-danger hcc-btn-sm amb-del" data-id="' +
            a.id +
            '">Remove</button></td>';
          tbody.appendChild(tr);
        });
        tbody.querySelectorAll(".amb-edit").forEach(function (b) {
          b.onclick = function () {
            openEditor(parseInt(b.getAttribute("data-id"), 10), list);
          };
        });
        tbody.querySelectorAll(".amb-gps-link").forEach(function (b) {
          b.onclick = function () {
            openGpsLinkModal(parseInt(b.getAttribute("data-id"), 10), list);
          };
        });
        tbody.querySelectorAll(".amb-gps").forEach(function (b) {
          b.onclick = function () {
            shareGps(parseInt(b.getAttribute("data-id"), 10));
          };
        });
        tbody.querySelectorAll(".amb-del").forEach(function (b) {
          b.onclick = function () {
            if (!confirm("Remove this ambulance from GurmadNet dispatch?")) return;
            apiAmb("/api/hospital/ambulances/" + b.getAttribute("data-id"), { method: "DELETE" }).then(
              function (r) {
                if (r && r.success === false) {
                  alert(r.message || "Delete failed");
                  return;
                }
                loadList();
              }
            );
          };
        });
    }

    ambPanelLoadList = loadList;
    ambPanelRenderList = renderAmbRows;

    function setAmbFormError(msg) {
      var err = el("amb-form-error");
      if (!err) return;
      if (!msg) {
        err.hidden = true;
        err.textContent = "";
        return;
      }
      err.hidden = false;
      err.textContent = msg;
    }

    function closeAmbForm() {
      var modal = el("amb-form-modal");
      if (modal) modal.classList.add("hidden");
      clearPendingPhoto();
      setAmbFormError("");
    }

    function photoBustUrl(url, stamp) {
      url = (url || "").trim();
      if (!url) return "";
      if (url.indexOf("?") >= 0) return url;
      return url + "?v=" + encodeURIComponent(stamp || String(Date.now()));
    }

    function openEditor(aid, list) {
      var existing = (list || []).filter(function (a) {
        return a.id === aid;
      })[0];
      var modal = el("amb-form-modal");
      var form = el("amb-editor-form");
      if (!modal || !form) return;

      clearPendingPhoto();
      el("amb-form-title").textContent = existing ? "Edit ambulance" : "Add ambulance";
      el("amb-edit-id").value = existing ? String(existing.id) : "";
      el("amb-call-sign").value = (existing && existing.call_sign) || "";
      el("amb-status").value = (existing && existing.status) || "available";
      el("amb-driver-name").value = (existing && existing.driver_name) || "";
      el("amb-driver-phone").value = (existing && existing.driver_phone) || "";
      el("amb-lat").value =
        existing && existing.latitude != null ? String(existing.latitude) : "";
      el("amb-lng").value =
        existing && existing.longitude != null ? String(existing.longitude) : "";
      var stamp = (existing && existing.updated_at) || String(Date.now());
      setPhotoPreview(photoBustUrl(existing && existing.driver_photo_url, stamp));
      setKindPreview("vehicle", photoBustUrl(existing && existing.vehicle_photo_url, stamp));
      setPhotoStatus("");
      setKindStatus("vehicle", "");
      setAmbFormError("");
      modal.classList.remove("hidden");
      setTimeout(function () {
        var focusEl = el("amb-call-sign");
        if (focusEl) focusEl.focus();
      }, 50);
    }

    function saveAmbForm(e) {
      if (e) e.preventDefault();
      setAmbFormError("");
      var callSign = (el("amb-call-sign").value || "").trim();
      var status = (el("amb-status").value || "").trim().toLowerCase();
      var driverName = (el("amb-driver-name").value || "").trim();
      var driverPhone = (el("amb-driver-phone").value || "").trim();
      var latIn = (el("amb-lat").value || "").trim();
      var lngIn = (el("amb-lng").value || "").trim();
      var aidRaw = (el("amb-edit-id").value || "").trim();
      var aid = aidRaw ? parseInt(aidRaw, 10) : null;

      if (!callSign) {
        setAmbFormError("Unit label / call sign is required.");
        el("amb-call-sign").focus();
        return;
      }
      if (["available", "busy", "offline"].indexOf(status) < 0) {
        setAmbFormError("Status must be available, busy, or offline.");
        return;
      }
      if (status === "available" && !driverPhone) {
        setAmbFormError("Driver phone is required when status is Available.");
        el("amb-driver-phone").focus();
        return;
      }
      if ((latIn && !lngIn) || (!latIn && lngIn)) {
        setAmbFormError("Enter both latitude and longitude, or leave both empty.");
        return;
      }

      var payload = {
        call_sign: callSign,
        status: status,
        driver_name: driverName,
        driver_phone: driverPhone,
        latitude: latIn || null,
        longitude: lngIn || null,
      };
      var saveBtn = el("amb-form-save");
      if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.textContent = "Saving…";
      }
      var req = aid
        ? apiAmb("/api/hospital/ambulances/" + aid, { method: "PUT", body: payload })
        : apiAmb("/api/hospital/ambulances", { method: "POST", body: payload });
      req
        .then(function (r) {
          if (r && r.success === false) {
            setAmbFormError(r.message || "Save failed");
            return null;
          }
          var saved = (r && r.ambulance) || {};
          var newId = saved.id || aid;
          if (!newId) return r;
          var chain = Promise.resolve(r);
          if (pendingPhotoFile) {
            chain = chain.then(function () {
              return uploadPhoto(newId, pendingPhotoFile, "driver").then(function (up) {
                if (up && up.success === false) {
                  setAmbFormError(up.message || "Unit saved, but driver photo upload failed.");
                  return null;
                }
                pendingPhotoFile = null;
                return r;
              });
            });
          } else if (removePhotoOnSave && aid) {
            chain = chain.then(function () {
              return deletePhoto(aid, "driver").then(function (del) {
                if (del && del.success === false) {
                  setAmbFormError(del.message || "Unit saved, but driver photo remove failed.");
                  return null;
                }
                return r;
              });
            });
          }
          if (pendingVehicleFile) {
            chain = chain.then(function (prev) {
              if (!prev) return null;
              return uploadPhoto(newId, pendingVehicleFile, "vehicle").then(function (up) {
                if (up && up.success === false) {
                  setAmbFormError(up.message || "Unit saved, but vehicle photo upload failed.");
                  return null;
                }
                pendingVehicleFile = null;
                return r;
              });
            });
          } else if (removeVehicleOnSave && aid) {
            chain = chain.then(function (prev) {
              if (!prev) return null;
              return deletePhoto(aid, "vehicle").then(function (del) {
                if (del && del.success === false) {
                  setAmbFormError(del.message || "Unit saved, but vehicle photo remove failed.");
                  return null;
                }
                return r;
              });
            });
          }
          return chain;
        })
        .then(function (r) {
          if (!r) return;
          closeAmbForm();
          loadList();
        })
        .catch(function () {
          setAmbFormError("Network error — try again.");
        })
        .then(function () {
          if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.textContent = "Save ambulance";
          }
        });
    }

    function handlePhotoPicked(kind) {
      var input = el(kind === "vehicle" ? "amb-vehicle-input" : "amb-photo-input");
      var file = input && input.files && input.files[0];
      if (!file) return;
      if (file.size > 5 * 1024 * 1024) {
        setAmbFormError("Photo must be 5 MB or smaller.");
        setKindStatus(kind, "Photo too large (max 5 MB).", true);
        input.value = "";
        return;
      }
      setAmbFormError("");
      if (kind === "vehicle") {
        pendingVehicleFile = file;
        removeVehicleOnSave = false;
      } else {
        pendingPhotoFile = file;
        removePhotoOnSave = false;
      }
      var previewUrl = URL.createObjectURL(file);
      setKindPreview(kind, previewUrl);
      var removeBtn = el(kind === "vehicle" ? "amb-vehicle-remove" : "amb-photo-remove");
      if (removeBtn) removeBtn.hidden = false;

      var aidRaw = (el("amb-edit-id").value || "").trim();
      var aid = aidRaw ? parseInt(aidRaw, 10) : null;
      if (aid) {
        setKindStatus(kind, "Uploading photo…");
        uploadPhoto(aid, file, kind)
          .then(function (up) {
            // Always refresh list — photo may have saved even if a side-effect failed
            loadList();
            if (!up || up.success === false) {
              setKindStatus(kind, (up && up.message) || "Photo upload failed", true);
              setAmbFormError((up && up.message) || "Photo upload failed — try again");
              return;
            }
            if (kind === "vehicle") pendingVehicleFile = null;
            else pendingPhotoFile = null;
            var savedUrl =
              kind === "vehicle"
                ? up.vehicle_photo_url || previewUrl
                : up.driver_photo_url || previewUrl;
            var stamp = (up.ambulance && up.ambulance.updated_at) || String(Date.now());
            if (savedUrl && savedUrl.indexOf("?") < 0) {
              savedUrl = savedUrl + "?v=" + encodeURIComponent(stamp);
            }
            setKindPreview(kind, savedUrl);
            setKindStatus(kind, kind === "vehicle" ? "Vehicle photo saved." : "Driver photo saved.");
          })
          .catch(function () {
            loadList();
            setKindStatus(kind, "Photo upload failed — try again.", true);
          });
      } else {
        setKindStatus(kind, "Photo ready — click Save ambulance to keep it.");
      }
    }

    function onPhotoPicked() {
      handlePhotoPicked("driver");
    }

    function onVehiclePicked() {
      handlePhotoPicked("vehicle");
    }

    function handlePhotoRemove(kind) {
      var aidRaw = (el("amb-edit-id").value || "").trim();
      var aid = aidRaw ? parseInt(aidRaw, 10) : null;
      if (kind === "vehicle") pendingVehicleFile = null;
      else pendingPhotoFile = null;
      var input = el(kind === "vehicle" ? "amb-vehicle-input" : "amb-photo-input");
      if (input) input.value = "";

      if (aid) {
        setKindStatus(kind, "Removing photo…");
        deletePhoto(aid, kind)
          .then(function (del) {
            if (del && del.success === false) {
              setKindStatus(kind, (del && del.message) || "Could not remove photo", true);
              return;
            }
            if (kind === "vehicle") removeVehicleOnSave = false;
            else removePhotoOnSave = false;
            setKindPreview(kind, "");
            setKindStatus(kind, "Photo removed.");
            loadList();
          })
          .catch(function () {
            setKindStatus(kind, "Could not remove photo", true);
          });
        return;
      }
      if (kind === "vehicle") removeVehicleOnSave = true;
      else removePhotoOnSave = true;
      setKindPreview(kind, "");
      setKindStatus(kind, "Photo cleared.");
      var removeBtn = el(kind === "vehicle" ? "amb-vehicle-remove" : "amb-photo-remove");
      if (removeBtn) removeBtn.hidden = true;
    }

    function onPhotoRemove() {
      handlePhotoRemove("driver");
    }

    function onVehicleRemove() {
      handlePhotoRemove("vehicle");
    }

    function pickPhoto() {
      var input = el("amb-photo-input");
      if (input) input.click();
    }

    function pickVehicle() {
      var input = el("amb-vehicle-input");
      if (input) input.click();
    }

    function fillAmbGpsFromDevice() {
      if (!navigator.geolocation) {
        setAmbFormError("GPS not available on this device.");
        return;
      }
      setAmbFormError("");
      var btn = el("amb-use-gps");
      if (btn) {
        btn.disabled = true;
        btn.textContent = "Getting GPS…";
      }
      navigator.geolocation.getCurrentPosition(
        function (pos) {
          el("amb-lat").value = Number(pos.coords.latitude).toFixed(6);
          el("amb-lng").value = Number(pos.coords.longitude).toFixed(6);
          if (btn) {
            btn.disabled = false;
            btn.textContent = "Use my GPS";
          }
        },
        function () {
          setAmbFormError("Could not read GPS. Allow location access.");
          if (btn) {
            btn.disabled = false;
            btn.textContent = "Use my GPS";
          }
        },
        { enableHighAccuracy: true, timeout: 15000 }
      );
    }

    function shareGps(aid) {
      if (!navigator.geolocation) {
        alert("GPS not available on this device");
        return;
      }
      navigator.geolocation.getCurrentPosition(
        function (pos) {
          apiAmb("/api/hospital/ambulances/" + aid + "/location", {
            method: "POST",
            body: { latitude: pos.coords.latitude, longitude: pos.coords.longitude },
          }).then(function (r) {
            if (r && r.success === false) {
              alert(r.message || "GPS update failed");
              return;
            }
            loadList();
          });
        },
        function () {
          alert("Could not read GPS. Allow location access.");
        },
        { enableHighAccuracy: true, timeout: 15000 }
      );
    }

    var addBtn = el("btn-add-hospital-amb");
    if (addBtn) {
      addBtn.onclick = function () {
        openEditor(null, []);
      };
    }
    var refreshBtn = el("btn-refresh-hospital-amb");
    if (refreshBtn) refreshBtn.onclick = loadList;

    var ambForm = el("amb-editor-form");
    if (ambForm) ambForm.onsubmit = saveAmbForm;
    var ambCancel = el("amb-form-cancel");
    if (ambCancel) ambCancel.onclick = closeAmbForm;
    var ambGpsBtn = el("amb-use-gps");
    if (ambGpsBtn) ambGpsBtn.onclick = fillAmbGpsFromDevice;
    var photoInput = el("amb-photo-input");
    if (photoInput) photoInput.onchange = onPhotoPicked;
    var photoPick = el("amb-photo-pick");
    if (photoPick) photoPick.onclick = pickPhoto;
    var photoRemove = el("amb-photo-remove");
    if (photoRemove) photoRemove.onclick = onPhotoRemove;
    var vehicleInput = el("amb-vehicle-input");
    if (vehicleInput) vehicleInput.onchange = onVehiclePicked;
    var vehiclePick = el("amb-vehicle-pick");
    if (vehiclePick) vehiclePick.onclick = pickVehicle;
    var vehicleRemove = el("amb-vehicle-remove");
    if (vehicleRemove) vehicleRemove.onclick = onVehicleRemove;
    var ambModal = el("amb-form-modal");
    if (ambModal) {
      ambModal.onclick = function (ev) {
        if (ev.target === ambModal) closeAmbForm();
      };
    }
    var gpsModal = el("gps-link-modal");
    if (gpsModal) {
      gpsModal.onclick = function (ev) {
        if (ev.target === gpsModal) closeGpsLinkModal();
      };
    }
    if (el("gps-link-copy")) el("gps-link-copy").onclick = copyGpsLink;
    if (el("gps-link-rotate")) el("gps-link-rotate").onclick = rotateGpsLink;
    if (el("gps-link-revoke")) el("gps-link-revoke").onclick = revokeGpsLink;
    if (el("gps-link-close")) el("gps-link-close").onclick = closeGpsLinkModal;

    loadList();
  }

  return {
    initRegistration: initRegistration,
    initProfileForm: initProfileForm,
    initAmbulancesPanel: initAmbulancesPanel,
    openDriverGpsLink: function (aid) {
      openGpsLinkModal(aid, []);
    },
    refreshAmbulancesTable: function (list) {
      if (!el("hospital-amb-table")) return;
      if (!ambPanelInited) {
        initAmbulancesPanel();
        return;
      }
      // Live poll already fetched ambulances — render without wiping photo form state
      if (Array.isArray(list) && typeof ambPanelRenderList === "function") {
        ambPanelRenderList(list, {});
        return;
      }
      if (typeof ambPanelLoadList === "function") ambPanelLoadList();
    },
  };
})();
