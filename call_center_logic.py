"""
Call Center Emergency Dispatch — business logic.

Stores call sessions separately from citizen SOS emergencies.
Dispatch reuses hospital_logic distance helpers and RESPONSE_STATIONS from app.
"""
from datetime import datetime

import hospital_logic as hl

CALLS_STORE = "call_center_calls"
CALL_CENTER_SETTINGS_KEY = "call_center"

CALL_STATUSES = (
    "ringing",
    "accepted",
    "connecting",
    "connected",
    "answered",
    "in_progress",
    "dispatched",
    "completed",
    "cancelled",
    "missed",
    "ended",
    "rejected",
    "failed",
)

# Active in-app voice sessions (WebRTC)
VOICE_ACTIVE = frozenset({"ringing", "accepted", "connecting", "connected", "answered", "in_progress"})
VOICE_CLOSED = frozenset({"ended", "rejected", "missed", "failed", "completed", "cancelled"})

EMERGENCY_TYPE_OPTIONS = (
    ("medical", "Medical", "hospital"),
    ("fire", "Fire", "fire"),
    ("security", "Police", "police"),
    ("accident", "Accident", "police"),
    ("family_help", "Family Emergency", "hospital"),
    ("other", "Other", "hospital"),
)

TYPE_TO_TEAM = {t: team for t, _label, team in EMERGENCY_TYPE_OPTIONS}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(value):
    if not value:
        return datetime.min
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(value)[:19], fmt)
        except ValueError:
            pass
    return datetime.min


def default_call_center_settings():
    return {
        "enabled": True,
        "phone_primary": "",
        "phone_secondary": "",
        "priority_medical": 1,
        "priority_fire": 1,
        "priority_police": 1,
        "priority_accident": 2,
        "priority_family_help": 2,
        "priority_other": 3,
        "auto_assign_nearest": True,
        "max_wait_seconds": 120,
        "operator_heartbeat_sec": 45,
    }


def load_calls(read_fn, save_fn=None):
    data = read_fn(CALLS_STORE, {"calls": [], "next_id": 1})
    data.setdefault("calls", [])
    data.setdefault("next_id", 1)
    return data


def save_calls(data, save_fn):
    save_fn(CALLS_STORE, data)


def get_call_by_id(data, call_id):
    for c in data.get("calls", []):
        if c.get("id") == call_id:
            return c
    return None


def normalize_call(call):
    call.setdefault("status", "ringing")
    call.setdefault("emergency_types", [])
    call.setdefault("dispatched_to", [])
    call.setdefault("emergency_ids", [])
    call.setdefault("nearest", {})
    call.setdefault("operator_id", None)
    call.setdefault("operator_name", "")
    call.setdefault("notes", "")
    call.setdefault("device_info", {})
    call.setdefault("duration_sec", 0)
    call.setdefault("end_time", None)
    call.setdefault("final_status", "")
    call.setdefault("location_updated_at", None)
    call.setdefault("voice_mode", False)
    call.setdefault("ended_by", None)
    call.setdefault("media_connected_at", None)
    return call


def _station_candidates(stations, read_fn):
    """
    Build police/fire candidate lists for nearest ranking.
    Accepts:
      - list of station rows
      - dict {police: row|list, fire: row|list} (legacy settings map)
      - None → load all open stations from facility_registry
    """
    candidates = {"police": [], "fire": []}

    def _add(row):
        if not isinstance(row, dict):
            return
        kind = (row.get("kind") or "").strip().lower()
        if kind not in candidates:
            # Legacy single map entries often omit kind; infer from caller key later
            return
        if (row.get("operating_status") or "open") == "closed":
            return
        if row.get("latitude") is None or row.get("longitude") is None:
            return
        candidates[kind].append(row)

    if isinstance(stations, list):
        for s in stations:
            _add(s)
    elif isinstance(stations, dict):
        for key in ("police", "fire"):
            val = stations.get(key)
            if isinstance(val, list):
                for s in val:
                    row = dict(s or {})
                    row.setdefault("kind", key)
                    _add(row)
            elif isinstance(val, dict) and val.get("latitude") is not None:
                row = dict(val)
                row.setdefault("kind", key)
                _add(row)

    if not candidates["police"] and not candidates["fire"]:
        try:
            import facility_registry as fr
            for s in fr.open_stations_with_coords(read_fn):
                _add(s)
        except Exception:
            pass
    return candidates


def create_incoming_call(payload, read_fn, save_fn, stations=None):
    """Citizen initiates Call Emergency Center — silent location push."""
    data = load_calls(read_fn, save_fn)
    cid = data["next_id"]
    data["next_id"] = cid + 1

    lat = payload.get("latitude")
    lng = payload.get("longitude")
    try:
        lat, lng = float(lat), float(lng)
        lat, lng = hl.validate_coordinates(lat, lng)
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc) or "Valid GPS location within Somalia is required.") from exc

    address = (payload.get("address") or payload.get("district") or "").strip()
    if not address:
        address = f"GPS {lat:.5f}, {lng:.5f}"

    voice_mode = bool(payload.get("voice_mode", True))
    call = normalize_call({
        "id": cid,
        "user_id": payload.get("user_id"),
        "caller_name": (payload.get("name") or "Unknown Caller").strip(),
        "phone": (payload.get("phone") or "").strip() or "Not provided",
        "latitude": lat,
        "longitude": lng,
        "address": address,
        "district": payload.get("district") or address,
        "status": "ringing",
        "start_time": _now(),
        "device_info": payload.get("device_info") or {},
        "source": "call_center",
        "accuracy_m": payload.get("accuracy_m"),
        "voice_mode": voice_mode,
        "ended_by": None,
        "media_connected_at": None,
    })
    call["nearest"] = find_nearest_responders(lat, lng, read_fn, save_fn, stations=stations)
    data["calls"].append(call)
    save_calls(data, save_fn)
    return call


def _ids_equal(a, b):
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return a == b and a is not None


def operator_has_active_voice(operator_id, read_fn, save_fn, exclude_call_id=None):
    data = load_calls(read_fn, save_fn)
    busy = {"accepted", "connecting", "connected", "answered", "in_progress"}
    for call in data.get("calls") or []:
        if call.get("status") not in busy:
            continue
        if exclude_call_id is not None and _ids_equal(call.get("id"), exclude_call_id):
            continue
        if _ids_equal(call.get("operator_id"), operator_id):
            return True
    return False


def claim_voice_call(call_id, operator, read_fn, save_fn):
    """
    Atomically assign a ringing voice call to one operator.
    Raises ValueError if already taken / closed.
    """
    data = load_calls(read_fn, save_fn)
    call = get_call_by_id(data, call_id)
    if not call:
        raise ValueError("Call not found.")
    if call.get("status") in VOICE_CLOSED:
        raise ValueError("Call already closed.")
    oid = call.get("operator_id")
    if oid and not _ids_equal(oid, operator.get("id")):
        raise ValueError("Call already accepted by another operator.")
    if call.get("status") not in ("ringing", "accepted", "connecting"):
        if oid and _ids_equal(oid, operator.get("id")):
            return call
        raise ValueError("Call is not available to accept.")
    if operator_has_active_voice(operator.get("id"), read_fn, save_fn, exclude_call_id=call_id):
        raise ValueError("You are already on another call.")
    call["operator_id"] = operator.get("id")
    call["operator_name"] = operator.get("name") or "Operator"
    call["status"] = "connecting"
    call["answered_at"] = call.get("answered_at") or _now()
    call["voice_mode"] = True
    save_calls(data, save_fn)
    return call


def mark_voice_connected(call_id, read_fn, save_fn):
    data = load_calls(read_fn, save_fn)
    call = get_call_by_id(data, call_id)
    if not call:
        raise ValueError("Call not found.")
    if call.get("status") in VOICE_CLOSED:
        raise ValueError("Call already closed.")
    call["status"] = "connected"
    call["media_connected_at"] = _now()
    if not call.get("answered_at"):
        call["answered_at"] = call["media_connected_at"]
    # Keep legacy dashboard filters happy
    call.setdefault("final_status", "")
    save_calls(data, save_fn)
    return call


def end_voice_call(call_id, ended_by, final_status, read_fn, save_fn, operator=None):
    """Terminate voice/session call. Idempotent if already closed."""
    data = load_calls(read_fn, save_fn)
    call = get_call_by_id(data, call_id)
    if not call:
        raise ValueError("Call not found.")
    if call.get("status") in VOICE_CLOSED:
        return call
    if operator and not call.get("operator_id"):
        call["operator_id"] = operator.get("id")
        call["operator_name"] = operator.get("name") or "Operator"
    status = final_status if final_status in CALL_STATUSES else "ended"
    call["status"] = status
    call["final_status"] = status
    call["ended_by"] = ended_by
    call["end_time"] = _now()
    start = _parse_dt(call.get("media_connected_at") or call.get("answered_at") or call.get("start_time"))
    end = _parse_dt(call["end_time"])
    call["duration_sec"] = max(0, int((end - start).total_seconds()))
    save_calls(data, save_fn)
    return call


def find_nearest_responders(lat, lng, read_fn, save_fn, stations=None):
    """
    Nearest hospital (via hospital_logic) + nearest open police/fire station by distance.
    stations: optional list of station rows, or legacy {police, fire} map.
    When omitted, loads all open stations from facility_registry.
    """
    result = {"hospital": None, "police": None, "fire": None}
    hdata = hl.seed_hospitals_if_empty(read_fn, save_fn)
    ranked = hl.hospitals_by_distance(lat, lng, hdata["hospitals"], emergency_only=True)
    if ranked:
        dist, hospital = ranked[0]
        result["hospital"] = {
            "id": hospital["id"],
            "name": hospital["name"],
            "phone": hospital.get("phone", ""),
            "address": hospital.get("address", ""),
            "city": hospital.get("city", ""),
            "district": hospital.get("district", ""),
            "latitude": hospital["latitude"],
            "longitude": hospital["longitude"],
            "distance_km": round(dist, 2),
            "eta_minutes": max(3, int((dist / 40) * 60)),
            "ambulance_available": hospital.get("ambulance_available", False),
        }

    candidates = _station_candidates(stations, read_fn)
    for key in ("police", "fire"):
        best = None
        best_dist = None
        for st in candidates.get(key) or []:
            try:
                dist = hl.haversine_km(lat, lng, float(st["latitude"]), float(st["longitude"]))
            except (TypeError, ValueError, KeyError):
                continue
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best = st
        if best is None:
            continue
        result[key] = {
            "id": best.get("id") if best.get("id") is not None else key,
            "name": best.get("name", key.title()),
            "phone": best.get("phone", "") or "",
            "address": best.get("address", "") or "",
            "city": best.get("city", "") or "",
            "district": best.get("district", "") or "",
            "latitude": best["latitude"],
            "longitude": best["longitude"],
            "distance_km": round(best_dist, 2),
            "eta_minutes": max(3, int((best_dist / 40) * 60)),
            "kind": key,
        }
    return result


def answer_call(call_id, operator, read_fn, save_fn, stations=None):
    data = load_calls(read_fn, save_fn)
    call = get_call_by_id(data, call_id)
    if not call:
        raise ValueError("Call not found.")
    if call.get("status") in VOICE_CLOSED:
        raise ValueError("Call already closed.")
    if call.get("operator_id") and not _ids_equal(call["operator_id"], operator.get("id")):
        if call.get("status") not in ("ringing",):
            raise ValueError("Call already handled by another operator.")
    call["operator_id"] = operator.get("id")
    call["operator_name"] = operator.get("name") or "Operator"
    # Preserve in-app voice media states; legacy non-voice stays "answered"
    if call.get("status") in ("connecting", "connected", "accepted"):
        pass
    else:
        call["status"] = "answered"
    call["answered_at"] = call.get("answered_at") or _now()
    if call.get("latitude") is not None and call.get("longitude") is not None:
        call["nearest"] = find_nearest_responders(
            call["latitude"], call["longitude"], read_fn, save_fn, stations=stations
        )
    save_calls(data, save_fn)
    return call


LOCATION_EDITABLE_STATUSES = frozenset({
    "ringing",
    "accepted",
    "connecting",
    "connected",
    "answered",
    "in_progress",
    "dispatched",
})


def update_call_location(call_id, payload, read_fn, save_fn, stations=None, operator=None):
    """Operator corrects caller GPS/address before (or while) dispatch."""
    data = load_calls(read_fn, save_fn)
    call = get_call_by_id(data, call_id)
    if not call:
        raise ValueError("Call not found.")
    if call.get("status") not in LOCATION_EDITABLE_STATUSES:
        raise ValueError("Cannot update location on a closed call.")

    lat = payload.get("latitude")
    lng = payload.get("longitude")
    try:
        lat, lng = float(lat), float(lng)
        lat, lng = hl.validate_coordinates(lat, lng)
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc) or "Valid GPS location within Somalia is required.") from exc

    address = (payload.get("address") or "").strip()
    if not address:
        address = (call.get("address") or "").strip() or f"GPS {lat:.5f}, {lng:.5f}"

    old_lat, old_lng = call.get("latitude"), call.get("longitude")
    call["latitude"] = lat
    call["longitude"] = lng
    call["address"] = address
    if payload.get("district") is not None:
        district = str(payload.get("district") or "").strip()
        if district:
            call["district"] = district
    elif address and address != call.get("district"):
        call["district"] = address
    if payload.get("accuracy_m") is not None:
        try:
            call["accuracy_m"] = float(payload.get("accuracy_m"))
        except (TypeError, ValueError):
            pass
    call["location_updated_at"] = _now()
    call["nearest"] = find_nearest_responders(lat, lng, read_fn, save_fn, stations=stations)

    stamp = call["location_updated_at"]
    who = (operator or {}).get("name") or "Operator"
    note_line = f"[{stamp}] Location updated by {who}: {old_lat}, {old_lng} → {lat}, {lng}"
    existing = (call.get("notes") or "").strip()
    call["notes"] = (existing + "\n" + note_line).strip() if existing else note_line

    save_calls(data, save_fn)
    return call


def set_call_status(call_id, status, read_fn, save_fn, notes=None, operator=None):
    if status not in CALL_STATUSES:
        raise ValueError("Invalid call status.")
    data = load_calls(read_fn, save_fn)
    call = get_call_by_id(data, call_id)
    if not call:
        raise ValueError("Call not found.")
    call["status"] = status
    if notes is not None:
        call["notes"] = notes
    if operator and not call.get("operator_id"):
        call["operator_id"] = operator.get("id")
        call["operator_name"] = operator.get("name") or "Operator"
    if status in ("completed", "cancelled", "missed"):
        call["end_time"] = _now()
        call["final_status"] = status
        start = _parse_dt(call.get("start_time"))
        end = _parse_dt(call["end_time"])
        call["duration_sec"] = max(0, int((end - start).total_seconds()))
    elif status == "in_progress":
        call["status"] = "in_progress"
    save_calls(data, save_fn)
    return call


def record_dispatch(call_id, emergency_types, emergency_ids, dispatched_to, read_fn, save_fn):
    data = load_calls(read_fn, save_fn)
    call = get_call_by_id(data, call_id)
    if not call:
        raise ValueError("Call not found.")
    types = []
    for t in emergency_types or []:
        t = str(t).strip().lower().replace(" ", "_")
        if t == "police":
            t = "security"
        if t == "family" or t == "family_emergency":
            t = "family_help"
        if t in TYPE_TO_TEAM or t == "other":
            types.append(t if t != "other" else "medical")
    call["emergency_types"] = list(dict.fromkeys(types))
    call["emergency_type"] = call["emergency_types"][0] if call["emergency_types"] else "medical"
    call["emergency_ids"] = list(emergency_ids or [])
    call["dispatched_to"] = list(dict.fromkeys(dispatched_to or []))
    call["status"] = "dispatched"
    call["dispatched_at"] = _now()
    save_calls(data, save_fn)
    return call


def active_calls(read_fn, save_fn=None):
    data = load_calls(read_fn, save_fn)
    active = {
        "ringing",
        "accepted",
        "connecting",
        "connected",
        "answered",
        "in_progress",
        "dispatched",
    }
    status_rank = {
        "ringing": 0,
        "accepted": 1,
        "connecting": 1,
        "connected": 1,
        "answered": 1,
        "in_progress": 1,
        "dispatched": 2,
    }
    rows = [normalize_call(c) for c in data["calls"] if c.get("status") in active]
    # Newest first; ringing always above answered/dispatched
    rows.sort(
        key=lambda c: (
            status_rank.get(c.get("status"), 9),
            -(int(c.get("id") or 0)),
        )
    )
    return rows


def call_history(read_fn, save_fn=None, limit=100):
    data = load_calls(read_fn, save_fn)
    calls = sorted(
        data["calls"],
        key=lambda c: c.get("start_time") or "",
        reverse=True,
    )
    return [normalize_call(c) for c in calls[:limit]]


def call_stats(read_fn, save_fn=None, online_operator_ids=None):
    data = load_calls(read_fn, save_fn)
    today = datetime.now().date()
    ringing = waiting = in_progress = resolved_today = 0
    durations = []
    for c in data["calls"]:
        st = c.get("status")
        if st == "ringing":
            ringing += 1
            waiting += 1
        elif st in ("accepted", "connecting", "connected", "answered", "in_progress", "dispatched"):
            in_progress += 1
        start = _parse_dt(c.get("start_time"))
        if start.date() == today and st in (
            "completed",
            "cancelled",
            "dispatched",
            "ended",
            "rejected",
            "missed",
        ):
            if st in ("completed", "cancelled", "ended", "rejected", "missed"):
                resolved_today += 1
            if c.get("duration_sec"):
                durations.append(c["duration_sec"])
            elif c.get("answered_at"):
                ans = _parse_dt(c.get("answered_at"))
                durations.append(max(0, int((ans - start).total_seconds())))
    avg = round(sum(durations) / len(durations) / 60, 1) if durations else 0
    return {
        "operators_online": len(online_operator_ids or []),
        "incoming_calls": ringing,
        "calls_waiting": waiting,
        "calls_in_progress": in_progress,
        "resolved_today": resolved_today,
        "avg_response_minutes": avg,
        "total_calls": len(data["calls"]),
    }


def resolve_dispatch_types(selected):
    """Map UI selections to emergency type + team pairs (supports multi-dispatch)."""
    pairs = []
    for raw in selected or []:
        t = str(raw).strip().lower().replace(" ", "_")
        if t in ("police", "security"):
            t = "security"
        elif t in ("family", "family_emergency", "family_help"):
            t = "family_help"
        elif t == "other":
            t = "medical"
        team = TYPE_TO_TEAM.get(t)
        if not team:
            continue
        pairs.append((t, team))
    # One emergency per responder team (medical preferred over family for hospital)
    by_team = {}
    for t, team in pairs:
        if team not in by_team:
            by_team[team] = t
        elif team == "hospital" and t == "medical":
            by_team[team] = "medical"
    return [(etype, team) for team, etype in by_team.items()]


def mark_operator_heartbeat(user, read_fn_users, save_fn_users, load_users_fn, save_users_fn):
    """Touch last_seen_call_center on operator user record."""
    udata = load_users_fn()
    for u in udata["users"]:
        if u.get("id") == user.get("id"):
            u["last_seen_call_center"] = _now()
            save_users_fn(udata)
            return u
    return user


def online_operators(load_users_fn, heartbeat_sec=45):
    udata = load_users_fn()
    now = datetime.now()
    online = []
    for u in udata["users"]:
        if u.get("role") != "call_center" or u.get("status") == "blocked":
            continue
        seen = _parse_dt(u.get("last_seen_call_center") or u.get("last_login"))
        if (now - seen).total_seconds() <= heartbeat_sec:
            online.append({"id": u["id"], "name": u.get("name"), "email": u.get("email")})
    return online
