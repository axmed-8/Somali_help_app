/**
 * Police Command Center — station-scoped incident desk.
 */
(function () {
  "use strict";

  var boot = window.POLICE_BOOT || {};
  var state = {
    station: boot.station || {},
    stationLinked: !!boot.stationLinked,
    emergencies: [],
    history: [],
    notifications: [],
    refreshMs: boot.refreshMs || 5000,
    map: null,
    layer: null,
    mapFitted: false,
    selectedId: null,
    chatCaseId: null,
    chatFilter: "all",
    chatSearch: "",
    chatMeta: {},
    chatPoll: null,
    timer: null,
  };

  var ACTIVE = {
    pending: 1,
    accepted: 1,
    dispatched: 1,
    in_progress: 1,
  };

  function $(id) {
    return document.getElementById(id);
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function api(url, opts) {
    opts = opts || {};
    var headers = { Accept: "application/json" };
    if (opts.body) headers["Content-Type"] = "application/json";
    return fetch(url, {
      method: opts.method || "GET",
      credentials: "same-origin",
      headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    }).then(function (r) {
      return r.json().then(function (d) {
        d = d || {};
        d._http = r.status;
        return d;
      });
    });
  }

  function fmtTime(iso) {
    try {
      return new Date(iso).toLocaleString("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
        day: "numeric",
        month: "short",
      });
    } catch (e) {
      return iso || "—";
    }
  }

  function formatTime(iso) {
    return fmtTime(iso);
  }

  function typeLabel(t) {
    return String(t || "security").replace(/_/g, " ");
  }

  function isMine(em) {
    var sid = state.station && state.station.id;
    if (!sid || !em) return false;
    return Number(em.assigned_station_id) === Number(sid);
  }

  function tickClock() {
    var el = $("hcc-clock");
    if (el) el.textContent = new Date().toLocaleTimeString();
  }

  /* ---------- Map ---------- */
  function initMap() {
    var el = $("pcc-map");
    if (!el || state.map || typeof L === "undefined") return;
    var lat = state.station.latitude != null ? Number(state.station.latitude) : 2.0469;
    var lng = state.station.longitude != null ? Number(state.station.longitude) : 45.3182;
    state.map = L.map(el, { zoomControl: true, scrollWheelZoom: true }).setView([lat, lng], 12);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap",
      maxZoom: 19,
    }).addTo(state.map);
    state.layer = L.layerGroup().addTo(state.map);
    setTimeout(function () {
      if (state.map) state.map.invalidateSize();
    }, 200);
  }

  function renderMap(fit) {
    if (!state.map || !state.layer) return;
    state.layer.clearLayers();
    var bounds = [];
    if (state.station.latitude != null && state.station.longitude != null) {
      var sLat = Number(state.station.latitude);
      var sLng = Number(state.station.longitude);
      L.circleMarker([sLat, sLng], {
        radius: 9,
        color: "#1d4ed8",
        fillColor: "#3b82f6",
        fillOpacity: 0.9,
        weight: 2,
      })
        .bindPopup("<strong>" + esc(state.station.name || "Station") + "</strong>")
        .addTo(state.layer);
      bounds.push([sLat, sLng]);
    }
    state.emergencies.forEach(function (em) {
      if (em.latitude == null || em.longitude == null) return;
      var lat = Number(em.latitude);
      var lng = Number(em.longitude);
      var color = isMine(em) ? "#22c55e" : "#ef4444";
      L.circleMarker([lat, lng], {
        radius: 8,
        color: color,
        fillColor: color,
        fillOpacity: 0.85,
        weight: 2,
      })
        .bindPopup(
          "<strong>#" +
            em.id +
            " · " +
            esc(typeLabel(em.type)) +
            "</strong><br>" +
            esc(em.location || em.district || "") +
            "<br>" +
            esc(em.status || "")
        )
        .addTo(state.layer);
      bounds.push([lat, lng]);
    });
    if (fit && bounds.length) {
      try {
        state.map.fitBounds(bounds, { padding: [28, 28], maxZoom: 14 });
        state.mapFitted = true;
      } catch (e) {}
    }
  }

  /* ---------- Cards ---------- */
  function isCallCenterCase(em) {
    return !!(em && (em.source === "call_center" || em.request_mode === "call_center"));
  }

  function cardHtml(em, compact) {
    var mine = isMine(em);
    var pending = (em.status || "") === "pending";
    var actions = "";
    if (pending) {
      actions +=
        '<button type="button" class="hcc-btn hcc-btn-primary hcc-btn-sm q-accept" data-id="' +
        em.id +
        '">Accept</button>';
    }
    if (mine && (em.status === "accepted" || em.status === "pending")) {
      actions +=
        '<button type="button" class="hcc-btn hcc-btn-primary hcc-btn-sm q-dispatch" data-id="' +
        em.id +
        '">Dispatch</button>';
    }
    if (mine && ACTIVE[em.status]) {
      if (em.status !== "pending") {
        actions +=
          '<button type="button" class="hcc-btn hcc-btn-ghost hcc-btn-sm q-complete" data-id="' +
          em.id +
          '">Complete</button>';
      }
      // Soft-assigned pending cases must be releasable so the queue cannot dead-lock
      actions +=
        '<button type="button" class="hcc-btn hcc-btn-ghost hcc-btn-sm q-release" data-id="' +
        em.id +
        '">Release</button>';
      if (em.status !== "pending") {
        actions +=
          '<button type="button" class="hcc-btn hcc-btn-ghost hcc-btn-sm q-arrived" data-id="' +
          em.id +
          '">Arrived</button>';
        actions +=
          '<button type="button" class="hcc-btn hcc-btn-ghost hcc-btn-sm q-chat" data-id="' +
          em.id +
          '">Chat</button>';
      }
    }
    if (em.phone) {
      actions +=
        '<button type="button" class="hcc-btn hcc-btn-ghost hcc-btn-sm q-call" data-phone="' +
        esc(em.phone) +
        '">Call</button>';
    }
    actions +=
      '<button type="button" class="hcc-btn hcc-btn-ghost hcc-btn-sm q-open" data-id="' +
      em.id +
      '">Details</button>';

    var gps =
      em.latitude != null && em.longitude != null
        ? Number(em.latitude).toFixed(4) + ", " + Number(em.longitude).toFixed(4)
        : "No GPS";
    var notes = (em.notes || "").trim();
    var cc = isCallCenterCase(em);

    return (
      '<article class="hcc-q-card' +
      (mine ? " pcc-mine" : "") +
      (cc ? " hcc-q-cc" : "") +
      '" data-id="' +
      em.id +
      '">' +
      '<div class="hcc-q-top">' +
      '<span class="pcc-type-pill ' +
      esc(em.type || "") +
      '">' +
      esc(typeLabel(em.type)) +
      "</span>" +
      (cc ? '<span class="hcc-badge hcc-badge-cc">Call Center</span>' : "") +
      "<span>" +
      esc(em.status || "") +
      "</span>" +
      "</div>" +
      '<div class="hcc-q-loc">#' +
      em.id +
      " · " +
      esc(em.location || em.district || "Unknown location") +
      "</div>" +
      '<div class="hcc-q-meta">' +
      "Caller: " +
      esc(em.caller_name || "Unknown") +
      " · " +
      esc(em.phone || "N/A") +
      "<br>GPS: " +
      esc(gps) +
      (em.tracking_active ? " · live tracking" : "") +
      "<br>" +
      fmtTime(em.timestamp) +
      (mine ? " · Your station" : " · Open for claim") +
      (notes ? "<br>Notes: " + esc(notes.slice(0, 120)) : "") +
      "</div>" +
      (compact ? "" : '<div class="hcc-q-actions">' + actions + "</div>") +
      "</article>"
    );
  }

  function bindQueueActions(root) {
    if (!root) return;
    root.querySelectorAll(".q-accept").forEach(function (b) {
      b.onclick = function () {
        mutate(b.getAttribute("data-id"), "accept");
      };
    });
    root.querySelectorAll(".q-dispatch").forEach(function (b) {
      b.onclick = function () {
        mutate(b.getAttribute("data-id"), "dispatch");
      };
    });
    root.querySelectorAll(".q-complete").forEach(function (b) {
      b.onclick = function () {
        mutate(b.getAttribute("data-id"), "complete");
      };
    });
    root.querySelectorAll(".q-release").forEach(function (b) {
      b.onclick = function () {
        if (!confirm("Release this case back to the open police queue?")) return;
        mutate(b.getAttribute("data-id"), "release");
      };
    });
    root.querySelectorAll(".q-open").forEach(function (b) {
      b.onclick = function () {
        openDetail(parseInt(b.getAttribute("data-id"), 10));
      };
    });
    root.querySelectorAll(".q-call").forEach(function (b) {
      b.onclick = function () {
        var phone = b.getAttribute("data-phone");
        if (phone) window.location.href = "tel:" + phone;
      };
    });
    root.querySelectorAll(".q-chat").forEach(function (b) {
      b.onclick = function () {
        openCaseChat(parseInt(b.getAttribute("data-id"), 10));
      };
    });
    root.querySelectorAll(".q-arrived").forEach(function (b) {
      b.onclick = function () {
        api("/api/emergencies/" + b.getAttribute("data-id") + "/responder", {
          method: "POST",
          body: { action: "arrived_at_scene" },
        }).then(loadAll);
      };
    });
  }

  function emptyHtml(title, sub) {
    return (
      '<div class="pcc-empty"><strong>' +
      esc(title) +
      "</strong><p>" +
      esc(sub) +
      "</p></div>"
    );
  }

  function syncStationChips() {
    var s = state.station || {};
    if ($("chip-station-name")) $("chip-station-name").textContent = s.name || "—";
    if ($("chip-station-area")) {
      $("chip-station-area").textContent = s.district || s.city || "—";
    }
    if ($("chip-station-phone")) $("chip-station-phone").textContent = s.phone || "—";
    if ($("chip-station-status")) {
      var st = String(s.operating_status || "open");
      $("chip-station-status").textContent = st.charAt(0).toUpperCase() + st.slice(1);
    }
  }

  function syncDutyBanner() {
    var banner = $("pcc-duty-banner");
    var title = $("pcc-duty-title");
    var sub = $("pcc-duty-sub");
    var meta = $("pcc-duty-meta");
    var n = state.emergencies.length;
    var pending = state.emergencies.filter(function (e) {
      return e.status === "pending";
    }).length;
    if (!banner) return;
    if (n > 0) {
      banner.classList.add("is-alert");
      if (title) {
        title.textContent =
          pending > 0
            ? pending + " incident" + (pending === 1 ? "" : "s") + " need accept"
            : n + " active incident" + (n === 1 ? "" : "s") + " on desk";
      }
      if (sub) {
        sub.textContent = "Accept → Dispatch → Complete. Cases stay on your station until released.";
      }
      if (meta) meta.textContent = "ACTIVE · " + n;
    } else {
      banner.classList.remove("is-alert");
      if (title) title.textContent = "Desk clear — standing by";
      if (sub) {
        sub.textContent = "Security & accident alerts for " + (state.station.name || "your station");
      }
      if (meta) meta.textContent = "WATCH · LIVE";
    }
  }

  function renderQueue() {
    var list = state.emergencies;
    var ccList = list.filter(isCallCenterCase);
    var otherList = list.filter(function (e) {
      return !isCallCenterCase(e);
    });
    var pending = list.filter(function (e) {
      return (e.status || "") === "pending";
    }).length;
    var pill = $("queue-high-pill");
    if (pill) pill.textContent = pending + " pending";
    var badge = $("nav-queue-badge");
    if (badge) {
      badge.hidden = list.length === 0;
      badge.textContent = String(list.length);
    }
    var ccPill = $("cc-inbox-pill");
    if (ccPill) ccPill.textContent = String(ccList.length);

    function fill(id, items, emptyTitle, emptySub) {
      var el = $(id);
      if (!el) return;
      if (!items.length) {
        el.innerHTML = emptyHtml(emptyTitle, emptySub);
        return;
      }
      el.innerHTML = items
        .map(function (em) {
          return cardHtml(em, false);
        })
        .join("");
      bindQueueActions(el);
    }

    fill(
      "cc-inbox-list",
      ccList,
      "No Call Center cases",
      "When Call Center alerts or dispatches to police, cases appear here."
    );
    fill(
      "queue-list",
      otherList.slice(0, 8),
      "Queue is clear",
      "New security or accident SOS will appear here automatically."
    );
    fill(
      "queue-full-list",
      list,
      "No active police incidents",
      "When a citizen or call center routes a case to police, it lands in this queue."
    );
    syncDutyBanner();
  }

  function renderHistory() {
    var el = $("history-list");
    if (!el) return;
    if (!state.history.length) {
      el.innerHTML = emptyHtml(
        "No history yet",
        "Completed and cancelled police cases will show here."
      );
      return;
    }
    el.innerHTML = state.history
      .slice(0, 40)
      .map(function (em) {
        return (
          '<article class="hcc-q-card">' +
          '<div class="hcc-q-loc">#' +
          em.id +
          " · " +
          esc(typeLabel(em.type)) +
          "</div>" +
          '<div class="hcc-q-meta">' +
          esc(em.location || "") +
          " · " +
          esc(em.status) +
          " · " +
          fmtTime(em.timestamp) +
          "</div>" +
          "</article>"
        );
      })
      .join("");
  }

  function renderKpis(avg) {
    var list = state.emergencies;
    var pending = list.filter(function (e) {
      return e.status === "pending";
    }).length;
    var dispatched = list.filter(function (e) {
      return e.status === "dispatched" || e.status === "in_progress";
    }).length;
    var today = new Date().toDateString();
    var doneToday = state.history.filter(function (e) {
      try {
        return new Date(e.timestamp).toDateString() === today;
      } catch (err) {
        return false;
      }
    }).length;
    if ($("kpi-active")) $("kpi-active").textContent = String(list.length);
    if ($("kpi-pending")) $("kpi-pending").textContent = String(pending);
    if ($("kpi-dispatched")) $("kpi-dispatched").textContent = String(dispatched);
    if ($("kpi-done")) $("kpi-done").textContent = String(doneToday);
    if ($("kpi-avg")) $("kpi-avg").textContent = avg != null ? avg + "m" : "—";
  }

  function renderNotifs() {
    var unread = state.notifications.filter(function (n) {
      return !n.read;
    }).length;
    if ($("notif-count")) $("notif-count").textContent = String(unread);
    var badge = $("nav-notif-badge");
    if (badge) {
      badge.hidden = unread === 0;
      badge.textContent = String(unread);
    }
    function fill(id, items) {
      var el = $(id);
      if (!el) return;
      if (!items.length) {
        el.innerHTML = emptyHtml("Inbox clear", "Dispatch alerts and case updates appear here.");
        return;
      }
      el.innerHTML = items
        .slice(0, 20)
        .map(function (n) {
          return (
            '<div class="hcc-notif-item' +
            (n.read ? "" : " unread") +
            '">' +
            "<p>" +
            esc(n.message || "") +
            "</p>" +
            "<small>" +
            fmtTime(n.created_at || n.timestamp) +
            "</small>" +
            "</div>"
          );
        })
        .join("");
    }
    fill("live-notif-list", state.notifications);
    fill("notif-full-list", state.notifications);
  }

  /* ---------- Actions ---------- */
  function mutate(eid, action) {
    api("/api/police/request/" + eid + "/" + action, { method: "POST", body: {} }).then(function (r) {
      if (r && r.success === false) {
        alert(r.message || "Action failed");
        return;
      }
      closeDetail();
      if (action === "accept") {
        afterAcceptOpenChat(eid);
      } else {
        loadAll();
      }
    });
  }

  function afterAcceptOpenChat(eid) {
    var welcome =
      "Police-ku waa aqbalay xaaladdaada. Halkan nala soo hadal. / We accepted your emergency — message us here.";
    return api("/api/messages/" + eid, {
      method: "POST",
      body: { text: welcome, unique_system: true, system_transition: "accepted" },
    })
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

  /* ---------- Case Chat (WhatsApp-style — same as hospital) ---------- */
  function openCaseChat(eid) {
    state.chatCaseId = eid;
    state.selectedId = eid;
    showPanel("chat");
    loadCaseChat();
  }

  function chatInitials(name) {
    var parts = String(name || "?")
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    if (!parts.length) return "?";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }

  function chatPersonKey(em) {
    if (!em) return "unknown";
    if (em.user_id != null && em.user_id !== "") return "u:" + em.user_id;
    var phone = String(em.phone || em.caller_phone || "").replace(/\D/g, "");
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
          type: "security",
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
        type: "security",
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
      ? (meta.lastRole === "police" || meta.lastRole === "admin" ? "You: " : "") + meta.lastText
      : em.location || em.district || typeLabel(em.type) || "No messages yet";
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
      "</strong><time>" +
      esc(when) +
      "</time></div>" +
      '<div class="hcc-wa-row-bottom"><span class="hcc-wa-preview">' +
      esc(String(preview).slice(0, 72)) +
      "</span>" +
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
        '<div class="hcc-chat-empty"><strong>Ma jiraan chats</strong><p>Qof kasta wuxuu liiska ku yimaadaa hal jeer — sida WhatsApp. Accept ka dib chat ayaa furmaya.</p></div>';
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
    var mine =
      m.sender_role === "police" ||
      m.sender_role === "admin" ||
      m.sender_role === "super_admin";
    var who = mine ? "Police" : m.sender_role === "citizen" ? "Citizen" : esc(m.sender_role || "User");
    var tick =
      m.status === "seen" ? " ✓✓" : m.status === "delivered" ? " ✓✓" : m.status === "sent" ? " ✓" : "";
    var caseTag =
      showCase && m._caseId
        ? '<span class="hcc-chat-case-tag">#' + m._caseId + "</span>"
        : state.chatCaseId
          ? '<span class="hcc-chat-case-tag">#' + state.chatCaseId + "</span>"
          : "";
    return (
      '<div class="hcc-chat-bubble ' +
      (mine ? "me" : "them") +
      '"><span class="hcc-chat-who">' +
      who +
      " " +
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
    var name =
      (person && person.name) || (em && (em.caller_name || em.phone)) || "Case #" + state.chatCaseId;
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
    if (box) {
      box.innerHTML = '<div class="hcc-muted" style="margin:auto;padding:20px;">Loading messages…</div>';
    }

    var loader = person
      ? loadMergedPersonMessages(person, box)
      : api("/api/messages/" + state.chatCaseId).then(function (data) {
          if (!box) return;
          if (data && data.success === false) {
            box.innerHTML =
              '<div class="hcc-chat-empty"><strong>Chat lama furin</strong><p>' +
              esc(data.message || "Forbidden or case not assigned to this station.") +
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
            box.innerHTML = msgs
              .map(function (m) {
                return chatBubbleHtml(m, false);
              })
              .join("");
            box.scrollTop = box.scrollHeight;
          }
        });

    return loader;
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

  function openDetail(eid) {
    var em =
      state.emergencies.filter(function (e) {
        return e.id === eid;
      })[0] ||
      state.history.filter(function (e) {
        return e.id === eid;
      })[0];
    if (!em) return;
    state.selectedId = eid;
    var modal = $("pcc-detail-modal");
    if ($("pcc-detail-title")) $("pcc-detail-title").textContent = "Incident #" + eid;
    if ($("pcc-detail-body")) {
      $("pcc-detail-body").innerHTML =
        "<p><span class=\"pcc-type-pill " +
        esc(em.type || "") +
        '">' +
        esc(typeLabel(em.type)) +
        "</span> · <strong>" +
        esc(em.status) +
        "</strong></p>" +
        "<p><strong>Location:</strong> " +
        esc(em.location || em.district || "—") +
        "</p>" +
        "<p><strong>Caller:</strong> " +
        esc(em.caller_name || "Unknown") +
        " · " +
        esc(em.phone || "N/A") +
        "</p>" +
        "<p><strong>Time:</strong> " +
        fmtTime(em.timestamp) +
        "</p>" +
        (em.notes ? "<p><strong>Notes:</strong> " + esc(em.notes) + "</p>" : "") +
        (em.latitude != null
          ? "<p><strong>GPS:</strong> " +
            Number(em.latitude).toFixed(5) +
            ", " +
            Number(em.longitude).toFixed(5) +
            "</p>"
          : "");
    }
    var actions = $("pcc-detail-actions");
    if (actions) {
      var html =
        '<button type="button" class="hcc-btn hcc-btn-ghost" id="pcc-detail-close">Close</button>';
      if (ACTIVE[em.status]) {
        if (!isMine(em) || em.status === "pending") {
          html +=
            '<button type="button" class="hcc-btn hcc-btn-primary" data-act="accept">Accept</button>';
        }
        if (isMine(em)) {
          html +=
            '<button type="button" class="hcc-btn hcc-btn-primary" data-act="dispatch">Dispatch</button>';
          html +=
            '<button type="button" class="hcc-btn hcc-btn-primary" data-act="complete">Complete</button>';
        }
      }
      actions.innerHTML = html;
      var closeBtn = $("pcc-detail-close");
      if (closeBtn) closeBtn.onclick = closeDetail;
      actions.querySelectorAll("[data-act]").forEach(function (b) {
        b.onclick = function () {
          mutate(eid, b.getAttribute("data-act"));
        };
      });
    }
    if (modal) modal.classList.remove("hidden");
    if (em.latitude != null && state.map) {
      state.map.setView([Number(em.latitude), Number(em.longitude)], 15);
    }
  }

  function closeDetail() {
    var modal = $("pcc-detail-modal");
    if (modal) modal.classList.add("hidden");
    state.selectedId = null;
  }

  /* ---------- Station form ---------- */
  function fillStationForm() {
    var s = state.station || {};
    if ($("st-name")) $("st-name").value = s.name || "";
    if ($("st-status")) $("st-status").value = s.operating_status || "open";
    if ($("st-phone")) $("st-phone").value = s.phone || "";
    if ($("st-city")) $("st-city").value = s.city || "";
    if ($("st-district")) $("st-district").value = s.district || "";
    if ($("st-address")) $("st-address").value = s.address || "";
    if ($("st-lat")) $("st-lat").value = s.latitude != null ? String(s.latitude) : "";
    if ($("st-lng")) $("st-lng").value = s.longitude != null ? String(s.longitude) : "";
  }

  function saveStation(e) {
    if (e) e.preventDefault();
    var err = $("st-form-error");
    if (err) {
      err.hidden = true;
      err.textContent = "";
    }
    if (!state.stationLinked) {
      if (err) {
        err.hidden = false;
        err.textContent = "Station is not linked to this account.";
      }
      return;
    }
    var payload = {
      name: ($("st-name") && $("st-name").value) || "",
      operating_status: ($("st-status") && $("st-status").value) || "open",
      phone: ($("st-phone") && $("st-phone").value) || "",
      city: ($("st-city") && $("st-city").value) || "",
      district: ($("st-district") && $("st-district").value) || "",
      address: ($("st-address") && $("st-address").value) || "",
      latitude: ($("st-lat") && $("st-lat").value) || null,
      longitude: ($("st-lng") && $("st-lng").value) || null,
    };
    api("/api/police/station", { method: "PUT", body: payload }).then(function (r) {
      if (r && r.success === false) {
        if (err) {
          err.hidden = false;
          err.textContent = r.message || "Save failed";
        }
        return;
      }
      state.station = (r && r.station) || state.station;
      syncStationChips();
      if ($("pcc-brand-name")) $("pcc-brand-name").textContent = state.station.name || "Police";
      if ($("pcc-op-status")) {
        $("pcc-op-status").textContent = String(state.station.operating_status || "open").replace(
          /^\w/,
          function (c) {
            return c.toUpperCase();
          }
        );
      }
      renderMap(false);
    });
  }

  /* ---------- Data ---------- */
  function loadAll() {
    if ($("refresh-time")) $("refresh-time").textContent = "Updating…";
    return Promise.all([
      api("/api/get_emergencies?type=police"),
      api("/api/get_emergencies?type=police&status=completed,resolved,cancelled"),
      api("/api/notifications"),
      state.stationLinked ? api("/api/police/station") : Promise.resolve(null),
    ]).then(function (results) {
      var live = results[0] || {};
      var hist = results[1] || {};
      var notes = results[2] || {};
      var st = results[3];
      if (live.refresh_interval) state.refreshMs = live.refresh_interval * 1000;
      state.emergencies = (live.emergencies || []).filter(function (e) {
        return ACTIVE[e.status];
      });
      state.history = hist.emergencies || [];
      state.notifications = notes.notifications || [];
      if (st && st.station) state.station = st.station;
      syncStationChips();
      renderKpis(live.avg_response_time);
      renderQueue();
      renderHistory();
      renderNotifs();
      renderChatCaseList();
      updateChatBadge();
      renderMap(!state.mapFitted);
      if ($("map-eta-line")) {
        $("map-eta-line").textContent =
          state.emergencies.length > 0
            ? state.emergencies.length + " on map"
            : "Security · accident";
      }
      if ($("refresh-time")) {
        $("refresh-time").textContent = "Live · " + new Date().toLocaleTimeString();
      }
    });
  }

  function showPanel(name) {
    ["live", "queue", "chat", "history", "station", "notifications"].forEach(function (p) {
      var el = $("panel-" + p);
      if (!el) return;
      if (p === name) el.classList.remove("hidden-panel");
      else el.classList.add("hidden-panel");
    });
    document.querySelectorAll(".hcc-nav a").forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-panel") === name);
    });
    var titles = {
      live: (state.station.name || "Police") + " Command Center",
      queue: "Incident Queue",
      chat: "Case Chat",
      history: "History",
      station: "Station Profile",
      notifications: "Notifications",
    };
    if ($("pcc-page-title")) $("pcc-page-title").textContent = titles[name] || titles.live;
    if (name === "station") fillStationForm();
    if (name === "chat") {
      renderChatCaseList();
      loadCaseChat();
      startChatPoll();
    } else {
      stopChatPoll();
    }
    if (state.map) {
      setTimeout(function () {
        state.map.invalidateSize();
      }, 100);
    }
  }

  function schedule() {
    if (state.timer) clearTimeout(state.timer);
    state.timer = setTimeout(function () {
      loadAll().finally(schedule);
    }, state.refreshMs);
  }

  function init() {
    initMap();
    fillStationForm();
    syncStationChips();
    syncDutyBanner();
    tickClock();
    setInterval(tickClock, 1000);

    document.querySelectorAll(".hcc-nav a").forEach(function (a) {
      a.onclick = function (ev) {
        ev.preventDefault();
        showPanel(a.getAttribute("data-panel"));
      };
    });
    var menu = $("sidebar-toggle");
    if (menu) {
      menu.onclick = function () {
        var side = $("sidebar");
        if (side) side.classList.toggle("open");
      };
    }
    if ($("btn-sos-refresh")) $("btn-sos-refresh").onclick = loadAll;
    if ($("btn-queue-refresh")) $("btn-queue-refresh").onclick = loadAll;
    if ($("btn-map-fit")) {
      $("btn-map-fit").onclick = function () {
        renderMap(true);
      };
    }
    if ($("btn-map-refresh")) $("btn-map-refresh").onclick = loadAll;
    if ($("btn-open-notifs")) {
      $("btn-open-notifs").onclick = function () {
        showPanel("notifications");
      };
    }
    if ($("btn-mark-read")) {
      $("btn-mark-read").onclick = function () {
        api("/api/notifications/read", { method: "POST", body: {} }).then(loadAll);
      };
    }
    var form = $("station-form");
    if (form) form.onsubmit = saveStation;
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
      chip.onclick = function () {
        sendCaseChat(chip.getAttribute("data-chat-text"));
      };
    });
    if ($("hcc-chat-search")) {
      $("hcc-chat-search").addEventListener("input", function () {
        state.chatSearch = $("hcc-chat-search").value || "";
        renderChatCaseList();
      });
    }
    document.querySelectorAll("[data-chat-filter]").forEach(function (btn) {
      btn.onclick = function () {
        state.chatFilter = btn.getAttribute("data-chat-filter") || "all";
        document.querySelectorAll("[data-chat-filter]").forEach(function (b) {
          b.classList.toggle("is-active", b === btn);
        });
        renderChatCaseList();
      };
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
    var detailModal = $("pcc-detail-modal");
    if (detailModal) {
      detailModal.onclick = function (ev) {
        if (ev.target === detailModal) closeDetail();
      };
    }

    loadAll().finally(schedule);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
