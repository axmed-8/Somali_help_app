/**
 * GurmadNet AI — Admin Command Center (Super Admin / Admin RBAC)
 */
(function () {
  "use strict";

  var defaultsEl = document.getElementById("admin-content-defaults");
  var DEFAULTS = defaultsEl ? JSON.parse(defaultsEl.textContent) : {};
  var emergenciesCache = {};
  var charts = {};
  var map;
  var mapLayer;
  var mapType = null;
  var refreshTimer;
  var adminConfig = {};

  try {
    adminConfig = JSON.parse(document.getElementById("admin-runtime-config").textContent || "{}");
  } catch (e) {
    adminConfig = {};
  }

  var PERMS = {};
  (adminConfig.permissions || []).forEach(function (p) {
    PERMS[p] = true;
  });
  var IS_SUPER = !!adminConfig.is_super_admin;

  function can(perm) {
    return !!PERMS[perm];
  }

  function $(id) {
    return document.getElementById(id);
  }

  var SECTION_PERMS = {
    overview: "dashboard",
    users: "users_ops",
    emergencies: "emergencies_view",
    dispatch: "emergencies_view",
    sos: "emergencies_view",
    callcenter: "call_center",
    ai: "ai",
    map: "dashboard",
    reports: "reports",
    settings: "settings_ops",
    system: "settings_system",
    content: "content_edit",
    appearance: "appearance",
    audit: "audit",
    backup: "backup",
    monitoring: "monitoring",
    profile: null, // every admin can manage their own account
    help: null,
  };

  var SHORTCUT_PERMS = {
    "add-citizen": "users_ops",
    "create-admin": "users_admins",
    "add-hospital": "users_ops",
    dispatch: "emergencies_view",
    report: "reports",
    backup: "backup",
    audit: "audit",
    settings: "settings_ops",
    ai: "ai",
  };

  function hideUnauthorized(el) {
    if (!el) return;
    el.classList.add("sac-no-access");
    el.setAttribute("hidden", "hidden");
    el.setAttribute("aria-hidden", "true");
    if (el.tagName === "A" || el.tagName === "BUTTON") {
      el.setAttribute("tabindex", "-1");
    }
  }

  function applyPermissions() {
    document.body.classList.toggle("sac-role-super", IS_SUPER);
    document.body.classList.toggle("sac-role-admin", !IS_SUPER);

    document.querySelectorAll("[data-perm]").forEach(function (el) {
      var need = el.getAttribute("data-perm");
      if (need && !can(need)) hideUnauthorized(el);
    });

    Object.keys(SECTION_PERMS).forEach(function (section) {
      var need = SECTION_PERMS[section];
      if (need && !can(need)) {
        var sec = $("sec-" + section);
        if (sec) hideUnauthorized(sec);
      }
    });

    document.querySelectorAll("[data-shortcut]").forEach(function (btn) {
      var action = btn.getAttribute("data-shortcut");
      var need = SHORTCUT_PERMS[action];
      if (need && !can(need)) hideUnauthorized(btn);
    });

    var pill = $("sac-role-pill");
    if (pill) {
      pill.textContent = IS_SUPER ? "Super Admin (Full Access)" : "Admin (Limited Access)";
    }
    var profileRole = $("sac-profile-role");
    if (profileRole) {
      profileRole.textContent = IS_SUPER ? "Super Administrator" : "Administrator";
    }
    if ($("btn-create-admin") && !can("users_admins")) hideUnauthorized($("btn-create-admin"));

    // Collapse empty nav groups
    document.querySelectorAll(".sac-nav-group").forEach(function (group) {
      var links = group.querySelectorAll(".sac-sub a:not(.sac-no-access):not([hidden])");
      if (!links.length) hideUnauthorized(group);
    });
  }

  function canOpenSection(name) {
    var need = SECTION_PERMS[name];
    if (need == null) return true;
    return can(need);
  }

  function api(url, opts) {
    return fetch(
      url,
      Object.assign({ headers: { "Content-Type": "application/json" }, credentials: "same-origin" }, opts || {})
    ).then(function (r) {
      return r.json();
    });
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleString();
    } catch (e) {
      return iso;
    }
  }

  function timeAgo(iso) {
    if (!iso) return "—";
    var t = new Date(iso).getTime();
    if (!t) return iso;
    var mins = Math.max(0, Math.round((Date.now() - t) / 60000));
    if (mins < 1) return "just now";
    if (mins < 60) return mins + " min ago";
    var hrs = Math.round(mins / 60);
    if (hrs < 24) return hrs + "h ago";
    return Math.round(hrs / 24) + "d ago";
  }

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function showSection(name) {
    if (!canOpenSection(name)) {
      showSection("overview");
      return;
    }
    document.querySelectorAll(".sac-section").forEach(function (s) {
      s.classList.toggle("active", s.id === "sec-" + name && !s.classList.contains("sac-no-access"));
    });
    document.querySelectorAll("[data-section]").forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-section") === name);
    });
    var sidebar = $("sac-sidebar");
    if (sidebar) sidebar.classList.remove("open");

    if (name === "overview") loadCommandCenter();
    if (name === "content" && can("content_edit")) loadContentEditor();
    if (name === "users" && can("users_ops")) loadUsers();
    if (name === "settings" && can("settings_ops")) loadSettingsForm();
    if (name === "system" && can("settings_system")) loadSystemConfig();
    if ((name === "emergencies" || name === "dispatch" || name === "sos") && can("emergencies_view")) {
      loadEmergencies();
    }
    if (name === "callcenter" && can("call_center")) loadCallCenter();
    if (name === "ai" && can("ai")) loadAiIntelligence();
    if (name === "reports" && can("reports")) loadReports();
    if (name === "appearance" && can("appearance")) loadAppearance();
    if (name === "audit" && can("audit")) loadAudit();
    if (name === "backup" && !can("backup")) return;
    if (name === "monitoring" && can("monitoring")) loadCommandCenter();
    if (name === "map" && can("dashboard")) {
      loadCommandCenter(true);
      setTimeout(function () {
        renderMapView("full", filteredMapMarkers(), !mapViews.full.fitted);
        resizeMapView("full");
      }, 220);
    }
    if (name === "overview") {
      setTimeout(function () {
        resizeMapView("overview");
      }, 220);
    }
    if (name === "profile") loadMyProfile();
  }

  function bindNav() {
    document.querySelectorAll("[data-section]").forEach(function (a) {
      a.addEventListener("click", function (e) {
        e.preventDefault();
        if (a.classList.contains("sac-no-access") || a.hasAttribute("hidden")) return;
        var section = a.getAttribute("data-section");
        if (!canOpenSection(section)) return;
        var need = a.getAttribute("data-perm");
        if (need && !can(need)) return;
        var role = a.getAttribute("data-role-filter");
        if (role != null && $("user-role-filter")) {
          $("user-role-filter").value = role;
        }
        showSection(section);
        if (section === "users") loadUsers();
      });
    });

    document.querySelectorAll(".sac-nav-group > button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        btn.parentElement.classList.toggle("open");
      });
    });

    var menu = $("sac-menu-btn");
    if (menu) {
      menu.addEventListener("click", function () {
        $("sac-sidebar").classList.toggle("open");
      });
    }

    function applyChartTheme() {
      if (!window.Chart) return;
      var light = document.body.classList.contains("sac-light");
      Chart.defaults.color = light ? "#475569" : "#94a3b8";
      Chart.defaults.borderColor = light ? "rgba(15,23,42,0.12)" : "rgba(148,163,184,0.16)";
    }

    var themeBtn = $("sac-theme-btn");
    if (themeBtn) {
      themeBtn.addEventListener("click", function () {
        document.body.classList.toggle("sac-light");
        localStorage.setItem("sac-theme", document.body.classList.contains("sac-light") ? "light" : "dark");
        applyChartTheme();
        var active = document.querySelector(".sac-section.active");
        if (active && (active.id === "sec-overview" || active.id === "sec-monitoring")) {
          loadCommandCenter();
        }
      });
    }
    if (localStorage.getItem("sac-theme") === "light") {
      document.body.classList.add("sac-light");
    }
    applyChartTheme();

    var fsBtn = $("sac-fullscreen-btn");
    if (fsBtn) {
      fsBtn.addEventListener("click", function () {
        if (!document.fullscreenElement) document.documentElement.requestFullscreen();
        else document.exitFullscreen();
      });
    }

    var search = $("sac-global-search");
    if (search) {
      search.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          var q = search.value.trim();
          if (!q) return;
          if ($("user-search")) $("user-search").value = q;
          showSection("users");
          loadUsers();
        }
      });
    }
  }

  function sparkline(canvasId, values, color) {
    var el = $(canvasId);
    if (!el || !window.Chart) return;
    if (charts[canvasId]) {
      charts[canvasId].destroy();
      delete charts[canvasId];
    }
    if (!hasNumericSeries(values)) {
      el.style.display = "none";
      return;
    }
    el.style.display = "block";
    var data = values.map(function (v) {
      return v == null ? 0 : Number(v);
    });
    charts[canvasId] = new Chart(el, {
      type: "line",
      data: {
        labels: data.map(function (_, i) {
          return i;
        }),
        datasets: [
          {
            data: data,
            borderColor: color || "#3b82f6",
            backgroundColor: "transparent",
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.35,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: { x: { display: false }, y: { display: false } },
      },
    });
  }

  function themeTickColor() {
    return document.body.classList.contains("sac-light") ? "#475569" : "#94a3b8";
  }

  function themeGridColor() {
    return document.body.classList.contains("sac-light")
      ? "rgba(15,23,42,0.08)"
      : "rgba(148,163,184,0.12)";
  }

  function clearChart(canvasId) {
    if (charts[canvasId]) {
      charts[canvasId].destroy();
      delete charts[canvasId];
    }
  }

  function showChartEmpty(canvasId) {
    var el = $(canvasId);
    if (!el) return;
    clearChart(canvasId);
    var wrap = el.parentElement;
    el.style.display = "none";
    if (wrap) {
      var existing = wrap.querySelector(".sac-empty");
      if (!existing) {
        var div = document.createElement("div");
        div.className = "sac-empty";
        div.setAttribute("role", "status");
        div.textContent = "No data available";
        wrap.appendChild(div);
      } else {
        existing.hidden = false;
      }
    }
  }

  function prepareChartCanvas(canvasId) {
    var el = $(canvasId);
    if (!el) return null;
    el.style.display = "block";
    var wrap = el.parentElement;
    if (wrap) {
      var existing = wrap.querySelector(".sac-empty");
      if (existing) existing.hidden = true;
    }
    return el;
  }

  function renderLineChart(canvasId, labels, values, color) {
    var el = prepareChartCanvas(canvasId);
    if (!el || !window.Chart) return;
    clearChart(canvasId);
    if (!hasNumericSeries(values)) {
      showChartEmpty(canvasId);
      return;
    }
    var tick = themeTickColor();
    var grid = themeGridColor();
    var series = (values || []).map(function (v) {
      return v == null ? 0 : Number(v);
    });
    charts[canvasId] = new Chart(el, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            data: series,
            borderColor: color || "#60a5fa",
            backgroundColor: "rgba(59,130,246,0.15)",
            fill: true,
            tension: 0.35,
            pointRadius: 3,
          },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: tick }, grid: { color: grid } },
          y: { ticks: { color: tick }, grid: { color: grid } },
        },
      },
    });
  }

  function renderDoughnut(canvasId, obj) {
    var el = prepareChartCanvas(canvasId);
    if (!el || !window.Chart) return;
    clearChart(canvasId);
    var labels = Object.keys(obj || {});
    var values = labels.map(function (k) {
      return obj[k];
    });
    if (!labels.length || !hasNumericSeries(values)) {
      showChartEmpty(canvasId);
      return;
    }
    charts[canvasId] = new Chart(el, {
      type: "doughnut",
      data: {
        labels: labels,
        datasets: [
          {
            data: values,
            backgroundColor: ["#ef4444", "#f59e0b", "#3b82f6", "#22c55e", "#a855f7", "#14b8a6"],
            borderWidth: 0,
          },
        ],
      },
      options: {
        responsive: true,
        plugins: {
          legend: {
            position: "bottom",
            labels: { color: themeTickColor(), boxWidth: 10 },
          },
        },
        cutout: "68%",
      },
    });
  }

  function markerColor(kind) {
    if (kind === "hospital") return "#22c55e";
    if (kind === "police") return "#3b82f6";
    if (kind === "fire") return "#f59e0b";
    if (kind === "emergency") return "#ef4444";
    return "#22d3ee";
  }

  // Dual admin maps (overview + full) — preserve user zoom on live GPS refresh
  var mapViews = {
    overview: {
      elId: "sac-map",
      emptyId: "sac-map-empty",
      map: null,
      type: null,
      layer: null,
      markers: {},
      circles: {},
      adminMarker: null,
      fitted: false,
    },
    full: {
      elId: "sac-map-full",
      emptyId: "sac-map-full-empty",
      map: null,
      type: null,
      layer: null,
      markers: {},
      circles: {},
      adminMarker: null,
      fitted: false,
    },
  };
  var lastMapMarkers = [];
  var mapFilterKind = "all"; // all | emergency | hospital | police | fire
  // Keep legacy aliases used elsewhere
  map = null;
  mapLayer = null;

  function filterLabel(kind) {
    if (kind === "emergency") return "SOS only";
    if (kind === "hospital") return "Hospitals only";
    if (kind === "police") return "Police only";
    if (kind === "fire") return "Fire only";
    return "All pins";
  }

  function filteredMapMarkers() {
    if (!mapFilterKind || mapFilterKind === "all") return lastMapMarkers.slice();
    return lastMapMarkers.filter(function (m) {
      return m.kind === mapFilterKind;
    });
  }

  function syncMapFilterButtons() {
    document.querySelectorAll("[data-map-filter]").forEach(function (btn) {
      var kind = btn.getAttribute("data-map-filter");
      btn.classList.toggle("is-active", kind === mapFilterKind);
    });
  }

  function applyMapFilter(kind, forceFit) {
    mapFilterKind = kind || "all";
    syncMapFilterButtons();
    var list = filteredMapMarkers();
    if (mapViews.overview) mapViews.overview.fitted = false;
    if (mapViews.full) mapViews.full.fitted = false;
    renderMapView("overview", list, forceFit !== false);
    if ($("sec-map") && $("sec-map").classList.contains("active")) {
      renderMapView("full", list, forceFit !== false);
    }
    // After render, force Somalia framing when All is selected
    if (mapFilterKind === "all") {
      setTimeout(function () {
        fitToSomalia(mapViews.overview);
        fitToSomalia(mapViews.full);
      }, 120);
    }
    var status = $("sac-map-gps-status");
    if (status) {
      if (mapFilterKind === "all") {
        status.textContent = "Full Somalia map · all pins (" + list.length + ")";
      } else if (!list.length) {
        status.textContent = filterLabel(mapFilterKind) + " — nothing on the map yet";
      } else {
        status.textContent =
          "Showing: " + filterLabel(mapFilterKind) + " (" + list.length + ") · tap All for full Somalia";
      }
    }
    updateMapFilterCounts();
  }

  function updateMapFilterCounts() {
    var counts = { emergency: 0, hospital: 0, police: 0, fire: 0 };
    lastMapMarkers.forEach(function (m) {
      if (counts[m.kind] != null) counts[m.kind] += 1;
    });
    document.querySelectorAll("[data-map-filter]").forEach(function (btn) {
      var kind = btn.getAttribute("data-map-filter");
      if (kind === "all") {
        btn.textContent = "All (" + lastMapMarkers.length + ")";
        return;
      }
      var label =
        kind === "emergency"
          ? "SOS"
          : kind === "hospital"
            ? "Hospital"
            : kind === "police"
              ? "Police"
              : kind === "fire"
                ? "Fire"
                : kind;
      var n = counts[kind] || 0;
      var long = btn.closest("#sec-map");
      btn.textContent = (long ? label + " only" : label) + " (" + n + ")";
    });
    syncMapFilterButtons();
  }

  // Full-country Somalia framing (All filter / default view)
  var SOMALIA_VIEW = {
    south: -1.7,
    west: 40.9,
    north: 12.0,
    east: 51.6,
    centerLat: 6.0,
    centerLng: 46.5,
    zoom: 6,
  };

  function mapsConfigCenter() {
    var cfg = window.GURMADNET_MAPS || {};
    return {
      lat: typeof cfg.defaultLat === "number" ? cfg.defaultLat : SOMALIA_VIEW.centerLat,
      lng: typeof cfg.defaultLng === "number" ? cfg.defaultLng : SOMALIA_VIEW.centerLng,
      zoom: typeof cfg.defaultZoom === "number" ? cfg.defaultZoom : SOMALIA_VIEW.zoom,
    };
  }

  function isInSomaliaPoint(lat, lng) {
    return (
      lat >= SOMALIA_VIEW.south &&
      lat <= SOMALIA_VIEW.north &&
      lng >= SOMALIA_VIEW.west &&
      lng <= SOMALIA_VIEW.east
    );
  }

  function fitToSomalia(view) {
    if (!view || !view.map) return;
    try {
      if (view.type === "google") {
        var gb = new google.maps.LatLngBounds(
          { lat: SOMALIA_VIEW.south, lng: SOMALIA_VIEW.west },
          { lat: SOMALIA_VIEW.north, lng: SOMALIA_VIEW.east }
        );
        view.map.fitBounds(gb);
        // Keep a readable national view (not city-level)
        google.maps.event.addListenerOnce(view.map, "idle", function () {
          if (view.map.getZoom() > 7) view.map.setZoom(6);
          if (view.map.getZoom() < 5) view.map.setZoom(6);
        });
      } else if (window.L) {
        view.map.fitBounds(
          [
            [SOMALIA_VIEW.south, SOMALIA_VIEW.west],
            [SOMALIA_VIEW.north, SOMALIA_VIEW.east],
          ],
          { padding: [24, 24], maxZoom: 6 }
        );
      }
      view.fitted = true;
    } catch (e) {}
  }

  function markerPopupHtml(m) {
    var meta = m.meta || {};
    var kindLabel =
      m.kind === "emergency"
        ? "SOS Emergency"
        : m.kind === "hospital"
          ? "Hospital"
          : m.kind === "police"
            ? "Police"
            : m.kind === "fire"
              ? "Fire"
              : esc(m.kind);
    var live = m.live
      ? '<div style="margin-top:6px;color:#dc2626;font-weight:700;">● Live GPS now</div>'
      : "";
    var acc =
      m.accuracy_m != null
        ? "<div>Accuracy: ±" + Math.round(m.accuracy_m) + " m</div>"
        : "";
    return (
      '<div style="min-width:160px">' +
      "<div style='font-size:0.95rem;font-weight:800;margin-bottom:4px'>" +
      esc(m.name) +
      "</div>" +
      "<div style='color:#64748b;font-size:0.8rem;margin-bottom:6px'>" +
      kindLabel +
      "</div>" +
      (meta.status ? "<div>Status: <strong>" + esc(meta.status) + "</strong></div>" : "") +
      (meta.caller ? "<div>Caller: " + esc(meta.caller) + "</div>" : "") +
      (meta.phone
        ? '<div>Phone: <a href="tel:' +
          esc(String(meta.phone).replace(/\s/g, "")) +
          '">' +
          esc(meta.phone) +
          "</a></div>"
        : "") +
      (meta.location ? "<div>" + esc(meta.location) + "</div>" : "") +
      (meta.updated_at ? "<div style='color:#64748b'>Updated: " + esc(fmtTime(meta.updated_at)) + "</div>" : "") +
      acc +
      live +
      "<div style='margin-top:6px;color:#94a3b8;font-size:0.75rem'>" +
      Number(m.lat).toFixed(5) +
      ", " +
      Number(m.lng).toFixed(5) +
      "</div></div>"
    );
  }

  function ensureMapView(viewKey) {
    var view = mapViews[viewKey];
    if (!view) return null;
    if (view.map) return view;
    var el = $(view.elId);
    if (!el) return null;
    var c = mapsConfigCenter();

    if (window.google && window.google.maps) {
      view.type = "google";
      view.map = new google.maps.Map(el, {
        center: { lat: SOMALIA_VIEW.centerLat, lng: SOMALIA_VIEW.centerLng },
        zoom: SOMALIA_VIEW.zoom,
        mapTypeControl: true,
        zoomControl: true,
        scaleControl: true,
        streetViewControl: false,
        fullscreenControl: true,
        scrollwheel: true,
        disableDoubleClickZoom: false,
        mapTypeId: "roadmap",
        gestureHandling: "greedy",
        restriction: {
          latLngBounds: {
            north: SOMALIA_VIEW.north + 2,
            south: SOMALIA_VIEW.south - 2,
            east: SOMALIA_VIEW.east + 2,
            west: SOMALIA_VIEW.west - 2,
          },
          strictBounds: false,
        },
        minZoom: 5,
      });
      view.layer = null;
    } else if (window.L) {
      view.type = "leaflet";
      var somaliaPad = L.latLngBounds(
        [SOMALIA_VIEW.south - 1.5, SOMALIA_VIEW.west - 1.5],
        [SOMALIA_VIEW.north + 1.5, SOMALIA_VIEW.east + 1.5]
      );
      view.map = L.map(el, {
        zoomControl: true,
        scrollWheelZoom: true,
        doubleClickZoom: true,
        touchZoom: true,
        boxZoom: true,
        dragging: true,
        keyboard: true,
        minZoom: 5,
        maxZoom: 19,
        maxBounds: somaliaPad,
        maxBoundsViscosity: 0.6,
      }).setView([SOMALIA_VIEW.centerLat, SOMALIA_VIEW.centerLng], SOMALIA_VIEW.zoom);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap",
        maxZoom: 19,
      }).addTo(view.map);
      view.layer = L.layerGroup().addTo(view.map);
      view.map.scrollWheelZoom.enable();
    } else {
      return null;
    }

    if (viewKey === "overview") {
      map = view.map;
      mapLayer = view.layer;
      mapType = view.type;
    }
    setTimeout(function () {
      resizeMapView(viewKey);
      fitToSomalia(view);
    }, 220);
    return view;
  }

  function resizeMapView(viewKey) {
    var view = mapViews[viewKey];
    if (!view || !view.map) return;
    if (view.type === "google") {
      google.maps.event.trigger(view.map, "resize");
    } else if (view.map.invalidateSize) {
      view.map.invalidateSize();
      if (view.map.scrollWheelZoom) view.map.scrollWheelZoom.enable();
    }
  }

  function clearMapViewMarkers(view) {
    if (view.type === "google") {
      Object.keys(view.markers).forEach(function (k) {
        view.markers[k].setMap(null);
      });
      Object.keys(view.circles).forEach(function (k) {
        view.circles[k].setMap(null);
      });
    } else if (view.layer) {
      view.layer.clearLayers();
    }
    view.markers = {};
    view.circles = {};
  }

  function fitMapView(view, points, force) {
    if (!view.map) return;
    if (view.fitted && !force) return;

    // "All" → always show full Somalia (like national overview)
    if (!mapFilterKind || mapFilterKind === "all" || !points || !points.length) {
      fitToSomalia(view);
      return;
    }

    // Filter modes → zoom to those pins inside Somalia
    var local = (points || []).filter(function (p) {
      return isInSomaliaPoint(p[0], p[1]);
    });
    if (!local.length) {
      fitToSomalia(view);
      return;
    }
    try {
      if (view.type === "google") {
        if (local.length === 1) {
          view.map.setCenter({ lat: local[0][0], lng: local[0][1] });
          view.map.setZoom(12);
        } else {
          var b = new google.maps.LatLngBounds();
          local.forEach(function (p) {
            b.extend({ lat: p[0], lng: p[1] });
          });
          view.map.fitBounds(b, 56);
          google.maps.event.addListenerOnce(view.map, "idle", function () {
            if (view.map.getZoom() > 14) view.map.setZoom(14);
            if (view.map.getZoom() < 6) view.map.setZoom(6);
          });
        }
      } else {
        if (local.length === 1) {
          view.map.setView(local[0], 12);
        } else {
          view.map.fitBounds(local, { padding: [36, 36], maxZoom: 12 });
        }
      }
      view.fitted = true;
    } catch (e) {
      fitToSomalia(view);
    }
  }

  function renderMapView(viewKey, markers, forceFit) {
    var view = ensureMapView(viewKey);
    if (!view) return;
    var empty = $(view.emptyId);
    clearMapViewMarkers(view);
    var points = [];

    // Always honor active filter (prevents hospital pins when SOS-only is selected)
    var list = markers || [];
    if (mapFilterKind && mapFilterKind !== "all") {
      list = list.filter(function (m) {
        return m.kind === mapFilterKind;
      });
    }

    list.forEach(function (m) {
      if (m.lat == null || m.lng == null) return;
      var key = String(m.kind) + ":" + String(m.id != null ? m.id : m.lat + "," + m.lng);
      var color = markerColor(m.kind);
      var html = markerPopupHtml(m);
      points.push([m.lat, m.lng]);

      if (view.type === "google") {
        var marker = new google.maps.Marker({
          position: { lat: m.lat, lng: m.lng },
          map: view.map,
          title: m.name || m.kind,
          zIndex: m.kind === "emergency" ? 5 : 3,
          icon: {
            path: google.maps.SymbolPath.CIRCLE,
            scale: m.kind === "emergency" ? (m.live ? 10 : 8) : 7,
            fillColor: color,
            fillOpacity: 0.95,
            strokeColor: "#fff",
            strokeWeight: 2,
          },
        });
        var info = new google.maps.InfoWindow({ content: html });
        marker.addListener("click", function () {
          info.open(view.map, marker);
        });
        view.markers[key] = marker;
        if (m.kind === "emergency" && m.accuracy_m && m.accuracy_m > 0 && m.accuracy_m < 2000) {
          view.circles[key] = new google.maps.Circle({
            map: view.map,
            center: { lat: m.lat, lng: m.lng },
            radius: Number(m.accuracy_m),
            fillColor: color,
            fillOpacity: 0.12,
            strokeColor: color,
            strokeOpacity: 0.45,
            strokeWeight: 1,
          });
        }
      } else if (view.layer) {
        var circle = L.circleMarker([m.lat, m.lng], {
          radius: m.kind === "emergency" ? (m.live ? 9 : 7) : 7,
          color: color,
          fillColor: color,
          fillOpacity: 0.9,
          weight: 2,
        });
        circle.bindPopup(html);
        circle.addTo(view.layer);
        view.markers[key] = circle;
        if (m.kind === "emergency" && m.accuracy_m && m.accuracy_m > 0 && m.accuracy_m < 2000) {
          var acc = L.circle([m.lat, m.lng], {
            radius: Number(m.accuracy_m),
            color: color,
            weight: 1,
            fillColor: color,
            fillOpacity: 0.12,
          });
          acc.addTo(view.layer);
          view.circles[key] = acc;
        }
      }
    });

    if (empty) empty.hidden = points.length > 0;
    fitMapView(view, points, !!forceFit);
    setTimeout(function () {
      resizeMapView(viewKey);
    }, 120);
  }

  function renderMap(markers, forceFit) {
    lastMapMarkers = markers || [];
    updateMapFilterCounts();
    var list = filteredMapMarkers();
    // Keep current filter view; only force-fit when caller asks (first load / Fit all)
    renderMapView("overview", list, forceFit);
    if ($("sec-map") && $("sec-map").classList.contains("active")) {
      renderMapView("full", list, forceFit);
    }
    var liveCount = lastMapMarkers.filter(function (m) {
      return m.kind === "emergency" && m.live;
    }).length;
    var status = $("sac-map-gps-status");
    if (status) {
      if (mapFilterKind !== "all") {
        status.textContent =
          "Showing: " +
          filterLabel(mapFilterKind) +
          " (" +
          list.length +
          ") · tap All for full Somalia";
      } else if (liveCount > 0) {
        status.textContent =
          "Full Somalia map · ● " + liveCount + " live SOS · all pins (" + list.length + ")";
      } else {
        status.textContent = "Full Somalia map · all pins (" + list.length + ")";
      }
    }
    var tip = $("sac-map-full-legend");
    if (tip) {
      tip.innerHTML =
        '<span class="hint" style="border:none;background:transparent;padding:0">' +
        (mapFilterKind === "all"
          ? "Tip: tap a colored button above to show only that type"
          : "Filtered: <strong>" +
            esc(filterLabel(mapFilterKind)) +
            "</strong> — tap All to reset") +
        "</span>";
    }
  }

  function recenterMaps() {
    applyMapFilter(mapFilterKind, true);
  }

  function setAdminGpsOnView(viewKey, lat, lng) {
    var view = ensureMapView(viewKey);
    if (!view || !view.map) return;
    if (view.type === "google") {
      if (view.adminMarker) view.adminMarker.setMap(null);
      view.adminMarker = new google.maps.Marker({
        position: { lat: lat, lng: lng },
        map: view.map,
        title: "Admin GPS",
        zIndex: 8,
        icon: {
          path: google.maps.SymbolPath.CIRCLE,
          scale: 8,
          fillColor: "#2563eb",
          fillOpacity: 1,
          strokeColor: "#fff",
          strokeWeight: 3,
        },
      });
      view.map.panTo({ lat: lat, lng: lng });
      if (view.map.getZoom() < 14) view.map.setZoom(15);
    } else {
      if (view.adminMarker) view.map.removeLayer(view.adminMarker);
      var icon = L.divIcon({
        className: "",
        html: '<div class="sac-admin-gps-dot"></div>',
        iconSize: [14, 14],
        iconAnchor: [7, 7],
      });
      view.adminMarker = L.marker([lat, lng], { icon: icon, zIndexOffset: 900 })
        .addTo(view.map)
        .bindPopup("Admin GPS (you)");
      view.map.setView([lat, lng], Math.max(view.map.getZoom() || 14, 15));
    }
  }

  function locateAdminGps() {
    if (!navigator.geolocation) {
      alert("Geolocation is not supported in this browser.");
      return;
    }
    var status = $("sac-map-gps-status");
    if (status) status.textContent = "Getting admin GPS…";
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        var lat = pos.coords.latitude;
        var lng = pos.coords.longitude;
        setAdminGpsOnView("overview", lat, lng);
        setAdminGpsOnView("full", lat, lng);
        if (status) {
          status.textContent =
            "Admin GPS: " + lat.toFixed(5) + ", " + lng.toFixed(5) + " (±" + Math.round(pos.coords.accuracy || 0) + " m)";
        }
      },
      function (err) {
        alert((err && err.message) || "Could not get GPS. Allow location permission.");
        if (status) status.textContent = "Admin GPS permission denied";
      },
      { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 }
    );
  }

  function setText(id, value) {
    var el = $(id);
    if (!el) return;
    if (value == null || value === "") el.textContent = "—";
    else el.textContent = String(value);
  }

  function emptyHtml(message) {
    return (
      '<div class="sac-empty" role="status">' +
      esc(message || "No data available") +
      "</div>"
    );
  }

  function hasNumericSeries(values) {
    if (!values || !values.length) return false;
    return values.some(function (v) {
      return v != null && Number(v) > 0;
    });
  }

  function renderFeed(items) {
    var box = $("sac-emergency-feed");
    if (!box) return;
    if (!items || !items.length) {
      box.innerHTML = emptyHtml("No data available");
      return;
    }
    box.innerHTML = items
      .map(function (e) {
        var cls = (e.type || "other").toLowerCase();
        return (
          '<div class="sac-feed-item">' +
          '<span class="sac-feed-type ' +
          esc(cls) +
          '">' +
          esc(e.type_label || e.type) +
          "</span>" +
          "<div><strong>" +
          esc(e.caller_name) +
          "</strong><p>" +
          esc(e.location) +
          " · " +
          esc(e.status) +
          (e.assigned_to ? " → " + esc(e.assigned_to) : "") +
          "</p></div>" +
          '<div class="sac-feed-meta">' +
          esc(timeAgo(e.timestamp)) +
          "<br>" +
          esc(e.priority || "") +
          "</div></div>"
        );
      })
      .join("");
    // Scroll starts at the first item (top of feed)
    box.scrollTop = 0;
  }

  function renderHealth(health) {
    var box = $("sac-health-grid");
    if (!box) return;
    if (!health || !Object.keys(health).length) {
      box.innerHTML = emptyHtml("No data available");
      return;
    }
    var labels = {
      database: "Database",
      api: "API Services",
      sms_gateway: "SMS Gateway",
      email_service: "Email Service",
      ai_engine: "AI Engine",
      google_maps: "Maps",
      cpu: "CPU Usage",
      memory: "Memory",
      storage: "Storage",
    };
    var order = [
      "database",
      "api",
      "sms_gateway",
      "email_service",
      "ai_engine",
      "google_maps",
      "cpu",
      "memory",
      "storage",
    ];
    var html = order
      .filter(function (key) {
        return health[key];
      })
      .map(function (key) {
        var h = health[key] || {};
        var ok = (h.status || "") === "healthy";
        var usage = h.usage != null ? " · " + h.usage + "%" : "";
        return (
          '<div class="sac-health-item"><strong>' +
          esc(labels[key] || key) +
          '</strong><div class="' +
          (ok ? "sac-ok" : "sac-warn") +
          '">' +
          esc((h.status || "unknown").toUpperCase()) +
          "</div><span>" +
          esc(h.detail || "") +
          esc(usage) +
          "</span></div>"
        );
      })
      .join("");
    box.innerHTML = html || emptyHtml("No data available");
  }

  function renderActivities(rows) {
    var tbody = document.querySelector("#sac-activity-table tbody");
    if (!tbody) return;
    tbody.innerHTML = "";
    if (!(rows || []).length) {
      tbody.innerHTML =
        "<tr><td colspan='4' class='sac-empty-cell'>No data available</td></tr>";
      return;
    }
    (rows || []).forEach(function (r) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" +
        esc(r.administrator) +
        "</td><td>" +
        esc(r.action) +
        "</td><td>" +
        esc(fmtTime(r.timestamp)) +
        "</td><td>" +
        esc(r.ip && r.ip !== "—" ? r.ip : "—") +
        "</td>";
      tbody.appendChild(tr);
    });
  }

  function renderLocations(byLocation) {
    var box = $("sac-top-locations");
    if (!box) return;
    var entries = Object.keys(byLocation || {}).map(function (k) {
      return [k, byLocation[k]];
    });
    if (!entries.length) {
      box.innerHTML = emptyHtml("No data available");
      return;
    }
    var max = Math.max.apply(
      null,
      entries.map(function (e) {
        return e[1];
      })
    );
    box.innerHTML = entries
      .map(function (e) {
        var pct = Math.round((e[1] / max) * 100);
        return (
          '<div class="sac-bar-row"><span>' +
          esc(e[0]) +
          '</span><div class="sac-bar-track"><div class="sac-bar-fill" style="width:' +
          pct +
          '%"></div></div><span>' +
          e[1] +
          "</span></div>"
        );
      })
      .join("");
  }

  function loadCommandCenter(focusMap) {
    api("/api/admin/command-center").then(function (d) {
      if (!d || d.success === false) return;
      setText("kpi-citizens", d.citizens != null ? d.citizens : 0);
      setText("kpi-active", d.active_emergencies != null ? d.active_emergencies : 0);
      setText("kpi-hospitals", d.hospitals_online != null ? d.hospitals_online : 0);
      setText("kpi-police", d.police_online != null ? d.police_online : 0);
      setText("kpi-fire", d.fire_online != null ? d.fire_online : 0);
      setText("kpi-ambulances", d.ambulances_available != null ? d.ambulances_available : 0);
      setText("kpi-ai", d.ai_alerts != null ? d.ai_alerts : 0);
      setText(
        "kpi-avg",
        d.avg_response_time != null ? d.avg_response_time + " min" : "No data available"
      );

      setText("kpi-citizens-trend", "From database");
      setText("kpi-active-trend", (d.emergencies_today || 0) + " today");
      setText("kpi-hospitals-trend", (d.hospitals_total || 0) + " registered");
      setText("kpi-police-trend", "Registered accounts");
      setText("kpi-fire-trend", "Registered accounts");
      setText("kpi-ambulances-trend", "From hospital records");
      setText("kpi-ai-trend", "Decisions today");
      setText(
        "kpi-avg-trend",
        d.avg_response_time != null ? "From dispatch history" : "No response samples yet"
      );

      // Only plot sparklines for metrics that have a real time series
      var dayVals = Object.values(d.emergencies_by_day || {});
      ["spark-citizens", "spark-hospitals", "spark-police", "spark-fire", "spark-ambulances", "spark-ai"].forEach(
        function (id) {
          sparkline(id, [], "#60a5fa");
        }
      );
      sparkline("spark-active", dayVals, "#f87171");
      sparkline("spark-avg", Object.values(d.avg_response_by_day || {}), "#a855f7");

      var labels = Object.keys(d.emergencies_by_day || {});
      var values = Object.values(d.emergencies_by_day || {});
      renderLineChart("chart-week-emergencies", labels, values, "#60a5fa");
      renderLineChart(
        "chart-week-response",
        Object.keys(d.avg_response_by_day || {}),
        Object.values(d.avg_response_by_day || {}),
        "#a855f7"
      );
      renderDoughnut("chart-types", d.by_type || {});

      var ws = d.week_summary || {};
      setText("week-total", ws.total != null ? ws.total : 0);
      setText("week-resolved", ws.resolved != null ? ws.resolved : 0);
      setText("week-pending", ws.pending != null ? ws.pending : 0);
      setText("week-cancelled", ws.cancelled != null ? ws.cancelled : 0);

      var ai = d.ai_center || {};
      var aiBox = $("sac-ai-center");
      if (aiBox) {
        if (!ai.has_data) {
          aiBox.innerHTML = emptyHtml("No data available");
        } else {
          // restore structure if previously emptied
          if (!aiBox.querySelector("#ai-alert-text")) {
            aiBox.innerHTML =
              '<div class="sac-ai-alert"><div class="tag" id="ai-alert-priority"></div><div id="ai-alert-text"></div></div>' +
              '<p class="hint" id="ai-recommend-text"></p>' +
              '<div class="sac-ai-metrics"><div><strong id="ai-accuracy"></strong><span>Avg confidence</span></div>' +
              '<div><strong id="ai-predicted"></strong><span>Decisions today</span></div></div>';
          }
          setText("ai-alert-text", ai.alert || "No data available");
          setText("ai-alert-priority", ai.priority || "—");
          setText("ai-recommend-text", ai.recommendation || "No data available");
          setText(
            "ai-accuracy",
            ai.prediction_accuracy != null ? ai.prediction_accuracy + "%" : "No data available"
          );
          setText(
            "ai-predicted",
            ai.incidents_predicted != null ? ai.incidents_predicted : "No data available"
          );
        }
      }

      renderFeed(d.emergency_feed);
      renderMap(d.map_markers || []);
      renderHealth(d.system_health);
      renderActivities(d.recent_activities);
      renderLocations(d.by_location);

      var hotlineCard = $("sac-hotline-card");
      var hotlineEl = $("sac-hotline-number");
      var hotlineDisplay = d.hotline || "+252613910872";
      var hotlineDigits = String(hotlineDisplay).replace(/\D/g, "");
      if (!hotlineDigits) hotlineDigits = "252613910872";
      var waUrl = "https://wa.me/" + hotlineDigits;
      if (hotlineEl) hotlineEl.textContent = hotlineDisplay;
      if (hotlineCard) {
        hotlineCard.setAttribute("href", waUrl);
        hotlineCard.setAttribute(
          "aria-label",
          "Open WhatsApp emergency hotline " + hotlineDisplay
        );
      }
      var status = $("sac-system-status-text");
      if (status) {
        status.textContent =
          "System Status: " +
          ((d.system_status || "operational").replace(/^./, function (c) {
            return c.toUpperCase();
          })) +
          " • Live";
      }

      var notif = $("sac-notif-count");
      if (notif) {
        notif.textContent = String(d.active_emergencies || 0);
        notif.hidden = !(d.active_emergencies > 0);
      }
      if (focusMap) {
        setTimeout(function () {
          renderMapView("full", filteredMapMarkers(), !mapViews.full.fitted);
          resizeMapView("full");
          resizeMapView("overview");
        }, 180);
      }
    });
  }

  /* ---- Existing management features (preserved) ---- */

  function loadAiIntelligence() {
    api("/api/admin/ai/stats").then(function (d) {
      if (!d.success) return;
      var s = d.stats || {};
      setText("ai-st-today", s.decisions_today != null ? s.decisions_today : "No data available");
      setText(
        "ai-st-conf",
        s.average_confidence != null
          ? Math.round((s.average_confidence || 0) * 100) + "%"
          : "No data available"
      );
      setText(
        "ai-st-improve",
        s.average_response_improvement != null
          ? s.average_response_improvement + "%"
          : "No data available"
      );
      setText(
        "ai-st-approved",
        s.approved_recommendations != null ? s.approved_recommendations : "No data available"
      );
      setText(
        "ai-st-rejected",
        s.rejected_recommendations != null ? s.rejected_recommendations : "No data available"
      );
      setText(
        "ai-st-manual",
        s.manual_selections != null ? s.manual_selections : "No data available"
      );
      setText("ai-provider-label", d.ai_provider || "rule_based");
      setText("ai-enabled-label", d.ai_enabled ? "Yes" : "No");
    });
  }

  function loadCallCenter() {
    api("/api/admin/call-center/stats").then(function (d) {
      if (!d.success) return;
      var s = d.stats || {};
      setText("cc-st-online", s.operators_online);
      setText("cc-st-incoming", s.incoming_calls);
      setText("cc-st-waiting", s.calls_waiting);
      setText("cc-st-progress", s.calls_in_progress);
      setText("cc-st-resolved", s.resolved_today);
      setText("cc-st-avg", s.avg_response_minutes);
      var st = d.settings || {};
      if ($("cc_enabled")) $("cc_enabled").checked = !!st.enabled;
      if ($("cc_phone_primary")) $("cc_phone_primary").value = st.phone_primary || "";
      if ($("cc_phone_secondary")) $("cc_phone_secondary").value = st.phone_secondary || "";
      var ops = $("cc-operators-list");
      if (ops) {
        ops.innerHTML =
          (d.operators || [])
            .map(function (o) {
              return "<span class='role-pill'>" + esc(o.name) + " · " + esc(o.email) + " · " + esc(o.status) + "</span>";
            })
            .join(" ") || "<p class='hint'>No operators yet.</p>";
      }
      var tbody = document.querySelector("#cc-recent-table tbody");
      if (tbody) {
        tbody.innerHTML = "";
        (d.recent_calls || []).forEach(function (c) {
          var tr = document.createElement("tr");
          tr.innerHTML =
            "<td>#" +
            esc(c.id) +
            "</td><td>" +
            esc(c.caller_name) +
            "</td><td>" +
            esc(c.phone) +
            "</td><td>" +
            esc((c.emergency_types || []).join(", ") || c.emergency_type || "—") +
            "</td><td>" +
            esc(c.operator_name || "—") +
            "</td><td>" +
            esc(c.status) +
            "</td><td>" +
            esc(fmtTime(c.start_time)) +
            "</td>";
          tbody.appendChild(tr);
        });
      }
    });
    api("/api/admin/settings").then(function (s) {
      if (s.call_center_priority_medical != null && $("cc_pri_medical"))
        $("cc_pri_medical").value = s.call_center_priority_medical;
      if (s.call_center_priority_fire != null && $("cc_pri_fire")) $("cc_pri_fire").value = s.call_center_priority_fire;
      if (s.call_center_priority_police != null && $("cc_pri_police"))
        $("cc_pri_police").value = s.call_center_priority_police;
    });
  }

  function loadContentEditor() {
    api("/api/admin/content").then(function (content) {
      var box = $("content-editor");
      if (!box) return;
      box.innerHTML = "";
      Object.keys(content).forEach(function (key) {
        var row = document.createElement("div");
        row.className = "content-row";
        row.innerHTML =
          "<label>" +
          esc(key) +
          "</label>" +
          '<input type="text" data-key="' +
          esc(key) +
          '" value="' +
          esc(content[key] || "").replace(/"/g, "&quot;") +
          '">' +
          '<button type="button" class="btn-sm btn-save-content" data-key="' +
          esc(key) +
          '">Save</button>' +
          (can("content_reset")
            ? '<button type="button" class="btn-sm btn-reset-content" data-key="' +
              esc(key) +
              '">Reset</button>'
            : "");
        box.appendChild(row);
      });
      box.querySelectorAll(".btn-save-content").forEach(function (btn) {
        btn.onclick = function () {
          var key = btn.getAttribute("data-key");
          var val = box.querySelector('input[data-key="' + key + '"]').value;
          api("/api/admin/content/update", { method: "POST", body: JSON.stringify({ key: key, value: val }) }).then(
            function () {
              alert("Saved: " + key);
            }
          );
        };
      });
      box.querySelectorAll(".btn-reset-content").forEach(function (btn) {
        btn.onclick = function () {
          if (!can("content_reset")) return;
          var key = btn.getAttribute("data-key");
          api("/api/admin/content/reset", { method: "POST", body: JSON.stringify({ key: key }) }).then(function (r) {
            if (!r || r.success === false) {
              alert((r && r.message) || "Reset not allowed");
              return;
            }
            box.querySelector('input[data-key="' + key + '"]').value = r.content[key] || DEFAULTS[key] || "";
          });
        };
      });
    });
  }

  function roleOptionsHtml(selected, opts) {
    opts = opts || {};
    var roles = [
      ["citizen", "Citizen"],
      ["hospital", "Hospital"],
      ["police", "Police"],
      ["fire", "Fire"],
      ["call_center", "Call Center"],
    ];
    if (can("users_admins") || opts.includePrivileged) {
      roles.push(["admin", "Admin (limited)"]);
      if (IS_SUPER) roles.push(["super_admin", "Super Admin (full)"]);
    }
    return roles
      .map(function (r) {
        return (
          '<option value="' +
          r[0] +
          '"' +
          (selected === r[0] ? " selected" : "") +
          ">" +
          r[1] +
          "</option>"
        );
      })
      .join("");
  }

  function roleLabel(role) {
    if (role === "super_admin") return "Super Admin";
    if (role === "admin") return "Admin";
    return role || "";
  }

  function handleUserApiResult(r, okMessage) {
    if (!r || r.success === false) {
      alert((r && r.message) || "Action failed");
      return false;
    }
    if (okMessage) alert(okMessage);
    closeModal();
    loadUsers();
    return true;
  }

  var STAFF_CREATE_ROLES = [
    ["admin", "Admin"],
    ["hospital", "Hospital"],
    ["police", "Police"],
    ["fire", "Fire"],
  ];

  function staffRoleOptionsHtml(selected) {
    return STAFF_CREATE_ROLES.map(function (r) {
      return (
        '<option value="' +
        r[0] +
        '"' +
        (selected === r[0] ? " selected" : "") +
        ">" +
        r[1] +
        "</option>"
      );
    }).join("");
  }

  /** + Add user — citizens only (Admin + Super Admin) */
  function openAddCitizenModal() {
    openModal(
      "Add citizen",
      '<label class="sac-field"><span>Full name</span><input id="nu-name" placeholder="Full name" required></label>' +
        '<label class="sac-field"><span>Email</span><input id="nu-email" type="email" placeholder="Email" required></label>' +
        '<label class="sac-field"><span>Phone</span><input id="nu-phone" placeholder="Phone (optional)"></label>' +
        '<label class="sac-field"><span>Password</span><input id="nu-pass" type="password" placeholder="Min. 6 characters" autocomplete="new-password"></label>' +
        '<input type="hidden" id="nu-role" value="citizen">' +
        '<p class="hint">This creates a <strong>citizen</strong> account only.</p>'
    );
    $("modal-save").onclick = function () {
      submitCreateUser("citizen", "Citizen created successfully.");
    };
  }

  var hospitalsCache = [];

  function hospitalOptionsHtml(selectedId, includeEmpty) {
    var opts = includeEmpty
      ? '<option value="">— Select hospital facility —</option>'
      : "";
    (hospitalsCache || []).forEach(function (h) {
      opts +=
        '<option value="' +
        esc(h.id) +
        '"' +
        (String(selectedId) === String(h.id) ? " selected" : "") +
        ">" +
        esc(h.name) +
        "</option>";
    });
    return opts;
  }

  function hospitalFieldHtml(selectedId, idAttr) {
    idAttr = idAttr || "nu-hospital";
    return (
      '<label class="sac-field" id="' +
      idAttr +
      '-wrap" hidden><span>Linked hospital</span><select id="' +
      idAttr +
      '">' +
      hospitalOptionsHtml(selectedId, true) +
      "</select>" +
      '<p class="hint">Hospital login must belong to a facility in the hospitals table.</p></label>'
    );
  }

  function toggleHospitalField(roleSelectId, wrapId) {
    var roleEl = $(roleSelectId);
    var wrap = $(wrapId);
    if (!wrap) return;
    var role = (roleEl && roleEl.value) || "";
    wrap.hidden = role !== "hospital";
  }

  function ensureHospitalsCache() {
    if (hospitalsCache && hospitalsCache.length) {
      return Promise.resolve(hospitalsCache);
    }
    return api("/api/hospitals").then(function (data) {
      hospitalsCache = (data && data.hospitals) || [];
      return hospitalsCache;
    });
  }

  /** + Create staff — Super Admin only: Admin, Hospital, Police, Fire */
  function openCreateStaffModal(presetRole) {
    if (!can("users_admins")) {
      alert("Only Super Admin can create Admin, Hospital, Police, or Fire accounts.");
      return;
    }
    var selected = presetRole && ["admin", "hospital", "police", "fire"].indexOf(presetRole) >= 0
      ? presetRole
      : "admin";
    ensureHospitalsCache().then(function () {
      openModal(
        "Create staff account",
        '<label class="sac-field"><span>Full name</span><input id="nu-name" placeholder="Full name" required></label>' +
          '<label class="sac-field"><span>Email</span><input id="nu-email" type="email" placeholder="Email" required></label>' +
          '<label class="sac-field"><span>Phone</span><input id="nu-phone" placeholder="Phone (optional)"></label>' +
          '<label class="sac-field"><span>Password</span><input id="nu-pass" type="password" placeholder="Min. 6 characters" autocomplete="new-password"></label>' +
          '<label class="sac-field"><span>Role</span><select id="nu-role">' +
          staffRoleOptionsHtml(selected) +
          "</select></label>" +
          hospitalFieldHtml("", "nu-hospital") +
          '<p class="hint">Choose one: <strong>Admin</strong>, <strong>Hospital</strong>, <strong>Police</strong>, or <strong>Fire</strong>.</p>'
      );
      if ($("nu-role")) $("nu-role").value = selected;
      toggleHospitalField("nu-role", "nu-hospital-wrap");
      if ($("nu-role")) {
        $("nu-role").addEventListener("change", function () {
          toggleHospitalField("nu-role", "nu-hospital-wrap");
        });
      }
      $("modal-save").onclick = function () {
        var role = ($("nu-role") && $("nu-role").value) || "admin";
        submitCreateUser(role, roleLabel(role) + " account created successfully.");
      };
    });
  }

  function submitCreateUser(role, okMessage) {
    var payload = {
      name: ($("nu-name") && $("nu-name").value) || "",
      email: ($("nu-email") && $("nu-email").value) || "",
      phone: ($("nu-phone") && $("nu-phone").value) || "",
      password: ($("nu-pass") && $("nu-pass").value) || "",
      role: role || "citizen",
    };
    if (payload.role === "hospital") {
      payload.hospital_id = ($("nu-hospital") && $("nu-hospital").value) || "";
      if (!payload.hospital_id) {
        alert("Select a hospital facility to link this account");
        return;
      }
    }
    if (!payload.name.trim() || !payload.email.trim()) {
      alert("Name and email are required");
      return;
    }
    if (!payload.password || payload.password.length < 6) {
      alert("Password must be at least 6 characters");
      return;
    }
    api("/api/admin/users/create", {
      method: "POST",
      body: JSON.stringify(payload),
    }).then(function (r) {
      if (r && r.success && $("user-role-filter")) {
        $("user-role-filter").value = payload.role;
      }
      handleUserApiResult(r, okMessage || "Account created successfully.");
    });
  }

  // Back-compat alias used by shortcuts
  function openCreateUserModal(opts) {
    opts = opts || {};
    if (opts.role === "citizen" || !opts.role) {
      openAddCitizenModal();
      return;
    }
    openCreateStaffModal(opts.role);
  }

  function loadUsers() {
    var q = ($("user-search") && $("user-search").value) || "";
    var role = ($("user-role-filter") && $("user-role-filter").value) || "";
    var url = "/api/admin/users?q=" + encodeURIComponent(q) + "&role=" + encodeURIComponent(role);
    var hint = $("admin-users-hint");
    if (hint) hint.hidden = role !== "admin" && role !== "";
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data.hospitals) hospitalsCache = data.hospitals;
        var tbody = document.querySelector("#users-table tbody");
        if (!tbody) return;
        tbody.innerHTML = "";
        if (!(data.users || []).length) {
          tbody.innerHTML =
            "<tr><td colspan='10' class='sac-empty-cell'>No data available</td></tr>";
          return;
        }
        (data.users || []).forEach(function (u) {
          var tr = document.createElement("tr");
          var nameCell =
            esc(u.name) +
            (u.is_self ? ' <span class="sac-you-badge">You</span>' : "") +
            (u.role === "super_admin" ? ' <span class="sac-you-badge">Super</span>' : "");
          var blockLabel = u.status === "blocked" ? "Unblock" : "Block";
          var canMutate =
            !u.is_self && (!u.is_privileged || can("users_admins"));
          var linked =
            u.role === "hospital"
              ? u.hospital_name
                ? esc(u.hospital_name)
                : '<span class="sac-muted">Not linked</span>'
              : "—";
          tr.innerHTML =
            "<td>" +
            esc(u.id) +
            "</td><td>" +
            nameCell +
            "</td><td>" +
            esc(u.email) +
            "</td><td>" +
            esc(u.phone) +
            "</td><td>" +
            esc(roleLabel(u.role)) +
            "</td><td>" +
            linked +
            "</td><td>" +
            esc(u.status) +
            "</td><td>" +
            esc(fmtTime(u.created_at)) +
            "</td><td>" +
            esc(fmtTime(u.last_login)) +
            "</td>" +
            '<td class="actions-cell">' +
            '<button class="btn-sm" data-act="edit" data-id="' +
            u.id +
            '">Edit</button> ' +
            (canMutate
              ? '<button class="btn-sm" data-act="block" data-id="' +
                u.id +
                '">' +
                blockLabel +
                "</button> " +
                '<button class="btn-sm" data-act="delete" data-id="' +
                u.id +
                '">Delete</button> '
              : "") +
            '<button class="btn-sm" data-act="activity" data-id="' +
            u.id +
            '">History</button></td>';
          tbody.appendChild(tr);
        });
        bindUserActions(data.users || []);
      });
  }

  function bindUserActions(users) {
    document.querySelectorAll("#users-table [data-act]").forEach(function (btn) {
      btn.onclick = function () {
        var id = parseInt(btn.getAttribute("data-id"), 10);
        var act = btn.getAttribute("data-act");
        var u = users.find(function (x) {
          return x.id === id;
        });
        if (act === "block") {
          api("/api/admin/users/block", { method: "POST", body: JSON.stringify({ id: id }) }).then(
            function (r) {
              handleUserApiResult(r);
            }
          );
        } else if (act === "delete") {
          var label =
            u && (u.role === "admin" || u.role === "super_admin")
              ? "this " + roleLabel(u.role) + " account"
              : "this user";
          if (confirm("Delete " + label + " permanently?")) {
            api("/api/admin/users/delete", { method: "POST", body: JSON.stringify({ id: id }) }).then(
              function (r) {
                handleUserApiResult(r);
              }
            );
          }
        } else if (act === "activity" && u) {
          var html =
            (u.activity || [])
              .map(function (a) {
                return "<li>" + esc(a.action) + " — " + esc(fmtTime(a.timestamp)) + "</li>";
              })
              .join("") || "<li>No data available</li>";
          openModal("Activity: " + u.name, "<ul>" + html + "</ul>");
          $("modal-save").onclick = closeModal;
        } else if (act === "edit" && u) {
          var lockRole = u.is_self || (u.is_privileged && !can("users_admins"));
          var canStatus = !u.is_self && (!u.is_privileged || can("users_admins"));
          ensureHospitalsCache().then(function () {
            openModal(
              u.is_privileged ? "Edit " + roleLabel(u.role) : "Edit user",
              '<label class="sac-field"><span>Full name</span><input id="eu-name" value="' +
                esc(u.name) +
                '"></label>' +
                '<label class="sac-field"><span>Email</span><input id="eu-email" type="email" value="' +
                esc(u.email) +
                '"></label>' +
                '<label class="sac-field"><span>Phone</span><input id="eu-phone" value="' +
                esc(u.phone) +
                '"></label>' +
                '<label class="sac-field"><span>Role</span><select id="eu-role"' +
                (lockRole ? " disabled" : "") +
                ">" +
                roleOptionsHtml(u.role, { includePrivileged: can("users_admins") }) +
                "</select></label>" +
                hospitalFieldHtml(u.hospital_id || "", "eu-hospital") +
                '<label class="sac-field"><span>Status</span><select id="eu-status"' +
                (canStatus ? "" : " disabled") +
                '><option value="active">active</option><option value="blocked">blocked</option></select></label>' +
                '<label class="sac-field"><span>New password</span><input id="eu-pass" type="password" placeholder="Leave blank to keep current" autocomplete="new-password"></label>' +
                (u.is_self
                  ? '<p class="hint">You can update your profile details. Role and status for your own account are locked.</p>'
                  : "")
            );
            if ($("eu-status")) $("eu-status").value = u.status || "active";
            if ($("eu-role")) $("eu-role").value = u.role || "citizen";
            toggleHospitalField("eu-role", "eu-hospital-wrap");
            if ($("eu-role") && !$("eu-role").disabled) {
              $("eu-role").addEventListener("change", function () {
                toggleHospitalField("eu-role", "eu-hospital-wrap");
              });
            }
            $("modal-save").onclick = function () {
              var body = {
                id: id,
                name: $("eu-name").value,
                email: $("eu-email").value,
                phone: $("eu-phone").value,
              };
              if ($("eu-role") && !$("eu-role").disabled) body.role = $("eu-role").value;
              if ($("eu-status") && !$("eu-status").disabled) body.status = $("eu-status").value;
              var roleNow =
                (body.role || ($("eu-role") && $("eu-role").value) || u.role || "").toLowerCase();
              if (roleNow === "hospital") {
                body.hospital_id = ($("eu-hospital") && $("eu-hospital").value) || "";
                if (!body.hospital_id) {
                  alert("Select a hospital facility for this hospital account");
                  return;
                }
              }
              var pass = ($("eu-pass") && $("eu-pass").value) || "";
              if (pass) body.password = pass;
              api("/api/admin/users/edit", {
                method: "POST",
                body: JSON.stringify(body),
              }).then(function (r) {
                handleUserApiResult(r, "Account updated.");
              });
            };
          });
        }
      };
    });
  }

  function loadSettingsForm() {
    api("/api/admin/settings").then(function (s) {
      ["sos_enabled", "maintenance_mode", "sms_notifications"].forEach(function (k) {
        if ($(k)) $(k).checked = !!s[k];
      });
      ["ambulance_response_time", "police_response_time", "fire_response_time", "refresh_interval", "max_emergencies_per_day"].forEach(
        function (k) {
          if ($(k)) $(k).value = s[k];
        }
      );
    });
  }

  var systemConfigState = { settings: {}, groups: [], secretKeys: {} };

  function collectSystemConfigPayload() {
    var form = $("system-config-form");
    if (!form) return {};
    var payload = {};
    form.querySelectorAll("[data-sys-key]").forEach(function (el) {
      var key = el.getAttribute("data-sys-key");
      if (!key) return;
      var type = el.getAttribute("data-sys-type") || "text";
      if (type === "checkbox") {
        payload[key] = !!el.checked;
      } else if (type === "number") {
        var n = el.value === "" ? null : Number(el.value);
        if (n === null || Number.isNaN(n)) return;
        payload[key] = n;
      } else if (type === "password") {
        if (String(el.value || "").trim() !== "") payload[key] = el.value;
      } else {
        payload[key] = el.value;
      }
    });
    return payload;
  }

  function renderSystemConfigField(field, settings) {
    var key = field.key;
    var type = field.type || "text";
    var val = settings[key];
    var wrap = document.createElement("div");
    wrap.className = "sys-cfg-field" + (type === "textarea" ? " sys-cfg-full" : "");
    var id = "sys_" + key;
    var label = document.createElement("label");
    label.setAttribute("for", id);
    label.textContent = field.label || key;
    wrap.appendChild(label);

    if (type === "checkbox") {
      var tog = document.createElement("label");
      tog.className = "toggle-row";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.id = id;
      cb.setAttribute("data-sys-key", key);
      cb.setAttribute("data-sys-type", "checkbox");
      cb.checked = !!val;
      tog.appendChild(cb);
      tog.appendChild(document.createTextNode(" Enabled"));
      wrap.appendChild(tog);
    } else if (type === "select") {
      var sel = document.createElement("select");
      sel.id = id;
      sel.setAttribute("data-sys-key", key);
      sel.setAttribute("data-sys-type", "select");
      (field.options || []).forEach(function (opt) {
        var o = document.createElement("option");
        o.value = opt.value;
        o.textContent = opt.label;
        if (String(val) === String(opt.value)) o.selected = true;
        sel.appendChild(o);
      });
      wrap.appendChild(sel);
    } else if (type === "textarea") {
      var ta = document.createElement("textarea");
      ta.id = id;
      ta.setAttribute("data-sys-key", key);
      ta.setAttribute("data-sys-type", "textarea");
      ta.value = val == null ? "" : String(val);
      wrap.appendChild(ta);
    } else {
      var input = document.createElement("input");
      input.id = id;
      input.setAttribute("data-sys-key", key);
      input.setAttribute("data-sys-type", type);
      if (type === "number") {
        input.type = "number";
        if (field.min != null) input.min = field.min;
        if (field.max != null) input.max = field.max;
        if (field.step != null) input.step = field.step;
        input.value = val == null ? "" : val;
      } else if (type === "color") {
        input.type = "color";
        input.value = val || "#2563eb";
      } else if (type === "password") {
        input.type = "password";
        input.autocomplete = "new-password";
        input.placeholder = settings[key + "_set"] ? "•••••••• (leave blank to keep)" : "Enter secret";
        input.value = "";
      } else if (type === "email") {
        input.type = "email";
        input.value = val == null ? "" : String(val);
      } else if (type === "tel") {
        input.type = "tel";
        input.value = val == null ? "" : String(val);
      } else {
        input.type = "text";
        input.value = val == null ? "" : String(val);
      }

      if (key === "app_logo_url" || key === "app_favicon_url") {
        var row = document.createElement("div");
        row.className = "sys-cfg-upload-row";
        row.appendChild(input);
        var file = document.createElement("input");
        file.type = "file";
        file.accept = "image/*";
        file.setAttribute("aria-label", "Upload " + key);
        var kind = key === "app_logo_url" ? "logo" : "favicon";
        file.onchange = function () {
          if (!file.files || !file.files[0]) return;
          var fd = new FormData();
          fd.append("kind", kind);
          fd.append("file", file.files[0]);
          fetch("/api/admin/system-settings/upload", {
            method: "POST",
            body: fd,
            credentials: "same-origin",
          })
            .then(function (r) {
              return r.json();
            })
            .then(function (res) {
              if (!res || !res.success) {
                alert((res && res.message) || "Upload failed");
                return;
              }
              input.value = res.url || "";
              alert((kind === "logo" ? "Logo" : "Favicon") + " uploaded.");
              loadSystemConfig();
            });
        };
        row.appendChild(file);
        if (val) {
          var img = document.createElement("img");
          img.className = "sys-cfg-preview";
          img.src = String(val);
          img.alt = "";
          row.appendChild(img);
        }
        wrap.appendChild(row);
      } else {
        wrap.appendChild(input);
      }

      if (type === "password" && settings[key + "_set"]) {
        var hint = document.createElement("span");
        hint.className = "sys-cfg-secret-hint";
        hint.textContent = "A value is already stored.";
        wrap.appendChild(hint);
      }
    }

    if (field.hint) {
      var h = document.createElement("span");
      h.className = "sys-cfg-secret-hint";
      h.textContent = field.hint;
      wrap.appendChild(h);
    }
    return wrap;
  }

  function renderSystemConfig(data) {
    systemConfigState.settings = data.settings || {};
    systemConfigState.groups = data.groups || [];
    systemConfigState.secretKeys = {};
    (data.secret_keys || []).forEach(function (k) {
      systemConfigState.secretKeys[k] = true;
    });

    var dbInfo = $("sys-cfg-db-info");
    if (dbInfo) {
      var db = data.database || {};
      dbInfo.innerHTML =
        "<strong>Database:</strong> " +
        esc(db.backend || "—") +
        " &nbsp;·&nbsp; Settings are stored in the application database and applied at runtime.";
    }

    var toc = $("sys-cfg-toc");
    var host = $("sys-cfg-groups");
    if (!host) return;
    host.innerHTML = "";
    if (toc) toc.innerHTML = "";

    systemConfigState.groups.forEach(function (group) {
      if (toc) {
        var a = document.createElement("a");
        a.href = "#sys-group-" + group.id;
        a.textContent = group.title;
        toc.appendChild(a);
      }
      var card = document.createElement("div");
      card.className = "sys-cfg-group";
      card.id = "sys-group-" + group.id;
      var h3 = document.createElement("h3");
      h3.textContent = group.title;
      card.appendChild(h3);
      if (group.description) {
        var desc = document.createElement("p");
        desc.className = "hint";
        desc.textContent = group.description;
        card.appendChild(desc);
      }
      var fields = document.createElement("div");
      fields.className = "sys-cfg-fields";
      (group.fields || []).forEach(function (field) {
        fields.appendChild(renderSystemConfigField(field, systemConfigState.settings));
      });
      card.appendChild(fields);
      host.appendChild(card);
    });
  }

  function loadSystemConfig() {
    if (!can("settings_system")) return;
    api("/api/admin/system-settings").then(function (data) {
      if (!data || data.success === false) {
        alert((data && data.message) || "Could not load system settings");
        return;
      }
      renderSystemConfig(data);
    });
  }

  function saveSystemConfig() {
    if (!can("settings_system")) return;
    var payload = collectSystemConfigPayload();
    api("/api/admin/system-settings", {
      method: "POST",
      body: JSON.stringify({ settings: payload }),
    }).then(function (r) {
      if (!r || r.success === false) {
        alert((r && r.message) || "Could not save system settings");
        return;
      }
      alert("System settings saved (" + (r.updated ? r.updated.length : 0) + " fields).");
      loadSystemConfig();
      loadSettingsForm();
      loadAppearance();
    });
  }

  function loadEmergencies() {
    api("/api/admin/emergencies").then(function (data) {
      var tbody = document.querySelector("#emergencies-table tbody");
      if (!tbody) return;
      tbody.innerHTML = "";
      if (!(data.emergencies || []).length) {
        tbody.innerHTML =
          "<tr><td colspan='9' class='sac-empty-cell'>No data available</td></tr>";
        return;
      }
      (data.emergencies || []).forEach(function (em) {
        emergenciesCache[em.id] = em;
        var tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" +
          esc(em.id) +
          "</td><td>" +
          esc(em.type) +
          "</td><td>" +
          esc(em.location) +
          "</td><td>" +
          esc(em.caller_name) +
          "</td><td>" +
          esc(em.phone) +
          "</td><td>" +
          esc(fmtTime(em.timestamp)) +
          "</td>" +
          '<td><select data-id="' +
          em.id +
          '" class="em-status">' +
          ["pending", "dispatched", "in_progress", "completed", "cancelled", "resolved"]
            .map(function (s) {
              return '<option value="' + s + '"' + (em.status === s ? " selected" : "") + ">" + s + "</option>";
            })
            .join("") +
          "</select></td>" +
          '<td><select data-id="' +
          em.id +
          '" class="em-assign">' +
          ["hospital", "police", "fire"]
            .map(function (s) {
              return (
                '<option value="' +
                s +
                '"' +
                ((em.assigned_to || "") === s ? " selected" : "") +
                ">" +
                s +
                "</option>"
              );
            })
            .join("") +
          "</select></td>" +
          '<td class="actions-cell">' +
          '<button class="btn-sm" data-call="' +
          esc(em.phone || "") +
          '">Call</button> ' +
          '<button class="btn-sm btn-em-map" data-id="' +
          em.id +
          '">Map</button> ' +
          (can("emergencies_delete")
            ? '<button class="btn-sm em-del" data-id="' + em.id + '">Delete</button>'
            : "") +
          "</td>";
        tbody.appendChild(tr);
      });
      tbody.querySelectorAll(".em-status, .em-assign").forEach(function (sel) {
        sel.onchange = function () {
          var id = parseInt(sel.getAttribute("data-id"), 10);
          var row = sel.closest("tr");
          api("/api/admin/emergencies/update", {
            method: "POST",
            body: JSON.stringify({
              id: id,
              status: row.querySelector(".em-status").value,
              assigned_to: row.querySelector(".em-assign").value,
            }),
          });
        };
      });
      tbody.querySelectorAll("[data-call]").forEach(function (b) {
        b.onclick = function () {
          var p = b.getAttribute("data-call");
          if (p && p !== "Not provided") window.location.href = "tel:" + p.replace(/\s/g, "");
        };
      });
      tbody.querySelectorAll(".btn-em-map").forEach(function (b) {
        b.onclick = function () {
          var em = emergenciesCache[b.getAttribute("data-id")];
          if (em && window.EmergencyLocation) EmergencyLocation.showEmergencyOnMap(em);
        };
      });
      tbody.querySelectorAll(".em-del").forEach(function (b) {
        b.onclick = function () {
          if (confirm("Delete emergency?")) {
            api("/api/admin/emergencies/delete", {
              method: "POST",
              body: JSON.stringify({ id: parseInt(b.getAttribute("data-id"), 10) }),
            }).then(loadEmergencies);
          }
        };
      });
    });
  }

  function renderBarChart(containerId, dataObj) {
    var el = $(containerId);
    if (!el) return;
    el.innerHTML = "";
    var keys = Object.keys(dataObj || {});
    if (!keys.length) {
      el.innerHTML = emptyHtml("No data available");
      return;
    }
    var max = Math.max.apply(
      null,
      keys.map(function (k) {
        return dataObj[k];
      }).concat([1])
    );
    keys.forEach(function (label) {
      var row = document.createElement("div");
      row.className = "chart-row";
      var pct = Math.round((dataObj[label] / max) * 100);
      row.innerHTML =
        '<span class="chart-label">' +
        esc(label) +
        '</span><div class="chart-bar-wrap"><div class="chart-bar" style="width:' +
        pct +
        '%"></div></div><span class="chart-val">' +
        dataObj[label] +
        "</span>";
      el.appendChild(row);
    });
  }

  /* ---- Executive Analytics (Power BI style) ---- */
  var BI_TYPE_LABELS = {
    medical: "Medical",
    accident: "Accident",
    fire: "Fire",
    security: "Security",
    family_help: "Family Help",
    other: "Other",
  };
  var BI_COLORS = ["#3b82f6", "#f59e0b", "#ef4444", "#a855f7", "#22c55e", "#22d3ee", "#fb7185", "#94a3b8"];
  var biFiltersBound = false;
  var biLastPayload = null;

  function biChartOpts() {
    var tick = themeTickColor();
    var grid = themeGridColor();
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          labels: { color: tick, boxWidth: 10, font: { size: 11 } },
        },
      },
      scales: {
        x: { ticks: { color: tick, maxRotation: 0 }, grid: { color: grid } },
        y: { beginAtZero: true, ticks: { color: tick, precision: 0 }, grid: { color: grid } },
      },
    };
  }

  function biDestroy(id) {
    clearChart(id);
  }

  function biPrettyType(t) {
    return BI_TYPE_LABELS[t] || String(t || "Other").replace(/_/g, " ").replace(/^./, function (c) {
      return c.toUpperCase();
    });
  }

  function renderBiDonut(canvasId, obj, labelFn) {
    var el = prepareChartCanvas(canvasId);
    if (!el || !window.Chart) return;
    biDestroy(canvasId);
    var keys = Object.keys(obj || {});
    var values = keys.map(function (k) {
      return Number(obj[k]) || 0;
    });
    if (!keys.length || !hasNumericSeries(values)) {
      showChartEmpty(canvasId);
      return;
    }
    var labels = keys.map(function (k) {
      return labelFn ? labelFn(k) : k;
    });
    charts[canvasId] = new Chart(el, {
      type: "doughnut",
      data: {
        labels: labels,
        datasets: [
          {
            data: values,
            backgroundColor: keys.map(function (_, i) {
              return BI_COLORS[i % BI_COLORS.length];
            }),
            borderWidth: 0,
            hoverOffset: 6,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "68%",
        plugins: {
          legend: {
            position: "right",
            labels: { color: themeTickColor(), boxWidth: 10, font: { size: 11 } },
          },
        },
      },
    });
  }

  function renderBiLine(canvasId, labels, values) {
    var el = prepareChartCanvas(canvasId);
    if (!el || !window.Chart) return;
    biDestroy(canvasId);
    if (!hasNumericSeries(values)) {
      showChartEmpty(canvasId);
      return;
    }
    var opts = biChartOpts();
    charts[canvasId] = new Chart(el, {
      type: "line",
      data: {
        labels: labels || [],
        datasets: [
          {
            label: "Emergencies",
            data: (values || []).map(function (v) {
              return Number(v) || 0;
            }),
            borderColor: "#3b82f6",
            backgroundColor: "rgba(59,130,246,0.18)",
            fill: true,
            tension: 0.35,
            pointRadius: 3,
            pointBackgroundColor: "#60a5fa",
            borderWidth: 2.5,
          },
        ],
      },
      options: opts,
    });
  }

  function renderBiBar(canvasId, obj) {
    var el = prepareChartCanvas(canvasId);
    if (!el || !window.Chart) return;
    biDestroy(canvasId);
    var keys = Object.keys(obj || {});
    var values = keys.map(function (k) {
      return Number(obj[k]) || 0;
    });
    if (!keys.length || !hasNumericSeries(values)) {
      showChartEmpty(canvasId);
      return;
    }
    var opts = biChartOpts();
    opts.indexAxis = "y";
    opts.plugins.legend = { display: false };
    charts[canvasId] = new Chart(el, {
      type: "bar",
      data: {
        labels: keys,
        datasets: [
          {
            data: values,
            backgroundColor: "rgba(59,130,246,0.75)",
            borderRadius: 6,
            borderSkipped: false,
            maxBarThickness: 22,
          },
        ],
      },
      options: opts,
    });
  }

  function renderBiStacked(canvasId, stacked) {
    var el = prepareChartCanvas(canvasId);
    if (!el || !window.Chart) return;
    biDestroy(canvasId);
    var labels = (stacked && stacked.labels) || [];
    var a = (stacked && stacked.active) || [];
    var r = (stacked && stacked.resolved) || [];
    var o = (stacked && stacked.other) || [];
    if (!labels.length || (!hasNumericSeries(a) && !hasNumericSeries(r) && !hasNumericSeries(o))) {
      showChartEmpty(canvasId);
      return;
    }
    var opts = biChartOpts();
    opts.scales.x.stacked = true;
    opts.scales.y.stacked = true;
    charts[canvasId] = new Chart(el, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Active",
            data: a,
            backgroundColor: "#f59e0b",
            borderRadius: 3,
            maxBarThickness: 28,
          },
          {
            label: "Resolved",
            data: r,
            backgroundColor: "#22c55e",
            borderRadius: 3,
            maxBarThickness: 28,
          },
          {
            label: "Other",
            data: o,
            backgroundColor: "#64748b",
            borderRadius: 3,
            maxBarThickness: 28,
          },
        ],
      },
      options: opts,
    });
  }

  function fillBiSelect(selectId, values, allLabel, selected) {
    var sel = $(selectId);
    if (!sel) return;
    var keep = selected != null ? selected : sel.value;
    sel.innerHTML = '<option value="">' + esc(allLabel) + "</option>";
    (values || []).forEach(function (v) {
      var opt = document.createElement("option");
      opt.value = v;
      opt.textContent = selectId === "bi-filter-type" ? biPrettyType(v) : v;
      if (String(v) === String(keep)) opt.selected = true;
      sel.appendChild(opt);
    });
  }

  function renderBiTable(rows) {
    var tbody = document.querySelector("#bi-recent-table tbody");
    if (!tbody) return;
    tbody.innerHTML = "";
    if (!(rows || []).length) {
      tbody.innerHTML = "<tr><td colspan='5' class='sac-empty-cell'>No cases in this filter</td></tr>";
      return;
    }
    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>#" +
        esc(r.id) +
        "</td><td>" +
        esc(biPrettyType(r.type)) +
        "</td><td>" +
        esc(r.region || "—") +
        "</td><td><span class='bi-status'>" +
        esc(r.status || "—") +
        "</span></td><td>" +
        esc(fmtTime(r.timestamp)) +
        "</td>";
      tbody.appendChild(tr);
    });
  }

  function biQuery() {
    var days = ($("bi-filter-days") && $("bi-filter-days").value) || "7";
    var region = ($("bi-filter-region") && $("bi-filter-region").value) || "";
    var type = ($("bi-filter-type") && $("bi-filter-type").value) || "";
    var status = ($("bi-filter-status") && $("bi-filter-status").value) || "";
    return (
      "/api/admin/analytics?days=" +
      encodeURIComponent(days) +
      "&region=" +
      encodeURIComponent(region) +
      "&type=" +
      encodeURIComponent(type) +
      "&status=" +
      encodeURIComponent(status)
    );
  }

  function exportBiCsv(d) {
    if (!d) return;
    var k = d.kpis || {};
    var lines = [
      "GurmadNet AI — Executive Analytics",
      "Range," + ((d.filters && d.filters.range_start) || "") + " - " + ((d.filters && d.filters.range_end) || ""),
      "Updated," + (d.updated_at || ""),
      "",
      "KPI,Value",
      "Total," + (k.total || 0),
      "Active," + (k.active || 0),
      "Resolved," + (k.resolved || 0),
      "Resolution Rate %," + (k.resolution_rate || 0),
      "Avg Response," + (k.avg_response_display || "—"),
      "",
      "Type,Count",
    ];
    Object.keys(d.by_type || {}).forEach(function (t) {
      lines.push(biPrettyType(t) + "," + d.by_type[t]);
    });
    lines.push("");
    lines.push("Region,Count");
    Object.keys(d.by_region || {}).forEach(function (r) {
      lines.push('"' + String(r).replace(/"/g, '""') + '",' + d.by_region[r]);
    });
    var blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = "gurmadnet-executive-analytics.csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function bindBiFilters() {
    if (biFiltersBound) return;
    biFiltersBound = true;
    if ($("bi-apply")) $("bi-apply").onclick = function () {
      loadReports();
    };
    if ($("bi-reset")) {
      $("bi-reset").onclick = function () {
        if ($("bi-filter-days")) $("bi-filter-days").value = "7";
        if ($("bi-filter-region")) $("bi-filter-region").value = "";
        if ($("bi-filter-type")) $("bi-filter-type").value = "";
        if ($("bi-filter-status")) $("bi-filter-status").value = "";
        loadReports();
      };
    }
    if ($("bi-refresh")) $("bi-refresh").onclick = function () {
      loadReports();
    };
    if ($("bi-export")) {
      $("bi-export").onclick = function () {
        exportBiCsv(biLastPayload);
      };
    }
    ["bi-filter-days", "bi-filter-region", "bi-filter-type", "bi-filter-status"].forEach(function (id) {
      if ($(id)) {
        $(id).addEventListener("change", function () {
          loadReports();
        });
      }
    });
  }

  function loadReports() {
    bindBiFilters();
    api(biQuery()).then(function (d) {
      if (!d || d.success === false) return;
      biLastPayload = d;
      var k = d.kpis || {};
      var f = d.filters || {};
      var opts = d.filter_options || {};

      fillBiSelect("bi-filter-region", opts.regions || [], "All regions", f.region || "");
      fillBiSelect("bi-filter-type", opts.types || [], "All types", f.type || "");
      // Keep status select options stable; only sync value
      if ($("bi-filter-status") && f.status != null) $("bi-filter-status").value = f.status || "";
      if ($("bi-filter-days") && f.days) $("bi-filter-days").value = String(f.days);

      setText("bi-updated", "Last updated: " + (d.updated_at || "—"));
      setText("bi-range", (f.range_start || "—") + " → " + (f.range_end || "—"));
      setText("bi-kpi-total", k.total != null ? k.total : 0);
      setText("bi-kpi-active", k.active != null ? k.active : 0);
      setText("bi-kpi-resolved", k.resolved != null ? k.resolved : 0);
      setText("bi-kpi-rate", "Resolution rate " + (k.resolution_rate != null ? k.resolution_rate : 0) + "%");
      setText("bi-kpi-avg", k.avg_response_display || "—");
      setText(
        "bi-kpi-hospitals",
        (k.hospitals_online != null ? k.hospitals_online : 0) +
          " / " +
          (k.hospitals_total != null ? k.hospitals_total : 0)
      );
      setText(
        "bi-kpi-ambulances",
        (k.ambulances_available != null ? k.ambulances_available : 0) +
          " / " +
          (k.ambulances_total != null ? k.ambulances_total : 0)
      );
      setText("bi-kpi-total-sub", "Filtered period · " + (f.days || 7) + " days");

      renderBiDonut("bi-chart-donut", d.by_type || {}, biPrettyType);
      renderBiLine("bi-chart-line", (d.trend && d.trend.labels) || [], (d.trend && d.trend.values) || []);
      renderBiBar("bi-chart-bar", d.by_region || {});
      renderBiStacked("bi-chart-stacked", d.stacked_status || {});
      renderBiDonut("bi-chart-status", d.by_status || {}, function (s) {
        return String(s || "").replace(/_/g, " ");
      });
      renderBiTable(d.recent || []);
    });
  }

  function loadAppearance() {
    api("/api/admin/settings").then(function (s) {
      if ($("color_hospital")) $("color_hospital").value = s.color_hospital || "#2E7D32";
      if ($("color_police")) $("color_police").value = s.color_police || "#1565C0";
      if ($("color_fire")) $("color_fire").value = s.color_fire || "#C62828";
      if ($("dark_mode")) $("dark_mode").checked = !!s.dark_mode;
    });
  }

  function loadAudit() {
    api("/api/admin/audit").then(function (d) {
      var tbody = document.querySelector("#audit-table tbody");
      if (!tbody) return;
      tbody.innerHTML = "";
      if (!(d.entries || []).length) {
        tbody.innerHTML =
          "<tr><td colspan='5' class='sac-empty-cell'>No data available</td></tr>";
        return;
      }
      (d.entries || []).forEach(function (r) {
        var tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" +
          esc(r.administrator) +
          "</td><td>" +
          esc(r.action) +
          "</td><td>" +
          esc(r.entity_type) +
          " #" +
          esc(r.entity_id) +
          "</td><td>" +
          esc(fmtTime(r.timestamp)) +
          "</td><td>" +
          esc(r.ip && r.ip !== "—" ? r.ip : "—") +
          "</td>";
        tbody.appendChild(tr);
      });
    });
  }

  function openModal(title, body) {
    $("modal-title").textContent = title;
    $("modal-body").innerHTML = body;
    $("modal").classList.remove("hidden");
  }
  function closeModal() {
    $("modal").classList.add("hidden");
  }

  /* ---- My Profile (self-only) ---- */
  var myProfilePhoto = "";

  function setAdminAvatarEl(el, photo, fallbackName) {
    if (!el) return;
    el.textContent = "";
    if (photo && String(photo).indexOf("data:image/") === 0) {
      var img = document.createElement("img");
      img.src = photo;
      img.alt = "";
      el.appendChild(img);
    } else {
      el.textContent = String(fallbackName || "A").charAt(0).toUpperCase();
    }
  }

  function showProfileMsg(id, text, ok) {
    var el = $(id);
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || "";
    el.classList.toggle("is-error", !ok);
    el.classList.toggle("is-ok", !!ok);
  }

  function applyProfileToChrome(p) {
    if (!p) return;
    setAdminAvatarEl($("sac-top-avatar"), p.profile_photo, p.name);
    setAdminAvatarEl($("ap-avatar"), p.profile_photo, p.name);
    if ($("sac-top-name")) $("sac-top-name").textContent = p.name || "";
    if ($("ap-display-name")) $("ap-display-name").textContent = p.name || "";
    if ($("ap-display-email")) $("ap-display-email").textContent = p.email || "";
    if ($("ap-role-chip")) $("ap-role-chip").textContent = p.role_label || "Administrator";
    if ($("sac-profile-role")) $("sac-profile-role").textContent = p.role_label || "Administrator";
    myProfilePhoto = p.profile_photo || "";
    if ($("ap-photo-remove")) $("ap-photo-remove").hidden = !myProfilePhoto;
  }

  function loadMyProfile() {
    api("/api/admin/profile").then(function (d) {
      if (!d.success || !d.profile) {
        showProfileMsg("ap-info-msg", (d && d.message) || "Could not load profile", false);
        return;
      }
      var p = d.profile;
      applyProfileToChrome(p);
      if ($("ap-name")) $("ap-name").value = p.name || "";
      if ($("ap-email")) $("ap-email").value = p.email || "";
      if ($("ap-phone")) $("ap-phone").value = p.phone || "";
      if ($("ap-status")) $("ap-status").textContent = (p.status || "active").replace(/^./, function (c) {
        return c.toUpperCase();
      });
      if ($("ap-last-login")) $("ap-last-login").textContent = p.last_login || "—";
      if ($("ap-created")) $("ap-created").textContent = p.created_at || "—";
      var s = p.settings || {};
      if ($("ap-notify-sos")) $("ap-notify-sos").checked = s.notify_email_on_sos !== false;
      if ($("ap-notify-dispatch")) $("ap-notify-dispatch").checked = s.notify_email_on_dispatch !== false;
    });
  }

  function saveMyProfile(payload, msgId) {
    return api("/api/admin/profile", { method: "PUT", body: JSON.stringify(payload) }).then(function (d) {
      if (!d.success) {
        showProfileMsg(msgId, (d && d.message) || "Could not save", false);
        return d;
      }
      if (d.profile) applyProfileToChrome(d.profile);
      showProfileMsg(msgId, "Saved.", true);
      return d;
    });
  }

  function bindMyProfileForms() {
    if ($("ap-info-form")) {
      $("ap-info-form").onsubmit = function (e) {
        e.preventDefault();
        saveMyProfile(
          {
            name: ($("ap-name") && $("ap-name").value) || "",
            email: ($("ap-email") && $("ap-email").value) || "",
            phone: ($("ap-phone") && $("ap-phone").value) || "",
            profile_photo: myProfilePhoto,
          },
          "ap-info-msg"
        );
      };
    }
    if ($("ap-password-form")) {
      $("ap-password-form").onsubmit = function (e) {
        e.preventDefault();
        var np = ($("ap-new-password") && $("ap-new-password").value) || "";
        var cp = ($("ap-confirm-password") && $("ap-confirm-password").value) || "";
        if (np !== cp) {
          showProfileMsg("ap-password-msg", "New passwords do not match.", false);
          return;
        }
        saveMyProfile(
          {
            current_password: ($("ap-current-password") && $("ap-current-password").value) || "",
            new_password: np,
            confirm_password: cp,
          },
          "ap-password-msg"
        ).then(function (d) {
          if (d && d.success) {
            if ($("ap-current-password")) $("ap-current-password").value = "";
            if ($("ap-new-password")) $("ap-new-password").value = "";
            if ($("ap-confirm-password")) $("ap-confirm-password").value = "";
          }
        });
      };
    }
    if ($("ap-settings-form")) {
      $("ap-settings-form").onsubmit = function (e) {
        e.preventDefault();
        saveMyProfile(
          {
            settings: {
              notify_email_on_sos: !!( $("ap-notify-sos") && $("ap-notify-sos").checked ),
              notify_email_on_dispatch: !!( $("ap-notify-dispatch") && $("ap-notify-dispatch").checked ),
            },
          },
          "ap-settings-msg"
        );
      };
    }
    if ($("ap-photo-input")) {
      $("ap-photo-input").onchange = function (e) {
        var file = e.target.files && e.target.files[0];
        if (!file) return;
        if (file.size > 90000) {
          showProfileMsg("ap-info-msg", "Photo too large. Use a smaller image (under ~90KB).", false);
          e.target.value = "";
          return;
        }
        var reader = new FileReader();
        reader.onload = function () {
          var dataUrl = String(reader.result || "");
          if (dataUrl.indexOf("data:image/") !== 0) {
            showProfileMsg("ap-info-msg", "Please choose an image file.", false);
            return;
          }
          if (dataUrl.length > 120000) {
            showProfileMsg("ap-info-msg", "Photo too large after encoding.", false);
            return;
          }
          myProfilePhoto = dataUrl;
          setAdminAvatarEl($("ap-avatar"), myProfilePhoto, ($("ap-name") && $("ap-name").value) || "A");
          if ($("ap-photo-remove")) $("ap-photo-remove").hidden = false;
          saveMyProfile({ profile_photo: myProfilePhoto }, "ap-info-msg");
        };
        reader.readAsDataURL(file);
      };
    }
    if ($("ap-photo-remove")) {
      $("ap-photo-remove").onclick = function () {
        myProfilePhoto = "";
        setAdminAvatarEl($("ap-avatar"), "", ($("ap-name") && $("ap-name").value) || "A");
        $("ap-photo-remove").hidden = true;
        saveMyProfile({ profile_photo: "" }, "ap-info-msg");
      };
    }
  }

  function bindForms() {
    bindMyProfileForms();
    if ($("modal-cancel")) $("modal-cancel").onclick = closeModal;

    if ($("user-search")) $("user-search").addEventListener("input", loadUsers);
    if ($("user-role-filter")) $("user-role-filter").addEventListener("change", loadUsers);

    if ($("btn-add-user")) {
      $("btn-add-user").onclick = function () {
        openAddCitizenModal();
      };
    }
    if ($("btn-create-admin")) {
      $("btn-create-admin").onclick = function () {
        openCreateStaffModal("admin");
      };
    }

    if ($("system-config-form")) {
      $("system-config-form").onsubmit = function (e) {
        e.preventDefault();
        saveSystemConfig();
      };
    }
    if ($("sys-cfg-save-all")) {
      $("sys-cfg-save-all").onclick = function () {
        saveSystemConfig();
      };
    }
    if ($("sys-cfg-reload")) {
      $("sys-cfg-reload").onclick = function () {
        loadSystemConfig();
      };
    }

    ["btn-map-locate", "btn-overview-locate"].forEach(function (id) {
      if ($(id)) $(id).onclick = locateAdminGps;
    });
    ["btn-map-recenter", "btn-overview-recenter"].forEach(function (id) {
      if ($(id)) $(id).onclick = recenterMaps;
    });
    if ($("btn-map-refresh")) {
      $("btn-map-refresh").onclick = function () {
        loadCommandCenter(true);
      };
    }
    document.querySelectorAll("[data-map-filter]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var kind = btn.getAttribute("data-map-filter") || "all";
        // Tap same filter again → show all
        if (kind === mapFilterKind && kind !== "all") {
          applyMapFilter("all", true);
        } else {
          applyMapFilter(kind, true);
        }
      });
    });

    if ($("settings-form")) {
      $("settings-form").onsubmit = function (e) {
        e.preventDefault();
        var payload = {};
        if ($("sms_notifications")) payload.sms_notifications = $("sms_notifications").checked;
        if ($("ambulance_response_time")) {
          payload.ambulance_response_time = parseInt($("ambulance_response_time").value, 10);
        }
        if ($("police_response_time")) {
          payload.police_response_time = parseInt($("police_response_time").value, 10);
        }
        if ($("fire_response_time")) {
          payload.fire_response_time = parseInt($("fire_response_time").value, 10);
        }
        if ($("refresh_interval")) {
          payload.refresh_interval = parseInt($("refresh_interval").value, 10);
        }
        if (can("settings_system")) {
          if ($("sos_enabled")) payload.sos_enabled = $("sos_enabled").checked;
          if ($("maintenance_mode")) payload.maintenance_mode = $("maintenance_mode").checked;
          if ($("max_emergencies_per_day")) {
            payload.max_emergencies_per_day = parseInt($("max_emergencies_per_day").value, 10);
          }
        }
        api("/api/admin/settings/update", {
          method: "POST",
          body: JSON.stringify(payload),
        }).then(function (r) {
          if (!r || r.success === false) {
            alert((r && r.message) || "Could not save settings");
            return;
          }
          if (r.rejected_keys && r.rejected_keys.length) {
            alert("Saved allowed settings. Super Admin required for: " + r.rejected_keys.join(", "));
          } else {
            alert("Settings saved.");
          }
        });
      };
    }

    if ($("appearance-form")) {
      $("appearance-form").onsubmit = function (e) {
        e.preventDefault();
        api("/api/admin/settings/update", {
          method: "POST",
          body: JSON.stringify({
            color_hospital: $("color_hospital").value,
            color_police: $("color_police").value,
            color_fire: $("color_fire").value,
            dark_mode: $("dark_mode").checked,
          }),
        }).then(function () {
          alert("Appearance saved.");
        });
      };
    }

    if ($("cc-settings-form")) {
      $("cc-settings-form").onsubmit = function (e) {
        e.preventDefault();
        api("/api/admin/call-center/settings", {
          method: "POST",
          body: JSON.stringify({
            enabled: $("cc_enabled").checked,
            phone_primary: $("cc_phone_primary").value,
            phone_secondary: $("cc_phone_secondary").value,
            priority_medical: parseInt($("cc_pri_medical").value, 10),
            priority_fire: parseInt($("cc_pri_fire").value, 10),
            priority_police: parseInt($("cc_pri_police").value, 10),
          }),
        }).then(function () {
          alert("Call Center settings saved.");
          loadCallCenter();
        });
      };
    }

    if ($("btn-backup-now")) {
      $("btn-backup-now").onclick = function () {
        api("/api/admin/backup", { method: "POST", body: "{}" }).then(function (r) {
          alert(r.success ? "Backup created:\n" + r.backup_path : "Backup failed");
          loadAudit();
        });
      };
    }

    document.querySelectorAll("[data-shortcut]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (btn.classList.contains("sac-no-access") || btn.hasAttribute("hidden")) return;
        var action = btn.getAttribute("data-shortcut");
        var need = SHORTCUT_PERMS[action];
        if (need && !can(need)) return;
        if (action === "add-citizen") {
          showSection("users");
          if ($("user-role-filter")) $("user-role-filter").value = "citizen";
          openAddCitizenModal();
        } else if (action === "create-admin") {
          showSection("users");
          loadUsers();
          openCreateStaffModal("admin");
        } else if (action === "add-hospital") {
          showSection("users");
          if ($("user-role-filter")) $("user-role-filter").value = "hospital";
          openCreateStaffModal("hospital");
        } else if (action === "dispatch") {
          showSection("emergencies");
        } else if (action === "report") {
          showSection("reports");
        } else if (action === "backup") {
          showSection("backup");
        } else if (action === "settings") {
          showSection("settings");
        } else if (action === "audit") {
          showSection("audit");
        } else if (action === "ai") {
          showSection("ai");
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    applyPermissions();
    bindNav();
    bindForms();
    showSection("overview");
    loadCommandCenter();
    refreshTimer = setInterval(function () {
      var active = document.querySelector(".sac-section.active");
      if (!active) return;
      // Keep live GPS pins fresh without resetting zoom (renderMap preserves view)
      if (active.id === "sec-overview" || active.id === "sec-map") {
        loadCommandCenter(active.id === "sec-map");
      }
    }, 10000);
  });
})();
