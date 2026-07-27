/**
 * GurmadNet in-app WebRTC voice helper (citizen ↔ call center).
 * Signaling via Socket.IO: call:offer / call:answer / call:ice-candidate.
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
    this._remoteAudio = null;
    this._handlers = {};
    this._connectedEmitted = false;
    this._ended = false;
    this._offerStarted = false;
    this._pendingIce = [];
    this._remoteDescSet = false;
  }

  GurmadVoiceCall.prototype.on = function (event, fn) {
    this._handlers[event] = fn;
    return this;
  };

  GurmadVoiceCall.prototype._emit = function (event, payload) {
    var fn = this._handlers[event];
    if (fn) fn(payload);
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

  GurmadVoiceCall.prototype._bindSocket = function () {
    var self = this;
    var s = self.socket;
    if (!s || s._gnVoiceBound) return;
    s._gnVoiceBound = true;

    s.on("call:offer", function (payload) {
      if (!payload || !sameCallId(payload.call_id, self.callId)) return;
      self._onRemoteOffer(payload.sdp);
    });
    s.on("call:answer", function (payload) {
      if (!payload || !sameCallId(payload.call_id, self.callId)) return;
      self._onRemoteAnswer(payload.sdp);
    });
    s.on("call:ice-candidate", function (payload) {
      if (!payload || !sameCallId(payload.call_id, self.callId)) return;
      self._addIceCandidate(payload.candidate);
    });
    s.on("call:accept", function (payload) {
      if (!payload || !sameCallId(payload.call_id, self.callId)) return;
      self._emit("accepted", payload);
      if (self.role === "citizen") {
        self._startAsOfferer();
      }
    });
    s.on("call:connected", function (payload) {
      if (!payload || !sameCallId(payload.call_id, self.callId)) return;
      self._emit("connected", payload);
    });
    s.on("call:end", function (payload) {
      if (!payload || (self.callId && !sameCallId(payload.call_id, self.callId))) return;
      self._emit("ended", payload || {});
      self.hangup(false);
    });
    s.on("call:reject", function (payload) {
      if (!payload || (self.callId && !sameCallId(payload.call_id, self.callId))) return;
      self._emit("rejected", payload || {});
      self.hangup(false);
    });
    s.on("call:failed", function (payload) {
      self._emit("failed", payload || {});
    });
    s.on("call:busy", function (payload) {
      self._emit("busy", payload || {});
    });
    s.on("call:ringing", function (payload) {
      self._emit("ringing", payload || {});
    });
    s.on("disconnect", function () {
      self._emit("reconnecting", { reason: "socket_disconnect" });
    });
    s.on("reconnect", function () {
      if (self.callId) {
        s.emit("call:join", { call_id: self.callId });
      }
    });
  };

  GurmadVoiceCall.prototype._addIceCandidate = function (candidate) {
    var self = this;
    if (!candidate) return;
    if (!self.pc || !self._remoteDescSet) {
      self._pendingIce.push(candidate);
      return;
    }
    self.pc.addIceCandidate(new RTCIceCandidate(candidate)).catch(function () {});
  };

  GurmadVoiceCall.prototype._flushIce = function () {
    var self = this;
    if (!self.pc || !self._remoteDescSet) return;
    var queued = self._pendingIce.splice(0, self._pendingIce.length);
    queued.forEach(function (c) {
      self.pc.addIceCandidate(new RTCIceCandidate(c)).catch(function () {});
    });
  };

  GurmadVoiceCall.prototype.requestMic = function () {
    var self = this;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return Promise.reject(new Error("Microphone Permission Required"));
    }
    return navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      },
      video: false
    }).then(function (stream) {
      self.localStream = stream;
      return stream;
    });
  };

  GurmadVoiceCall.prototype.setCallId = function (callId, iceServers) {
    this.callId = callId == null ? null : Number(callId);
    if (iceServers && iceServers.length) this.iceServers = iceServers;
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
          // Already accepted while we were getting mic ready → start offer now.
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
    this.socket.emit("call:start", { call_id: this.callId });
  };

  GurmadVoiceCall.prototype.accept = function () {
    if (!this.socket || !this.callId) return;
    this.socket.emit("call:accept", { call_id: this.callId });
  };

  GurmadVoiceCall.prototype.reject = function () {
    if (!this.socket || !this.callId) return;
    this.socket.emit("call:reject", { call_id: this.callId });
  };

  GurmadVoiceCall.prototype.end = function () {
    if (this.socket && this.callId && !this._ended) {
      this.socket.emit("call:end", { call_id: this.callId });
    }
    this.hangup(false);
  };

  GurmadVoiceCall.prototype._ensurePc = function () {
    var self = this;
    if (self.pc) return self.pc;
    self.pc = new RTCPeerConnection({ iceServers: self.iceServers });
    self.remoteStream = new MediaStream();
    self._ensureRemoteAudio();

    if (self.localStream) {
      self.localStream.getTracks().forEach(function (track) {
        self.pc.addTrack(track, self.localStream);
      });
    }

    self.pc.onicecandidate = function (ev) {
      if (ev.candidate && self.socket && self.callId) {
        self.socket.emit("call:ice-candidate", {
          call_id: self.callId,
          candidate: ev.candidate.toJSON()
        });
      }
    };

    self.pc.ontrack = function (ev) {
      if (ev.streams && ev.streams[0]) {
        self.remoteStream = ev.streams[0];
      } else if (ev.track) {
        self.remoteStream.addTrack(ev.track);
      }
      self._ensureRemoteAudio();
      self._remoteAudio.srcObject = self.remoteStream;
      var play = self._remoteAudio.play();
      if (play && play.catch) play.catch(function () {});
      self._maybeMarkConnected();
    };

    self.pc.onconnectionstatechange = function () {
      if (!self.pc) return;
      var st = self.pc.connectionState;
      if (st === "connected") {
        self._emit("media", { state: "connected" });
        self._maybeMarkConnected();
      } else if (st === "connecting") {
        self._emit("media", { state: "connecting" });
      } else if (st === "disconnected") {
        self._emit("reconnecting", { reason: "webrtc_disconnected" });
      } else if (st === "failed") {
        self._emit("failed", { message: "Connection Failed" });
      } else if (st === "closed") {
        self._emit("media", { state: "closed" });
      }
    };

    self.pc.oniceconnectionstatechange = function () {
      if (!self.pc) return;
      var st = self.pc.iceConnectionState;
      if (st === "connected" || st === "completed") {
        self._maybeMarkConnected();
      } else if (st === "failed") {
        self._emit("failed", { message: "Connection Failed" });
      } else if (st === "disconnected") {
        self._emit("reconnecting", { reason: "ice_disconnected" });
      }
    };

    return self.pc;
  };

  GurmadVoiceCall.prototype._ensureRemoteAudio = function () {
    if (this._remoteAudio) return;
    var audio = document.createElement("audio");
    audio.autoplay = true;
    audio.playsInline = true;
    audio.setAttribute("playsinline", "true");
    audio.setAttribute("aria-hidden", "true");
    audio.style.display = "none";
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
    this.socket.emit("call:connected", { call_id: this.callId });
    this._emit("connected", { call_id: this.callId, status: "connected" });
  };

  GurmadVoiceCall.prototype._startAsOfferer = function () {
    var self = this;
    if (self._offerStarted || self._ended) return;
    if (!self.localStream) {
      // Mic not ready yet — retry shortly
      setTimeout(function () {
        if (!self._ended) self._startAsOfferer();
      }, 200);
      return;
    }
    self._offerStarted = true;
    self._ensurePc();
    self.pc.createOffer({ offerToReceiveAudio: true }).then(function (offer) {
      return self.pc.setLocalDescription(offer).then(function () {
        self.socket.emit("call:offer", { call_id: self.callId, sdp: offer });
      });
    }).catch(function (err) {
      self._offerStarted = false;
      self._emit("failed", { message: (err && err.message) || "WebRTC offer failed" });
    });
  };

  GurmadVoiceCall.prototype._onRemoteOffer = function (sdp) {
    var self = this;
    if (!sdp || self._ended) return;
    self._ensurePc();
    self.pc.setRemoteDescription(new RTCSessionDescription(sdp)).then(function () {
      self._remoteDescSet = true;
      self._flushIce();
      return self.pc.createAnswer();
    }).then(function (answer) {
      return self.pc.setLocalDescription(answer).then(function () {
        self.socket.emit("call:answer", { call_id: self.callId, sdp: answer });
      });
    }).catch(function (err) {
      self._emit("failed", { message: (err && err.message) || "WebRTC answer failed" });
    });
  };

  GurmadVoiceCall.prototype._onRemoteAnswer = function (sdp) {
    var self = this;
    if (!sdp || !self.pc || self._ended) return;
    self.pc.setRemoteDescription(new RTCSessionDescription(sdp)).then(function () {
      self._remoteDescSet = true;
      self._flushIce();
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
    }
    return this.speakerOn;
  };

  GurmadVoiceCall.prototype.toggleSpeaker = function () {
    return this.setSpeaker(!this.speakerOn);
  };

  GurmadVoiceCall.prototype.hangup = function (notify) {
    if (this._ended) return;
    this._ended = true;
    if (notify && this.socket && this.callId) {
      try {
        this.socket.emit("call:end", { call_id: this.callId });
      } catch (e) {}
    }
    if (this.pc) {
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
