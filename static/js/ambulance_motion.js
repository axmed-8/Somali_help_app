/**
 * Frontend-only ambulance movement visualisation.
 * Does not change dispatch / DB — animates map markers along road routes.
 *
 * Phases: responding → transporting → returning → available
 */
(function (global) {
  "use strict";

  var PHASE = {
    responding: {
      id: "responding",
      label: "Responding",
      routeColor: "#3b82f6",
      markerClass: "hcc-amb-marker hcc-amb-marker-responding",
    },
    transporting: {
      id: "transporting",
      label: "Transporting Patient",
      routeColor: "#ef4444",
      markerClass: "hcc-amb-marker hcc-amb-marker-transporting",
    },
    returning: {
      id: "returning",
      label: "Returning to Base",
      routeColor: "#22c55e",
      markerClass: "hcc-amb-marker hcc-amb-marker-returning",
    },
  };

  var SPEED_FACTOR = 0.4; // faster than real-time for usable demos
  var MIN_LEG_MS = 18000;
  var MAX_LEG_MS = 150000;

  function haversineKm(lat1, lng1, lat2, lng2) {
    var R = 6371;
    var toRad = Math.PI / 180;
    var dLat = (lat2 - lat1) * toRad;
    var dLng = (lng2 - lng1) * toRad;
    var a =
      Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(lat1 * toRad) * Math.cos(lat2 * toRad) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function toLatLngs(coords) {
    // API returns GeoJSON [lng, lat]
    return (coords || []).map(function (c) {
      return [c[1], c[0]];
    });
  }

  function straightRoute(from, to) {
    var steps = 24;
    var pts = [];
    for (var i = 0; i <= steps; i++) {
      var t = i / steps;
      pts.push([from[0] + (to[0] - from[0]) * t, from[1] + (to[1] - from[1]) * t]);
    }
    var km = haversineKm(from[0], from[1], to[0], to[1]) || 0.5;
    return {
      latlngs: pts,
      distanceKm: km,
      durationMin: Math.max(1, Math.round((km / 35) * 60)),
    };
  }

  function fetchRoute(from, to) {
    var url =
      "/api/route?from=" +
      encodeURIComponent(from[0] + "," + from[1]) +
      "&to=" +
      encodeURIComponent(to[0] + "," + to[1]);
    return fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.success && d.coordinates && d.coordinates.length > 1) {
          return {
            latlngs: toLatLngs(d.coordinates),
            distanceKm: Number(d.distance_km) || 0,
            durationMin: Number(d.duration_minutes) || 1,
          };
        }
        return straightRoute(from, to);
      })
      .catch(function () {
        return straightRoute(from, to);
      });
  }

  function interpolateAlong(latlngs, t) {
    if (!latlngs || !latlngs.length) return null;
    if (t <= 0) return latlngs[0];
    if (t >= 1) return latlngs[latlngs.length - 1];
    var segs = [];
    var total = 0;
    for (var i = 1; i < latlngs.length; i++) {
      var d = haversineKm(latlngs[i - 1][0], latlngs[i - 1][1], latlngs[i][0], latlngs[i][1]) || 0.0001;
      segs.push(d);
      total += d;
    }
    if (total <= 0) return latlngs[0];
    var target = t * total;
    var acc = 0;
    for (var j = 0; j < segs.length; j++) {
      if (acc + segs[j] >= target) {
        var local = (target - acc) / segs[j];
        var a = latlngs[j];
        var b = latlngs[j + 1];
        return [a[0] + (b[0] - a[0]) * local, a[1] + (b[1] - a[1]) * local];
      }
      acc += segs[j];
    }
    return latlngs[latlngs.length - 1];
  }

  function remainingKm(latlngs, t) {
    if (!latlngs || latlngs.length < 2) return 0;
    var pos = interpolateAlong(latlngs, t);
    if (!pos) return 0;
    // remaining path length
    var segs = [];
    var total = 0;
    for (var i = 1; i < latlngs.length; i++) {
      var d = haversineKm(latlngs[i - 1][0], latlngs[i - 1][1], latlngs[i][0], latlngs[i][1]) || 0;
      segs.push(d);
      total += d;
    }
    var target = t * total;
    var acc = 0;
    var rem = 0;
    var started = false;
    for (var j = 0; j < segs.length; j++) {
      if (!started) {
        if (acc + segs[j] >= target) {
          rem += acc + segs[j] - target;
          started = true;
        }
      } else {
        rem += segs[j];
      }
      acc += segs[j];
    }
    // also include last hop from interpolated point if needed
    if (pos && latlngs.length) {
      var end = latlngs[latlngs.length - 1];
      return Math.max(rem, haversineKm(pos[0], pos[1], end[0], end[1]) || 0);
    }
    return rem;
  }

  function AmbulanceMotion(opts) {
    this.map = null;
    this.layer = null;
    this.missions = {}; // aid -> mission
    this.getHospital = opts.getHospital || function () { return {}; };
    this.onStatus = opts.onStatus || function () {};
    this.onVisualAvailable = opts.onVisualAvailable || function () {};
    this.isLiveTracked = opts.isLiveTracked || function () { return false; };
  }

  AmbulanceMotion.prototype.attach = function (map) {
    if (!map || typeof L === "undefined") return;
    this.map = map;
    if (!this.layer) this.layer = L.layerGroup().addTo(map);
  };

  AmbulanceMotion.prototype.activeIds = function () {
    return Object.keys(this.missions).map(function (k) {
      return parseInt(k, 10);
    });
  };

  AmbulanceMotion.prototype.clearMission = function (aid) {
    var m = this.missions[aid];
    if (!m) return;
    if (m.raf) cancelAnimationFrame(m.raf);
    if (m.marker && this.layer) this.layer.removeLayer(m.marker);
    if (m.routeLine && this.layer) this.layer.removeLayer(m.routeLine);
    delete this.missions[aid];
  };

  AmbulanceMotion.prototype.destroy = function () {
    var self = this;
    Object.keys(this.missions).forEach(function (id) {
      self.clearMission(id);
    });
  };

  AmbulanceMotion.prototype._setMarkerIcon = function (phaseId, callSign) {
    var meta = PHASE[phaseId] || PHASE.responding;
    var cross =
      phaseId === "transporting"
        ? '<span class="hcc-amb-cross" title="Patient on board">✚</span>'
        : "";
    return L.divIcon({
      className: "",
      html:
        '<div class="' +
        meta.markerClass +
        '">' +
        cross +
        '<span class="hcc-amb-dot"></span><em>' +
        (callSign || "AMB") +
        "</em></div>",
      iconSize: [44, 44],
      iconAnchor: [22, 22],
    });
  };

  AmbulanceMotion.prototype._emitStatus = function (mission) {
    var meta = PHASE[mission.phase] || PHASE.responding;
    var remKm = remainingKm(mission.latlngs, mission.t);
    var remMin =
      mission.durationMin && mission.distanceKm
        ? Math.max(1, Math.round((remKm / Math.max(mission.distanceKm, 0.01)) * mission.durationMin))
        : Math.max(1, Math.round((remKm / 35) * 60));
    this.onStatus({
      aid: mission.aid,
      callSign: mission.callSign,
      phase: mission.phase,
      label: meta.label,
      color: meta.routeColor,
      km: remKm,
      etaMin: remMin,
      carrying: mission.phase === "transporting",
      position: interpolateAlong(mission.latlngs, mission.t),
    });
  };

  AmbulanceMotion.prototype._startLeg = function (mission, phaseId, from, to, onArrive) {
    var self = this;
    var meta = PHASE[phaseId];
    mission.phase = phaseId;
    mission.t = 0;
    mission.onArrive = onArrive;
    if (mission.routeLine && this.layer) this.layer.removeLayer(mission.routeLine);
    if (mission.raf) cancelAnimationFrame(mission.raf);

    fetchRoute(from, to).then(function (route) {
      if (!self.missions[mission.aid]) return;
      mission.latlngs = route.latlngs;
      mission.distanceKm = route.distanceKm;
      mission.durationMin = route.durationMin;
      var ms = (route.durationMin || 1) * 60000 * SPEED_FACTOR;
      mission.legMs = Math.max(MIN_LEG_MS, Math.min(MAX_LEG_MS, ms));
      mission.startedAt = performance.now();

      mission.routeLine = L.polyline(route.latlngs, {
        color: meta.routeColor,
        weight: 5,
        opacity: 0.85,
        lineJoin: "round",
      }).addTo(self.layer);

      var startPos = route.latlngs[0];
      if (!mission.marker) {
        mission.marker = L.marker(startPos, {
          icon: self._setMarkerIcon(phaseId, mission.callSign),
          zIndexOffset: 800,
        }).addTo(self.layer);
      } else {
        mission.marker.setLatLng(startPos);
        mission.marker.setIcon(self._setMarkerIcon(phaseId, mission.callSign));
      }

      self._emitStatus(mission);
      self._tick(mission);
    });
  };

  AmbulanceMotion.prototype._tick = function (mission) {
    var self = this;
    if (!this.missions[mission.aid]) return;

    function frame(now) {
      if (!self.missions[mission.aid]) return;
      var elapsed = now - mission.startedAt;
      mission.t = Math.min(1, elapsed / mission.legMs);
      var pos = interpolateAlong(mission.latlngs, mission.t);
      if (pos && mission.marker) mission.marker.setLatLng(pos);
      if (elapsed % 400 < 20 || mission.t >= 1) self._emitStatus(mission);

      if (mission.t >= 1) {
        mission.raf = null;
        if (typeof mission.onArrive === "function") mission.onArrive();
        return;
      }
      mission.raf = requestAnimationFrame(frame);
    }
    mission.raf = requestAnimationFrame(frame);
  };

  AmbulanceMotion.prototype._beginResponding = function (mission, citizen, base) {
    var self = this;
    var from = mission.position || base;
    this._startLeg(mission, "responding", from, citizen, function () {
      mission.position = citizen.slice();
      self._beginTransporting(mission, citizen, base);
    });
  };

  AmbulanceMotion.prototype._beginTransporting = function (mission, citizen, hospital) {
    var self = this;
    var from = mission.position || citizen;
    this._startLeg(mission, "transporting", from, hospital, function () {
      mission.position = hospital.slice();
      self._beginReturning(mission, hospital, mission.base);
    });
  };

  AmbulanceMotion.prototype._beginReturning = function (mission, hospital, base) {
    var self = this;
    var from = mission.position || hospital;
    var dest = base || hospital;
    this._startLeg(mission, "returning", from, dest, function () {
      mission.position = dest.slice();
      var aid = mission.aid;
      self.clearMission(aid);
      self.onStatus({
        aid: aid,
        callSign: mission.callSign,
        phase: "available",
        label: "Available",
        color: "#22c55e",
        km: 0,
        etaMin: 0,
        carrying: false,
        position: dest,
      });
      self.onVisualAvailable(aid, dest);
    });
  };

  /**
   * Sync missions from live desk data. Frontend visualisation only.
   */
  AmbulanceMotion.prototype.sync = function (ctx) {
    if (!this.map || !this.layer) return;
    var self = this;
    var hospital = this.getHospital() || {};
    var hPos =
      hospital.latitude != null && hospital.longitude != null
        ? [Number(hospital.latitude), Number(hospital.longitude)]
        : null;
    var emergencies = ctx.emergencies || [];
    var ambulances = ctx.ambulances || [];
    var byId = {};
    ambulances.forEach(function (a) {
      byId[a.id] = a;
    });

    var activeAids = {};

    emergencies.forEach(function (em) {
      var aid = em.assigned_ambulance_id;
      if (!aid) return;
      var st = (em.status || "").toLowerCase();
      if (["cancelled"].indexOf(st) >= 0) return;
      if (this.isLiveTracked(aid)) return; // real GPS takes over

      var unit = byId[aid];
      if (!unit) return;
      if (!hPos) return;
      if (em.latitude == null || em.longitude == null) return;

      // Terminal cases: if already completed and no mission, skip (or let return finish)
      if (["completed", "resolved"].indexOf(st) >= 0 && !this.missions[aid]) return;

      activeAids[aid] = true;
      var citizen = [Number(em.latitude), Number(em.longitude)];
      var start =
        unit.latitude != null && unit.longitude != null
          ? [Number(unit.latitude), Number(unit.longitude)]
          : hPos.slice();

      var existing = this.missions[aid];
      if (existing) {
        // Keep running animation; only retarget if emergency coords changed a lot mid-leg
        return;
      }

      var mission = {
        aid: aid,
        callSign: unit.call_sign || em.assigned_ambulance_call_sign || "AMB",
        base: hPos.slice(),
        position: start.slice(),
        emergencyId: em.id,
        phase: null,
        t: 0,
        latlngs: null,
        marker: null,
        routeLine: null,
        raf: null,
      };
      this.missions[aid] = mission;

      // Map backend status → starting phase (then auto-advance by animation)
      if (st === "in_progress") {
        this._beginTransporting(mission, citizen, hPos);
      } else if (st === "dispatched" || st === "accepted") {
        this._beginResponding(mission, citizen, start);
      } else if (unit.status === "busy") {
        this._beginResponding(mission, citizen, start);
      } else {
        this.clearMission(aid);
      }
    }.bind(this));

    // Drop missions for units no longer on an active case (unless mid-return)
    Object.keys(this.missions).forEach(function (id) {
      var aid = parseInt(id, 10);
      var m = self.missions[aid];
      if (!m) return;
      if (activeAids[aid]) return;
      if (m.phase === "returning" || m.phase === "transporting") return;
      self.clearMission(aid);
    });
  };

  global.AmbulanceMotion = AmbulanceMotion;
  global.AmbulanceMotionPhases = PHASE;
})(window);
