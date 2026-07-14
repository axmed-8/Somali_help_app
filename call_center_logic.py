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
    "answered",
    "in_progress",
    "dispatched",
    "completed",
    "cancelled",
    "missed",
)

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
        "phone_primary": "+252612000999",
        "phone_secondary": "+252612000998",
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
    return call


def create_incoming_call(payload, read_fn, save_fn):
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
    })
    call["nearest"] = find_nearest_responders(lat, lng, read_fn, save_fn)
    data["calls"].append(call)
    save_calls(data, save_fn)
    return call


def find_nearest_responders(lat, lng, read_fn, save_fn, stations=None):
    """
    Nearest hospital (via hospital_logic) + police/fire stations.
    stations: optional RESPONSE_STATIONS-like dict from app.
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

    stations = stations or {
        "fire": {"latitude": 2.052, "longitude": 45.328, "name": "Fire & Rescue Station", "phone": "+252612000911"},
        "police": {"latitude": 2.038, "longitude": 45.315, "name": "Police Response Unit", "phone": "+252612000912"},
    }
    for key in ("police", "fire"):
        st = stations.get(key)
        if not st:
            continue
        dist = hl.haversine_km(lat, lng, st["latitude"], st["longitude"])
        result[key] = {
            "id": key,
            "name": st.get("name", key.title()),
            "phone": st.get("phone", ""),
            "latitude": st["latitude"],
            "longitude": st["longitude"],
            "distance_km": round(dist, 2),
            "eta_minutes": max(3, int((dist / 40) * 60)),
        }
    return result


def answer_call(call_id, operator, read_fn, save_fn):
    data = load_calls(read_fn, save_fn)
    call = get_call_by_id(data, call_id)
    if not call:
        raise ValueError("Call not found.")
    if call.get("status") in ("completed", "cancelled", "missed"):
        raise ValueError("Call already closed.")
    if call.get("operator_id") and call["operator_id"] != operator.get("id"):
        if call.get("status") not in ("ringing",):
            raise ValueError("Call already handled by another operator.")
    call["operator_id"] = operator.get("id")
    call["operator_name"] = operator.get("name") or "Operator"
    call["status"] = "answered"
    call["answered_at"] = _now()
    if call.get("latitude") and call.get("longitude"):
        call["nearest"] = find_nearest_responders(
            call["latitude"], call["longitude"], read_fn, save_fn
        )
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
    active = {"ringing", "answered", "in_progress", "dispatched"}
    return [normalize_call(c) for c in data["calls"] if c.get("status") in active]


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
        elif st in ("answered", "in_progress"):
            in_progress += 1
        elif st == "dispatched":
            in_progress += 1
        start = _parse_dt(c.get("start_time"))
        if start.date() == today and st in ("completed", "cancelled", "dispatched"):
            if st in ("completed", "cancelled"):
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
