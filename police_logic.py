"""Police / fire station desk helpers (queue visibility + station binding)."""
from __future__ import annotations

import facility_registry as fr

ACTIVE_STATUSES = frozenset({
    "pending",
    "dispatched",
    "in_progress",
    "accepted",
    "pending_hospital",
})
COMPLETED = frozenset({"completed", "resolved", "cancelled"})


def station_view(row):
    s = fr.normalize_station(row or {})
    return {
        "id": s.get("id"),
        "kind": s.get("kind") or "police",
        "name": s.get("name") or "",
        "city": s.get("city") or "",
        "region": s.get("region") or "",
        "district": s.get("district") or "",
        "address": s.get("address") or "",
        "latitude": s.get("latitude"),
        "longitude": s.get("longitude"),
        "phone": s.get("phone") or "",
        "operating_status": s.get("operating_status") or "open",
        "owner_user_id": s.get("owner_user_id"),
        "updated_at": s.get("updated_at") or "",
    }


def get_user_station(user, kind, read_fn):
    """Return (station_id, station_row) for a police/fire operator."""
    if not user:
        return None, None
    sid = user.get("station_id")
    if not sid:
        return None, None
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        return None, None
    data = fr.load_stations(read_fn)
    row = fr.get_station(data, sid)
    if not row:
        return None, None
    if kind and (row.get("kind") or "").lower() != kind:
        return None, None
    return sid, fr.normalize_station(row)


def emergency_visible_to_station(em, station_id, role):
    """
    Station desk sees:
    - cases already assigned to this station, or
    - open pool: same team (assigned_to == role) and no station yet.
    """
    if not em or not station_id or not role:
        return False
    try:
        sid = int(station_id)
    except (TypeError, ValueError):
        return False
    asid = em.get("assigned_station_id")
    if asid not in (None, ""):
        try:
            return int(asid) == sid
        except (TypeError, ValueError):
            return False
    assigned_to = (em.get("assigned_to") or "").strip().lower()
    return assigned_to == role


def claim_station(em, station, role):
    """Bind emergency to station after accept. Raises ValueError on conflict."""
    if not em or not station:
        raise ValueError("Missing emergency or station")
    sid = station.get("id")
    if sid is None:
        raise ValueError("Invalid station")
    asid = em.get("assigned_station_id")
    if asid not in (None, ""):
        try:
            if int(asid) != int(sid):
                raise ValueError("Already claimed by another station")
        except (TypeError, ValueError) as exc:
            if "Already claimed" in str(exc):
                raise
            raise ValueError("Already claimed by another station") from exc
    status = (em.get("status") or "").lower()
    if status in COMPLETED:
        raise ValueError("Case is already closed")
    em["assigned_station_id"] = int(sid)
    em["assigned_to"] = role
    em["assigned_team_label"] = station.get("name") or em.get("assigned_team_label") or role.title()
    if station.get("latitude") is not None and station.get("longitude") is not None:
        em["responder_latitude"] = station["latitude"]
        em["responder_longitude"] = station["longitude"]
    if station.get("phone"):
        em["contact_number"] = station.get("phone")
    return em


def release_station(em, station_id):
    """Return case to open team pool (reject / release)."""
    asid = em.get("assigned_station_id")
    if asid is None or asid == "":
        return em
    try:
        if int(asid) != int(station_id):
            raise ValueError("Not assigned to your station")
    except (TypeError, ValueError) as exc:
        if "Not assigned" in str(exc):
            raise
        raise ValueError("Not assigned to your station") from exc
    em["assigned_station_id"] = None
    return em


def nearest_open_station(kind, lat, lng, read_fn, max_km=80.0):
    """Pick nearest open station of kind with coordinates (optional auto-hint)."""
    import hospital_logic as hl

    if lat is None or lng is None:
        return None
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return None
    data = fr.load_stations(read_fn)
    best = None
    best_km = None
    for s in data.get("stations") or []:
        if (s.get("kind") or "").lower() != kind:
            continue
        if (s.get("operating_status") or "open").lower() == "closed":
            continue
        if s.get("latitude") is None or s.get("longitude") is None:
            continue
        try:
            km = hl.haversine_km(lat, lng, float(s["latitude"]), float(s["longitude"]))
        except (TypeError, ValueError):
            continue
        if max_km is not None and km > max_km:
            continue
        if best is None or km < best_km:
            best = fr.normalize_station(s)
            best_km = km
    if best is not None:
        best["_distance_km"] = round(best_km, 2) if best_km is not None else None
    return best
