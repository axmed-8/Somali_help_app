"""Hospital database, nearest-hospital search, and request escalation."""
import math
from datetime import datetime, timedelta

MOGADISHU_CENTER = (2.0469, 45.3182)


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def normalize_hospital(h):
    h.setdefault("services", [])
    h.setdefault("specialties", [])
    h.setdefault("ambulance_available", False)
    h.setdefault("emergency_capacity", 10)
    h.setdefault("rating", 4.0)
    h.setdefault("status", "open")
    h.setdefault("phone", "")
    h.setdefault("city", "Mogadishu")
    h.setdefault("region", "Banadir")
    return h


def sorted_hospitals_by_distance(hospitals, lat, lng, only_open=True, emergency_capable=True):
    results = []
    for h in hospitals:
        if only_open and h.get("status") != "open":
            continue
        if emergency_capable and not h.get("accepts_emergency", True):
            continue
        hlat = h.get("latitude")
        hlng = h.get("longitude")
        if hlat is None or hlng is None:
            continue
        dist = haversine_km(lat, lng, float(hlat), float(hlng))
        eta_min = max(3, int(dist * 3.5))
        results.append({**h, "distance_km": round(dist, 2), "eta_minutes": eta_min})
    results.sort(key=lambda x: x["distance_km"])
    return results


def build_hospital_queue(hospitals, lat, lng, limit=8):
    ordered = sorted_hospitals_by_distance(hospitals, lat, lng)
    return [h["id"] for h in ordered[:limit]]


def hospital_by_id(hospitals, hid):
    for h in hospitals:
        if h["id"] == hid:
            return h
    return None


def parse_deadline(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(value)[:19], fmt)
        except ValueError:
            continue
    return None


def should_escalate(emergency, timeout_seconds):
    if emergency.get("hospital_status") != "pending":
        return False
    deadline = parse_deadline(emergency.get("response_deadline"))
    if not deadline:
        return False
    return datetime.now() > deadline


def escalate_emergency(emergency, hospitals, timeout_seconds):
    """Move to next hospital in queue if current did not respond in time."""
    if not should_escalate(emergency, timeout_seconds):
        return False, emergency

    queue = emergency.get("hospital_queue") or []
    idx = emergency.get("hospital_index", 0) + 1
    if idx >= len(queue):
        emergency["hospital_status"] = "no_hospital_available"
        emergency["patient_message"] = "All nearby hospitals were notified. Admin is alerting more units."
        return True, emergency

    emergency["hospital_index"] = idx
    emergency["hospital_id"] = queue[idx]
    emergency["response_deadline"] = (datetime.now() + timedelta(seconds=timeout_seconds)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    h = hospital_by_id(hospitals, emergency["hospital_id"])
    name = h["name"] if h else "Next hospital"
    emergency["patient_message"] = f"Forwarded to {name}. Waiting for response..."
    emergency["escalation_log"] = emergency.get("escalation_log", []) + [
        {"hospital_id": emergency["hospital_id"], "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    ]
    return True, emergency
