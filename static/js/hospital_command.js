/**
 * Hospital Emergency Command Center desk
 * Queue · Map · Ambulances · Notifications · Timeline · Nearest unit
 */
(function () {
  "use strict";

  var ACTIVE = {
    pending: 1,
    pending_hospital: 1,
    accepted: 1,
    dispatched: 1,
    in_progress: 1,
    en_route: 1,
    arrived: 1,
    arrived_at_scene: 1,
    reached_victim: 1,
    transporting: 1,
    open: 1,
  };
  var DONE_TODAY = { completed: 1, resolved: 1 };

  var state = {
    emergencies: [],
    history: [],
    ambulances: [],
    notifications: [],
    selectedId: null,
    map: null,
    layer: null,
    routeLine: null,
    selfMarker: null,
    mapFilter: "all",
    mapFitted: false,
    refreshMs: typeof HCC_REFRESH_MS === "number" ? HCC_REFRESH_MS : 5000,
    modalAction: null,
    liveGps: { aid: null, stop: null, lastPush: 0, firstFix: false },
    ambMotion: null,
    motionPhaseByAid: {},
    chatCaseId: null,
    chatPoll: null,
    chatFilter: "all",
    chatSearch: "",
    chatMeta: {},
    chatMsgCache: {},
  };

  function $(id) {
    return document.getElementById(id);
  }

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function api(url, opts) {
    opts = opts || {};
    return fetch(url, {
      method: opts.method || "GET",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: opts.body != null ? JSON.stringify(opts.body) : undefined,
    }).then(function (r) {
      return r.json().then(function (d) {
        d = d || {};
        d._http = r.status;
        return d;
      });
    });
  }

  function hospital() {
    return window.HOSPITAL_DATA || {};
  }

  function haversineKm(lat1, lng1, lat2, lng2) {
    if (lat1 == null || lng1 == null || lat2 == null || lng2 == null) return null;
    var R = 6371;
    var toRad = Math.PI / 180;
    var dLat = (lat2 - lat1) * toRad;
    var dLng = (lng2 - lng1) * toRad;
    var a =
      Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(lat1 * toRad) * Math.cos(lat2 * toRad) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function formatTime(iso) {
    try {
      return new Date(iso).toLocaleString("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
        day: "numeric",
        month: "short",
      });
    } catch (e) {
      return iso || "";
    }
  }

  function elapsed(iso) {
    if (!iso) return "";
    var ms = Date.now() - new Date(iso).getTime();
    if (isNaN(ms) || ms < 0) return "";
    var m = Math.floor(ms / 60000);
    if (m < 60) return m + " min ago";
    return Math.floor(m / 60) + "h " + (m % 60) + "m ago";
  }

  function priorityOf(em) {
    var p = (em.priority || em.type || "").toString().toLowerCase();
    if (p.indexOf("critical") >= 0 || p.indexOf("trauma") >= 0 || p === "high") return "high";
    if (p.indexOf("fire") >= 0 || p === "medium") return "med";
    return "low";
  }

  function selectedEmergency() {
    if (!state.selectedId) return null;
    return (
      state.emergencies.filter(function (e) {
        return e.id === state.selectedId;
      })[0] || null
    );
  }

  function availableUnits() {
    return state.ambulances.filter(function (a) {
      return (a.status || "") === "available";
    });
  }

  function nearestUnitFor(em) {
    var units = availableUnits();
    if (!units.length || !em) return null;
    var best = null;
    var bestD = Infinity;
    units.forEach(function (u) {
      var lat = u.latitude != null ? u.latitude : hospital().latitude;
      var lng = u.longitude != null ? u.longitude : hospital().longitude;
      var d = haversineKm(lat, lng, em.latitude, em.longitude);
      if (d == null) d = 999;
      if (d < bestD) {
        bestD = d;
        best = { unit: u, km: d };
      }
    });
    return best;
  }

  /* ---------- Map (admin-style filters + circle markers) ---------- */
  function markerColor(kind, status) {
    if (kind === "hospital") return "#22c55e";
    if (kind === "emergency") return "#ef4444";
    if (kind === "ambulance") {
      if (status === "busy") return "#f59e0b";
      if (status === "offline") return "#64748b";
      return "#22d3ee";
    }
    return "#22d3ee";
  }

  function syncMapFilterButtons() {
    document.querySelectorAll("[data-hcc-map-filter]").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.getAttribute("data-hcc-map-filter") === state.mapFilter);
      var kind = btn.getAttribute("data-hcc-map-filter");
      if (kind === "all") {
        var n =
          (hospital().latitude != null ? 1 : 0) +
          state.emergencies.filter(function (e) {
            return e.latitude != null;
          }).length +
          state.ambulances.filter(function (a) {
            return a.latitude != null;
          }).length;
        btn.textContent = "All (" + n + ")";
        return;
      }
      var count = 0;
      if (kind === "hospital") count = hospital().latitude != null ? 1 : 0;
      if (kind === "emergency") {
        count = state.emergencies.filter(function (e) {
          return e.latitude != null;
        }).length;
      }
      if (kind === "ambulance") {
        count = state.ambulances.filter(function (a) {
          return a.latitude != null;
        }).length;
      }
      var label = kind === "emergency" ? "SOS" : kind === "ambulance" ? "Ambulance" : "Hospital";
      btn.textContent = label + " (" + count + ")";
    });
  }

  function ensureMap() {
    if (state.map || typeof L === "undefined") return;
    var el = $("hospital-overview-map");
    if (!el) return;
    var h = hospital();
    var lat = h.latitude != null ? Number(h.latitude) : 2.0469;
    var lng = h.longitude != null ? Number(h.longitude) : 45.3182;
    state.map = L.map(el, {
      zoomControl: true,
      scrollWheelZoom: true,
      doubleClickZoom: true,
      touchZoom: true,
      dragging: true,
      minZoom: 5,
      maxZoom: 19,
    }).setView([lat, lng], 13);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap",
      maxZoom: 19,
    }).addTo(state.map);
    state.layer = L.layerGroup().addTo(state.map);
    ensureAmbMotion();
    setTimeout(function () {
      state.map.invalidateSize();
    }, 200);
  }

  function ensureAmbMotion() {
    if (state.ambMotion || typeof AmbulanceMotion === "undefined") return;
    state.ambMotion = new AmbulanceMotion({
      getHospital: hospital,
      isLiveTracked: function (aid) {
        return state.liveGps.aid === aid;
      },
      onStatus: function (info) {
        state.motionPhaseByAid[info.aid] = info;
        var banner = $("hcc-amb-motion-banner");
        var phaseEl = $("hcc-amb-motion-phase");
        var metaEl = $("hcc-amb-motion-meta");
        var etaEl = $("map-eta-line");
        if (info.phase === "available") {
          if (banner) banner.hidden = true;
          if (etaEl) etaEl.textContent = (info.callSign || "Unit") + " available at base";
          delete state.motionPhaseByAid[info.aid];
          renderAmbStrip();
          return;
        }
        if (banner) {
          banner.hidden = false;
          banner.style.borderColor = info.color || "#94a3b8";
        }
        if (phaseEl) {
          phaseEl.textContent = (info.callSign || "AMB") + " · " + (info.label || "");
          phaseEl.style.color = info.color || "#e2e8f0";
        }
        if (metaEl) {
          metaEl.textContent =
            (info.km != null ? info.km.toFixed(1) + " km left" : "") +
            (info.etaMin != null ? " · ETA ~" + info.etaMin + " min" : "") +
            (info.carrying ? " · Patient on board" : "");
        }
        if (etaEl) {
          etaEl.textContent =
            (info.label || "En route") +
            " · " +
            (info.km != null ? info.km.toFixed(1) + " km" : "") +
            (info.etaMin != null ? " · ETA ~" + info.etaMin + " min" : "");
        }
      },
      onVisualAvailable: function (aid, dest) {
        // Visual only — do not change backend dispatch. Soft-update local pin.
        var unit = state.ambulances.filter(function (a) {
          return a.id === aid;
        })[0];
        if (unit && dest) {
          unit.latitude = dest[0];
          unit.longitude = dest[1];
        }
        renderAmbStrip();
        renderMap(false);
      },
    });
    if (state.map) state.ambMotion.attach(state.map);
  }

  function syncAmbMotion() {
    ensureAmbMotion();
    if (!state.ambMotion || !state.map) return;
    state.ambMotion.attach(state.map);
    state.ambMotion.sync({
      emergencies: state.emergencies,
      ambulances: state.ambulances,
    });
  }

  function motionActiveIds() {
    return state.ambMotion ? state.ambMotion.activeIds() : [];
  }

  function popupHtml(title, kindLabel, lines) {
    return (
      '<div style="min-width:160px">' +
      "<div style='font-size:0.95rem;font-weight:800;margin-bottom:4px'>" +
      esc(title) +
      "</div>" +
      "<div style='color:#64748b;font-size:0.8rem;margin-bottom:6px'>" +
      esc(kindLabel) +
      "</div>" +
      (lines || []).join("") +
      "</div>"
    );
  }

  function renderMap(forceFit) {
    ensureMap();
    if (!state.map || !state.layer) return;
    state.layer.clearLayers();
    if (state.routeLine) {
      state.map.removeLayer(state.routeLine);
      state.routeLine = null;
    }
    syncMapFilterButtons();
    var h = hospital();
    var bounds = [];
    var shown = 0;
    var filter = state.mapFilter || "all";

    function showKind(kind) {
      return filter === "all" || filter === kind;
    }

    if (showKind("hospital") && h.latitude != null && h.longitude != null) {
      var hospColor = markerColor("hospital");
      var hp = L.circleMarker([h.latitude, h.longitude], {
        radius: 8,
        color: hospColor,
        weight: 2,
        fillColor: hospColor,
        fillOpacity: 0.9,
      }).bindPopup(
        popupHtml(h.name || "Hospital", "Hospital", [
          h.phone ? "<div>Phone: " + esc(h.phone) + "</div>" : "",
          "<div style='margin-top:6px;color:#94a3b8;font-size:0.75rem'>" +
            Number(h.latitude).toFixed(5) +
            ", " +
            Number(h.longitude).toFixed(5) +
            "</div>",
        ])
      );
      hp.addTo(state.layer);
      bounds.push([h.latitude, h.longitude]);
      shown += 1;
    }

    if (showKind("emergency")) {
      state.emergencies.forEach(function (em) {
        if (em.latitude == null || em.longitude == null) return;
        var live = !!em.tracking_active;
        var emColor = markerColor("emergency");
        var m = L.circleMarker([em.latitude, em.longitude], {
          radius: live ? 9 : 7,
          color: emColor,
          weight: 2,
          fillColor: emColor,
          fillOpacity: 0.9,
        }).bindPopup(
          popupHtml("#" + em.id + " · " + (em.type || "Emergency"), "SOS Emergency", [
            em.status ? "<div>Status: <strong>" + esc(String(em.status).replace(/_/g, " ")) + "</strong></div>" : "",
            em.caller_name ? "<div>Caller: " + esc(em.caller_name) + "</div>" : "",
            em.phone
              ? '<div>Phone: <a href="tel:' +
                esc(String(em.phone).replace(/\s/g, "")) +
                '">' +
                esc(em.phone) +
                "</a></div>"
              : "",
            em.location ? "<div>" + esc(em.location) + "</div>" : "",
            live ? '<div style="margin-top:6px;color:#dc2626;font-weight:700;">Live GPS</div>' : "",
          ])
        );
        m.addTo(state.layer);
        bounds.push([em.latitude, em.longitude]);
        shown += 1;
      });
    }

    if (showKind("ambulance")) {
      var motionIds = motionActiveIds();
      state.ambulances.forEach(function (a) {
        if (a.latitude == null || a.longitude == null) return;
        if (motionIds.indexOf(a.id) >= 0) return;
        var ambColor = markerColor("ambulance", a.status);
        var motionInfo = state.motionPhaseByAid[a.id];
        var m = L.circleMarker([a.latitude, a.longitude], {
          radius: 7,
          color: ambColor,
          weight: 2,
          fillColor: ambColor,
          fillOpacity: 0.9,
        }).bindPopup(
          popupHtml(a.call_sign || "Ambulance", "Ambulance", [
            "<div>Status: <strong>" +
              esc(motionInfo && motionInfo.label ? motionInfo.label : a.status || "") +
              "</strong></div>",
            a.driver_name ? "<div>Driver: " + esc(a.driver_name) + "</div>" : "",
            a.driver_phone
              ? '<div>Phone: <a href="tel:' +
                esc(String(a.driver_phone).replace(/\s/g, "")) +
                '">' +
                esc(a.driver_phone) +
                "</a></div>"
              : "",
          ])
        );
        m.addTo(state.layer);
        bounds.push([a.latitude, a.longitude]);
        shown += 1;
      });
      motionIds.forEach(function () {
        shown += 1;
      });
    }

    var empty = $("hcc-map-empty");
    if (empty) empty.hidden = shown > 0;

    var em = selectedEmergency();
    var etaEl = $("map-eta-line");
    var statusEl = $("hcc-map-status");
    var motionBusy = motionActiveIds().length > 0;
    if (!motionBusy && em && em.latitude != null && (filter === "all" || filter === "emergency")) {
      var fromLat = em.assigned_ambulance_latitude != null ? em.assigned_ambulance_latitude : h.latitude;
      var fromLng = em.assigned_ambulance_longitude != null ? em.assigned_ambulance_longitude : h.longitude;
      if (em.assigned_ambulance_id) {
        var unit = state.ambulances.filter(function (a) {
          return a.id === em.assigned_ambulance_id;
        })[0];
        if (unit && unit.latitude != null) {
          fromLat = unit.latitude;
          fromLng = unit.longitude;
        }
      }
      if (fromLat != null && fromLng != null) {
        state.routeLine = L.polyline(
          [
            [fromLat, fromLng],
            [em.latitude, em.longitude],
          ],
          { color: "#22c55e", dashArray: "8 8", weight: 3 }
        ).addTo(state.map);
        var km = haversineKm(fromLat, fromLng, em.latitude, em.longitude);
        var eta = km != null ? Math.max(1, Math.round((km / 35) * 60)) : null;
        if (etaEl) {
          etaEl.textContent =
            km != null
              ? km.toFixed(1) +
                " km · ETA ~" +
                eta +
                " min" +
                (em.assigned_ambulance_call_sign ? " · " + em.assigned_ambulance_call_sign : "")
              : "Route ready";
        }
      }
    } else if (etaEl) {
      etaEl.textContent = "Select a case to see route / ETA";
    }

    if (statusEl) {
      if (filter === "all") statusEl.textContent = "All pins (" + shown + ") · scroll to zoom · drag to move";
      else if (!shown) statusEl.textContent = "No pins for this filter — tap All";
      else statusEl.textContent = "Filtered view (" + shown + ") · tap All to reset";
    }

    var shouldFit = forceFit === true || (!state.mapFitted && forceFit !== false);
    if (shouldFit && bounds.length) {
      try {
        if (bounds.length === 1) state.map.setView(bounds[0], 14);
        else state.map.fitBounds(bounds, { padding: [28, 28], maxZoom: 14 });
        state.mapFitted = true;
      } catch (e) {}
    }
    setTimeout(function () {
      if (state.map) state.map.invalidateSize();
    }, 120);
  }

  function locateSelfOnMap() {
    if (!navigator.geolocation) {
      alert("Geolocation is not supported in this browser.");
      return;
    }
    var status = $("hcc-map-status");
    if (status) status.textContent = "Getting GPS…";
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        ensureMap();
        var lat = pos.coords.latitude;
        var lng = pos.coords.longitude;
        if (state.selfMarker) state.map.removeLayer(state.selfMarker);
        state.selfMarker = L.circleMarker([lat, lng], {
          radius: 8,
          color: "#93c5fd",
          weight: 3,
          fillColor: "#2563eb",
          fillOpacity: 1,
        })
          .bindPopup("Your GPS")
          .addTo(state.map);
        state.map.setView([lat, lng], Math.max(state.map.getZoom() || 14, 15));
        if (status) {
          status.textContent =
            "Your GPS: " + lat.toFixed(5) + ", " + lng.toFixed(5) + " (±" + Math.round(pos.coords.accuracy || 0) + " m)";
        }
      },
      function (err) {
        alert((err && err.message) || "Could not get GPS. Allow location permission.");
        if (status) status.textContent = "GPS permission denied";
      },
      { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 }
    );
  }

  /* ---------- KPIs ---------- */
  function renderKpis(avgFromApi) {
    var active = state.emergencies.length;
    var avail = state.ambulances.filter(function (a) {
      return a.status === "available";
    }).length;
    var busy = state.ambulances.filter(function (a) {
      return a.status === "busy";
    }).length;
    var today = new Date().toDateString();
    var done = state.history.filter(function (e) {
      try {
        return new Date(e.timestamp).toDateString() === today && DONE_TODAY[e.status];
      } catch (err) {
        return false;
      }
    }).length;
    if ($("kpi-active")) $("kpi-active").textContent = String(active);
    // high priority filled below
    if ($("kpi-amb-avail")) $("kpi-amb-avail").textContent = String(avail);
    if ($("kpi-amb-busy")) $("kpi-amb-busy").textContent = String(busy);
    if ($("kpi-done")) $("kpi-done").textContent = String(done);
    if ($("kpi-units")) $("kpi-units").textContent = String(avail + busy);
    if ($("kpi-avg")) {
      $("kpi-avg").textContent = avgFromApi != null ? avgFromApi + " min" : "—";
    }
    var high = state.emergencies.filter(function (e) {
      return priorityOf(e) === "high";
    }).length;
    if ($("kpi-active-sub")) {
      $("kpi-active-sub").textContent = high ? high + " high priority" : "In queue";
    }
    if ($("queue-high-pill")) $("queue-high-pill").textContent = high + " High Priority";
    if ($("queue-count")) $("queue-count").textContent = active + " active";
    var qBadge = $("nav-queue-badge");
    if (qBadge) {
      if (active > 0) {
        qBadge.hidden = false;
        qBadge.textContent = String(active);
      } else {
        qBadge.hidden = true;
      }
    }
    if ($("pf-amb-ready")) {
      $("pf-amb-ready").value = avail + " available (from your units)";
    }
  }

  /* ---------- Queue cards ---------- */
  function isCallCenterCase(em) {
    return (em && (em.source === "call_center" || em.request_mode === "call_center"));
  }

  function queueCardHtml(em) {
    var prio = priorityOf(em);
    var prioClass = prio === "high" ? "hcc-prio-high" : prio === "med" ? "hcc-prio-med" : "hcc-prio-low";
    var dist = haversineKm(hospital().latitude, hospital().longitude, em.latitude, em.longitude);
    var st = (em.status || "").replace(/_/g, " ");
    var pending = em.status === "pending_hospital" || em.status === "pending";
    var amb =
      em.assigned_ambulance_call_sign
        ? esc(em.assigned_ambulance_call_sign) +
          (em.assigned_ambulance_driver_phone ? " · " + esc(em.assigned_ambulance_driver_phone) : "")
        : "";
    var actions =
      (pending
        ? '<button type="button" class="hcc-btn hcc-btn-success hcc-btn-sm q-accept" data-id="' +
          em.id +
          '">Accept</button>' +
          '<button type="button" class="hcc-btn hcc-btn-danger hcc-btn-sm q-reject" data-id="' +
          em.id +
          '">Reject</button>'
        : "") +
      (!pending
        ? '<button type="button" class="hcc-btn hcc-btn-primary hcc-btn-sm q-assign" data-id="' +
          em.id +
          '">Assign unit</button>'
        : "") +
      '<button type="button" class="hcc-btn hcc-btn-ghost hcc-btn-sm q-call" data-phone="' +
      esc(em.phone || "") +
      '">Call</button>' +
      '<button type="button" class="hcc-btn hcc-btn-primary hcc-btn-sm q-chat" data-id="' +
      em.id +
      '">Chat</button>' +
      '<button type="button" class="hcc-btn hcc-btn-ghost hcc-btn-sm q-arrived" data-id="' +
      em.id +
      '">Arrived</button>' +
      '<button type="button" class="hcc-btn hcc-btn-ghost hcc-btn-sm q-reached" data-id="' +
      em.id +
      '">Reached</button>';

    var ccBadge = isCallCenterCase(em)
      ? '<span class="hcc-badge hcc-badge-cc">Call Center</span>'
      : "";

    return (
      '<article class="hcc-q-card' +
      (state.selectedId === em.id ? " is-selected" : "") +
      (isCallCenterCase(em) ? " hcc-q-cc" : "") +
      '" data-select="' +
      em.id +
      '">' +
      '<div class="hcc-q-top"><span class="hcc-q-type ' +
      prioClass +
      '">' +
      esc((em.type || "medical").replace(/_/g, " ").toUpperCase()) +
      "</span>" +
      ccBadge +
      '<span class="hcc-badge hcc-badge-active">' +
      esc(st) +
      "</span></div>" +
      '<div class="hcc-q-loc">' +
      esc(em.location || em.district || "Unknown location") +
      "</div>" +
      '<div class="hcc-q-meta">Caller: ' +
      esc(em.caller_name || "Unknown") +
      " · " +
      esc(em.phone || "N/A") +
      "<br>" +
      (dist != null ? dist.toFixed(1) + " km from hospital · " : "") +
      esc(elapsed(em.timestamp) || formatTime(em.timestamp)) +
      (amb ? "<br>Unit: " + amb : "") +
      (isCallCenterCase(em) && em.notes
        ? "<br>CC notes: " + esc(String(em.notes).slice(0, 140))
        : "") +
      '</div><div class="hcc-q-actions">' +
      actions +
      "</div></article>"
    );
  }

  function bindQueueActions(root) {
    if (!root) return;
    root.querySelectorAll("[data-select]").forEach(function (card) {
      card.addEventListener("click", function (e) {
        if (e.target.closest("button")) return;
        state.selectedId = parseInt(card.getAttribute("data-select"), 10);
        renderQueue();
        renderSuggestion();
        renderTimeline();
        renderMap(false);
      });
    });
    root.querySelectorAll(".q-accept").forEach(function (b) {
      b.onclick = function (e) {
        e.stopPropagation();
        openAssignModal(parseInt(b.getAttribute("data-id"), 10), "accept");
      };
    });
    root.querySelectorAll(".q-assign").forEach(function (b) {
      b.onclick = function (e) {
        e.stopPropagation();
        openAssignModal(parseInt(b.getAttribute("data-id"), 10), "assign");
      };
    });
    root.querySelectorAll(".q-reject").forEach(function (b) {
      b.onclick = function (e) {
        e.stopPropagation();
        var id = b.getAttribute("data-id");
        api("/api/hospital/request/" + id + "/reject", { method: "POST", body: {} }).then(loadAll);
      };
    });
    root.querySelectorAll(".q-call").forEach(function (b) {
      b.onclick = function (e) {
        e.stopPropagation();
        var phone = b.getAttribute("data-phone");
        if (phone) window.location.href = "tel:" + phone;
      };
    });
    root.querySelectorAll(".q-chat").forEach(function (b) {
      b.onclick = function (e) {
        e.stopPropagation();
        openCaseChat(parseInt(b.getAttribute("data-id"), 10));
      };
    });
    root.querySelectorAll(".q-arrived").forEach(function (b) {
      b.onclick = function (e) {
        e.stopPropagation();
        api("/api/emergencies/" + b.getAttribute("data-id") + "/responder", {
          method: "POST",
          body: { action: "arrived_at_scene" },
        }).then(loadAll);
      };
    });
    root.querySelectorAll(".q-reached").forEach(function (b) {
      b.onclick = function (e) {
        e.stopPropagation();
        api("/api/emergencies/" + b.getAttribute("data-id") + "/responder", {
          method: "POST",
          body: { action: "reached_victim" },
        }).then(loadAll);
      };
    });
  }

  function renderQueue() {
    var ccList = state.emergencies.filter(isCallCenterCase);
    var otherList = state.emergencies.filter(function (em) {
      return !isCallCenterCase(em);
    });

    var pill = $("cc-inbox-pill");
    if (pill) pill.textContent = String(ccList.length);
    var ccEl = $("cc-inbox-list");
    if (ccEl) {
      ccEl.innerHTML = ccList.length
        ? ccList.map(queueCardHtml).join("")
        : '<div class="hcc-empty">No Call Center cases yet</div>';
      bindQueueActions(ccEl);
    }

    var html =
      otherList.length === 0
        ? '<div class="hcc-empty">No other active emergencies (SOS / walk-in)</div>'
        : otherList.map(queueCardHtml).join("");
    ["queue-list", "queue-list-full"].forEach(function (id) {
      var el = $(id);
      if (!el) return;
      // Full queue panel still shows everything
      if (id === "queue-list-full") {
        el.innerHTML =
          state.emergencies.length === 0
            ? '<div class="hcc-empty">No active emergencies assigned to your hospital</div>'
            : state.emergencies.map(queueCardHtml).join("");
      } else {
        el.innerHTML = html;
      }
      bindQueueActions(el);
    });
    renderChatCaseList();
  }

  /* ---------- Case Chat (WhatsApp-style inbox) ---------- */
  function openCaseChat(eid) {
    state.chatCaseId = eid;
    state.selectedId = eid;
    showPanel("chat");
    loadCaseChat();
  }

  function chatInitials(name) {
    var parts = String(name || "?").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "?";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }

  function chatPersonKey(em) {
    if (!em) return "unknown";
    if (em.user_id != null && em.user_id !== "") return "u:" + em.user_id;
    var phone = String(em.phone || em.caller_phone || "")
      .replace(/\D/g, "");
    if (phone.length >= 7) return "p:" + phone;
    var name = String(em.caller_name || "")
      .trim()
      .toLowerCase()
      .replace(/\s+/g, " ");
    if (name) return "n:" + name;
    return "c:" + em.id;
  }

  function chatUnreadForCase(eid) {
    return (state.notifications || []).filter(function (n) {
      if (n.read) return false;
      var rid = n.request_id || n.emergency_id || n.related_id;
      if (parseInt(rid, 10) !== parseInt(eid, 10)) return false;
      var t = String(n.message || n.title || n.type || "").toLowerCase();
      return t.indexOf("message") !== -1 || t.indexOf("chat") !== -1 || t.indexOf("fariin") !== -1;
    }).length;
  }

  function chatUnreadForPerson(person) {
    return (person.cases || []).reduce(function (sum, em) {
      return sum + chatUnreadForCase(em.id);
    }, 0);
  }

  function findChatCase(eid) {
    var id = parseInt(eid, 10);
    return (
      state.emergencies.filter(function (e) {
        return e.id === id;
      })[0] ||
      (state.history || []).filter(function (e) {
        return e.id === id;
      })[0] ||
      null
    );
  }

  function pickBestCase(cases) {
    if (!cases || !cases.length) return null;
    var active = cases.filter(function (c) {
      return ACTIVE[c.status];
    });
    var pool = active.length ? active : cases.slice();
    pool.sort(function (a, b) {
      var ma = state.chatMeta[a.id] || {};
      var mb = state.chatMeta[b.id] || {};
      var ta = ma.lastAt || a.timestamp || "";
      var tb = mb.lastAt || b.timestamp || "";
      if (ta !== tb) return ta < tb ? 1 : -1;
      return (b.id || 0) - (a.id || 0);
    });
    return pool[0];
  }

  function allChatCasesFlat() {
    var byId = {};
    function add(em) {
      if (!em || em.id == null) return;
      var id = parseInt(em.id, 10);
      if (!id) return;
      var prev = byId[id];
      if (!prev || ACTIVE[em.status]) byId[id] = em;
    }
    state.emergencies.forEach(add);
    (state.history || []).slice(0, 80).forEach(add);
    if (state.chatCaseId && !byId[state.chatCaseId]) {
      add(
        findChatCase(state.chatCaseId) || {
          id: state.chatCaseId,
          caller_name: "Case #" + state.chatCaseId,
          status: "open",
          type: "medical",
        }
      );
    }
    (state.notifications || []).forEach(function (n) {
      var rid = parseInt(n.request_id || n.emergency_id || n.related_id, 10);
      if (!rid || byId[rid]) return;
      add({
        id: rid,
        caller_name: "Case #" + rid,
        status: "message",
        type: "medical",
        location: String(n.message || "").slice(0, 80),
      });
    });
    return Object.keys(byId).map(function (k) {
      return byId[k];
    });
  }

  function chatInboxPeople() {
    var groups = {};
    allChatCasesFlat().forEach(function (em) {
      var key = chatPersonKey(em);
      if (!groups[key]) {
        groups[key] = {
          key: key,
          name: em.caller_name || em.phone || "Caller",
          phone: em.phone || "",
          user_id: em.user_id || null,
          cases: [],
        };
      }
      groups[key].cases.push(em);
      if (em.caller_name) groups[key].name = em.caller_name;
      if (em.phone) groups[key].phone = em.phone;
      if (em.user_id != null) groups[key].user_id = em.user_id;
    });
    return Object.keys(groups)
      .map(function (k) {
        var g = groups[k];
        g.cases.sort(function (a, b) {
          return (b.id || 0) - (a.id || 0);
        });
        g.primary = pickBestCase(g.cases);
        g.hasActive = g.cases.some(function (c) {
          return ACTIVE[c.status];
        });
        g.unread = chatUnreadForPerson(g);
        var meta = g.primary ? state.chatMeta[g.primary.id] || {} : {};
        g.sortAt = meta.lastAt || (g.primary && g.primary.timestamp) || "";
        return g;
      })
      .sort(function (a, b) {
        if (a.sortAt !== b.sortAt) return a.sortAt < b.sortAt ? 1 : -1;
        return (b.primary && b.primary.id) - (a.primary && a.primary.id);
      });
  }

  function filteredChatInbox() {
    var q = String(state.chatSearch || "")
      .trim()
      .toLowerCase();
    return chatInboxPeople().filter(function (person) {
      if (state.chatFilter === "unread" && !person.unread) return false;
      if (state.chatFilter === "active" && !person.hasActive) return false;
      if (!q) return true;
      var caseBits = person.cases
        .map(function (c) {
          return [c.id, c.location, c.district, c.status, c.type].join(" ");
        })
        .join(" ");
      var hay = [person.name, person.phone, caseBits].join(" ").toLowerCase();
      return hay.indexOf(q) !== -1;
    });
  }

  function rememberChatPreview(eid, msgs) {
    var list = msgs || [];
    var last = list.length ? list[list.length - 1] : null;
    state.chatMeta[eid] = {
      lastText: last ? last.text || "" : "",
      lastAt: last ? last.timestamp || "" : "",
      lastRole: last ? last.sender_role || "" : "",
      count: list.length,
    };
  }

  function personOwnsCase(person, eid) {
    return (person.cases || []).some(function (c) {
      return c.id === eid;
    });
  }

  function chatRowHtml(person) {
    var em = person.primary;
    if (!em) return "";
    var name = person.name || "Caller";
    var meta = state.chatMeta[em.id] || {};
    var unread = person.unread;
    var preview = meta.lastText
      ? (meta.lastRole === "hospital" || meta.lastRole === "admin" ? "You: " : "") + meta.lastText
      : em.location || em.district || (em.type || "medical").replace(/_/g, " ") || "No messages yet";
    var when = formatTime(meta.lastAt || em.timestamp) || "";
    var caseCount = person.cases.length;
    var selected = state.chatCaseId && personOwnsCase(person, state.chatCaseId);
    return (
      '<button type="button" class="hcc-wa-row' +
      (selected ? " is-active" : "") +
      (unread ? " has-unread" : "") +
      '" data-chat-case="' +
      em.id +
      '" data-person-key="' +
      esc(person.key) +
      '" role="listitem">' +
      '<div class="hcc-wa-avatar">' +
      esc(chatInitials(name)) +
      "</div>" +
      '<div class="hcc-wa-row-main">' +
      '<div class="hcc-wa-row-top"><strong>' +
      esc(name) +
      '</strong><time>' +
      esc(when) +
      "</time></div>" +
      '<div class="hcc-wa-row-bottom"><span class="hcc-wa-preview">' +
      esc(String(preview).slice(0, 72)) +
      '</span>' +
      (unread
        ? '<span class="hcc-wa-unread">' + Math.min(unread, 99) + "</span>"
        : person.hasActive
          ? '<span class="hcc-wa-dot" title="Active"></span>'
          : "") +
      "</div>" +
      '<div class="hcc-wa-row-meta">Latest #' +
      em.id +
      " · " +
      esc(String(em.status || "open").replace(/_/g, " ")) +
      (caseCount > 1 ? " · " + caseCount + " cases" : "") +
      "</div></div></button>"
    );
  }

  function renderChatCaseSwitch(person) {
    var wrap = $("hcc-chat-case-switch-wrap");
    var sel = $("hcc-chat-case-switch");
    if (!wrap || !sel) return;
    if (!person || !person.cases || person.cases.length < 2) {
      wrap.hidden = true;
      sel.innerHTML = "";
      return;
    }
    wrap.hidden = false;
    sel.innerHTML = person.cases
      .map(function (c) {
        return (
          '<option value="' +
          c.id +
          '"' +
          (c.id === state.chatCaseId ? " selected" : "") +
          ">#" +
          c.id +
          " · " +
          esc(String(c.status || "").replace(/_/g, " ") || "case") +
          (c.timestamp ? " · " + esc(formatTime(c.timestamp) || "") : "") +
          "</option>"
        );
      })
      .join("");
  }

  function findPersonByCase(eid) {
    var id = parseInt(eid, 10);
    return (
      chatInboxPeople().filter(function (p) {
        return personOwnsCase(p, id);
      })[0] || null
    );
  }

  function renderChatCaseList() {
    var list = $("hcc-chat-case-list");
    var count = $("hcc-chat-case-count");
    var people = filteredChatInbox();
    if (count) count.textContent = String(chatInboxPeople().length);
    if (!list) return;
    if (!people.length) {
      list.innerHTML =
        '<div class="hcc-chat-empty"><strong>Ma jiraan chats</strong><p>Qof kasta wuxuu liiska ku yimaadaa hal jeer — sida WhatsApp. Marka emergency cusub sameeyo, case-kiisa ayaa ku xirmaa isla chat-kiisa.</p></div>';
      return;
    }
    list.innerHTML = people.map(chatRowHtml).join("");
    list.querySelectorAll("[data-chat-case]").forEach(function (btn) {
      btn.onclick = function () {
        state.chatCaseId = parseInt(btn.getAttribute("data-chat-case"), 10);
        state.selectedId = state.chatCaseId;
        renderChatCaseList();
        loadCaseChat();
      };
    });
  }

  function chatBubbleHtml(m, showCase) {
    var mine = m.sender_role === "hospital" || m.sender_role === "admin" || m.sender_role === "super_admin";
    var who = mine ? "Hospital" : m.sender_role === "citizen" ? "Citizen" : esc(m.sender_role || "User");
    var tick = m.status === "seen" ? " ✓✓" : m.status === "delivered" ? " ✓✓" : m.status === "sent" ? " ✓" : "";
    var caseTag =
      showCase && m._caseId
        ? '<span class="hcc-chat-case-tag">#' + m._caseId + "</span>"
        : "";
    return (
      '<div class="hcc-chat-bubble ' +
      (mine ? "me" : "them") +
      '"><span class="hcc-chat-who">' +
      who +
      caseTag +
      "</span>" +
      esc(m.text || "") +
      "<time>" +
      esc(formatTime(m.timestamp) || m.timestamp || "") +
      tick +
      "</time></div>"
    );
  }

  function chatCaseDivider(em) {
    return (
      '<div class="hcc-chat-day">Case #' +
      em.id +
      " · " +
      esc(String(em.status || "").replace(/_/g, " ") || "case") +
      (em.timestamp ? " · " + esc(formatTime(em.timestamp) || "") : "") +
      "</div>"
    );
  }

  function loadMergedPersonMessages(person, box) {
    var cases = (person.cases || []).slice(0, 8);
    if (!cases.length) {
      box.innerHTML =
        '<div class="hcc-chat-empty"><strong>Bilow wadahadal</strong><p>Fariin u dir citizen-ka — wuu arki doonaa dashboard-kiisa.</p></div>';
      return Promise.resolve();
    }
    return Promise.all(
      cases.map(function (c) {
        return api("/api/messages/" + c.id).then(function (data) {
          var msgs = (data && data.messages) || [];
          rememberChatPreview(c.id, msgs);
          return msgs.map(function (m) {
            var copy = Object.assign({}, m);
            copy._caseId = c.id;
            return copy;
          });
        });
      })
    ).then(function (chunks) {
      var byCase = {};
      cases.forEach(function (c, i) {
        byCase[c.id] = chunks[i] || [];
      });
      var chronological = cases.slice().sort(function (a, b) {
        return String(a.timestamp || "") < String(b.timestamp || "") ? -1 : 1;
      });
      var html = [];
      var multi = cases.length > 1;
      chronological.forEach(function (c) {
        var msgs = byCase[c.id] || [];
        if (multi) html.push(chatCaseDivider(c));
        if (!msgs.length && multi) {
          html.push('<div class="hcc-chat-empty-mini">No messages on this case</div>');
        } else {
          msgs.forEach(function (m) {
            html.push(chatBubbleHtml(m, multi));
          });
        }
      });
      if (!html.length) {
        box.innerHTML =
          '<div class="hcc-chat-empty"><strong>Bilow wadahadal</strong><p>Accept ka dib halkan ayaad isku la hadashaan — 1 qof = 1 chat.</p></div>';
      } else {
        box.innerHTML = html.join("");
        box.scrollTop = box.scrollHeight;
      }
      renderChatCaseList();
      updateChatBadge();
    });
  }

  function loadCaseChat() {
    var box = $("hcc-chat-messages");
    var form = $("hcc-chat-form");
    var quick = $("hcc-chat-quick");
    var refresh = $("hcc-chat-refresh");
    var title = $("hcc-chat-title");
    var sub = $("hcc-chat-sub");
    var avatar = $("hcc-chat-avatar");
    var switchWrap = $("hcc-chat-case-switch-wrap");
    if (!state.chatCaseId) {
      if (box) {
        box.innerHTML =
          '<div class="hcc-chat-empty"><strong>Dooro chat</strong><p>Liiska bidix — qof dooro (hal qof = hal chat).</p></div>';
      }
      if (form) form.hidden = true;
      if (quick) quick.hidden = true;
      if (refresh) refresh.hidden = true;
      if (switchWrap) switchWrap.hidden = true;
      if (title) title.textContent = "Case Chat";
      if (sub) sub.textContent = "Dooro qof liiska ka mid ah · Select a person";
      if (avatar) avatar.textContent = "?";
      return;
    }
    var em = findChatCase(state.chatCaseId);
    var person = findPersonByCase(state.chatCaseId);
    var name = (person && person.name) || (em && (em.caller_name || em.phone)) || "Case #" + state.chatCaseId;
    if (title) title.textContent = name;
    if (sub) {
      var caseLabel = em
        ? "Writing on #" +
          state.chatCaseId +
          " · " +
          (em.location || em.district || "Citizen") +
          " · " +
          String(em.status || "").replace(/_/g, " ")
        : "Writing on #" + state.chatCaseId;
      if (person && person.cases.length > 1) {
        caseLabel += " · " + person.cases.length + " cases · 1 person";
      }
      sub.textContent = caseLabel;
    }
    if (avatar) avatar.textContent = chatInitials(name);
    renderChatCaseSwitch(person);
    if (form) form.hidden = false;
    if (quick) quick.hidden = false;
    if (refresh) refresh.hidden = false;
    if (box) box.innerHTML = '<div class="hcc-muted" style="margin:auto;padding:20px;">Loading messages…</div>';

    var loader = person
      ? loadMergedPersonMessages(person, box)
      : api("/api/messages/" + state.chatCaseId).then(function (data) {
          if (!box) return;
          if (data && data.success === false) {
            box.innerHTML =
              '<div class="hcc-chat-empty"><strong>Chat lama furin</strong><p>' +
              esc(data.message || "Forbidden or case not assigned to this hospital.") +
              "</p></div>";
            return;
          }
          var msgs = (data && data.messages) || [];
          rememberChatPreview(state.chatCaseId, msgs);
          renderChatCaseList();
          updateChatBadge();
          if (!msgs.length) {
            box.innerHTML =
              '<div class="hcc-chat-empty"><strong>Bilow wadahadal</strong><p>Fariin u dir citizen-ka — wuu arki doonaa dashboard-kiisa.</p></div>';
          } else {
            box.innerHTML = msgs.map(function (m) {
              return chatBubbleHtml(m, false);
            }).join("");
            box.scrollTop = box.scrollHeight;
          }
        });

    return loader;
  }

  function afterAcceptOpenChat(eid) {
    var welcome =
      "Hospital-ku waa aqbalay codsigaaga. Halkan nala soo hadal haddii aad u baahan tahay caawimaad. / We accepted your request — message us here.";
    return api("/api/messages/" + eid, { method: "POST", body: { text: welcome } })
      .catch(function () {
        return null;
      })
      .then(function () {
        return loadAll();
      })
      .then(function () {
        openCaseChat(eid);
      });
  }

  function updateChatBadge() {
    var badge = $("nav-chat-badge");
    if (!badge) return;
    var unread = (state.notifications || []).filter(function (n) {
      if (n.read) return false;
      var t = String(n.message || n.title || n.type || "").toLowerCase();
      return t.indexOf("message") !== -1 || t.indexOf("chat") !== -1 || t.indexOf("fariin") !== -1;
    }).length;
    if (unread > 0) {
      badge.hidden = false;
      badge.textContent = String(Math.min(unread, 99));
    } else {
      badge.hidden = true;
    }
  }

  function sendCaseChat(textOverride) {
    var input = $("hcc-chat-input");
    var text = (textOverride || (input && input.value) || "").trim();
    if (!text || !state.chatCaseId) return;
    var sendBtn = $("hcc-chat-send");
    if (sendBtn) sendBtn.disabled = true;
    api("/api/messages/" + state.chatCaseId, { method: "POST", body: { text: text } })
      .then(function (r) {
        if (r && r.success === false) {
          alert(r.message || "Could not send message");
          return;
        }
        if (input) input.value = "";
        loadCaseChat();
      })
      .finally(function () {
        if (sendBtn) sendBtn.disabled = false;
      });
  }

  function startChatPoll() {
    stopChatPoll();
    state.chatPoll = setInterval(function () {
      if (state.chatCaseId && $("panel-chat") && !$("panel-chat").classList.contains("hidden-panel")) {
        loadCaseChat();
      }
    }, 8000);
  }

  function stopChatPoll() {
    if (state.chatPoll) {
      clearInterval(state.chatPoll);
      state.chatPoll = null;
    }
  }

  function renderHistory() {
    var el = $("history-container");
    if (!el) return;
    if (!state.history.length) {
      el.innerHTML = '<div class="hcc-empty">No history yet</div>';
      return;
    }
    el.innerHTML = state.history
      .slice(0, 40)
      .map(function (em) {
        return (
          '<article class="hcc-q-card"><div class="hcc-q-top"><span class="hcc-q-type">' +
          esc((em.type || "medical").toUpperCase()) +
          '</span><span class="hcc-badge hcc-badge-ok">' +
          esc((em.status || "").replace(/_/g, " ")) +
          '</span></div><div class="hcc-q-loc">' +
          esc(em.location || "") +
          '</div><div class="hcc-q-meta">' +
          esc(formatTime(em.timestamp)) +
          (em.assigned_ambulance_call_sign ? " · " + esc(em.assigned_ambulance_call_sign) : "") +
          "</div></article>"
        );
      })
      .join("");
  }

  /* ---------- Ambulance strip ---------- */
  function ambStatusTone(a, motion) {
    var st = (motion && motion.phase) || a.status || "offline";
    if (st === "available") return "ok";
    if (st === "maintenance" || st === "offline") return "maint";
    return "busy";
  }

  function ambAssignedCase(aid) {
    return (
      state.emergencies.filter(function (e) {
        return e.assigned_ambulance_id === aid;
      })[0] || null
    );
  }

  function ambFleetAvatarHtml(a) {
    var name = a.driver_name || a.call_sign || "?";
    var initials = String(name)
      .trim()
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map(function (p) {
        return p.charAt(0).toUpperCase();
      })
      .join("") || "?";
    if (a.driver_photo_url) {
      var bust = a.updated_at ? ("?v=" + encodeURIComponent(String(a.updated_at))) : "";
      return (
        '<div class="hcc-fleet-avatar has-photo" style="background-image:url(\'' +
        String(a.driver_photo_url).replace(/'/g, "%27") +
        bust +
        "')\" title=\"" +
        esc(name) +
        '"></div>'
      );
    }
    return '<div class="hcc-fleet-avatar" aria-hidden="true">' + esc(initials) + "</div>";
  }

  function ambFleetPhotoHtml(a) {
    if (a.vehicle_photo_url) {
      var bust = a.updated_at ? ("?v=" + encodeURIComponent(String(a.updated_at))) : "";
      return (
        '<div class="hcc-fleet-photo has-vehicle-img" style="background-image:url(\'' +
        String(a.vehicle_photo_url).replace(/'/g, "%27") +
        bust +
        "')\" role=\"img\" aria-label=\"" +
        esc(a.call_sign || "Ambulance") +
        '"></div>'
      );
    }
    return (
      '<div class="hcc-fleet-photo has-vehicle" aria-hidden="true">' +
      '<svg viewBox="0 0 120 72" xmlns="http://www.w3.org/2000/svg">' +
      '<rect x="8" y="28" width="88" height="28" rx="6" fill="#f8fafc"/>' +
      '<path d="M28 28h36l12 14H28z" fill="#e2e8f0"/>' +
      '<rect x="70" y="18" width="18" height="18" rx="3" fill="#ef4444"/>' +
      '<path d="M76 21v12M70 27h12" stroke="#fff" stroke-width="3" stroke-linecap="round"/>' +
      '<circle cx="30" cy="56" r="8" fill="#0f172a" stroke="#94a3b8" stroke-width="2"/>' +
      '<circle cx="78" cy="56" r="8" fill="#0f172a" stroke="#94a3b8" stroke-width="2"/>' +
      '<rect x="96" y="34" width="14" height="16" rx="3" fill="#cbd5e1"/>' +
      "</svg></div>"
    );
  }

  function renderAmbStrip() {
    var el = $("amb-strip");
    if (!el) return;
    if (!state.ambulances.length) {
      el.innerHTML =
        '<div class="hcc-empty">No units yet — add ambulances under Ambulances tab</div>';
      return;
    }
    el.innerHTML = state.ambulances
      .map(function (a) {
        var liveOn = state.liveGps.aid === a.id;
        var motion = state.motionPhaseByAid[a.id];
        var tone = ambStatusTone(a, motion);
        var statusLabel = motion && motion.label ? motion.label : String(a.status || "offline").replace(/_/g, " ");
        var em = ambAssignedCase(a.id);
        var loc =
          (em && (em.location || em.district)) ||
          (a.latitude != null
            ? Number(a.latitude).toFixed(4) + ", " + Number(a.longitude).toFixed(4)
            : "No GPS yet");
        var canDispatch =
          a.status === "available" && !(motion && motion.phase && motion.phase !== "available");
        var primaryBtn = canDispatch
          ? '<button type="button" class="hcc-fleet-primary hcc-fleet-primary-ok amb-dispatch" data-id="' +
            a.id +
            '">Dispatch</button>'
          : a.status === "offline" || a.status === "maintenance"
            ? '<button type="button" class="hcc-fleet-primary hcc-fleet-primary-maint amb-driver-link" data-id="' +
              a.id +
              '">Details</button>'
            : '<button type="button" class="hcc-fleet-primary hcc-fleet-primary-busy amb-track" data-id="' +
              a.id +
              '">Track</button>';
        var secondary =
          '<div class="hcc-fleet-secondary">' +
          (liveOn
            ? '<button type="button" class="hcc-btn hcc-btn-amber hcc-btn-sm amb-gps-stop" data-id="' +
              a.id +
              '">Stop GPS</button>'
            : '<button type="button" class="hcc-btn hcc-btn-ghost hcc-btn-sm amb-gps" data-id="' +
              a.id +
              '">Live GPS</button>') +
          '<button type="button" class="hcc-btn hcc-btn-ghost hcc-btn-sm amb-driver-link" data-id="' +
          a.id +
          '">Driver link</button></div>';

        return (
          '<article class="hcc-amb-card hcc-fleet-card tone-' +
          tone +
          (liveOn ? " is-live-gps" : "") +
          (motion && motion.phase && motion.phase !== "available" ? " is-in-motion" : "") +
          '">' +
          '<div class="hcc-fleet-head">' +
          ambFleetAvatarHtml(a) +
          "<div><strong>" +
          esc(a.call_sign || "Unit") +
          '</strong><em class="hcc-fleet-status">' +
          esc(statusLabel) +
          (liveOn ? " · LIVE" : "") +
          "</em></div></div>" +
          ambFleetPhotoHtml(a) +
          '<div class="hcc-fleet-driver">' +
          "<span>Driver</span><strong>" +
          esc(a.driver_name || "No driver name") +
          "</strong>" +
          (a.driver_phone ? "<small>" + esc(a.driver_phone) + "</small>" : "") +
          "</div>" +
          '<div class="hcc-fleet-meta">' +
          "<div><span>Location</span><strong>" +
          esc(loc) +
          "</strong></div>" +
          (motion && motion.etaMin != null
            ? "<div><span>ETA</span><strong>~" + motion.etaMin + " min</strong></div>"
            : em
              ? "<div><span>Case</span><strong>#" + em.id + "</strong></div>"
              : "<div><span>Phone</span><strong>" + esc(a.driver_phone || "—") + "</strong></div>") +
          "</div>" +
          primaryBtn +
          secondary +
          "</article>"
        );
      })
      .join("");

    el.querySelectorAll(".amb-dispatch").forEach(function (b) {
      b.onclick = function () {
        var aid = parseInt(b.getAttribute("data-id"), 10);
        var em = selectedEmergency();
        if (!em) {
          alert("Select an emergency in the queue first");
          return;
        }
        api("/api/hospital/request/" + em.id + "/assign-ambulance", {
          method: "POST",
          body: { ambulance_unit_id: aid },
        }).then(function (r) {
          if (r && r.success === false) {
            alert(r.message || "Dispatch failed");
            return;
          }
          loadAll();
        });
      };
    });
    el.querySelectorAll(".amb-track").forEach(function (b) {
      b.onclick = function () {
        var aid = parseInt(b.getAttribute("data-id"), 10);
        var unit = state.ambulances.filter(function (a) {
          return a.id === aid;
        })[0];
        var em = state.emergencies.filter(function (e) {
          return e.assigned_ambulance_id === aid;
        })[0];
        if (em) state.selectedId = em.id;
        renderQueue();
        renderSuggestion();
        renderTimeline();
        renderMap(false);
        if (unit && unit.latitude != null && state.map) {
          state.map.setView([unit.latitude, unit.longitude], 15);
        }
      };
    });
    el.querySelectorAll(".amb-gps").forEach(function (b) {
      b.onclick = function () {
        startLiveGps(parseInt(b.getAttribute("data-id"), 10));
      };
    });
    el.querySelectorAll(".amb-gps-stop").forEach(function (b) {
      b.onclick = function () {
        stopLiveGps();
        renderAmbStrip();
      };
    });
    el.querySelectorAll(".amb-driver-link").forEach(function (b) {
      b.onclick = function () {
        var aid = parseInt(b.getAttribute("data-id"), 10);
        if (window.HospitalApp && typeof HospitalApp.openDriverGpsLink === "function") {
          HospitalApp.openDriverGpsLink(aid);
        } else {
          showPanel("ambulances");
        }
      };
    });
  }

  function stopLiveGps() {
    if (state.liveGps.stop) {
      try {
        state.liveGps.stop();
      } catch (e) {}
    }
    state.liveGps.aid = null;
    state.liveGps.stop = null;
    state.liveGps.lastPush = 0;
    state.liveGps.firstFix = false;
    if ($("hcc-map-status")) {
      $("hcc-map-status").textContent = "Live GPS stopped";
    }
  }

  function applyLiveAmbulanceFix(aid, lat, lng, follow) {
    var unit = state.ambulances.filter(function (a) {
      return a.id === aid;
    })[0];
    if (unit) {
      unit.latitude = lat;
      unit.longitude = lng;
    }
    state.emergencies.forEach(function (em) {
      if (em.assigned_ambulance_id === aid) {
        em.assigned_ambulance_latitude = lat;
        em.assigned_ambulance_longitude = lng;
        em.responder_latitude = lat;
        em.responder_longitude = lng;
      }
    });
    renderAmbStrip();
    renderMap(false);
    // Only recenter when follow=true (Track / first Live GPS fix) — not every tick
    if (follow && state.map) {
      try {
        state.map.setView([lat, lng], Math.max(state.map.getZoom() || 14, 14));
      } catch (e) {}
    }
  }

  function startLiveGps(aid) {
    if (!navigator.geolocation) {
      alert("GPS not available on this device");
      return;
    }
    if (state.liveGps.aid === aid && state.liveGps.stop) {
      return;
    }
    stopLiveGps();
    state.liveGps.aid = aid;
    state.liveGps.firstFix = true;
    if (state.ambMotion) state.ambMotion.clearMission(aid);
    if ($("hcc-map-status")) $("hcc-map-status").textContent = "Starting live GPS…";

    function pushFix(lat, lng) {
      if (state.liveGps.aid !== aid) return;
      var now = Date.now();
      var follow = !!state.liveGps.firstFix;
      if (follow) state.liveGps.firstFix = false;
      // Always refresh local map; throttle server writes
      applyLiveAmbulanceFix(aid, lat, lng, follow);
      if (now - state.liveGps.lastPush < 2000) return;
      state.liveGps.lastPush = now;
      api("/api/hospital/ambulances/" + aid + "/location", {
        method: "POST",
        body: { latitude: lat, longitude: lng },
      }).then(function (r) {
        if (r && r.success === false) {
          if ($("hcc-map-status")) $("hcc-map-status").textContent = r.message || "GPS push failed";
          return;
        }
        if ($("hcc-map-status")) {
          $("hcc-map-status").textContent =
            "Live GPS · " + Number(lat).toFixed(5) + ", " + Number(lng).toFixed(5);
        }
      });
    }

    navigator.geolocation.getCurrentPosition(
      function (pos) {
        pushFix(pos.coords.latitude, pos.coords.longitude);
      },
      function (err) {
        alert((err && err.message) || "Allow location permission for live GPS.");
        stopLiveGps();
        renderAmbStrip();
      },
      { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 }
    );

    var watchId = navigator.geolocation.watchPosition(
      function (pos) {
        if (state.liveGps.aid !== aid) return;
        pushFix(pos.coords.latitude, pos.coords.longitude);
      },
      function () {},
      { enableHighAccuracy: true, maximumAge: 2000, timeout: 25000 }
    );
    state.liveGps.stop = function () {
      try {
        navigator.geolocation.clearWatch(watchId);
      } catch (e) {}
    };
    renderAmbStrip();
  }

  /* ---------- Notifications ---------- */
  function renderNotifs() {
    var unread = state.notifications.filter(function (n) {
      return !n.read;
    }).length;
    if ($("notif-count")) $("notif-count").textContent = String(unread || state.notifications.length || 0);
    var nBadge = $("nav-notif-badge");
    if (nBadge) {
      if (unread > 0) {
        nBadge.hidden = false;
        nBadge.textContent = String(unread);
      } else {
        nBadge.hidden = true;
      }
    }
    updateChatBadge();
    var html =
      state.notifications.length === 0
        ? '<div class="hcc-empty">No notifications</div>'
        : state.notifications
            .slice(0, 30)
            .map(function (n) {
              return (
                '<div class="hcc-notif-item' +
                (!n.read ? " unread" : "") +
                '">' +
                esc(n.message || n.title || "Update") +
                "<time>" +
                esc(formatTime(n.created_at || n.timestamp)) +
                "</time></div>"
              );
            })
            .join("");
    ["notif-rail", "notif-full"].forEach(function (id) {
      var el = $(id);
      if (el) el.innerHTML = html;
    });
  }

  /* ---------- Suggestion + Timeline ---------- */
  function renderSuggestion() {
    var el = $("suggest-body");
    if (!el) return;
    var em = selectedEmergency();
    if (!em) {
      el.innerHTML = '<p class="hcc-muted">Select an emergency to get the nearest available ambulance.</p>';
      return;
    }
    var best = nearestUnitFor(em);
    if (!best) {
      el.innerHTML = '<p class="hcc-muted">No available ambulances. Add or free a unit first.</p>';
      return;
    }
    var eta = Math.max(1, Math.round((best.km / 35) * 60));
    el.innerHTML =
      "<p>Closest available unit for <strong>#" +
      em.id +
      "</strong></p>" +
      '<div class="hcc-suggest-unit"><strong>' +
      esc(best.unit.call_sign) +
      "</strong><br>" +
      esc(best.unit.driver_name || "Driver") +
      " · " +
      esc(best.unit.driver_phone || "—") +
      "</div>" +
      '<div class="hcc-suggest-stats">' +
      "<div><span>ETA</span><strong>~" +
      eta +
      " min</strong></div>" +
      "<div><span>Distance</span><strong>" +
      best.km.toFixed(1) +
      " km</strong></div>" +
      "<div><span>Status</span><strong>Available</strong></div>" +
      "</div>" +
      "<p class=\"hcc-muted\">Closest available ambulance by distance from the emergency scene.</p>" +
      '<div class="hcc-suggest-actions">' +
      '<button type="button" class="hcc-btn hcc-btn-primary" id="btn-accept-suggest">Accept Recommendation</button>' +
      '<button type="button" class="hcc-btn hcc-btn-ghost" id="btn-choose-manual">Choose Manually</button>' +
      "</div>";
    var btn = $("btn-accept-suggest");
    if (btn) {
      btn.onclick = function () {
        var path =
          em.status === "pending_hospital" || em.status === "pending"
            ? "/api/hospital/request/" + em.id + "/accept"
            : "/api/hospital/request/" + em.id + "/assign-ambulance";
        api(path, { method: "POST", body: { ambulance_unit_id: best.unit.id } }).then(function (r) {
          if (r && r.success === false) {
            alert(r.message || "Failed");
            return;
          }
          if (em.status === "pending_hospital" || em.status === "pending") afterAcceptOpenChat(em.id);
          else loadAll();
        });
      };
    }
    var manual = $("btn-choose-manual");
    if (manual) {
      manual.onclick = function () {
        var mode =
          em.status === "pending_hospital" || em.status === "pending" ? "accept" : "assign";
        openAssignModal(em.id, mode);
      };
    }
  }

  function renderTimeline() {
    var el = $("timeline-list");
    if (!el) return;
    var em = selectedEmergency();
    if (!em) {
      el.innerHTML = '<li class="hcc-muted">No case selected</li>';
      return;
    }
    var items = [];
    items.push({ label: "Emergency received", ts: em.timestamp });
    (em.status_history || []).forEach(function (h) {
      items.push({
        label: (h.status || h.note || "Update").toString().replace(/_/g, " "),
        ts: h.timestamp,
      });
    });
    var rs = em.responder_status || {};
    Object.keys(rs).forEach(function (k) {
      items.push({ label: k.replace(/_/g, " "), ts: rs[k] });
    });
    if (em.assigned_ambulance_call_sign) {
      items.push({
        label: "Ambulance " + em.assigned_ambulance_call_sign + " assigned",
        ts: em.accepted_at || em.timestamp,
      });
    }
    items.sort(function (a, b) {
      return String(a.ts || "").localeCompare(String(b.ts || ""));
    });
    el.innerHTML = items
      .map(function (it) {
        return "<li>" + esc(it.label) + "<time>" + esc(formatTime(it.ts)) + "</time></li>";
      })
      .join("");
  }

  /* ---------- Modal assign ---------- */
  function openModal(title, bodyHtml, onSave) {
    var modal = $("hcc-modal");
    if (!modal) return;
    $("hcc-modal-title").textContent = title;
    $("hcc-modal-body").innerHTML = bodyHtml;
    modal.classList.remove("hidden");
    state.modalAction = onSave;
  }

  function closeModal() {
    var modal = $("hcc-modal");
    if (modal) modal.classList.add("hidden");
    state.modalAction = null;
  }

  function openAssignModal(eid, mode) {
    state.selectedId = eid;
    var em =
      state.emergencies.filter(function (e) {
        return e.id === eid;
      })[0] || null;
    var units = availableUnits();
    var best = nearestUnitFor(em);
    if (!units.length) {
      alert("No available ambulances. Add a unit or free a busy one first.");
      return;
    }
    var html =
      '<p class="hcc-muted">Case #' +
      eid +
      " — pick a unit for dispatch</p><div class=\"hcc-unit-pick\">" +
      units
        .map(function (u) {
          var isBest = best && best.unit.id === u.id;
          var d =
            em &&
            haversineKm(
              u.latitude != null ? u.latitude : hospital().latitude,
              u.longitude != null ? u.longitude : hospital().longitude,
              em.latitude,
              em.longitude
            );
          return (
            '<label class="' +
            (isBest ? "is-best" : "") +
            '"><input type="radio" name="pick-amb" value="' +
            u.id +
            '"' +
            (isBest ? " checked" : "") +
            "> <span><strong>" +
            esc(u.call_sign) +
            "</strong>" +
            (isBest ? " · recommended" : "") +
            "<br>" +
            esc(u.driver_name || "") +
            " · " +
            esc(u.driver_phone || "") +
            (d != null ? "<br>" + d.toFixed(1) + " km" : "") +
            "</span></label>"
          );
        })
        .join("") +
      "</div>";
    openModal(mode === "accept" ? "Accept + assign ambulance" : "Assign ambulance", html, function () {
      var picked = document.querySelector('input[name="pick-amb"]:checked');
      if (!picked) {
        alert("Select an ambulance");
        return;
      }
      var aid = parseInt(picked.value, 10);
      var url =
        mode === "accept"
          ? "/api/hospital/request/" + eid + "/accept"
          : "/api/hospital/request/" + eid + "/assign-ambulance";
      api(url, { method: "POST", body: { ambulance_unit_id: aid } }).then(function (r) {
        if (r && r.success === false) {
          alert(r.message || "Failed");
          return;
        }
        closeModal();
        if (mode === "accept") afterAcceptOpenChat(eid);
        else loadAll();
      });
    });
  }

  /* ---------- Data load ---------- */
  function loadAll() {
    if ($("refresh-time")) $("refresh-time").textContent = "Updating…";
    return Promise.all([
      api("/api/get_emergencies?type=medical"),
      api("/api/get_emergencies?type=medical&status=completed,resolved,cancelled"),
      api("/api/hospital/ambulances"),
      api("/api/notifications"),
    ]).then(function (results) {
      var live = results[0] || {};
      var hist = results[1] || {};
      var amb = results[2] || {};
      var notes = results[3] || {};
      if (live.refresh_interval) state.refreshMs = live.refresh_interval * 1000;
      state.emergencies = (live.emergencies || []).filter(function (e) {
        return ACTIVE[e.status];
      });
      state.history = hist.emergencies || [];
      // Snapshot live GPS before poll replaces ambulances (avoids pin jumping back)
      var liveAid = state.liveGps.aid;
      var liveSnap = null;
      if (liveAid) {
        var prev = state.ambulances.filter(function (a) {
          return a.id === liveAid;
        })[0];
        if (prev && prev.latitude != null && prev.longitude != null) {
          liveSnap = { lat: prev.latitude, lng: prev.longitude };
        }
      }
      state.ambulances = amb.ambulances || [];
      if (liveAid && liveSnap) {
        state.ambulances.forEach(function (a) {
          if (a.id === liveAid) {
            a.latitude = liveSnap.lat;
            a.longitude = liveSnap.lng;
          }
        });
      }
      state.notifications = notes.notifications || notes || [];
      if (!Array.isArray(state.notifications)) state.notifications = [];
      if (
        state.selectedId &&
        !state.emergencies.some(function (e) {
          return e.id === state.selectedId;
        })
      ) {
        state.selectedId = state.emergencies[0] ? state.emergencies[0].id : null;
      }
      if (!state.selectedId && state.emergencies[0]) state.selectedId = state.emergencies[0].id;

      if (liveAid && liveSnap) {
        state.emergencies.forEach(function (em) {
          if (em.assigned_ambulance_id === liveAid) {
            em.assigned_ambulance_latitude = liveSnap.lat;
            em.assigned_ambulance_longitude = liveSnap.lng;
            em.responder_latitude = liveSnap.lat;
            em.responder_longitude = liveSnap.lng;
          }
        });
      }

      renderKpis(live.avg_response_time);
      renderQueue();
      renderHistory();
      renderAmbStrip();
      renderNotifs();
      renderSuggestion();
      renderTimeline();
      renderMap(!state.mapFitted);
      syncAmbMotion();
      if ($("refresh-time")) {
        $("refresh-time").textContent = "Live · " + new Date().toLocaleTimeString();
      }
      if (window.HospitalApp && typeof HospitalApp.refreshAmbulancesTable === "function") {
        HospitalApp.refreshAmbulancesTable(state.ambulances);
      }
    });
  }

  /* ---------- Navigation ---------- */
  function showPanel(name) {
    ["live", "queue", "chat", "ambulances", "history", "notifications", "profile"].forEach(function (p) {
      var el = $("panel-" + p);
      if (!el) return;
      if (p === name) el.classList.remove("hidden-panel");
      else el.classList.add("hidden-panel");
    });
    document.querySelectorAll(".hcc-nav a").forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-panel") === name);
    });
    var titles = {
      live: (hospital().name || "Hospital") + " Command Center",
      queue: "Emergency Queue",
      chat: "Case Chat",
      ambulances: "Ambulances",
      history: "History",
      notifications: "Notifications",
      profile: "Hospital Profile",
    };
    if ($("hcc-page-title")) $("hcc-page-title").textContent = titles[name] || titles.live;
    if (name === "profile" && window.HospitalApp) HospitalApp.initProfileForm(hospital());
    if (name === "ambulances" && window.HospitalApp) HospitalApp.initAmbulancesPanel();
    if (name === "chat") {
      if (!state.chatCaseId && state.selectedId) state.chatCaseId = state.selectedId;
      if (!state.chatCaseId && state.emergencies[0]) state.chatCaseId = state.emergencies[0].id;
      renderChatCaseList();
      loadCaseChat();
      startChatPoll();
    } else {
      stopChatPoll();
    }
    if (name === "live" || name === "queue") {
      setTimeout(function () {
        if (state.map) state.map.invalidateSize();
      }, 100);
    }
  }

  function init() {
    document.querySelectorAll(".hcc-nav a").forEach(function (a) {
      a.addEventListener("click", function (e) {
        e.preventDefault();
        showPanel(a.getAttribute("data-panel"));
        var sb = $("sidebar");
        if (sb) sb.classList.remove("open");
      });
    });
    if ($("sidebar-toggle")) {
      $("sidebar-toggle").onclick = function () {
        $("sidebar").classList.toggle("open");
      };
    }
    if ($("btn-open-notifs")) {
      $("btn-open-notifs").onclick = function () {
        showPanel("notifications");
      };
    }
    if ($("btn-refresh-amb-strip")) $("btn-refresh-amb-strip").onclick = loadAll;
    if ($("btn-sos-refresh")) $("btn-sos-refresh").onclick = loadAll;
    if ($("btn-hcc-locate")) $("btn-hcc-locate").onclick = locateSelfOnMap;
    if ($("btn-hcc-fit"))
      $("btn-hcc-fit").onclick = function () {
        renderMap(true);
      };
    if ($("btn-hcc-map-refresh")) $("btn-hcc-map-refresh").onclick = loadAll;
    document.querySelectorAll("[data-hcc-map-filter]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.mapFilter = btn.getAttribute("data-hcc-map-filter") || "all";
        renderMap(true);
      });
    });
    if ($("hcc-modal-cancel")) $("hcc-modal-cancel").onclick = closeModal;
    if ($("hcc-modal-save")) {
      $("hcc-modal-save").onclick = function () {
        if (typeof state.modalAction === "function") state.modalAction();
      };
    }
    if ($("btn-mark-all-read")) {
      $("btn-mark-all-read").onclick = function () {
        api("/api/notifications/read", { method: "POST", body: { all: true } }).then(loadAll);
      };
    }
    if ($("hcc-chat-form")) {
      $("hcc-chat-form").addEventListener("submit", function (e) {
        e.preventDefault();
        sendCaseChat();
      });
    }
    if ($("hcc-chat-refresh")) {
      $("hcc-chat-refresh").onclick = function () {
        loadCaseChat();
      };
    }
    document.querySelectorAll(".hcc-chat-chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        sendCaseChat(chip.getAttribute("data-chat-text") || chip.textContent);
      });
    });
    if ($("hcc-chat-search")) {
      $("hcc-chat-search").addEventListener("input", function () {
        state.chatSearch = $("hcc-chat-search").value || "";
        renderChatCaseList();
      });
    }
    document.querySelectorAll("[data-chat-filter]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.chatFilter = btn.getAttribute("data-chat-filter") || "all";
        document.querySelectorAll("[data-chat-filter]").forEach(function (b) {
          b.classList.toggle("is-active", b === btn);
        });
        renderChatCaseList();
      });
    });
    if ($("hcc-chat-case-switch")) {
      $("hcc-chat-case-switch").addEventListener("change", function () {
        var id = parseInt($("hcc-chat-case-switch").value, 10);
        if (!id) return;
        state.chatCaseId = id;
        state.selectedId = id;
        renderChatCaseList();
        loadCaseChat();
      });
    }
    function tickClock() {
      var el = $("hcc-clock");
      if (!el) return;
      try {
        el.textContent = new Date().toLocaleString("en-GB", {
          hour: "2-digit",
          minute: "2-digit",
          day: "numeric",
          month: "short",
          year: "numeric",
        });
      } catch (e) {}
    }
    tickClock();
    setInterval(tickClock, 30000);
    ensureMap();
    loadAll();
    setInterval(loadAll, state.refreshMs);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
