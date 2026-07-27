"""Facility registries: police/fire stations, ambulances, call centers (MySQL via read/save)."""
from datetime import datetime

STATIONS_STORE = "response_stations"
AMBULANCES_STORE = "ambulance_units"
CALL_CENTERS_STORE = "call_centers"

VALID_OPERATING = frozenset({"open", "limited", "closed"})
VALID_STATION_KINDS = frozenset({"police", "fire"})
# Dispatch essentials only — hospitals own fleet ops outside GurmadNet.
# "maintenance" accepted as alias of offline for older rows.
VALID_AMBULANCE_STATUS = frozenset({"available", "busy", "offline", "maintenance"})
DISPATCH_AMBULANCE_STATUS = frozenset({"available", "busy", "offline"})


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _fcoord(val):
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        raise ValueError("Invalid coordinates")


# ----- response stations (police / fire) -----

def normalize_station(row):
    r = dict(row or {})
    r.setdefault("kind", "police")
    r.setdefault("name", "")
    r.setdefault("city", "")
    r.setdefault("region", "")
    r.setdefault("district", "")
    r.setdefault("address", "")
    r.setdefault("latitude", None)
    r.setdefault("longitude", None)
    r.setdefault("phone", "")
    r.setdefault("operating_status", "open")
    r.setdefault("owner_user_id", None)
    r.setdefault("created_at", _now())
    r.setdefault("updated_at", _now())
    return r


def load_stations(read_fn, save_fn=None):
    return read_fn(STATIONS_STORE, {"stations": [], "next_id": 1})


def save_stations(data, save_fn):
    save_fn(STATIONS_STORE, data)


def get_station(data, sid):
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        return None
    for s in data.get("stations") or []:
        if s.get("id") == sid:
            return s
    return None


def create_station(payload, read_fn, save_fn):
    kind = (payload.get("kind") or "").strip().lower()
    if kind not in VALID_STATION_KINDS:
        raise ValueError("kind must be police or fire")
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("Name is required")
    status = (payload.get("operating_status") or "open").lower()
    if status not in VALID_OPERATING:
        raise ValueError("Invalid operating status")
    data = load_stations(read_fn)
    sid = data["next_id"]
    data["next_id"] = sid + 1
    row = normalize_station({
        "id": sid,
        "kind": kind,
        "name": name,
        "city": (payload.get("city") or "").strip(),
        "region": (payload.get("region") or "").strip(),
        "district": (payload.get("district") or "").strip(),
        "address": (payload.get("address") or "").strip(),
        "latitude": _fcoord(payload.get("latitude")),
        "longitude": _fcoord(payload.get("longitude")),
        "phone": (payload.get("phone") or "").strip(),
        "operating_status": status,
        "owner_user_id": payload.get("owner_user_id"),
        "created_at": _now(),
        "updated_at": _now(),
    })
    data["stations"].append(row)
    save_stations(data, save_fn)
    return row


def update_station(sid, payload, read_fn, save_fn):
    data = load_stations(read_fn)
    row = get_station(data, sid)
    if not row:
        raise ValueError("Station not found")
    if "name" in payload and payload["name"] is not None:
        name = str(payload["name"]).strip()
        if not name:
            raise ValueError("Name is required")
        row["name"] = name
    for field in ("city", "region", "district", "address", "phone"):
        if field in payload and payload[field] is not None:
            row[field] = str(payload[field]).strip()
    if "kind" in payload and payload["kind"] is not None:
        kind = str(payload["kind"]).strip().lower()
        if kind not in VALID_STATION_KINDS:
            raise ValueError("kind must be police or fire")
        row["kind"] = kind
    if "operating_status" in payload and payload["operating_status"] is not None:
        st = str(payload["operating_status"]).lower()
        if st not in VALID_OPERATING:
            raise ValueError("Invalid operating status")
        row["operating_status"] = st
    if "latitude" in payload or "longitude" in payload:
        row["latitude"] = _fcoord(payload.get("latitude", row.get("latitude")))
        row["longitude"] = _fcoord(payload.get("longitude", row.get("longitude")))
    if "owner_user_id" in payload:
        row["owner_user_id"] = payload.get("owner_user_id") or None
    row["updated_at"] = _now()
    save_stations(data, save_fn)
    return row


def delete_station(sid, read_fn, save_fn, users_data=None, emergencies_data=None):
    data = load_stations(read_fn)
    row = get_station(data, sid)
    if not row:
        raise ValueError("Station not found")
    sid = int(sid)
    if emergencies_data:
        active = {
            "pending", "dispatched", "in_progress", "pending_hospital", "accepted",
        }
        for em in emergencies_data.get("emergencies") or []:
            if em.get("assigned_station_id") == sid and (em.get("status") or "").lower() in active:
                raise ValueError("Cannot delete: station has active emergencies assigned")
    data["stations"] = [s for s in data["stations"] if s.get("id") != sid]
    save_stations(data, save_fn)
    if users_data:
        changed = False
        for u in users_data.get("users") or []:
            if u.get("station_id") == sid:
                u["station_id"] = None
                changed = True
        if changed:
            return data, users_data, True
    return data, users_data, False


def open_stations_with_coords(read_fn, kind=None):
    """All non-closed police/fire stations that have coordinates (for nearest ranking)."""
    data = load_stations(read_fn)
    out = []
    for s in data.get("stations") or []:
        k = (s.get("kind") or "").strip().lower()
        if k not in VALID_STATION_KINDS:
            continue
        if kind and k != kind:
            continue
        if (s.get("operating_status") or "open") == "closed":
            continue
        if s.get("latitude") is None or s.get("longitude") is None:
            continue
        out.append(normalize_station(s))
    return out


def stations_as_settings_map(read_fn):
    """Compat shape for get_response_stations: {police: {...}, fire: {...}} first open of each."""
    data = load_stations(read_fn)
    out = {}
    for kind in ("police", "fire"):
        for s in data.get("stations") or []:
            if s.get("kind") != kind:
                continue
            if (s.get("operating_status") or "open") == "closed":
                continue
            if s.get("latitude") is None or s.get("longitude") is None:
                continue
            out[kind] = {
                "id": s.get("id"),
                "name": s.get("name"),
                "latitude": s.get("latitude"),
                "longitude": s.get("longitude"),
                "phone": s.get("phone") or "",
                "city": s.get("city") or "",
                "district": s.get("district") or "",
            }
            break
    return out


# ----- ambulances (hospital-owned; GurmadNet stores dispatch essentials only) -----

def _normalize_ambulance_status(status):
    st = (status or "available").strip().lower()
    if st == "maintenance":
        st = "offline"
    if st not in DISPATCH_AMBULANCE_STATUS:
        raise ValueError("Status must be available, busy, or offline")
    return st


def _safe_driver_photo_url(raw):
    url = str(raw or "").strip()
    if not url:
        return ""
    path = url.split("?", 1)[0].split("#", 1)[0]
    marker = "/static/uploads/ambulances/"
    idx = path.find(marker)
    if idx < 0:
        raise ValueError("Invalid ambulance photo")
    return path[idx:]


def _safe_vehicle_photo_url(raw):
    return _safe_driver_photo_url(raw)


def normalize_ambulance(row):
    r = dict(row or {})
    r.setdefault("hospital_id", None)
    r.setdefault("call_sign", "")
    r.setdefault("plate_number", "")
    st = (r.get("status") or "available").lower()
    if st == "maintenance":
        st = "offline"
    r["status"] = st if st in DISPATCH_AMBULANCE_STATUS else "offline"
    r.setdefault("latitude", None)
    r.setdefault("longitude", None)
    r.setdefault("driver_name", "")
    r.setdefault("driver_phone", "")
    r.setdefault("driver_photo_url", "")
    r.setdefault("vehicle_photo_url", "")
    r.setdefault("gps_share_token", "")
    r.setdefault("notes", "")
    r.setdefault("created_at", _now())
    r.setdefault("updated_at", _now())
    return r


def ambulance_dispatch_view(row, hospital_name=""):
    """Essentials GurmadNet needs for coordination — not fleet management."""
    a = normalize_ambulance(row)
    token = (a.get("gps_share_token") or "").strip()
    return {
        "id": a.get("id"),
        "hospital_id": a.get("hospital_id"),
        "hospital_name": hospital_name or "",
        "call_sign": a.get("call_sign") or "",
        "status": a.get("status") or "offline",
        "latitude": a.get("latitude"),
        "longitude": a.get("longitude"),
        "driver_name": a.get("driver_name") or "",
        "driver_phone": a.get("driver_phone") or "",
        "driver_photo_url": a.get("driver_photo_url") or "",
        "vehicle_photo_url": a.get("vehicle_photo_url") or "",
        "gps_share_token": token,
        "gps_share_path": f"/driver/gps/{token}" if token else "",
        "updated_at": a.get("updated_at"),
    }


def load_ambulances(read_fn, save_fn=None):
    return read_fn(AMBULANCES_STORE, {"ambulances": [], "next_id": 1})


def save_ambulances(data, save_fn):
    save_fn(AMBULANCES_STORE, data)


def get_ambulance(data, aid):
    try:
        aid = int(aid)
    except (TypeError, ValueError):
        return None
    for a in data.get("ambulances") or []:
        if a.get("id") == aid:
            return a
    return None


def list_hospital_ambulances(data, hospital_id):
    try:
        hospital_id = int(hospital_id)
    except (TypeError, ValueError):
        return []
    return [
        normalize_ambulance(a)
        for a in (data.get("ambulances") or [])
        if a.get("hospital_id") == hospital_id
    ]


def create_ambulance(payload, read_fn, save_fn):
    try:
        hid = int(payload.get("hospital_id") or 0)
    except (TypeError, ValueError):
        hid = 0
    if not hid:
        raise ValueError("hospital_id is required")
    call_sign = (payload.get("call_sign") or "").strip()
    if not call_sign:
        raise ValueError("Unit label / call sign is required")
    status = _normalize_ambulance_status(payload.get("status") or "available")
    driver_phone = (payload.get("driver_phone") or "").strip()
    if status == "available" and not driver_phone:
        raise ValueError("Driver phone is required when an ambulance is available for dispatch")
    data = load_ambulances(read_fn)
    aid = data["next_id"]
    data["next_id"] = aid + 1
    row = normalize_ambulance({
        "id": aid,
        "hospital_id": hid,
        "call_sign": call_sign,
        "plate_number": (payload.get("plate_number") or "").strip(),
        "status": status,
        "latitude": _fcoord(payload.get("latitude")),
        "longitude": _fcoord(payload.get("longitude")),
        "driver_name": (payload.get("driver_name") or "").strip(),
        "driver_phone": driver_phone,
        "driver_photo_url": _safe_driver_photo_url(payload.get("driver_photo_url")),
        "vehicle_photo_url": _safe_vehicle_photo_url(payload.get("vehicle_photo_url")),
        "notes": (payload.get("notes") or "").strip()[:200],
        "created_at": _now(),
        "updated_at": _now(),
    })
    data["ambulances"].append(row)
    save_ambulances(data, save_fn)
    return row


def update_ambulance(aid, payload, read_fn, save_fn):
    data = load_ambulances(read_fn)
    row = get_ambulance(data, aid)
    if not row:
        raise ValueError("Ambulance not found")
    # hospital_id is fixed after create — hospitals own their units
    if "call_sign" in payload and payload["call_sign"] is not None:
        cs = str(payload["call_sign"]).strip()
        if not cs:
            raise ValueError("Unit label / call sign is required")
        row["call_sign"] = cs
    if "plate_number" in payload and payload["plate_number"] is not None:
        row["plate_number"] = str(payload["plate_number"]).strip()
    if "status" in payload and payload["status"] is not None:
        row["status"] = _normalize_ambulance_status(payload["status"])
    if "driver_name" in payload and payload["driver_name"] is not None:
        row["driver_name"] = str(payload["driver_name"]).strip()
    if "driver_phone" in payload and payload["driver_phone"] is not None:
        row["driver_phone"] = str(payload["driver_phone"]).strip()
    if "driver_photo_url" in payload:
        row["driver_photo_url"] = _safe_driver_photo_url(payload.get("driver_photo_url"))
    if "vehicle_photo_url" in payload:
        row["vehicle_photo_url"] = _safe_vehicle_photo_url(payload.get("vehicle_photo_url"))
    if "notes" in payload and payload["notes"] is not None:
        row["notes"] = str(payload["notes"]).strip()[:200]
    if "latitude" in payload or "longitude" in payload:
        row["latitude"] = _fcoord(payload.get("latitude", row.get("latitude")))
        row["longitude"] = _fcoord(payload.get("longitude", row.get("longitude")))
    if (row.get("status") or "") == "available" and not (row.get("driver_phone") or "").strip():
        raise ValueError("Driver phone is required when an ambulance is available for dispatch")
    row["updated_at"] = _now()
    save_ambulances(data, save_fn)
    return normalize_ambulance(row)


def delete_ambulance(aid, read_fn, save_fn):
    data = load_ambulances(read_fn)
    row = get_ambulance(data, aid)
    if not row:
        raise ValueError("Ambulance not found")
    data["ambulances"] = [a for a in data["ambulances"] if a.get("id") != int(aid)]
    save_ambulances(data, save_fn)
    return True


def get_ambulance_by_gps_token(token, read_fn):
    token = (token or "").strip()
    if not token or len(token) < 16:
        return None
    data = load_ambulances(read_fn)
    for a in data.get("ambulances") or []:
        if (a.get("gps_share_token") or "").strip() == token:
            return normalize_ambulance(a)
    return None


def issue_ambulance_gps_token(aid, read_fn, save_fn, rotate=False):
    """Create or return a stable share token for driver GPS page."""
    import secrets

    data = load_ambulances(read_fn)
    row = get_ambulance(data, aid)
    if not row:
        raise ValueError("Ambulance not found")
    existing = (row.get("gps_share_token") or "").strip()
    if existing and not rotate:
        return normalize_ambulance(row)
    # Ensure uniqueness across fleet
    used = {
        (a.get("gps_share_token") or "").strip()
        for a in (data.get("ambulances") or [])
        if (a.get("gps_share_token") or "").strip()
    }
    token = secrets.token_urlsafe(24)
    while token in used:
        token = secrets.token_urlsafe(24)
    row["gps_share_token"] = token
    row["updated_at"] = _now()
    save_ambulances(data, save_fn)
    return normalize_ambulance(row)


def revoke_ambulance_gps_token(aid, read_fn, save_fn):
    data = load_ambulances(read_fn)
    row = get_ambulance(data, aid)
    if not row:
        raise ValueError("Ambulance not found")
    row["gps_share_token"] = ""
    row["updated_at"] = _now()
    save_ambulances(data, save_fn)
    return normalize_ambulance(row)


def mark_ambulance_busy(aid, read_fn, save_fn):
    """Used when GurmadNet assigns a unit to an emergency."""
    return update_ambulance(aid, {"status": "busy"}, read_fn, save_fn)


def mark_ambulance_available(aid, read_fn, save_fn):
    """Release a unit after the emergency case ends."""
    return update_ambulance(aid, {"status": "available"}, read_fn, save_fn)


def sync_hospital_ambulance_counts(hospitals_data, ambulances_data):
    """Available units are authoritative for hospital dispatch readiness."""
    available = {}
    for a in ambulances_data.get("ambulances") or []:
        hid = a.get("hospital_id")
        if not hid:
            continue
        st = (a.get("status") or "").lower()
        if st == "maintenance":
            st = "offline"
        if st == "available":
            available[hid] = available.get(hid, 0) + 1
    changed = False
    for h in hospitals_data.get("hospitals") or []:
        hid = h.get("id")
        n = available.get(hid, 0)
        if h.get("ambulance_count") != n or bool(h.get("ambulance_available")) != (n > 0):
            h["ambulance_count"] = n
            h["ambulance_available"] = n > 0
            h["updated_at"] = _now()
            changed = True
    return changed


# ----- call centers -----

def normalize_call_center(row):
    r = dict(row or {})
    r.setdefault("name", "")
    r.setdefault("city", "")
    r.setdefault("region", "")
    r.setdefault("district", "")
    r.setdefault("address", "")
    r.setdefault("latitude", None)
    r.setdefault("longitude", None)
    r.setdefault("phone", "")
    r.setdefault("operating_status", "open")
    r.setdefault("owner_user_id", None)
    r.setdefault("created_at", _now())
    r.setdefault("updated_at", _now())
    return r


def load_call_centers(read_fn, save_fn=None):
    return read_fn(CALL_CENTERS_STORE, {"call_centers": [], "next_id": 1})


def save_call_centers(data, save_fn):
    save_fn(CALL_CENTERS_STORE, data)


def get_call_center(data, cid):
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        return None
    for c in data.get("call_centers") or []:
        if c.get("id") == cid:
            return c
    return None


def create_call_center(payload, read_fn, save_fn):
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("Name is required")
    status = (payload.get("operating_status") or "open").lower()
    if status not in VALID_OPERATING:
        raise ValueError("Invalid operating status")
    data = load_call_centers(read_fn)
    cid = data["next_id"]
    data["next_id"] = cid + 1
    row = normalize_call_center({
        "id": cid,
        "name": name,
        "city": (payload.get("city") or "").strip(),
        "region": (payload.get("region") or "").strip(),
        "district": (payload.get("district") or "").strip(),
        "address": (payload.get("address") or "").strip(),
        "latitude": _fcoord(payload.get("latitude")),
        "longitude": _fcoord(payload.get("longitude")),
        "phone": (payload.get("phone") or "").strip(),
        "operating_status": status,
        "owner_user_id": payload.get("owner_user_id"),
        "created_at": _now(),
        "updated_at": _now(),
    })
    data["call_centers"].append(row)
    save_call_centers(data, save_fn)
    return row


def update_call_center(cid, payload, read_fn, save_fn):
    data = load_call_centers(read_fn)
    row = get_call_center(data, cid)
    if not row:
        raise ValueError("Call center not found")
    if "name" in payload and payload["name"] is not None:
        name = str(payload["name"]).strip()
        if not name:
            raise ValueError("Name is required")
        row["name"] = name
    for field in ("city", "region", "district", "address", "phone"):
        if field in payload and payload[field] is not None:
            row[field] = str(payload[field]).strip()
    if "operating_status" in payload and payload["operating_status"] is not None:
        st = str(payload["operating_status"]).lower()
        if st not in VALID_OPERATING:
            raise ValueError("Invalid operating status")
        row["operating_status"] = st
    if "latitude" in payload or "longitude" in payload:
        row["latitude"] = _fcoord(payload.get("latitude", row.get("latitude")))
        row["longitude"] = _fcoord(payload.get("longitude", row.get("longitude")))
    if "owner_user_id" in payload:
        row["owner_user_id"] = payload.get("owner_user_id") or None
    row["updated_at"] = _now()
    save_call_centers(data, save_fn)
    return row


def delete_call_center(cid, read_fn, save_fn, users_data=None):
    data = load_call_centers(read_fn)
    row = get_call_center(data, cid)
    if not row:
        raise ValueError("Call center not found")
    cid = int(cid)
    data["call_centers"] = [c for c in data["call_centers"] if c.get("id") != cid]
    save_call_centers(data, save_fn)
    users_changed = False
    if users_data:
        for u in users_data.get("users") or []:
            if u.get("call_center_id") == cid:
                u["call_center_id"] = None
                users_changed = True
    return users_changed
