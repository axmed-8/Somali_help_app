/**
 * Driver GPS share page — watchPosition → tokenized hospital map update.
 */
(function () {
  "use strict";

  var cfg = window.DGPS || {};
  var watchId = null;
  var lastPush = 0;
  var sharing = false;

  function $(id) {
    return document.getElementById(id);
  }

  function setStatus(text, kind) {
    var el = $("dgps-status");
    if (!el) return;
    el.textContent = text;
    el.classList.remove("is-live", "is-error", "is-wait");
    if (kind) el.classList.add(kind);
  }

  function setCoords(lat, lng) {
    var el = $("dgps-coords");
    if (!el) return;
    el.textContent = "Live: " + Number(lat).toFixed(5) + ", " + Number(lng).toFixed(5);
  }

  function setSharingUi(on) {
    sharing = !!on;
    var start = $("dgps-start");
    var stop = $("dgps-stop");
    if (start) start.hidden = on;
    if (stop) stop.hidden = !on;
  }

  function pushLocation(lat, lng) {
    var now = Date.now();
    if (now - lastPush < 2000) return;
    lastPush = now;
    fetch(cfg.endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ latitude: lat, longitude: lng }),
      credentials: "same-origin",
    })
      .then(function (r) {
        return r.json().then(function (data) {
          return { ok: r.ok, data: data };
        });
      })
      .then(function (res) {
        if (!res.ok || (res.data && res.data.success === false)) {
          setStatus((res.data && res.data.message) || "GPS push failed", "is-error");
          return;
        }
        setCoords(lat, lng);
        setStatus("Sharing live GPS with hospital", "is-live");
      })
      .catch(function () {
        setStatus("Network error — retrying…", "is-wait");
      });
  }

  function startShare() {
    if (!navigator.geolocation) {
      setStatus("GPS not available on this phone", "is-error");
      return;
    }
    if (watchId != null) return;
    setStatus("Waiting for GPS fix…", "is-wait");
    setSharingUi(true);
    watchId = navigator.geolocation.watchPosition(
      function (pos) {
        pushLocation(pos.coords.latitude, pos.coords.longitude);
      },
      function (err) {
        setStatus((err && err.message) || "Allow location permission", "is-error");
        stopShare();
      },
      { enableHighAccuracy: true, maximumAge: 1000, timeout: 25000 }
    );
  }

  function stopShare() {
    if (watchId != null) {
      try {
        navigator.geolocation.clearWatch(watchId);
      } catch (e) {}
      watchId = null;
    }
    setSharingUi(false);
    if (!$("dgps-status") || !$("dgps-status").classList.contains("is-error")) {
      setStatus("Stopped — tap Start to share again", "");
    }
  }

  function init() {
    if (!cfg.endpoint) return;
    var start = $("dgps-start");
    var stop = $("dgps-stop");
    if (start) start.onclick = startShare;
    if (stop) stop.onclick = stopShare;
    window.addEventListener("pagehide", stopShare);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
