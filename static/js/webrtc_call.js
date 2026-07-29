/**
 * GurmadNet in-app WebRTC voice helper (citizen ↔ call center).
 * Signaling via Socket.IO: call:offer / call:answer / call:ice-candidate.
 * Audio is peer-to-peer WebRTC only — never streamed over Socket.IO.
 */
(function (global) {
  "use strict";

  var DEFAULT_ICE = [
    { urls: "stun:stun.l.google.com:19302" },
    { urls: "stun:stun1.l.google.com:19302" }
  ];

  function sameCallId(a, b) {
    if (a == null || b == null) return false;
    return Number(a) === Number(b);
  }

  function sdpPayload(desc) {
    if (!desc) return null;
    return { type: desc.type, sdp: desc.sdp };
  }

  function GurmadVoiceCall(options) {
    options = options || {};
    this.role = options.role || "citizen";
    this.socket = options.socket || null;
    this._ownsSocket = !options.socket;
    this.pc = null;
    this.localStream = null;
    this.remoteStream = null;
    this.callId = null;
    this.iceServers = DEFAULT_ICE;
    this.muted = false;
    this.speakerOn = true;
    this.state = "idle";
    this._remoteAudio = null;
    this._handlers = {};
    this._connectedEmitted = false;
    this._ended = false;
    this._offerStarted = false;
    this._answerStarted = false;
    this._pendingIce = [];
    this._remoteDescSet = false;
    this._iceRestartTried = false;
    this._failTimer = null;
    this._diag = !!options.debug || (typeof location !== "undefined" && /[?&]webrtc_debug=1/.test(location.search || ""));
  }

  GurmadVoiceCall.prototype.on = function (event, fn) {
    this._handlers[event] = fn;
    return this;
  };

  GurmadVoiceCall.prototype._emit = function (event, payload) {
    var fn = this._handlers[event];
    if (fn) fn(payload);
  };

  GurmadVoiceCall.prototype._log = function () {
    if (!this._diag) return;
    var args = ["[GurmadVoice]", "call=" + this.callId, "role=" + this.role].concat([].slice.call(arguments));
    try { console.log.apply(console, args); } catch (e) {}
  };

  GurmadVoiceCall.prototype._setState = function (next) {
    if (this.state === "ended" || this.state === "ending") {
      if (next !== "ended") return;
    }
    this.state = next;
    this._log("state", next);
  };

  GurmadVoiceCall.prototype.connectSocket = function () {
    var self = this;
    if (self.socket && self.socket.connected) {
      self._bindSocket();
      return Promise.resolve(self.socket);
    }
    if (self.socket && !self.socket.connected) {
      return new Promise(function (resolve, reject) {
        var done = false;
        self.socket.once("connect", function () {
          if (done) return;
          done = true;
          self._bindSocket();
          resolve(self.socket);
        });
        self.socket.once("connect_error", function (err) {
          if (done) return;
          done = true;
          reject(err || new Error("Socket connect failed"));
        });
        setTimeout(function () {
          if (!done) {
            done = true;
            reject(new Error("Socket connect timeout"));
          }
        }, 15000);
        try { self.socket.connect(); } catch (e) {}
      });
    }
    if (typeof io === "undefined") {
      return Promise.reject(new Error("Socket.IO client not loaded"));
    }
    return new Promise(function (resolve, reject) {
      self.socket = io({
        path: "/socket.io",
        transports: ["websocket", "polling"],
        withCredentials: true
      });
      self._ownsSocket = true;
      var settled = false;
      self.socket.on("connect", function () {
        if (settled) return;
        settled = true;
        self._bindSocket();
        resolve(self.socket);
      });
      self.socket.on("connect_error", function (err) {
        if (settled) return;
        settled = true;
        reject(err || new Error("Socket connect failed"));
      });
      setTimeout(function () {
        if (!settled) {
          settled = true;
          reject(new Error("Socket connect timeout"));
        }
      }, 15000);
    });
  };

  /**
   * Bind once per socket; always dispatch to the current active instance.
   * Prevents stale handlers after destroy()+reuse of Call Center ops socket.
   */
  GurmadVoiceCall.prototype._bindSocket = function () {
    var s = this.socket;
    if (!s) return;
    s._gnVoiceInstance = this;

    if (s._gnVoiceBound) return;
    s._gnVoiceBound = true;

    function active() {
      return s._gnVoiceInstance;
    }

    s.on("call:offer", function (payload) {
      var self = active();
      if (!self || self._ended) return;
      if (!payload || !sameCallId(payload.call_id, self.callId)) return;
      self._log("recv offer");
      self._onRemoteOffer(payload.sdp);
    });
    s.on("call:answer", function (payload) {
      var self = active();
      if (!self || self._ended) return;
      if (!payload || !sameCallId(payload.call_id, self.callId)) return;
      self._log("recv answer");
      self._onRemoteAnswer(payload.sdp);
    });
    s.on("call:ice-candidate", function (payload) {
      var self = active();
      if (!self || self._ended) return;
      if (!payload || !sameCallId(payload.call_id, self.callId)) return;
      self._addIceCandidate(payload.candidate);
    });
    s.on("call:accept", function (payload) {
      var self = active();
      if (!self || self._ended) return;
      if (!payload || !sameCallId(payload.call_id, self.callId)) return;
      self._setState("connecting");
      self._emit("accepted", payload);
      if (self.role === "citizen") {
        self._startAsOfferer();
      }
    });
    s.on("call:connected", function (payload) {
      var self = active();
      if (!self || self._ended) return;
      if (!payload || !sameCallId(payload.call_id, self.callId)) return;
      self._setState("connected");
      self._emit("connected", payload);
    });
    s.on("call:end", function (payload) {
      var self = active();
      if (!self || self._ended) return;
      if (!payload || (self.callId && !sameCallId(payload.call_id, self.callId))) return;
      self._emit("ended", payload || {});
      self.hangup(false);
    });
    s.on("call:reject", function (payload) {
      var self = active();
      if (!self || self._ended) return;
      if (!payload || (self.callId && !sameCallId(payload.call_id, self.callId))) return;
      self._emit("rejected", payload || {});
      self.hangup(false);
    });
    s.on("call:failed", function (payload) {
      var self = active();
      if (!self || self._ended) return;
      if (payload && payload.call_id != null && self.callId && !sameCallId(payload.call_id, self.callId)) return;
      self._emit("failed", payload || {});
    });
    s.on("call:busy", function (payload) {
      var self = active();
      if (!self || self._ended) return;
      self._emit("busy", payload || {});
    });
    s.on("call:ringing", function (payload) {
      var self = active();
      if (!self || self._ended) return;
      if (payload && payload.call_id != null && self.callId && !sameCallId(payload.call_id, self.callId)) return;
      self._setState("ringing");
      self._emit("ringing", payload || {});
    });
    // Socket blips must NOT hang up the WebRTC call.
    s.on("disconnect", function () {
      var self = active();
      if (!self || self._ended) return;
      self._emit("reconnecting", { reason: "socket_disconnect" });
    });
    s.on("reconnect", function () {
      var self = active();
      if (!self || self._ended || !self.callId) return;
      s.emit("call:join", { call_id: self.callId });
    });
  };

  GurmadVoiceCall.prototype._addIceCandidate = function (candidate) {
    var self = this;
    if (!candidate) return;
    if (!self.pc || !self._remoteDescSet) {
      self._pendingIce.push(candidate);
      self._log("queue ICE (remoteDesc not ready)");
      return;
    }
    self.pc.addIceCandidate(new RTCIceCandidate(candidate)).catch(function (err) {
      self._log("addIceCandidate error", err && err.message);
    });
  };

  GurmadVoiceCall.prototype._flushIce = function () {
    var self = this;
    if (!self.pc || !self._remoteDescSet) return;
    var queued = self._pendingIce.splice(0, self._pendingIce.length);
    self._log("flush ICE", queued.length);
    queued.forEach(function (c) {
      self.pc.addIceCandidate(new RTCIceCandidate(c)).catch(function () {});
    });
  };

  var HTTPS_REQUIRED_MSG = "Voice calls require HTTPS or localhost.";

  function isSecureMediaContext() {
    try {
      if (typeof window !== "undefined" && typeof window.isSecureContext === "boolean") {
        return window.isSecureContext;
      }
    } catch (e) {}
    var proto = (typeof location !== "undefined" && location.protocol) || "";
    var host = (typeof location !== "undefined" && location.hostname) || "";
    return proto === "https:" || host === "localhost" || host === "127.0.0.1" || host === "[::1]";
  }

  function insecureContextError() {
    var host = (typeof location !== "undefined" && location.host) || "";
    var proto = (typeof location !== "undefined" && location.protocol) || "";
    var err = new Error(HTTPS_REQUIRED_MSG);
    err.name = "SecurityError";
    err.secureContext = false;
    err.code = "INSECURE_CONTEXT";
    try {
      console.error("[GurmadVoice] WebRTC blocked — insecure HTTP origin", {
        message: HTTPS_REQUIRED_MSG,
        protocol: proto,
        host: host,
        isSecureContext: typeof window !== "undefined" ? window.isSecureContext : null
      });
    } catch (e) {}
    return err;
  }

  /**
   * Build a clear Error from getUserMedia / mediaDevices failures.
   * Preserves DOMException name (NotAllowedError, SecurityError, …).
   */
  function micFailureError(err, extra) {
    extra = extra || {};
    if (!isSecureMediaContext()) {
      return insecureContextError();
    }
    var name = (err && (err.name || err.code)) || extra.name || "Error";
    var detail = (err && err.message) || extra.detail || "";
    var lines = [];

    if (name === "NotAllowedError" || name === "PermissionDeniedError") {
      lines.push("Microphone permission denied (" + name + ").");
      lines.push("Enable microphone for this site in browser settings, then reload and try again.");
    } else if (name === "NotFoundError" || name === "DevicesNotFoundError") {
      lines.push("No microphone found (" + name + ").");
    } else if (name === "NotReadableError" || name === "TrackStartError") {
      lines.push("Microphone is busy or unreadable (" + name + "). Close other apps using the mic.");
    } else if (name === "SecurityError") {
      lines.push("Browser SecurityError — microphone blocked for this origin.");
    } else if (name === "AbortError") {
      lines.push("Microphone request was aborted (" + name + ").");
    } else if (name === "OverconstrainedError") {
      lines.push("Microphone constraints not satisfied (" + name + ").");
    } else if (name === "TypeError" || name === "NotSupportedError") {
      lines.push("getUserMedia is not available here (" + name + ").");
    } else if (extra.missingApi) {
      lines.push("navigator.mediaDevices.getUserMedia is missing.");
    } else {
      lines.push("Microphone error: " + name + (detail ? " — " + detail : ""));
    }

    if (detail && lines.join(" ").indexOf(detail) < 0) {
      lines.push("Detail: " + detail);
    }

    var message = lines.join(" ");
    var out = new Error(message);
    out.name = String(name);
    out.secureContext = true;
    out.original = err || null;
    return out;
  }

  GurmadVoiceCall.isSecureContext = isSecureMediaContext;
  GurmadVoiceCall.HTTPS_REQUIRED_MSG = HTTPS_REQUIRED_MSG;

  GurmadVoiceCall.describeMicError = function (err) {
    if (!err) return "Unknown microphone error";
    if (err.code === "INSECURE_CONTEXT" || err.secureContext === false) {
      return HTTPS_REQUIRED_MSG;
    }
    if (err.message && /Voice calls require HTTPS/i.test(err.message)) {
      return HTTPS_REQUIRED_MSG;
    }
    return err.message || String(err);
  };

  GurmadVoiceCall.prototype.requestMic = function () {
    var self = this;

    // Never call getUserMedia on insecure HTTP (e.g. http://192.168.x.x).
    if (!isSecureMediaContext()) {
      return Promise.reject(insecureContextError());
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      var missing = micFailureError(null, { name: "NotSupportedError", missingApi: true });
      try {
        console.error("[GurmadVoice] mediaDevices/getUserMedia unavailable", {
          hasMediaDevices: !!navigator.mediaDevices,
          isSecureContext: window.isSecureContext,
          error: missing.name,
          message: missing.message
        });
      } catch (e2) {}
      return Promise.reject(missing);
    }

    var permissionHint = Promise.resolve(null);
    try {
      if (navigator.permissions && navigator.permissions.query) {
        permissionHint = navigator.permissions.query({ name: "microphone" }).then(
          function (status) {
            return status && status.state;
          },
          function () {
            return null;
          }
        );
      }
    } catch (e3) {
      permissionHint = Promise.resolve(null);
    }

    return permissionHint.then(function (permState) {
      if (permState === "denied") {
        var denied = micFailureError(
          { name: "NotAllowedError", message: "Permission previously denied" },
          {}
        );
        try {
          console.error("[GurmadVoice] microphone permission state=denied", {
            error: denied.name,
            message: denied.message
          });
        } catch (e4) {}
        return Promise.reject(denied);
      }

      return navigator.mediaDevices
        .getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true
          },
          video: false
        })
        .then(function (stream) {
          self.localStream = stream;
          self._log("mic ok tracks=", stream.getAudioTracks().length);
          if (self.pc) self._attachLocalTracks();
          return stream;
        })
        .catch(function (err) {
          var wrapped = micFailureError(err);
          try {
            console.error("[GurmadVoice] getUserMedia failed", {
              name: err && err.name,
              message: err && err.message,
              permissionState: permState,
              isSecureContext: window.isSecureContext,
              host: location.host,
              wrapped: wrapped.message
            });
          } catch (e5) {}
          return Promise.reject(wrapped);
        });
    });
  };

  GurmadVoiceCall.prototype.setCallId = function (callId, iceServers) {
    this.callId = callId == null ? null : Number(callId);
    if (iceServers && iceServers.length) this.iceServers = iceServers;
    if (this.callId && this.state === "idle") this._setState("calling");
  };

  GurmadVoiceCall.prototype.join = function () {
    var self = this;
    return self.connectSocket().then(function () {
      return new Promise(function (resolve, reject) {
        var settled = false;
        function done(err, payload) {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          if (err) reject(err);
          else resolve(payload);
        }
        var timer = setTimeout(function () {
          done(new Error("Join call timeout"));
        }, 12000);

        function onJoined(payload) {
          if (!payload || !sameCallId(payload.call_id, self.callId)) return;
          if (payload.ice_servers) self.iceServers = payload.ice_servers;
          if (
            self.role === "citizen" &&
            payload.status &&
            ["accepted", "connecting", "connected"].indexOf(payload.status) >= 0
          ) {
            self._emit("accepted", payload);
            self._startAsOfferer();
          }
          done(null, payload);
        }
        function onFailed(payload) {
          if (payload && payload.call_id != null && !sameCallId(payload.call_id, self.callId)) return;
          done(new Error((payload && payload.message) || "Join failed"));
        }

        self.socket.once("call:joined", onJoined);
        self.socket.once("call:failed", onFailed);
        self.socket.emit("call:join", { call_id: self.callId });
      });
    });
  };

  GurmadVoiceCall.prototype.signalStart = function () {
    if (!this.socket || !this.callId) return;
    this._setState("ringing");
    this.socket.emit("call:start", { call_id: this.callId });
  };

  GurmadVoiceCall.prototype.accept = function () {
    if (!this.socket || !this.callId) return;
    this.unlockAudio();
    this._setState("connecting");
    this.socket.emit("call:accept", { call_id: this.callId });
  };

  GurmadVoiceCall.prototype.reject = function () {
    if (!this.socket || !this.callId) return;
    this.socket.emit("call:reject", { call_id: this.callId });
  };

  GurmadVoiceCall.prototype.end = function () {
    if (this._ended) return;
    this._setState("ending");
    if (this.socket && this.callId) {
      try {
        this.socket.emit("call:end", { call_id: this.callId });
      } catch (e) {}
    }
    this.hangup(false);
  };

  GurmadVoiceCall.prototype.unlockAudio = function () {
    var self = this;
    self._ensureRemoteAudio();
    if (!self._remoteAudio) return;
    self._remoteAudio.muted = !self.speakerOn;
    self._remoteAudio.volume = self.speakerOn ? 1 : 0;
    var play = self._remoteAudio.play();
    if (play && play.catch) play.catch(function () {});
  };

  GurmadVoiceCall.prototype._attachLocalTracks = function () {
    var self = this;
    if (!self.pc || !self.localStream) return;
    var have = {};
    self.pc.getSenders().forEach(function (sender) {
      if (sender.track) have[sender.track.id] = true;
    });
    self.localStream.getTracks().forEach(function (track) {
      if (!have[track.id]) {
        self.pc.addTrack(track, self.localStream);
        self._log("addTrack", track.kind, track.id);
      }
    });
  };

  GurmadVoiceCall.prototype._ensurePc = function () {
    var self = this;
    if (self.pc) return self.pc;
    self.pc = new RTCPeerConnection({ iceServers: self.iceServers });
    self.remoteStream = new MediaStream();
    self._ensureRemoteAudio();
    self._attachLocalTracks();

    self.pc.onicecandidate = function (ev) {
      if (ev.candidate && self.socket && self.callId && !self._ended) {
        var cand = ev.candidate;
        var typ = "";
        try {
          var m = String(cand.candidate || "").match(/\btyp\s+(\w+)/);
          typ = m ? m[1] : "";
          if (typ === "relay") self._relaySeen = true;
          self._log("local ICE", typ || "unknown");
        } catch (e) {}
        self.socket.emit("call:ice-candidate", {
          call_id: self.callId,
          candidate: cand.toJSON()
        });
      }
    };

    self.pc.ontrack = function (ev) {
      self._log("ontrack", ev.track && ev.track.kind);
      if (ev.streams && ev.streams[0]) {
        self.remoteStream = ev.streams[0];
      } else if (ev.track) {
        try { self.remoteStream.addTrack(ev.track); } catch (e) {}
      }
      self._ensureRemoteAudio();
      self._remoteAudio.srcObject = self.remoteStream;
      self._remoteAudio.muted = !self.speakerOn;
      self._remoteAudio.volume = self.speakerOn ? 1 : 0;
      var play = self._remoteAudio.play();
      if (play && play.catch) {
        play.catch(function () {
          self._log("remote play blocked — will retry on gesture");
        });
      }
      self._maybeMarkConnected();
    };

    self.pc.onconnectionstatechange = function () {
      if (!self.pc || self._ended) return;
      var st = self.pc.connectionState;
      self._log("connectionState", st, "ice", self.pc.iceConnectionState, "sig", self.pc.signalingState);
      if (st === "connected") {
        self._clearFailTimer();
        self._emit("media", { state: "connected" });
        self._maybeMarkConnected();
      } else if (st === "connecting") {
        self._emit("media", { state: "connecting" });
      } else if (st === "disconnected") {
        // Transient — do NOT hang up; ICE may recover.
        self._emit("reconnecting", { reason: "webrtc_disconnected" });
      } else if (st === "failed") {
        self._handleMediaFailed();
      } else if (st === "closed") {
        self._emit("media", { state: "closed" });
      }
    };

    self.pc.oniceconnectionstatechange = function () {
      if (!self.pc || self._ended) return;
      var st = self.pc.iceConnectionState;
      self._log("iceConnectionState", st, "gather", self.pc.iceGatheringState);
      if (st === "connected" || st === "completed") {
        self._clearFailTimer();
        self._maybeMarkConnected();
      } else if (st === "failed") {
        self._handleMediaFailed();
      } else if (st === "disconnected") {
        self._emit("reconnecting", { reason: "ice_disconnected" });
      }
    };

    return self.pc;
  };

  GurmadVoiceCall.prototype._clearFailTimer = function () {
    if (this._failTimer) {
      clearTimeout(this._failTimer);
      this._failTimer = null;
    }
  };

  GurmadVoiceCall.prototype._handleMediaFailed = function () {
    var self = this;
    if (self._ended) return;
    // One ICE restart attempt before declaring failure (helps flaky NAT).
    if (!self._iceRestartTried && self.pc && self.role === "citizen" && self._remoteDescSet) {
      self._iceRestartTried = true;
      self._log("ICE restart attempt");
      self.pc.createOffer({ iceRestart: true }).then(function (offer) {
        return self.pc.setLocalDescription(offer).then(function () {
          if (self.socket && self.callId && !self._ended) {
            self.socket.emit("call:offer", { call_id: self.callId, sdp: sdpPayload(offer) });
          }
        });
      }).catch(function () {
        self._scheduleFail("Connection Failed");
      });
      return;
    }
    self._scheduleFail("Connection Failed");
  };

  GurmadVoiceCall.prototype._scheduleFail = function (message) {
    var self = this;
    if (self._ended || self._failTimer) return;
    // Brief grace so a flicker to failed during renegotiation does not kill the call.
    self._failTimer = setTimeout(function () {
      self._failTimer = null;
      if (self._ended) return;
      if (self.pc && (self.pc.connectionState === "connected" ||
          self.pc.iceConnectionState === "connected" ||
          self.pc.iceConnectionState === "completed")) {
        return;
      }
      self._emit("failed", {
        message: message || "Connection Failed",
        hint: "Cross-network calls may require a TURN server."
      });
    }, 2500);
  };

  GurmadVoiceCall.prototype._ensureRemoteAudio = function () {
    if (this._remoteAudio) return;
    var audio = document.createElement("audio");
    audio.id = "gn-remote-audio-" + (this.role || "peer");
    audio.autoplay = true;
    audio.playsInline = true;
    audio.setAttribute("playsinline", "true");
    audio.setAttribute("webkit-playsinline", "true");
    audio.setAttribute("aria-hidden", "true");
    // Avoid display:none — mobile Safari often refuses to play hidden audio.
    audio.style.cssText = "position:fixed;width:1px;height:1px;opacity:0.01;pointer-events:none;left:0;bottom:0;z-index:-1;";
    audio.muted = false;
    audio.volume = 1;
    document.body.appendChild(audio);
    this._remoteAudio = audio;
  };

  GurmadVoiceCall.prototype._maybeMarkConnected = function () {
    if (this._connectedEmitted || !this.socket || !this.callId || this._ended) return;
    var pcOk = this.pc && (this.pc.connectionState === "connected" ||
      this.pc.iceConnectionState === "connected" ||
      this.pc.iceConnectionState === "completed");
    if (!pcOk) return;
    this._connectedEmitted = true;
    this._setState("connected");
    this.socket.emit("call:connected", { call_id: this.callId });
    this._emit("connected", { call_id: this.callId, status: "connected" });
  };

  GurmadVoiceCall.prototype._ensureMicThen = function (fn) {
    var self = this;
    if (self.localStream) return Promise.resolve().then(fn);
    return self.requestMic().then(fn);
  };

  GurmadVoiceCall.prototype._startAsOfferer = function () {
    var self = this;
    if (self._offerStarted || self._ended) return;
    if (self.role !== "citizen") return;
    self._offerStarted = true;
    self._ensureMicThen(function () {
      if (self._ended) return;
      self._ensurePc();
      self._attachLocalTracks();
          if (!self.localStream || !self.localStream.getAudioTracks().length) {
            self._offerStarted = false;
            self._emit("failed", {
              message: "No local audio track after getUserMedia (mic missing or stopped)."
            });
            return;
          }
      return self.pc.createOffer({ offerToReceiveAudio: true }).then(function (offer) {
        return self.pc.setLocalDescription(offer).then(function () {
          self._log("send offer");
          self.socket.emit("call:offer", { call_id: self.callId, sdp: sdpPayload(offer) });
        });
      });
    }).catch(function (err) {
      self._offerStarted = false;
      self._emit("failed", {
        message: (err && err.message) || "WebRTC offer failed"
      });
    });
  };

  GurmadVoiceCall.prototype._onRemoteOffer = function (sdp) {
    var self = this;
    if (!sdp || self._ended) return;
    if (self._answerStarted) {
      self._log("ignore duplicate offer");
      return;
    }
    self._answerStarted = true;
    self._ensureMicThen(function () {
      if (self._ended) return;
      self._ensurePc();
      self._attachLocalTracks();
          if (!self.localStream || !self.localStream.getAudioTracks().length) {
            self._answerStarted = false;
            self._emit("failed", {
              message: "No local audio track after getUserMedia (mic missing or stopped)."
            });
            return;
          }
      return self.pc.setRemoteDescription(new RTCSessionDescription(sdp)).then(function () {
        self._remoteDescSet = true;
        self._flushIce();
        return self.pc.createAnswer();
      }).then(function (answer) {
        return self.pc.setLocalDescription(answer).then(function () {
          self._log("send answer");
          self.socket.emit("call:answer", { call_id: self.callId, sdp: sdpPayload(answer) });
        });
      });
    }).catch(function (err) {
      self._answerStarted = false;
      self._emit("failed", {
        message: (err && err.message) || "WebRTC answer failed"
      });
    });
  };

  GurmadVoiceCall.prototype._onRemoteAnswer = function (sdp) {
    var self = this;
    if (!sdp || !self.pc || self._ended) return;
    if (self._remoteDescSet && self.pc.signalingState === "stable") {
      self._log("answer ignored (already stable)");
      return;
    }
    self.pc.setRemoteDescription(new RTCSessionDescription(sdp)).then(function () {
      self._remoteDescSet = true;
      self._flushIce();
      self._log("remote answer applied");
    }).catch(function (err) {
      self._emit("failed", { message: (err && err.message) || "WebRTC setAnswer failed" });
    });
  };

  GurmadVoiceCall.prototype.setMuted = function (muted) {
    this.muted = !!muted;
    if (this.localStream) {
      this.localStream.getAudioTracks().forEach(function (t) {
        t.enabled = !muted;
      });
    }
    return this.muted;
  };

  GurmadVoiceCall.prototype.toggleMute = function () {
    return this.setMuted(!this.muted);
  };

  GurmadVoiceCall.prototype.setSpeaker = function (on) {
    this.speakerOn = !!on;
    if (this._remoteAudio) {
      this._remoteAudio.muted = !this.speakerOn;
      this._remoteAudio.volume = this.speakerOn ? 1 : 0;
      if (this.speakerOn) {
        var play = this._remoteAudio.play();
        if (play && play.catch) play.catch(function () {});
      }
    }
    return this.speakerOn;
  };

  GurmadVoiceCall.prototype.toggleSpeaker = function () {
    return this.setSpeaker(!this.speakerOn);
  };

  GurmadVoiceCall.prototype.hangup = function (notify) {
    if (this._ended) return;
    this._ended = true;
    this._setState("ending");
    this._clearFailTimer();
    if (notify && this.socket && this.callId) {
      try {
        this.socket.emit("call:end", { call_id: this.callId });
      } catch (e) {}
    }
    if (this.pc) {
      try { this.pc.onicecandidate = null; } catch (e) {}
      try { this.pc.ontrack = null; } catch (e) {}
      try { this.pc.close(); } catch (e) {}
      this.pc = null;
    }
    if (this.localStream) {
      this.localStream.getTracks().forEach(function (t) {
        try { t.stop(); } catch (e) {}
      });
      this.localStream = null;
    }
    if (this._remoteAudio) {
      try {
        this._remoteAudio.pause();
        this._remoteAudio.srcObject = null;
        this._remoteAudio.remove();
      } catch (e) {}
      this._remoteAudio = null;
    }
    this.remoteStream = null;
    this._pendingIce = [];
    this._remoteDescSet = false;
    this._offerStarted = false;
    this._answerStarted = false;
    this._connectedEmitted = false;
    this._iceRestartTried = false;
    if (this.socket && this.socket._gnVoiceInstance === this) {
      this.socket._gnVoiceInstance = null;
    }
    this._setState("ended");
  };

  GurmadVoiceCall.prototype.destroy = function () {
    this.hangup(false);
    if (this.socket && this._ownsSocket) {
      try { this.socket.disconnect(); } catch (e) {}
      this.socket = null;
    }
  };

  global.GurmadVoiceCall = GurmadVoiceCall;
})(window);
