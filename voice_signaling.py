"""
WebRTC voice signaling for GurmadNet Call Center (Flask-SocketIO).

Events: call:start, call:incoming, call:accept, call:reject,
call:offer, call:answer, call:ice-candidate, call:connected,
call:end, call:failed, call:busy, call:taken
"""
from __future__ import annotations

import logging
import os
from functools import wraps

from flask import request, session

import call_center_logic as cc

logger = logging.getLogger(__name__)

socketio = None
_app_refs = {}
# Track live Socket.IO sids per user so brief reconnects / dual sockets
# do not hang up an active call prematurely.
_user_sids: dict = {}


def ice_servers():
    servers = [
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun1.l.google.com:19302"},
    ]
    # Optional TURN (set TURN_URLS / TURN_USERNAME / TURN_CREDENTIAL in env)
    turn_urls = (os.environ.get("TURN_URLS") or "").strip()
    if turn_urls:
        entry = {
            "urls": [u.strip() for u in turn_urls.split(",") if u.strip()],
        }
        user = (os.environ.get("TURN_USERNAME") or "").strip()
        cred = (os.environ.get("TURN_CREDENTIAL") or "").strip()
        if user:
            entry["username"] = user
        if cred:
            entry["credential"] = cred
        servers.append(entry)
    return servers


def init_socketio(app, *, read_json, save_json, get_user_by_id, now_str):
    """Attach SocketIO to Flask app. Returns socketio instance."""
    global socketio
    from flask_socketio import SocketIO, emit, join_room, leave_room

    # Threading is the stable default on Windows / Python 3.12+ and is fully
    # compatible with WebRTC signaling (Socket.IO only carries SDP/ICE; audio
    # is peer-to-peer). Override with SOCKETIO_ASYNC_MODE=eventlet|gevent if needed.
    forced = (os.environ.get("SOCKETIO_ASYNC_MODE") or "").strip().lower()
    if forced in ("threading", "eventlet", "gevent"):
        async_mode = forced
    else:
        async_mode = "threading"

    socketio = SocketIO(
        app,
        cors_allowed_origins="*",
        async_mode=async_mode,
        logger=False,
        engineio_logger=False,
        manage_session=False,
        ping_timeout=60,
        ping_interval=25,
    )
    logger.info("SocketIO async_mode=%s", socketio.async_mode)
    _app_refs.update(
        {
            "read_json": read_json,
            "save_json": save_json,
            "get_user_by_id": get_user_by_id,
            "now_str": now_str,
            "join_room": join_room,
            "leave_room": leave_room,
            "emit": emit,
        }
    )

    def _user():
        uid = session.get("user_id")
        if not uid:
            return None
        user, _ = get_user_by_id(uid)
        return user

    def _authed(required_roles=None):
        def decorator(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                user = _user()
                if not user:
                    emit("call:failed", {"message": "Not authenticated"})
                    return
                role = user.get("role")
                if required_roles and role not in required_roles:
                    emit("call:failed", {"message": "Forbidden"})
                    return
                return fn(user, *args, **kwargs)

            return wrapped

        return decorator

    def _track_sid(user_id, sid, add=True):
        try:
            key = int(user_id)
        except (TypeError, ValueError):
            key = user_id
        bucket = _user_sids.setdefault(key, set())
        if add:
            bucket.add(sid)
        else:
            bucket.discard(sid)
            if not bucket:
                _user_sids.pop(key, None)

    def _user_still_online(user_id):
        try:
            key = int(user_id)
        except (TypeError, ValueError):
            key = user_id
        return bool(_user_sids.get(key))

    def _accept_body(call):
        return {
            "call_id": call.get("id"),
            "status": call.get("status"),
            "operator_id": call.get("operator_id"),
            "operator_name": call.get("operator_name"),
            "call": _public_call(call),
            "ice_servers": ice_servers(),
        }

    def _emit_accept_to_peers(call):
        """Deliver accept to call room AND citizen personal room (race-safe)."""
        body = _accept_body(call)
        call_id = call.get("id")
        socketio.emit("call:accept", body, room=f"call_{call_id}")
        citizen_id = call.get("user_id")
        if citizen_id is not None:
            socketio.emit("call:accept", body, room=f"user_{citizen_id}")

    @socketio.on("connect")
    def on_connect():
        user = _user()
        if not user:
            return False
        role = user.get("role")
        _track_sid(user["id"], request.sid, True)
        join_room(f"user_{user['id']}")
        if role == "call_center":
            join_room("operators")
        logger.info("socket connect user=%s role=%s sid=%s", user.get("id"), role, request.sid)
        return True

    @socketio.on("disconnect")
    def on_disconnect():
        user = _user()
        if not user:
            return
        uid = user.get("id")
        _track_sid(uid, request.sid, False)
        role = user.get("role")
        # Grace period: dual sockets / brief reconnects must not kill the call.
        def _cleanup():
            try:
                socketio.sleep(4)
            except Exception:
                pass
            if _user_still_online(uid):
                return
            try:
                data = cc.load_calls(read_json, save_json)
                for call in list(data.get("calls") or []):
                    if call.get("status") not in cc.VOICE_ACTIVE:
                        continue
                    is_citizen = role == "citizen" and cc._ids_equal(call.get("user_id"), uid)
                    is_op = role == "call_center" and cc._ids_equal(call.get("operator_id"), uid)
                    if not (is_citizen or is_op):
                        continue
                    # Still ringing with no operator: mark missed for citizen leave
                    final = "missed" if (is_citizen and call.get("status") == "ringing") else "ended"
                    ended = cc.end_voice_call(
                        call["id"],
                        ended_by="citizen" if is_citizen else "operator",
                        final_status=final,
                        read_fn=read_json,
                        save_fn=save_json,
                    )
                    if ended:
                        body = {
                            "call_id": ended["id"],
                            "status": ended.get("status"),
                            "ended_by": ended.get("ended_by"),
                            "reason": "disconnect",
                        }
                        socketio.emit("call:end", body, room=f"call_{ended['id']}")
                        socketio.emit("call:end", body, room="operators")
                        if ended.get("user_id") is not None:
                            socketio.emit("call:end", body, room=f"user_{ended['user_id']}")
            except Exception:
                logger.exception("disconnect cleanup failed")

        try:
            socketio.start_background_task(_cleanup)
        except Exception:
            _cleanup()

    @socketio.on("call:join")
    @_authed()
    def on_join(user, payload=None):
        payload = payload or {}
        try:
            call_id = int(payload.get("call_id") or 0)
        except (TypeError, ValueError):
            emit("call:failed", {"message": "Invalid call_id"})
            return
        call = cc.get_call_by_id(cc.load_calls(read_json, save_json), call_id)
        if not call:
            emit("call:failed", {"message": "Call not found", "call_id": call_id})
            return
        role = user.get("role")
        if role == "citizen" and not cc._ids_equal(call.get("user_id"), user.get("id")):
            emit("call:failed", {"message": "Not your call", "call_id": call_id})
            return
        if role == "call_center" and call.get("operator_id") is not None:
            if not cc._ids_equal(call.get("operator_id"), user.get("id")) and call.get("status") != "ringing":
                emit("call:busy", {"call_id": call_id, "message": "Call taken by another operator"})
                return
        if call.get("status") in cc.VOICE_CLOSED:
            emit(
                "call:end",
                {
                    "call_id": call_id,
                    "status": call.get("status"),
                    "ended_by": call.get("ended_by"),
                    "reason": "already_closed",
                },
            )
            return
        join_room(f"call_{call_id}")
        emit(
            "call:joined",
            {
                "call_id": call_id,
                "status": call.get("status"),
                "ice_servers": ice_servers(),
                "call": _public_call(call),
            },
        )
        # If operator already accepted before citizen joined, catch them up now.
        if role == "citizen" and call.get("status") in ("accepted", "connecting", "connected"):
            emit("call:accept", _accept_body(call))

    @socketio.on("call:start")
    @_authed(("citizen",))
    def on_start(user, payload=None):
        """Citizen signals voice session is ready after REST initiate."""
        payload = payload or {}
        try:
            call_id = int(payload.get("call_id") or 0)
        except (TypeError, ValueError):
            emit("call:failed", {"message": "Invalid call_id"})
            return
        data = cc.load_calls(read_json, save_json)
        call = cc.get_call_by_id(data, call_id)
        if not call or not cc._ids_equal(call.get("user_id"), user.get("id")):
            emit("call:failed", {"message": "Call not found", "call_id": call_id})
            return
        if call.get("status") in cc.VOICE_CLOSED:
            emit(
                "call:end",
                {
                    "call_id": call_id,
                    "status": call.get("status"),
                    "ended_by": call.get("ended_by"),
                },
            )
            return
        call["voice_mode"] = True
        # Never stomp an already-accepted / connecting / connected call.
        if call.get("status") == "ringing" or not call.get("status"):
            call["status"] = "ringing"
            cc.save_calls(data, save_json)
            join_room(f"call_{call_id}")
            socketio.emit(
                "call:incoming",
                {
                    "call_id": call_id,
                    "call": _public_call(call),
                    "ice_servers": ice_servers(),
                },
                room="operators",
            )
            emit("call:ringing", {"call_id": call_id, "status": "ringing", "ice_servers": ice_servers()})
            return

        # Already claimed — make sure citizen is in the room and gets accept again.
        cc.save_calls(data, save_json)
        join_room(f"call_{call_id}")
        if call.get("status") in ("accepted", "connecting", "connected"):
            emit("call:accept", _accept_body(call))
            emit(
                "call:ringing",
                {
                    "call_id": call_id,
                    "status": call.get("status"),
                    "ice_servers": ice_servers(),
                },
            )

    @socketio.on("call:accept")
    @_authed(("call_center",))
    def on_accept(user, payload=None):
        payload = payload or {}
        try:
            call_id = int(payload.get("call_id") or 0)
        except (TypeError, ValueError):
            emit("call:failed", {"message": "Invalid call_id"})
            return
        try:
            call = cc.claim_voice_call(call_id, user, read_json, save_json)
        except ValueError as exc:
            emit("call:busy", {"call_id": call_id, "message": str(exc)})
            return
        join_room(f"call_{call_id}")
        _emit_accept_to_peers(call)
        socketio.emit(
            "call:taken",
            {
                "call_id": call_id,
                "operator_id": user.get("id"),
                "operator_name": user.get("name"),
            },
            room="operators",
        )

    @socketio.on("call:reject")
    @_authed(("call_center",))
    def on_reject(user, payload=None):
        payload = payload or {}
        try:
            call_id = int(payload.get("call_id") or 0)
        except (TypeError, ValueError):
            return
        try:
            call = cc.end_voice_call(
                call_id,
                ended_by="operator",
                final_status="rejected",
                read_fn=read_json,
                save_fn=save_json,
                operator=user,
            )
        except ValueError as exc:
            emit("call:failed", {"call_id": call_id, "message": str(exc)})
            return
        if call:
            body = {"call_id": call_id, "status": "rejected", "ended_by": "operator"}
            socketio.emit("call:reject", body, room=f"call_{call_id}")
            socketio.emit("call:reject", body, room="operators")
            if call.get("user_id") is not None:
                socketio.emit("call:reject", body, room=f"user_{call['user_id']}")

    @socketio.on("call:offer")
    @_authed()
    def on_offer(user, payload=None):
        _relay_signal(user, payload, "call:offer")

    @socketio.on("call:answer")
    @_authed()
    def on_answer(user, payload=None):
        _relay_signal(user, payload, "call:answer")

    @socketio.on("call:ice-candidate")
    @_authed()
    def on_ice(user, payload=None):
        _relay_signal(user, payload, "call:ice-candidate")

    @socketio.on("call:connected")
    @_authed()
    def on_connected(user, payload=None):
        payload = payload or {}
        try:
            call_id = int(payload.get("call_id") or 0)
        except (TypeError, ValueError):
            return
        if not _can_control(user, call_id):
            return
        try:
            call = cc.mark_voice_connected(call_id, read_json, save_json)
        except ValueError:
            return
        body = {
            "call_id": call_id,
            "status": "connected",
            "answered_at": call.get("answered_at"),
            "media_connected_at": call.get("media_connected_at"),
        }
        socketio.emit("call:connected", body, room=f"call_{call_id}")
        if call.get("user_id") is not None:
            socketio.emit("call:connected", body, room=f"user_{call['user_id']}")

    @socketio.on("call:end")
    @_authed()
    def on_end(user, payload=None):
        payload = payload or {}
        try:
            call_id = int(payload.get("call_id") or 0)
        except (TypeError, ValueError):
            return
        if not _can_control(user, call_id):
            emit("call:failed", {"call_id": call_id, "message": "Not allowed"})
            return
        role = "citizen" if user.get("role") == "citizen" else "operator"
        try:
            # Citizen hanging up while still ringing => missed for operators
            data = cc.load_calls(read_json, save_json)
            existing = cc.get_call_by_id(data, call_id)
            final = "ended"
            if role == "citizen" and existing and existing.get("status") == "ringing":
                final = "missed"
            call = cc.end_voice_call(
                call_id,
                ended_by=role,
                final_status=final,
                read_fn=read_json,
                save_fn=save_json,
                operator=user if role == "operator" else None,
            )
        except ValueError as exc:
            emit("call:failed", {"call_id": call_id, "message": str(exc)})
            return
        body = {
            "call_id": call_id,
            "status": call.get("status") if call else "ended",
            "ended_by": role,
            "duration_sec": (call or {}).get("duration_sec") or 0,
        }
        socketio.emit("call:end", body, room=f"call_{call_id}")
        socketio.emit("call:end", body, room="operators")
        if call and call.get("user_id") is not None:
            socketio.emit("call:end", body, room=f"user_{call['user_id']}")

    def _can_control(user, call_id):
        call = cc.get_call_by_id(cc.load_calls(read_json, save_json), call_id)
        if not call:
            return False
        if user.get("role") == "citizen":
            return cc._ids_equal(call.get("user_id"), user.get("id"))
        if user.get("role") == "call_center":
            if call.get("status") == "ringing":
                return True
            return cc._ids_equal(call.get("operator_id"), user.get("id"))
        return False

    def _relay_signal(user, payload, event_name):
        payload = payload or {}
        try:
            call_id = int(payload.get("call_id") or 0)
        except (TypeError, ValueError):
            return
        if not _can_control(user, call_id):
            return
        socketio.emit(
            event_name,
            {
                "call_id": call_id,
                "from_user_id": user.get("id"),
                "sdp": payload.get("sdp"),
                "candidate": payload.get("candidate"),
            },
            room=f"call_{call_id}",
            include_self=False,
        )

    def _public_call(call):
        return {
            "id": call.get("id"),
            "status": call.get("status"),
            "caller_name": call.get("caller_name"),
            "phone": call.get("phone"),
            "user_id": call.get("user_id"),
            "latitude": call.get("latitude"),
            "longitude": call.get("longitude"),
            "address": call.get("address"),
            "district": call.get("district"),
            "operator_id": call.get("operator_id"),
            "operator_name": call.get("operator_name"),
            "emergency_ids": call.get("emergency_ids") or [],
            "voice_mode": bool(call.get("voice_mode")),
            "start_time": call.get("start_time"),
            "answered_at": call.get("answered_at"),
            "ended_by": call.get("ended_by"),
        }

    app.extensions = getattr(app, "extensions", {}) or {}
    app.extensions["socketio"] = socketio
    return socketio


def emit_incoming_call(call):
    """Called from REST after initiate so operators get the event immediately."""
    if not socketio:
        return
    socketio.emit(
        "call:incoming",
        {
            "call_id": call.get("id"),
            "call": {
                "id": call.get("id"),
                "status": call.get("status"),
                "caller_name": call.get("caller_name"),
                "phone": call.get("phone"),
                "user_id": call.get("user_id"),
                "latitude": call.get("latitude"),
                "longitude": call.get("longitude"),
                "address": call.get("address"),
                "district": call.get("district"),
                "voice_mode": True,
                "start_time": call.get("start_time"),
                "emergency_ids": call.get("emergency_ids") or [],
            },
            "ice_servers": ice_servers(),
        },
        room="operators",
    )
