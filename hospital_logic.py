"""Hospital database, nearest-hospital routing, and escalation logic."""
import math
import os
import re
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "database")
HOSPITALS_STORE = "hospitals"
NOTIFICATIONS_STORE = "notifications"
MESSAGES_STORE = "messages"
# Legacy aliases for tests
HOSPITALS_FILE = HOSPITALS_STORE
NOTIFICATIONS_FILE = NOTIFICATIONS_STORE
MESSAGES_FILE = MESSAGES_STORE

# Approximate bounding box for Somalia (lat/lng validation)
SOMALIA_LAT_MIN, SOMALIA_LAT_MAX = -1.7, 12.0
SOMALIA_LNG_MIN, SOMALIA_LNG_MAX = 40.9, 51.6

# Verified Mogadishu hospitals — preferred over external geocoders
KNOWN_SOMALIA_HOSPITALS = [
    {
        "name": "Banadir Hospital",
        "aliases": ("banadir", "banadir hospital", "banadir hospital mogadishu"),
        "city": "Mogadishu", "district": "Wadajir", "region": "Banadir",
        "address": "Wadajir District, Mogadishu, Somalia",
        "latitude": 2.0520, "longitude": 45.3250,
    },
    {
        "name": "Digfeer Hospital",
        "aliases": ("digfeer", "digfer", "digfeer hospital", "digfer hospital"),
        "city": "Mogadishu", "district": "Warta Nabada", "region": "Banadir",
        "address": "Digfer Area, Mogadishu, Somalia",
        "latitude": 2.0380, "longitude": 45.3350,
    },
    {
        "name": "Medina Hospital",
        "aliases": ("medina", "medina hospital", "medina hospital mogadishu"),
        "city": "Mogadishu", "district": "Hamar Weyne", "region": "Banadir",
        "address": "Medina Street, Hamar Weyne, Mogadishu, Somalia",
        "latitude": 2.0400, "longitude": 45.3400,
    },
    {
        "name": "Erdogan Hospital",
        "aliases": ("erdogan", "erdogan hospital", "erdogan hospital mogadishu"),
        "city": "Mogadishu", "district": "Hodan", "region": "Banadir",
        "address": "Hodan District, Mogadishu, Somalia",
        "latitude": 2.0445, "longitude": 45.3370,
    },
    {
        "name": "Aamin Ambulance Hospital",
        "aliases": ("aamin", "aamin ambulance", "aamin hospital"),
        "city": "Mogadishu", "district": "Hodan", "region": "Banadir",
        "address": "Hodan District, Afgooye Road, Mogadishu, Somalia",
        "latitude": 2.0469, "longitude": 45.3182,
    },
]


MOGADISHU_CENTER = (2.0469, 45.3182)
MAX_LOCAL_DISTANCE_KM = 80


def is_in_somalia(lat, lng):
    """Return True if coordinates fall within Somalia bounding box."""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return False
    return (
        SOMALIA_LAT_MIN <= lat <= SOMALIA_LAT_MAX
        and SOMALIA_LNG_MIN <= lng <= SOMALIA_LNG_MAX
    )


def best_emergency_coords(em, fallback=None):
    """Return the best valid Somalia coordinates for an emergency record."""
    if fallback is None:
        fallback = MOGADISHU_CENTER
    lat, lng = em.get("latitude"), em.get("longitude")
    if lat is not None and lng is not None and is_in_somalia(lat, lng):
        return round(float(lat), 6), round(float(lng), 6)
    for fix in reversed(em.get("location_history") or []):
        flat, flng = fix.get("latitude"), fix.get("longitude")
        if flat is not None and flng is not None and is_in_somalia(flat, flng):
            return round(float(flat), 6), round(float(flng), 6)
    return fallback


def resolve_hospital_coords(hospital):
    """Return verified Somalia coordinates for a hospital (known-directory fallback)."""
    if not hospital:
        return None
    lat, lng = hospital.get("latitude"), hospital.get("longitude")
    if lat is not None and lng is not None and is_in_somalia(lat, lng):
        return round(float(lat), 6), round(float(lng), 6)
    name = (hospital.get("name") or "").strip().lower()
    for known in KNOWN_SOMALIA_HOSPITALS:
        names = {known["name"].lower()} | {a.lower() for a in known.get("aliases", ())}
        if name in names or any(name in n or n in name for n in names):
            return known["latitude"], known["longitude"]
    return None


def filter_somalia_trail(trail):
    """Keep only GPS fixes that fall within Somalia."""
    out = []
    for fix in trail or []:
        lat, lng = fix.get("latitude"), fix.get("longitude")
        if lat is not None and lng is not None and is_in_somalia(lat, lng):
            out.append(fix)
    return out


def cap_local_distance_km(dist):
    """Return distance if plausible for Somalia emergency routing, else None."""
    if dist is None:
        return None
    try:
        dist = float(dist)
    except (TypeError, ValueError):
        return None
    if dist > MAX_LOCAL_DISTANCE_KM:
        return None
    return round(dist, 2)


def search_known_hospitals(query):
    """Return known Somalia hospitals matching query (exact matches first)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    exact = []
    partial = []
    for h in KNOWN_SOMALIA_HOSPITALS:
        names = {h["name"].lower()} | {a.lower() for a in h.get("aliases", ())}
        if q in names:
            exact.append(h)
        elif any(q in n or n in q for n in names):
            partial.append(h)
        elif q.replace(" hospital", "") in h["name"].lower():
            partial.append(h)
    return exact or partial

VALID_OPERATING = ("open", "limited", "closed")
SERVICE_OPTIONS = [
    "Emergency", "Trauma", "General", "Surgery", "ICU", "Maternity",
    "Pediatrics", "Ambulance", "Laboratory", "Radiology", "Pharmacy",
]

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


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def validate_coordinates(lat, lng):
    """Return (lat, lng) floats or raise ValueError."""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError) as exc:
        raise ValueError("Valid latitude and longitude are required.") from exc
    if not (SOMALIA_LAT_MIN <= lat <= SOMALIA_LAT_MAX):
        raise ValueError(f"Latitude must be within Somalia ({SOMALIA_LAT_MIN} to {SOMALIA_LAT_MAX}).")
    if not (SOMALIA_LNG_MIN <= lng <= SOMALIA_LNG_MAX):
        raise ValueError(f"Longitude must be within Somalia ({SOMALIA_LNG_MIN} to {SOMALIA_LNG_MAX}).")
    return round(lat, 6), round(lng, 6)


def normalize_hospital_record(h):
    """Ensure complete hospital profile schema."""
    h.setdefault("district", h.get("city", ""))
    h.setdefault("address", "")
    h.setdefault("city", "")
    h.setdefault("region", "")
    h.setdefault("emergency_contacts", [])
    if isinstance(h.get("emergency_contacts"), str):
        h["emergency_contacts"] = [
            x.strip() for x in re.split(r"[,;\n]+", h["emergency_contacts"]) if x.strip()
        ]
    if not h.get("emergency_contacts") and h.get("phone"):
        h["emergency_contacts"] = [h["phone"]]
    h.setdefault("services", h.get("specialties") or [])
    h.setdefault("specialties", h.get("services") or [])
    h.setdefault("ambulance_available", False)
    h.setdefault("ambulance_count", 1 if h.get("ambulance_available") else 0)
    h.setdefault("emergency_capacity", 10)
    h.setdefault("operating_status", "open")
    h.setdefault("rating", 4.0)
    h.setdefault("contact_email", "")
    h.setdefault("owner_user_id", None)
    h.setdefault("location_verified", bool(h.get("latitude") and h.get("longitude")))
    h.setdefault("created_at", _now())
    h.setdefault("updated_at", _now())
    return h


def migrate_all_hospitals(read_fn, save_fn):
    data = load_hospitals(read_fn, save_fn)
    changed = False
    for h in data["hospitals"]:
        before = dict(h)
        normalize_hospital_record(h)
        if h != before:
            changed = True
    if changed:
        save_hospitals(data, save_fn)
    return data


def parse_services(raw):
    if isinstance(raw, list):
        return [s.strip() for s in raw if str(s).strip()]
    if isinstance(raw, str):
        return [s.strip() for s in re.split(r"[,;\n]+", raw) if s.strip()]
    return []


def parse_emergency_contacts(raw, fallback_phone=""):
    contacts = parse_services(raw) if raw else []
    if not contacts and fallback_phone:
        contacts = [fallback_phone.strip()]
    return contacts[:5]


def create_hospital(payload, read_fn, save_fn):
    """Register a new hospital with validated location."""
    name = (payload.get("name") or "").strip()
    region = (payload.get("region") or "").strip()
    district = (payload.get("district") or "").strip()
    address = (payload.get("address") or "").strip()
    city = (payload.get("city") or district or "").strip()
    phone = (payload.get("phone") or "").strip()
    if not name or not region or not district or not address or not phone:
        raise ValueError("Name, region, district, address, and phone are required.")
    lat, lng = validate_coordinates(payload.get("latitude"), payload.get("longitude"))
    services = parse_services(payload.get("services") or payload.get("services_list"))
    if not services:
        raise ValueError("Select at least one medical service.")
    ambulance = payload.get("ambulance_available") in (True, "true", "1", "on", "yes")
    status = (payload.get("operating_status") or "open").lower()
    if status not in VALID_OPERATING:
        raise ValueError("Invalid operating status.")
    data = load_hospitals(read_fn, save_fn)
    if any(h.get("name", "").lower() == name.lower() for h in data["hospitals"]):
        raise ValueError("A hospital with this name is already registered.")
    hid = data["next_id"]
    data["next_id"] += 1
    hospital = normalize_hospital_record({
        "id": hid,
        "name": name,
        "region": region,
        "district": district,
        "city": city,
        "address": address,
        "latitude": lat,
        "longitude": lng,
        "phone": phone,
        "emergency_contacts": parse_emergency_contacts(
            payload.get("emergency_contacts"), phone
        ),
        "services": services,
        "specialties": services,
        "ambulance_available": ambulance,
        "ambulance_count": int(payload.get("ambulance_count") or (3 if ambulance else 0)),
        "emergency_capacity": int(payload.get("emergency_capacity") or 10),
        "operating_status": status,
        "rating": 4.0,
        "contact_email": (payload.get("contact_email") or "").strip(),
        "owner_user_id": payload.get("owner_user_id"),
        "location_verified": True,
        "created_at": _now(),
        "updated_at": _now(),
    })
    data["hospitals"].append(hospital)
    save_hospitals(data, save_fn)
    return hospital


def update_hospital(hid, payload, read_fn, save_fn):
    data = load_hospitals(read_fn, save_fn)
    hospital = get_hospital_by_id(data, hid)
    if not hospital:
        raise ValueError("Hospital not found.")
    if payload.get("name"):
        hospital["name"] = payload["name"].strip()
    for field in ("region", "district", "city", "address", "phone", "contact_email"):
        if field in payload and payload[field] is not None:
            hospital[field] = str(payload[field]).strip()
    if "latitude" in payload and "longitude" in payload:
        lat, lng = validate_coordinates(payload["latitude"], payload["longitude"])
        hospital["latitude"] = lat
        hospital["longitude"] = lng
        hospital["location_verified"] = True
    if "services" in payload:
        services = parse_services(payload["services"])
        hospital["services"] = services
        hospital["specialties"] = services
    if "emergency_contacts" in payload:
        hospital["emergency_contacts"] = parse_emergency_contacts(
            payload["emergency_contacts"], hospital.get("phone", "")
        )
    if "ambulance_available" in payload:
        hospital["ambulance_available"] = payload["ambulance_available"] in (
            True, "true", "1", "on", "yes"
        )
    if "ambulance_count" in payload:
        hospital["ambulance_count"] = int(payload["ambulance_count"] or 0)
    if "emergency_capacity" in payload:
        hospital["emergency_capacity"] = int(payload["emergency_capacity"] or 0)
    if "operating_status" in payload:
        st = str(payload["operating_status"]).lower()
        if st not in VALID_OPERATING:
            raise ValueError("Invalid operating status.")
        hospital["operating_status"] = st
    hospital["updated_at"] = _now()
    normalize_hospital_record(hospital)
    save_hospitals(data, save_fn)
    return hospital

def load_hospitals(read_fn, save_fn):
    return read_fn(HOSPITALS_STORE, {"hospitals": [], "next_id": 1})


def save_hospitals(data, save_fn):
    save_fn(HOSPITALS_STORE, data)


def seed_hospitals_if_empty(read_fn, save_fn):
    data = load_hospitals(read_fn, save_fn)
    if data["hospitals"]:
        return data
    samples = [
        ("Aamin Ambulance Hospital", "Mogadishu", "Banadir", "Hodan", "Hodan District, Afgooye Road, Mogadishu",
         2.0469, 45.3182, "+252 61 500 1001", ["+252 61 500 1001", "+252 61 999 0001"],
         ["Emergency", "Trauma", "Ambulance"], True, 20, 4.8, None),
        ("Medina Hospital", "Mogadishu", "Banadir", "Hamar Weyne", "Medina Street, Hamar Weyne, Mogadishu",
         2.0400, 45.3400, "+252 61 500 2002", ["+252 61 500 2002"],
         ["General", "Surgery", "ICU"], True, 15, 4.6, None),
        ("Banadir Hospital", "Mogadishu", "Banadir", "Wadajir", "Wadajir District, Mogadishu",
         2.0520, 45.3250, "+252 61 500 3003", ["+252 61 500 3003"],
         ["Emergency", "Maternity", "Pediatrics"], True, 18, 4.5, None),
        ("Digfer Hospital", "Mogadishu", "Banadir", "Warta Nabada", "Digfer Area, Mogadishu",
         2.0380, 45.3350, "+252 61 500 4004", ["+252 61 500 4004"],
         ["Emergency", "General"], False, 12, 4.3, None),
        ("Hargeisa Group Hospital", "Hargeisa", "Maroodi Jeex", "Hargeisa Central", "Central Hargeisa",
         9.5632, 44.0670, "+252 63 400 1001", ["+252 63 400 1001"],
         ["Emergency", "General", "Surgery"], True, 14, 4.4, None),
        ("Bosaso General Hospital", "Bosaso", "Bari", "Bosaso", "Bosaso City Center",
         11.2842, 49.1816, "+252 90 500 1001", ["+252 90 500 1001"],
         ["Emergency", "General"], True, 10, 4.2, None),
        ("Kismayo General Hospital", "Kismayo", "Lower Juba", "Kismayo", "Kismayo Port Road",
         -0.3582, 42.5454, "+252 69 500 1001", ["+252 69 500 1001"],
         ["Emergency", "Maternity"], True, 8, 4.1, None),
        ("Baidoa Regional Hospital", "Baidoa", "Bay", "Baidoa", "Baidoa Main Street",
         3.1167, 43.6500, "+252 61 600 1001", ["+252 61 600 1001"],
         ["Emergency", "General"], False, 8, 4.0, None),
    ]
    for row in samples:
        (name, city, region, district, address, lat, lng, phone, econtacts,
         services, ambulance, capacity, rating, email) = row
        hid = data["next_id"]
        data["next_id"] += 1
        data["hospitals"].append(normalize_hospital_record({
            "id": hid,
            "name": name,
            "city": city,
            "region": region,
            "district": district,
            "address": address,
            "latitude": lat,
            "longitude": lng,
            "phone": phone,
            "emergency_contacts": econtacts,
            "services": services,
            "specialties": services,
            "ambulance_available": ambulance,
            "ambulance_count": 3 if ambulance else 0,
            "emergency_capacity": capacity,
            "rating": rating,
            "operating_status": "open",
            "contact_email": email or "",
            "location_verified": True,
        }))
    save_hospitals(data, save_fn)
    return data


def filter_hospitals(hospitals, city="", region="", specialty="", q=""):
    result = [h for h in hospitals if h.get("operating_status", "open") == "open"]
    if city:
        result = [h for h in result if city.lower() in h.get("city", "").lower()]
    if region:
        result = [h for h in result if region.lower() in h.get("region", "").lower()]
    if specialty:
        result = [
            h for h in result
            if any(specialty.lower() in s.lower() for s in h.get("specialties", []))
        ]
    if q:
        ql = q.lower()
        result = [
            h for h in result
            if ql in h.get("name", "").lower()
            or ql in h.get("city", "").lower()
            or ql in h.get("district", "").lower()
            or ql in h.get("address", "").lower()
        ]
    return result


def hospitals_by_distance(lat, lng, hospitals, emergency_only=True):
    ranked = []
    for h in hospitals:
        if emergency_only and h.get("operating_status") != "open":
            continue
        if h.get("emergency_capacity", 0) <= 0:
            continue
        dist = haversine_km(lat, lng, h["latitude"], h["longitude"])
        ranked.append((dist, h))
    ranked.sort(key=lambda x: x[0])
    return ranked


def get_hospital_by_id(hospitals_data, hid):
    for h in hospitals_data.get("hospitals", []):
        if h["id"] == hid:
            return h
    return None


def build_escalation_queue(lat, lng, hospitals_data, exclude_ids=None):
    exclude_ids = exclude_ids or []
    ranked = hospitals_by_distance(lat, lng, hospitals_data["hospitals"])
    return [h["id"] for dist, h in ranked if h["id"] not in exclude_ids]


def assign_next_hospital(emergency, hospitals_data, timeout_seconds):
    queue = emergency.get("escalation_queue") or []
    idx = emergency.get("escalation_index", 0)
    if idx >= len(queue):
        emergency["status"] = "no_hospital_available"
        return None
    hid = queue[idx]
    hospital = get_hospital_by_id(hospitals_data, hid)
    if not hospital:
        emergency["escalation_index"] = idx + 1
        return assign_next_hospital(emergency, hospitals_data, timeout_seconds)
    emergency["assigned_hospital_id"] = hid
    emergency["assigned_hospital_name"] = hospital["name"]
    emergency["hospital_distance_km"] = round(
        haversine_km(
            emergency.get("latitude") or 0,
            emergency.get("longitude") or 0,
            hospital["latitude"],
            hospital["longitude"],
        ),
        2,
    )
    emergency["response_deadline"] = (
        datetime.now() + timedelta(seconds=timeout_seconds)
    ).strftime("%Y-%m-%d %H:%M:%S")
    emergency["status"] = "pending_hospital"
    emergency["escalation_index"] = idx
    return hospital


def process_escalations(emergencies, hospitals_data, timeout_seconds, save_emergencies_fn, load_emergencies_fn, add_notification_fn):
    """Forward requests if hospital did not respond in time."""
    changed = False
    now = datetime.now()
    for em in emergencies:
        if em.get("status") not in ("pending_hospital", "pending"):
            continue
        deadline = _parse_dt(em.get("response_deadline"))
        if not em.get("response_deadline") or now <= deadline:
            continue
        old_hid = em.get("assigned_hospital_id")
        em["escalation_index"] = em.get("escalation_index", 0) + 1
        if old_hid:
            add_notification_fn(
                "hospital",
                old_hid,
                f"Request #{em['id']} escalated (no response in time)",
                em["id"],
            )
        hospital = assign_next_hospital(em, hospitals_data, timeout_seconds)
        if hospital:
            add_notification_fn(
                "hospital",
                hospital["id"],
                f"NEW emergency request #{em['id']} — respond now",
                em["id"],
            )
            add_notification_fn(
                "patient",
                em.get("user_id"),
                f"Request sent to {hospital['name']} ({em.get('hospital_distance_km')} km)",
                em["id"],
            )
        changed = True
    if changed:
        edata = load_emergencies_fn()
        edata["emergencies"] = emergencies
        save_emergencies_fn(edata)


def load_notifications(read_fn, save_fn):
    return read_fn(NOTIFICATIONS_STORE, {"notifications": [], "next_id": 1})


def save_notifications(data, save_fn):
    save_fn(NOTIFICATIONS_STORE, data)


NOTIFICATION_TYPES = (
    "request_received", "request_accepted", "team_assigned", "team_dispatched",
    "team_arrived", "emergency_completed", "system_alert", "announcement",
)


def add_notification(read_fn, save_fn, target_type, target_id, message, request_id=None, ntype="system_alert"):
    data = load_notifications(read_fn, save_fn)
    data["notifications"].append({
        "id": data["next_id"],
        "timestamp": _now(),
        "target_type": target_type,
        "target_id": target_id,
        "message": message,
        "request_id": request_id,
        "type": ntype if ntype in NOTIFICATION_TYPES else "system_alert",
        "read": False,
    })
    data["next_id"] += 1
    save_notifications(data, save_fn)


def get_notifications_for(read_fn, target_type, target_id, unread_only=False, limit=50):
    data = read_fn(NOTIFICATIONS_STORE, {"notifications": [], "next_id": 1})
    out = []
    for n in data["notifications"]:
        if n.get("target_type") == target_type and n.get("target_id") == target_id:
            if unread_only and n.get("read"):
                continue
            n.setdefault("type", "system_alert")
            out.append(n)
    out.sort(key=lambda x: x["timestamp"], reverse=True)
    return out[:limit]


def mark_notifications_read(read_fn, save_fn, target_type, target_id, notification_ids=None):
    data = load_notifications(read_fn, save_fn)
    for n in data["notifications"]:
        if n.get("target_type") != target_type or n.get("target_id") != target_id:
            continue
        if notification_ids is None or n["id"] in notification_ids:
            n["read"] = True
    save_notifications(data, save_fn)


def load_messages(read_fn, save_fn):
    return read_fn(MESSAGES_STORE, {"messages": [], "next_id": 1})


def save_messages(data, save_fn):
    save_fn(MESSAGES_STORE, data)


def add_message(read_fn, save_fn, request_id, sender_role, sender_id, text, msg_type="text", audio_data=""):
    data = load_messages(read_fn, save_fn)
    body = text[:2000] if msg_type != "voice" else (audio_data[:500000] or text[:500000])
    msg = {
        "id": data["next_id"],
        "request_id": request_id,
        "sender_role": sender_role,
        "sender_id": sender_id,
        "text": body,
        "msg_type": msg_type if msg_type in ("text", "voice") else "text",
        "timestamp": _now(),
        "status": "sent",
        "delivered_at": None,
        "seen_at": None,
    }
    data["next_id"] += 1
    data["messages"].append(msg)
    save_messages(data, save_fn)
    return msg


def get_messages_for_request(read_fn, save_fn, request_id, viewer_role=None):
    data = load_messages(read_fn, save_fn)
    msgs = [m for m in data["messages"] if m["request_id"] == request_id]
    msgs.sort(key=lambda x: x["timestamp"])
    changed = False
    for m in data["messages"]:
        if m["request_id"] != request_id:
            continue
        m.setdefault("status", "sent")
        if viewer_role and m.get("sender_role") != viewer_role:
            if m["status"] == "sent":
                m["status"] = "delivered"
                m["delivered_at"] = _now()
                changed = True
            elif m["status"] == "delivered":
                m["status"] = "seen"
                m["seen_at"] = _now()
                changed = True
    if changed:
        save_messages(data, save_fn)
    return [m for m in data["messages"] if m["request_id"] == request_id]
