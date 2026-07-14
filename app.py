import csv
import importlib.util
import io
import json
import os
import secrets
import tempfile
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import hospital_logic as hl
import call_center_logic as cc
from ai_engine import get_ai_engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "database")
STORE_USERS = "users"
STORE_EMERGENCIES = "emergencies"
STORE_CONTENT = "system_content"
STORE_SETTINGS = "settings"
STORE_AUDIT = "audit_log"
STORE_ANNOUNCEMENTS = "announcements"
STORE_CALL_CENTER = "call_center_calls"
# Legacy aliases (tests write temp JSON files using these paths)
USERS_FILE = STORE_USERS
EMERGENCIES_FILE = STORE_EMERGENCIES
CONTENT_FILE = STORE_CONTENT
SETTINGS_FILE = STORE_SETTINGS
AUDIT_FILE = STORE_AUDIT
ANNOUNCEMENTS_FILE = STORE_ANNOUNCEMENTS
CALL_CENTER_FILE = STORE_CALL_CENTER

TEAM_LABELS = {
    "hospital": "Medical Response Team",
    "police": "Police Emergency Team",
    "fire": "Fire & Rescue Team",
    "admin": "Emergency Dispatch Center",
    "call_center": "Emergency Call Center",
}

COMPLETED_STATUSES = ("resolved", "completed", "cancelled", "no_hospital_available")

RESPONSE_STATIONS = {
    "fire": {
        "latitude": 2.052,
        "longitude": 45.328,
        "name": "Fire & Rescue Station",
        "phone": "+252612000911",
    },
    "police": {
        "latitude": 2.038,
        "longitude": 45.315,
        "name": "Police Response Unit",
        "phone": "+252612000912",
    },
}


def configure_hospital_db(db_dir):
    """Keep hospital_logic store keys aligned with app (tests use temp JSON dir)."""
    hl.DATABASE_DIR = db_dir
    hl.HOSPITALS_STORE = "hospitals"
    hl.NOTIFICATIONS_STORE = "notifications"
    hl.MESSAGES_STORE = "messages"


configure_hospital_db(DATABASE_DIR)

_file_locks = {}
_file_locks_guard = threading.Lock()


def _path_lock(path):
    with _file_locks_guard:
        if path not in _file_locks:
            _file_locks[path] = threading.Lock()
        return _file_locks[path]


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

TYPE_MAP = {
    "medical": ["medical", "family_help", "family help"],
    "police": ["police", "security", "accident"],
    "fire": ["fire"],
}

TYPE_LABELS = {
    "medical": "Medical",
    "accident": "Accident",
    "fire": "Fire",
    "security": "Security",
    "family_help": "Family Help",
    "other": "Other",
}

STATUS_VALUES = ["pending", "dispatched", "in_progress", "completed", "cancelled", "resolved", "pending_hospital", "accepted"]
VALID_ROLES = ["citizen", "hospital", "police", "fire", "admin", "call_center"]

ROLE_HOME = {
    "citizen": "/dashboard",
    "hospital": "/hospital",
    "police": "/police",
    "fire": "/fire",
    "admin": "/admin",
    "call_center": "/call-center",
}

ROLE_API_TYPE = {"hospital": "medical", "police": "police", "fire": "fire"}

def _resolve_use_mysql():
    if os.environ.get("GURMADNET_DB", "").lower() == "json":
        return False
    if os.environ.get("GURMADNET_DB", "").lower() == "mysql":
        return True
    cfg_path = os.path.join(DATABASE_DIR, "db_config.env")
    if not os.path.exists(cfg_path):
        return False
    try:
        from database.connection import load_config
        from database import mysql_store

        if not mysql_store.available():
            return False
        if load_config().get("password") in ("", "YOUR_PASSWORD"):
            return False
        conn = mysql_store.connect()
        conn.close()
        return True
    except Exception:
        return False


USE_MYSQL = _resolve_use_mysql()

# Ensure Call Center + AI MySQL schema before any role seeding (prevents ENUM truncation)
if USE_MYSQL:
    try:
        from database import mysql_store as _ms_boot

        _ms_boot.ensure_call_center_schema()
        _ms_boot.ensure_ai_schema()
    except Exception as _cc_schema_exc:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "Call Center/AI MySQL schema ensure skipped: %s", _cc_schema_exc
        )


DEFAULT_CONTENT = {
    "app_name": "GurmadNet AI",
    "sos_button_text": "SOS",
    "sos_subtitle": "Tap SOS button to start emergency request",
    "confirmation_message": "Help is on the way!",
    "hospital_dashboard_title": "Aamin Ambulance - Hospital Dashboard",
    "police_dashboard_title": "Hamar Police - Police Dashboard",
    "fire_dashboard_title": "KM4 Fire Station - Fire Dashboard",
    "admin_dashboard_title": "GurmadNet AI — Admin Control Panel",
    "call_center_dashboard_title": "GurmadNet AI — Emergency Call Center",
    "welcome_citizen": "Mogadishu & Somalia — 24/7 Emergency Response",
    "emergency_type_medical": "Medical",
    "emergency_type_accident": "Accident",
    "emergency_type_fire": "Fire",
    "emergency_type_security": "Security",
    "emergency_type_family": "Family Help",
    "call_center_button_text": "Call Emergency Center",
    "call_center_hint": "Speak with an operator — your GPS is shared automatically",
}

DEFAULT_SETTINGS = {
    "sos_enabled": True,
    "ambulance_response_time": 8,
    "police_response_time": 6,
    "fire_response_time": 9,
    "refresh_interval": 5,
    "max_emergencies_per_day": 100,
    "maintenance_mode": False,
    "sms_notifications": False,
    "color_hospital": "#2E7D32",
    "color_police": "#1565C0",
    "color_fire": "#C62828",
    "dark_mode": False,
    "hospital_response_timeout_sec": 120,
    "google_maps_api_key": os.environ.get("GOOGLE_MAPS_API_KEY", ""),
    "call_center_enabled": True,
    "call_center_phone": "+252612000999",
    "call_center_phone_secondary": "+252612000998",
    "call_center_priority_medical": 1,
    "call_center_priority_fire": 1,
    "call_center_priority_police": 1,
    "call_center_auto_nearest": True,
    "call_center_heartbeat_sec": 45,
    "ai_enabled": True,
    "ai_provider": "rule_based",
}


def ensure_database_dir():
    os.makedirs(DATABASE_DIR, exist_ok=True)


def _json_file_path(entity):
    """Map store entity to temp JSON file path (test mode only)."""
    key = os.path.basename(str(entity))
    if not key.endswith(".json"):
        key = f"{key}.json"
    return os.path.join(DATABASE_DIR, key)


def read_store(entity, default):
    ms = _mysql_backend()
    if ms:
        return ms.read_store(entity, default)
    path = _json_file_path(entity)
    ensure_database_dir()
    if not os.path.exists(path):
        save_store(entity, default)
        return json.loads(json.dumps(default))
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        save_store(entity, default)
        return json.loads(json.dumps(default))


def save_store(entity, data):
    """Persist data to MySQL (production) or temp JSON file (tests)."""
    ms = _mysql_backend()
    if ms:
        ms.save_store(entity, data)
        return
    path = _json_file_path(entity)
    ensure_database_dir()
    with _path_lock(path):
        fd, tmp_path = tempfile.mkstemp(dir=DATABASE_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise


def read_json(entity, default):
    return read_store(entity, default)


def save_json(entity, data):
    save_store(entity, data)


def append_audit(action, entity_type, entity_id, details=None, user_id=None):
    log = read_json(AUDIT_FILE, {"entries": [], "next_id": 1})
    entry = {
        "id": log["next_id"],
        "timestamp": now_str(),
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "user_id": user_id or session.get("user_id"),
        "details": details or {},
    }
    log["next_id"] += 1
    log["entries"] = ([entry] + log["entries"])[:5000]
    save_json(AUDIT_FILE, log)


def normalize_emergency_record(em):
    em.setdefault("location_history", [])
    em.setdefault("responder_status", {})
    em.setdefault("user_id", None)
    em.setdefault("request_mode", "legacy")
    em.setdefault("escalation_queue", [])
    em.setdefault("escalation_index", 0)
    em.setdefault("status_history", [])
    em.setdefault("assigned_hospital_id", None)
    em.setdefault("assigned_hospital_name", "")
    return em


def _ai_engine():
    """Provider-agnostic AI Emergency Engine (never talks to a vendor SDK directly)."""
    return get_ai_engine(read_json, save_json)


def _ai_context_from_emergency(emergency, source="sos", extra=None):
    """Build analysis context from an emergency record without mutating dispatch."""
    edata = load_emergencies()
    hdata = hl.load_hospitals(read_json, save_json)
    uid = emergency.get("user_id")
    history = [
        {
            "id": e.get("id"),
            "type": e.get("type"),
            "status": e.get("status"),
            "timestamp": e.get("timestamp"),
        }
        for e in edata.get("emergencies", [])
        if uid and e.get("user_id") == uid and e.get("id") != emergency.get("id")
    ][-10:]
    active = [
        e for e in edata.get("emergencies", [])
        if e.get("status") not in COMPLETED_STATUSES
    ]
    ctx = {
        "emergency_id": emergency.get("id"),
        "call_id": emergency.get("call_id"),
        "type": emergency.get("type"),
        "notes": emergency.get("notes") or "",
        "description": emergency.get("notes") or "",
        "latitude": emergency.get("latitude"),
        "longitude": emergency.get("longitude"),
        "address": emergency.get("location") or emergency.get("district") or "",
        "district": emergency.get("district") or "",
        "caller_name": emergency.get("caller_name"),
        "phone": emergency.get("phone"),
        "source": source,
        "emergency_history": history,
        "active_emergencies": active,
        "hospitals": hdata.get("hospitals", []),
        "police_station": RESPONSE_STATIONS.get("police"),
        "fire_station": RESPONSE_STATIONS.get("fire"),
    }
    if emergency.get("latitude") is not None and emergency.get("longitude") is not None:
        try:
            ctx["nearest"] = cc.find_nearest_responders(
                emergency["latitude"],
                emergency["longitude"],
                read_json,
                save_json,
                RESPONSE_STATIONS,
            )
        except Exception:
            ctx["nearest"] = {}
    if extra:
        ctx.update(extra)
    return ctx


def _schedule_ai_analysis(emergency, source="sos"):
    """
    Run AI Decision + Smart Dispatch recommendation in parallel.
    Never blocks or alters SOS auto-dispatch. AI never dispatches.
    """
    settings = load_settings()
    if not settings.get("ai_enabled", True):
        return None
    try:
        engine = _ai_engine()
        context = _ai_context_from_emergency(emergency, source=source)
        eid = emergency.get("id")
        assigned_to = emergency.get("assigned_to")
        assigned_hid = emergency.get("assigned_hospital_id")
        assigned_hname = emergency.get("assigned_hospital_name")
        call_id = emergency.get("call_id")

        def _on_done(result):
            try:
                analysis = (result or {}).get("analysis") or {}
                recommendation = (result or {}).get("recommendation") or {}
                decision = "auto_sos" if source == "sos" else (
                    "auto_healthcare" if source == "healthcare" else "queued"
                )
                if source == "call_center":
                    decision = "call_center_created"
                engine.record_dispatch_result({
                    "emergency_id": eid,
                    "call_id": call_id,
                    "recommendation_id": recommendation.get("id"),
                    "analysis_id": analysis.get("id"),
                    "human_decision": decision,
                    "dispatched_to": [assigned_to] if assigned_to else [],
                    "emergency_ids": [eid] if eid else [],
                    "assigned_hospital_id": assigned_hid,
                    "assigned_hospital_name": assigned_hname,
                    "notes": f"Parallel AI analysis after {source} dispatch path",
                })
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "AI dispatch_result memory write failed for emergency %s", eid
                )

        return engine.analyze_emergency_async(context, on_done=_on_done)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "AI schedule failed for emergency %s", emergency.get("id")
        )
        return None


def _ai_record_outcome(emergency):
    """Persist final outcome into AI Memory for future strategic modules."""
    try:
        settings = load_settings()
        if not settings.get("ai_enabled", True):
            return
        _ai_engine().record_outcome({
            "emergency_id": emergency.get("id"),
            "call_id": emergency.get("call_id"),
            "status": emergency.get("status"),
            "final_status": emergency.get("status"),
            "assigned_to": emergency.get("assigned_to"),
            "type": emergency.get("type"),
            "district": emergency.get("district"),
            "latitude": emergency.get("latitude"),
            "longitude": emergency.get("longitude"),
            "notes": emergency.get("notes"),
        })
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "AI outcome memory write failed for emergency %s", emergency.get("id")
        )


def _notify(target_type, target_id, message, request_id=None, ntype="system_alert"):
    hl.add_notification(read_json, save_json, target_type, target_id, message, request_id, ntype)


def _notify_admins(message, request_id=None, ntype="system_alert"):
    udata = load_users()
    for u in udata["users"]:
        if u.get("role") == "admin" and u.get("status") == "active":
            _notify("admin", u["id"], message, request_id, ntype)


def _auto_dispatch_emergency(emergency):
    """Route emergency to the correct response team automatically."""
    eid = emergency["id"]
    uid = emergency.get("user_id")
    etype = emergency.get("type", "medical")
    assign_map = {
        "medical": "hospital",
        "family_help": "hospital",
        "fire": "fire",
        "security": "police",
        "accident": "police",
    }
    team = assign_map.get(etype, "hospital")
    emergency["assigned_to"] = team
    emergency["assigned_team_label"] = TEAM_LABELS.get(team, "Emergency Response Team")
    emergency.setdefault("status_history", [])

    _notify("patient", uid, "Your emergency request has been received.", eid, "request_received")
    _notify_admins(f"New emergency #{eid} ({etype}) — auto-dispatch initiated.", eid, "system_alert")

    if team == "hospital" and emergency.get("latitude") and emergency.get("longitude"):
        settings = load_settings()
        timeout = int(settings.get("hospital_response_timeout_sec", 120))
        hdata = hl.seed_hospitals_if_empty(read_json, save_json)
        emergency["escalation_queue"] = hl.build_escalation_queue(
            emergency["latitude"], emergency["longitude"], hdata
        )
        emergency["escalation_index"] = 0
        if emergency["escalation_queue"]:
            hospital = hl.assign_next_hospital(emergency, hdata, timeout)
            if hospital:
                dist = emergency.get("hospital_distance_km")
                dist_txt = f" ({dist} km)" if dist is not None else ""
                _append_status(
                    emergency,
                    "pending_hospital",
                    f"Nearest hospital: {hospital['name']}{dist_txt}",
                )
                _notify("hospital", hospital["id"], f"URGENT: Emergency #{eid} — respond now", eid, "team_assigned")
                _notify(
                    "patient",
                    uid,
                    f"Nearest hospital {hospital['name']}{dist_txt} has been assigned to your request.",
                    eid,
                    "team_assigned",
                )
        else:
            emergency["status"] = "pending"
            _append_status(emergency, "pending", "Awaiting dispatch assignment")
    else:
        emergency["status"] = "pending"
        _append_status(emergency, "pending", f"Routed to {emergency['assigned_team_label']}")
        _notify("patient", uid, f"{emergency['assigned_team_label']} notified.", eid, "team_assigned")


def _user_hospital_id(user):
    if not user:
        return None
    return user.get("hospital_id")


def _get_user_hospital(user):
    hid = _user_hospital_id(user)
    if not hid:
        return None, None
    hdata = hl.load_hospitals(read_json, save_json)
    return hid, hl.get_hospital_by_id(hdata, hid)


def _link_user_to_hospital(user_id, hospital_id):
    user, udata = get_user_by_id(user_id)
    if user:
        user["hospital_id"] = hospital_id
        save_users(udata)


def _role_home(user):
    if user and user.get("role") == "hospital" and not user.get("hospital_id"):
        return url_for("hospital_register")
    return ROLE_HOME.get(user.get("role") if user else None, "/login")


def _run_escalations():
    settings = load_settings()
    timeout = int(settings.get("hospital_response_timeout_sec", 120))
    edata = load_emergencies()
    hdata = hl.load_hospitals(read_json, save_json)

    def _save(ed):
        save_emergencies(ed)

    def _load():
        return load_emergencies()

    hl.process_escalations(
        edata["emergencies"],
        hdata,
        timeout,
        _save,
        _load,
        lambda tt, tid, msg, rid=None: _notify(tt, tid, msg, rid),
    )


def _append_status(em, status, note=""):
    em.setdefault("status_history", [])
    em["status_history"].append({"status": status, "timestamp": now_str(), "note": note})
    em["status"] = status


def _create_healthcare_emergency(data, request_mode="emergency"):
    """Create emergency, route to nearest or preferred hospital."""
    settings = load_settings()
    timeout = int(settings.get("hospital_response_timeout_sec", 120))
    hdata = hl.seed_hospitals_if_empty(read_json, save_json)

    lat = data.get("latitude")
    lng = data.get("longitude")
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None, "Valid GPS location is required."

    edata = load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1

    fix = build_location_fix(data)
    fix["latitude"] = lat
    fix["longitude"] = lng

    emergency = {
        "id": eid,
        "user_id": session.get("user_id"),
        "type": normalize_type(data.get("type", "medical")),
        "request_mode": request_mode,
        "location": data.get("location") or fix.get("district", "Somalia"),
        "district": data.get("district") or fix.get("district", ""),
        "latitude": lat,
        "longitude": lng,
        "accuracy_m": fix.get("accuracy_m"),
        "method": fix.get("method", "gps"),
        "confidence": fix.get("confidence"),
        "timestamp": now_str(),
        "caller_name": data.get("name") or session.get("name", "Anonymous"),
        "phone": data.get("phone") or "Not provided",
        "assigned_to": "hospital",
        "location_history": [fix],
        "responder_status": {},
        "status_history": [],
        "notes": data.get("notes", ""),
    }
    _apply_tracking_fields(emergency, fix)

    if request_mode == "preferred":
        hid = int(data.get("hospital_id", 0))
        hospital = hl.get_hospital_by_id(hdata, hid)
        if not hospital:
            return None, "Hospital not found."
        emergency["escalation_queue"] = [hid]
        emergency["escalation_index"] = 0
        hl.assign_next_hospital(emergency, hdata, timeout)
        _append_status(emergency, "pending_hospital", f"Sent to chosen hospital: {hospital['name']}")
    else:
        emergency["escalation_queue"] = hl.build_escalation_queue(lat, lng, hdata)
        emergency["escalation_index"] = 0
        if not emergency["escalation_queue"]:
            emergency["status"] = "no_hospital_available"
            _append_status(emergency, "no_hospital_available", "No hospitals available")
        else:
            hospital = hl.assign_next_hospital(emergency, hdata, timeout)
            if hospital:
                _append_status(emergency, "pending_hospital", f"Nearest: {hospital['name']}")
                _notify("hospital", hospital["id"], f"URGENT: Emergency request #{eid} — respond now", eid)

    _notify("patient", session.get("user_id"), "Your emergency request has been submitted.", eid)
    edata["emergencies"].append(emergency)
    save_emergencies(edata)
    append_audit("healthcare_request", "emergency", eid, {"mode": request_mode})
    _run_escalations()
    _schedule_ai_analysis(emergency, source="healthcare")
    return emergency, None


def normalize_user_record(user):
    """Ensure user matches the users table schema."""
    if "name" not in user and user.get("full_name"):
        user["name"] = user.pop("full_name")
    if "name" not in user:
        user["name"] = "Unknown User"
    user.pop("full_name", None)
    if "username" not in user:
        user["username"] = user.get("email", "user").split("@")[0]
    user.setdefault("phone", "")
    user.setdefault("status", "active")
    user.setdefault("activity", [])
    user.setdefault("profile_photo", "")
    user.setdefault("emergency_contact_name", "")
    user.setdefault("emergency_contact_phone", "")
    user.setdefault("emergency_contact_relation", "")
    user.setdefault("address", "")
    user.setdefault("city", "")
    user.setdefault("date_of_birth", "")
    user.setdefault("blood_type", "")
    user.setdefault("medical_notes", "")
    user.setdefault("saved_locations", [])
    return user


def user_name(user):
    if not user:
        return "User"
    return user.get("name") or user.get("full_name", "User")


def prepare_user_for_template(user):
    if not user:
        return None
    u = normalize_user_record(dict(user))
    return u


def _mysql_backend():
    if not USE_MYSQL:
        return None
    try:
        from database import mysql_store
        if mysql_store.available():
            return mysql_store
    except ImportError:
        pass
    return None


def load_users():
    data = read_json(USERS_FILE, {"users": [], "next_id": 1})
    data["users"] = [normalize_user_record(u) for u in data.get("users", [])]
    return data


def save_users(data):
    data["users"] = [normalize_user_record(u) for u in data.get("users", [])]
    save_json(USERS_FILE, data)


def load_emergencies():
    data = read_json(EMERGENCIES_FILE, {"emergencies": [], "next_id": 1})
    data["emergencies"] = [normalize_emergency_record(e) for e in data.get("emergencies", [])]
    return data


def save_emergencies(data):
    data["emergencies"] = [normalize_emergency_record(e) for e in data.get("emergencies", [])]
    save_json(EMERGENCIES_FILE, data)


def get_emergency_by_id(eid):
    edata = load_emergencies()
    for em in edata["emergencies"]:
        if em["id"] == eid:
            return em, edata
    return None, edata


def _apply_tracking_fields(emergency, fix=None):
    """Mark emergency as actively tracked with GPS."""
    emergency["tracking_active"] = True
    emergency["last_location_update"] = now_str()
    if fix:
        emergency.setdefault("location_history", [])
        if not emergency["location_history"]:
            emergency["location_history"].append(fix)


def _can_access_emergency(em, role, user=None):
    """Citizen owner, assigned hospital, responder role, or admin."""
    if not em:
        return False
    if role == "admin":
        return True
    if role == "citizen":
        return em.get("user_id") == session.get("user_id")
    if role == "hospital":
        user = user or current_user()
        hid = _user_hospital_id(user)
        return hid and em.get("assigned_hospital_id") == hid
    if role in ROLE_API_TYPE:
        return matches_filter(em.get("type"), ROLE_API_TYPE[role])
    return False


def build_location_fix(data):
    lat = data.get("latitude")
    lng = data.get("longitude")
    if lat is not None and lng is not None:
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            lat, lng = None, None
    confidence = data.get("confidence")
    try:
        confidence = int(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    accuracy = data.get("accuracy_m") or data.get("accuracy")
    try:
        accuracy = float(accuracy) if accuracy is not None else None
    except (TypeError, ValueError):
        accuracy = None
    speed = data.get("speed_mps")
    heading = data.get("heading")
    try:
        speed = float(speed) if speed is not None else None
    except (TypeError, ValueError):
        speed = None
    try:
        heading = float(heading) if heading is not None else None
    except (TypeError, ValueError):
        heading = None
    if lat is not None and lng is not None and not hl.is_in_somalia(lat, lng):
        lat, lng = None, None
    return {
        "timestamp": now_str(),
        "latitude": lat,
        "longitude": lng,
        "district": data.get("district") or "",
        "building": data.get("building") or "",
        "floor": data.get("floor") or "",
        "room": data.get("room") or "",
        "altitude_m": data.get("altitude_m"),
        "accuracy_m": accuracy,
        "uncertainty_m": accuracy,
        "speed_mps": speed,
        "heading": heading,
        "method": data.get("method") or "gps",
        "confidence": confidence,
    }


def load_content():
    stored = read_json(CONTENT_FILE, DEFAULT_CONTENT.copy())
    merged = DEFAULT_CONTENT.copy()
    merged.update(stored)
    return merged


def save_content(content):
    save_json(CONTENT_FILE, content)


def load_settings():
    stored = read_json(SETTINGS_FILE, DEFAULT_SETTINGS.copy())
    merged = DEFAULT_SETTINGS.copy()
    merged.update(stored)
    return merged


def save_settings(settings):
    save_json(SETTINGS_FILE, settings)


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_iso():
    return now_str()


def parse_dt(value):
    if not value:
        return datetime.min
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(str(value)[:26], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.min


def seed_defaults():
    udata = load_users()
    if not udata["users"]:
        defaults = [
            ("Admin User", "admin@emergency.so", "admin123", "admin", "0612345678"),
        ]
        for name, email, password, role, phone in defaults:
            uid = udata["next_id"]
            udata["next_id"] += 1
            udata["users"].append(
                {
                    "id": uid,
                    "name": name,
                    "email": email,
                    "phone": phone,
                    "password_hash": generate_password_hash(password),
                    "role": role,
                    "status": "active",
                    "created_at": now_str(),
                    "last_login": None,
                    "activity": [],
                }
            )
        save_users(udata)

    # Ensure Call Center operator demo account exists (additive, never removes users)
    if USE_MYSQL:
        try:
            from database import mysql_store as _ms

            _ms.ensure_call_center_schema()
        except Exception:
            pass
    udata = load_users()
    if not any(u.get("email", "").lower() == "operator@callcenter.so" for u in udata["users"]):
        uid = udata["next_id"]
        udata["next_id"] += 1
        udata["users"].append(
            {
                "id": uid,
                "name": "Call Center Operator",
                "email": "operator@callcenter.so",
                "phone": "+252612000999",
                "password_hash": generate_password_hash("123456"),
                "role": "call_center",
                "status": "active",
                "created_at": now_str(),
                "last_login": None,
                "activity": [{"action": "Account seeded", "timestamp": now_str()}],
            }
        )
        try:
            save_users(udata)
        except Exception as exc:
            # Surface ENUM truncation clearly instead of crashing obscurely
            import logging

            logging.error(
                "Failed to seed call_center operator (check users.role ENUM includes "
                "call_center). Error: %s",
                exc,
            )
            # Remove the failed user from memory so app can still start
            udata["users"] = [
                u for u in udata["users"] if u.get("email", "").lower() != "operator@callcenter.so"
            ]
            udata["next_id"] = max((u["id"] for u in udata["users"]), default=0) + 1

    edata = load_emergencies()
    if not edata["emergencies"]:
        samples = [
            ("medical", "Wadajir District, Mogadishu", 2.03, 45.33, "Ahmed Hassan", "+252 61 234 5678", "pending", "hospital"),
            ("fire", "Bakaro Market, Mogadishu", 2.02, 45.32, "Fatima Ali", "+252 61 876 5432", "pending", "fire"),
            ("security", "KM4 Junction, Mogadishu", 2.04, 45.34, "Omar Yusuf", "+252 61 111 2233", "dispatched", "police"),
            ("accident", "Howlwadaag, Mogadishu", 2.05, 45.35, "Hawa Mohamed", "+252 61 444 5566", "pending", "police"),
        ]
        for etype, location, lat, lng, name, phone, status, assigned in samples:
            eid = edata["next_id"]
            edata["next_id"] += 1
            edata["emergencies"].append(
                {
                    "id": eid,
                    "type": etype,
                    "location": location + " (" + str(lat) + ", " + str(lng) + ")",
                    "district": location,
                    "latitude": lat,
                    "longitude": lng,
                    "caller_name": name,
                    "phone": phone,
                    "timestamp": now_iso(),
                    "status": status,
                    "assigned_to": assigned,
                }
            )
        save_emergencies(edata)

    if not os.path.exists(CONTENT_FILE):
        save_content(DEFAULT_CONTENT.copy())
    if not os.path.exists(SETTINGS_FILE):
        save_settings(DEFAULT_SETTINGS.copy())
    hl.seed_hospitals_if_empty(read_json, save_json)
    hl.migrate_all_hospitals(read_json, save_json)
    seed_announcements_if_empty()


def get_user_by_login(login):
    key = (login or "").strip().lower()
    udata = load_users()
    for user in udata["users"]:
        if user["email"].lower() == key:
            return user, udata
        if user.get("username", "").lower() == key:
            return user, udata
    return None, udata


def get_user_by_id(uid):
    udata = load_users()
    for user in udata["users"]:
        if user["id"] == uid:
            return user, udata
    return None, udata


def log_activity(user, action):
    entry = {"action": action, "timestamp": now_str()}
    user.setdefault("activity", [])
    user["activity"] = ([entry] + user["activity"])[:50]


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    user, _ = get_user_by_id(uid)
    return prepare_user_for_template(user)


def login_user(user):
    user = normalize_user_record(user)
    session["user_id"] = user["id"]
    session["role"] = user["role"]
    session["name"] = user["name"]
    session["email"] = user["email"]


def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        settings = load_settings()
        if settings.get("maintenance_mode") and session.get("role") != "admin":
            flash("System is under maintenance. Try again later.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return wrapped


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login", next=request.path))
            settings = load_settings()
            if settings.get("maintenance_mode") and session.get("role") != "admin":
                flash("System is under maintenance.", "error")
                return redirect(url_for("login"))
            if session.get("role") not in roles:
                flash("You do not have permission to access that page.", "error")
                return redirect(ROLE_HOME.get(session.get("role"), "/login"))
            return f(*args, **kwargs)

        return wrapped

    return decorator


def admin_required(f):
    return role_required("admin")(f)


def call_center_required(f):
    return role_required("call_center")(f)


def normalize_type(raw_type):
    if not raw_type:
        return "medical"
    t = str(raw_type).strip().lower().replace(" ", "_")
    mapping = {
        "medical": "medical",
        "accident": "accident",
        "fire": "fire",
        "security": "security",
        "family_help": "family_help",
        "family": "family_help",
        "other": "medical",
    }
    return mapping.get(t, t)


def matches_filter(emergency_type, filter_type):
    if not filter_type:
        return True
    allowed = TYPE_MAP.get(filter_type.lower(), [filter_type.lower()])
    return emergency_type in allowed


def emergencies_today_count():
    edata = load_emergencies()
    today = datetime.now().date()
    return sum(
        1
        for e in edata["emergencies"]
        if parse_dt(e["timestamp"]).date() == today
    )


@app.context_processor
def inject_globals():
    return {
        "content": load_content(),
        "settings": load_settings(),
        "auth_user": current_user(),
        "google_maps_key": load_settings().get("google_maps_api_key", ""),
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        user = current_user()
        return redirect(_role_home(user))

    if request.method == "POST":
        login_id = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user, udata = get_user_by_login(login_id)

        if user and user.get("status") == "blocked":
            flash("Your account has been blocked. Contact admin.", "error")
        elif user and check_password_hash(user["password_hash"], password):
            user["last_login"] = now_str()
            log_activity(user, "Logged in")
            save_users(udata)
            login_user(user)
            flash("Welcome back, " + user_name(user) + "!", "success")
            nxt = request.args.get("next")
            if nxt and nxt.startswith("/"):
                return redirect(nxt)
            return redirect(_role_home(user))
        else:
            flash("Invalid email/username or password.", "error")

    return render_template("login.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user, udata = get_user_by_login(email)
        if user:
            token = secrets.token_urlsafe(32)
            user["reset_token"] = token
            user["reset_expires"] = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
            save_users(udata)
            flash(
                "If that email exists, a reset link was generated. "
                f"Reset URL: {url_for('reset_password', token=token, _external=True)}",
                "success",
            )
        else:
            flash("If that email exists, reset instructions were sent.", "success")
        return redirect(url_for("login"))
    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    udata = load_users()
    user = None
    for u in udata["users"]:
        if u.get("reset_token") == token:
            user = u
            break
    if not user:
        flash("Invalid or expired reset link.", "error")
        return redirect(url_for("login"))
    expires = parse_dt(user.get("reset_expires"))
    if expires < datetime.now():
        flash("Reset link expired. Request a new one.", "error")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        pw = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(pw) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif pw != confirm:
            flash("Passwords do not match.", "error")
        else:
            user["password_hash"] = generate_password_hash(pw)
            user.pop("reset_token", None)
            user.pop("reset_expires", None)
            save_users(udata)
            flash("Password updated. You can log in now.", "success")
            return redirect(url_for("login"))
    return render_template("reset_password.html", token=token)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if session.get("user_id"):
        user = current_user()
        return redirect(_role_home(user))

    if request.method == "POST":
        name = request.form.get("name", "").strip() or request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        role = request.form.get("role", "citizen")

        if role == "admin" or role == "call_center":
            flash("Cannot register as admin or call center operator.", "error")
        elif not name or not email or not password:
            flash("All required fields must be filled.", "error")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        elif role not in VALID_ROLES or role in ("admin", "call_center"):
            flash("Invalid role.", "error")
        else:
            udata = load_users()
            if any(u["email"].lower() == email.lower() for u in udata["users"]):
                flash("Email already registered.", "error")
            else:
                uid = udata["next_id"]
                udata["next_id"] += 1
                user = {
                    "id": uid,
                    "name": name,
                    "email": email,
                    "phone": phone or "",
                    "password_hash": generate_password_hash(password),
                    "role": role,
                    "status": "active",
                    "created_at": now_str(),
                    "last_login": now_str(),
                    "activity": [{"action": "Account created", "timestamp": now_str()}],
                }
                udata["users"].append(user)
                save_users(udata)
                login_user(user)
                flash("Account created successfully!", "success")
                if role == "hospital":
                    return redirect(url_for("hospital_register"))
                return redirect(ROLE_HOME[role])

    return render_template("signup.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/")
@role_required("citizen")
def index():
    return render_template("index.html", user=current_user())


@app.route("/dashboard")
@role_required("citizen")
def user_dashboard():
    return render_template("user_dashboard.html", user=current_user())


def load_announcements():
    return read_json(ANNOUNCEMENTS_FILE, {"announcements": [], "next_id": 1})


def seed_announcements_if_empty():
    data = load_announcements()
    if data["announcements"]:
        return
    data["announcements"] = [
        {
            "id": 1,
            "title": "Welcome to Somalia Emergency Response",
            "body": "Tap SOS for immediate help. Your location is shared automatically with dispatch.",
            "timestamp": now_str(),
            "priority": "info",
        },
        {
            "id": 2,
            "title": "24/7 Emergency Hotline",
            "body": "For life-threatening emergencies, call 999 while your app request is processed.",
            "timestamp": now_str(),
            "priority": "alert",
        },
    ]
    data["next_id"] = 3
    save_json(ANNOUNCEMENTS_FILE, data)


def _somalia_bounds_ok(lat, lng):
    return hl.is_in_somalia(lat, lng)


def _known_hospital_results(query):
    """Build geocode results from verified Somalia hospital directory."""
    out = []
    for h in hl.search_known_hospitals(query):
        out.append({
            "lat": h["latitude"],
            "lng": h["longitude"],
            "display_name": f"{h['name']}, {h['address']}",
            "name": h["name"],
            "address": h["address"],
            "city": h["city"],
            "district": h["district"],
            "region": h["region"],
            "source": "known_hospital",
            "match_score": 100,
        })
    return out


def _normalize_somalia_query(q):
    q = (q or "").strip()
    if not q:
        return q
    lower = q.lower()
    if "somalia" not in lower:
        q = f"{q}, Mogadishu, Somalia"
    elif "mogadishu" not in lower and any(
        kw in lower for kw in ("banadir", "digfeer", "digfer", "medina", "erdogan", "hospital")
    ):
        q = f"{q}, Mogadishu"
    return q


def _nominatim_in_somalia(row):
    addr = row.get("address") or {}
    country = (addr.get("country_code") or addr.get("country") or "").lower()
    if country and country not in ("so", "somalia", "somali"):
        return False
    try:
        lat, lng = float(row["lat"]), float(row["lon"])
    except (KeyError, TypeError, ValueError):
        return False
    return _somalia_bounds_ok(lat, lng)


def _score_geocode_match(query, row, parsed_name):
    q = query.lower().strip()
    name = (parsed_name or "").lower()
    display = (row.get("display_name") or "").lower()
    if name == q or q == name.replace(" hospital", ""):
        return 90
    if q in name or q in display:
        return 75
    if name in q:
        return 60
    return 10


def _parse_nominatim_address(addr):
    if not isinstance(addr, dict):
        return {"address": "", "city": "", "district": "", "region": ""}
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("municipality")
        or addr.get("state_district")
        or ""
    )
    district = (
        addr.get("suburb")
        or addr.get("neighbourhood")
        or addr.get("district")
        or addr.get("county")
        or addr.get("quarter")
        or ""
    )
    region = addr.get("state") or addr.get("region") or addr.get("county") or ""
    street = addr.get("road") or addr.get("pedestrian") or addr.get("footway") or ""
    parts = [p for p in (street, district, city) if p]
    return {
        "address": ", ".join(parts) if parts else "",
        "city": city,
        "district": district,
        "region": region,
    }


def _nominatim_request(path, params):
    query = urllib.parse.urlencode(params)
    url = f"https://nominatim.openstreetmap.org/{path}?{query}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "GurmadNetAI/1.0 (Somalia emergency response)"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode())


@app.route("/api/geocode/search")
@role_required("hospital")
def geocode_search():
    """Search Somalia locations — known hospitals first, then Nominatim (Somalia only)."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"success": True, "results": [], "rejected": 0})
    results = _known_hospital_results(q)
    seen = {(round(r["lat"], 5), round(r["lng"], 5)) for r in results}
    rejected = 0
    try:
        somalia_q = _normalize_somalia_query(q)
        rows = _nominatim_request("search", {
            "q": somalia_q,
            "format": "json",
            "addressdetails": 1,
            "countrycodes": "so",
            "limit": 15,
            "viewbox": f"{hl.SOMALIA_LNG_MIN},{hl.SOMALIA_LAT_MAX},{hl.SOMALIA_LNG_MAX},{hl.SOMALIA_LAT_MIN}",
            "bounded": 1,
        })
        nominatim_candidates = []
        for row in rows:
            if not _nominatim_in_somalia(row):
                rejected += 1
                continue
            lat, lng = float(row["lat"]), float(row["lon"])
            key = (round(lat, 5), round(lng, 5))
            if key in seen:
                continue
            seen.add(key)
            parsed = _parse_nominatim_address(row.get("address", {}))
            name = row.get("name") or (row.get("display_name", "").split(",")[0])
            nominatim_candidates.append({
                "lat": lat,
                "lng": lng,
                "display_name": row.get("display_name", ""),
                "name": name,
                "address": parsed["address"] or row.get("display_name", ""),
                "city": parsed["city"] or "Mogadishu",
                "district": parsed["district"],
                "region": parsed["region"] or "Banadir",
                "source": "nominatim",
                "match_score": _score_geocode_match(q, row, name),
            })
        nominatim_candidates.sort(key=lambda x: -x["match_score"])
        results.extend(nominatim_candidates)
    except Exception as exc:
        if not results:
            return jsonify({
                "success": False,
                "message": str(exc),
                "results": [],
                "rejected": rejected,
            }), 502
    if not results:
        return jsonify({
            "success": False,
            "message": "Location must be in Somalia.",
            "results": [],
            "rejected": rejected,
        })
    return jsonify({
        "success": True,
        "results": results[:12],
        "rejected": rejected,
        "require_selection": len(results) > 1,
    })


@app.route("/api/geocode/reverse")
@role_required("hospital")
def geocode_reverse():
    """Reverse geocode coordinates within Somalia."""
    try:
        lat = float(request.args.get("lat", ""))
        lng = float(request.args.get("lng", ""))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Valid lat/lng required."}), 400
    try:
        hl.validate_coordinates(lat, lng)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    try:
        rows = _nominatim_request("reverse", {
            "lat": lat,
            "lon": lng,
            "format": "json",
            "addressdetails": 1,
            "zoom": 18,
        })
        if isinstance(rows, list):
            row = rows[0] if rows else {}
        else:
            row = rows
        addr = row.get("address", {}) if isinstance(row, dict) else {}
        country = (addr.get("country_code") or addr.get("country") or "").lower()
        if country and country not in ("so", "somalia", "somali"):
            return jsonify({"success": False, "message": "Location must be in Somalia."}), 400
        parsed = _parse_nominatim_address(addr)
        return jsonify({
            "success": True,
            "result": {
                "lat": lat,
                "lng": lng,
                "display_name": row.get("display_name", ""),
                "name": row.get("name") or parsed.get("city") or "Selected location",
                "address": parsed["address"] or row.get("display_name", ""),
                "city": parsed["city"] or "Mogadishu",
                "district": parsed["district"],
                "region": parsed["region"] or "Banadir",
            },
        })
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 502


@app.route("/hospital")
@role_required("hospital")
def hospital_dashboard():
    user = current_user()
    hid, hospital = _get_user_hospital(user)
    if not hid or not hospital:
        return redirect(url_for("hospital_register"))
    return render_template(
        "hospital_dashboard.html",
        user=user,
        hospital=hospital,
    )


@app.route("/hospital/register", methods=["GET", "POST"])
@role_required("hospital")
def hospital_register():
    user = current_user()
    hid, hospital = _get_user_hospital(user)
    if hid and hospital:
        return redirect(url_for("hospital_dashboard"))

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        if not data:
            data = {
                "name": request.form.get("name", ""),
                "region": request.form.get("region", ""),
                "district": request.form.get("district", ""),
                "city": request.form.get("city", ""),
                "address": request.form.get("address", ""),
                "phone": request.form.get("phone", ""),
                "emergency_contacts": request.form.get("emergency_contacts", ""),
                "services": request.form.getlist("services"),
                "ambulance_available": request.form.get("ambulance_available"),
                "ambulance_count": request.form.get("ambulance_count", 0),
                "emergency_capacity": request.form.get("emergency_capacity", 10),
                "operating_status": request.form.get("operating_status", "open"),
                "latitude": request.form.get("latitude"),
                "longitude": request.form.get("longitude"),
                "contact_email": user.get("email") or request.form.get("contact_email", ""),
            }
        try:
            data["owner_user_id"] = user["id"]
            data["contact_email"] = data.get("contact_email") or user.get("email", "")
            if not data.get("phone"):
                data["phone"] = user.get("phone", "")
            lat, lng = hl.validate_coordinates(data.get("latitude"), data.get("longitude"))
            data["latitude"], data["longitude"] = lat, lng
            import logging
            logging.info(
                "Hospital registration location — name=%s address=%s city=%s district=%s lat=%s lng=%s user_id=%s",
                data.get("name"),
                data.get("address"),
                data.get("city"),
                data.get("district"),
                lat,
                lng,
                user.get("id"),
            )
            append_audit(
                "hospital_location_selected",
                "hospital",
                0,
                {
                    "name": data.get("name"),
                    "address": data.get("address"),
                    "city": data.get("city"),
                    "district": data.get("district"),
                    "latitude": lat,
                    "longitude": lng,
                },
                user.get("id"),
            )
            hospital = hl.create_hospital(data, read_json, save_json)
            _link_user_to_hospital(user["id"], hospital["id"])
            append_audit("hospital_registered", "hospital", hospital["id"], {"name": hospital["name"]})
            if request.is_json:
                return jsonify({"success": True, "hospital": hospital})
            flash(f"Hospital '{hospital['name']}' registered successfully.", "success")
            return redirect(url_for("hospital_dashboard"))
        except ValueError as exc:
            if request.is_json:
                return jsonify({"success": False, "message": str(exc)}), 400
            flash(str(exc), "error")

    return render_template(
        "hospital_register.html",
        user=user,
        service_options=hl.SERVICE_OPTIONS,
    )


@app.route("/api/hospital/profile", methods=["GET", "PUT"])
@role_required("hospital")
def api_hospital_profile():
    user = current_user()
    hid, hospital = _get_user_hospital(user)
    if not hid or not hospital:
        return jsonify({"success": False, "message": "Complete hospital registration first."}), 403

    if request.method == "GET":
        return jsonify({"success": True, "hospital": hospital})

    data = request.get_json(silent=True) or {}
    try:
        hospital = hl.update_hospital(hid, data, read_json, save_json)
        append_audit("hospital_profile_updated", "hospital", hid)
        return jsonify({"success": True, "hospital": hospital})
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400


@app.route("/police")
@role_required("police")
def police_dashboard():
    return render_template("police_dashboard.html", user=current_user())


@app.route("/fire")
@role_required("fire")
def fire_dashboard():
    return render_template("fire_dashboard.html", user=current_user())


@app.route("/admin")
@role_required("admin")
def admin_dashboard():
    return render_template("admin_dashboard.html", user=current_user())


@app.route("/api/location/ip")
def location_ip():
    """Approximate location when GPS is denied (JSON database app, no external DB)."""
    default = {
        "lat": 2.0469,
        "lng": 45.3182,
        "district": "KM4 Junction, Mogadishu",
        "source": "default",
    }
    try:
        req = urllib.request.Request(
            "http://ip-api.com/json/?fields=status,lat,lon,city,country",
            headers={"User-Agent": "EmergencyHelpApp/1.0"},
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            payload = json.loads(resp.read().decode())
        if payload.get("status") == "success":
            lat = float(payload.get("lat", default["lat"]))
            lng = float(payload.get("lon", default["lng"]))
            city = payload.get("city") or "Mogadishu"
            country = payload.get("country") or "Somalia"
            district = f"{city}, {country}"
            if "somalia" not in country.lower() and "mogadishu" not in city.lower():
                lat, lng = default["lat"], default["lng"]
                district = default["district"]
            return jsonify(
                {"lat": lat, "lng": lng, "district": district, "source": "ip"}
            )
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return jsonify(default)


@app.route("/api/hospitals", methods=["GET"])
@login_required
def api_hospitals():
    hdata = hl.seed_hospitals_if_empty(read_json, save_json)
    city = request.args.get("city", "")
    region = request.args.get("region", "")
    specialty = request.args.get("specialty", "")
    q = request.args.get("q", "")
    lat = request.args.get("lat")
    lng = request.args.get("lng")
    hospitals = hl.filter_hospitals(hdata["hospitals"], city, region, specialty, q)
    if lat and lng:
        try:
            lat, lng = float(lat), float(lng)
            ranked = hl.hospitals_by_distance(lat, lng, hospitals, emergency_only=False)
            hospitals = [{**h, "distance_km": round(d, 2)} for d, h in ranked]
        except (TypeError, ValueError):
            pass
    return jsonify({"hospitals": hospitals, "count": len(hospitals)})


@app.route("/api/nearest_hospital", methods=["GET"])
@role_required("citizen")
def api_nearest_hospital():
    """Return the closest open hospital for patient GPS coordinates."""
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    if lat is None or lng is None:
        return jsonify({"success": False, "message": "lat and lng required"}), 400
    hdata = hl.seed_hospitals_if_empty(read_json, save_json)
    ranked = hl.hospitals_by_distance(lat, lng, hdata["hospitals"])
    if not ranked:
        return jsonify({"success": False, "message": "No hospital available nearby"}), 404
    dist_km, hospital = ranked[0]
    eta_min = max(3, int((dist_km / 40) * 60))
    return jsonify({
        "success": True,
        "hospital": {
            "id": hospital["id"],
            "name": hospital["name"],
            "phone": hospital.get("phone", ""),
            "city": hospital.get("city", ""),
            "latitude": hospital["latitude"],
            "longitude": hospital["longitude"],
            "distance_km": round(dist_km, 2),
            "eta_minutes": eta_min,
            "ambulance_available": hospital.get("ambulance_available", False),
        },
    })


@app.route("/api/healthcare/emergency", methods=["POST"])
@role_required("citizen")
def api_healthcare_emergency():
    settings = load_settings()
    if not settings.get("sos_enabled", True):
        return jsonify({"success": False, "message": "SOS disabled."}), 403
    data = request.get_json(silent=True) or {}
    emergency, err = _create_healthcare_emergency(data, request_mode="emergency")
    if err:
        return jsonify({"success": False, "message": err}), 400
    return jsonify({
        "success": True,
        "id": emergency["id"],
        "status": emergency["status"],
        "hospital": {
            "id": emergency.get("assigned_hospital_id"),
            "name": emergency.get("assigned_hospital_name"),
            "distance_km": emergency.get("hospital_distance_km"),
        },
        "message": "Emergency sent to nearest hospital.",
    })


@app.route("/api/healthcare/preferred", methods=["POST"])
@role_required("citizen")
def api_healthcare_preferred():
    data = request.get_json(silent=True) or {}
    if not data.get("hospital_id"):
        return jsonify({"success": False, "message": "Select a hospital."}), 400
    emergency, err = _create_healthcare_emergency(data, request_mode="preferred")
    if err:
        return jsonify({"success": False, "message": err}), 400
    return jsonify({
        "success": True,
        "id": emergency["id"],
        "status": emergency["status"],
        "hospital": {
            "id": emergency.get("assigned_hospital_id"),
            "name": emergency.get("assigned_hospital_name"),
        },
    })


@app.route("/api/my_emergencies")
@role_required("citizen")
def api_my_emergencies():
    uid = session.get("user_id")
    edata = load_emergencies()
    mine = [e for e in edata["emergencies"] if e.get("user_id") == uid]
    mine.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    out = []
    now = datetime.now()
    for e in mine[:5]:
        ts = parse_dt(e.get("timestamp", ""))
        delta = now - ts
        if delta.days > 0:
            ago = f"{delta.days} day{'s' if delta.days > 1 else ''} ago"
        elif delta.seconds >= 3600:
            h = delta.seconds // 3600
            ago = f"{h} hour{'s' if h > 1 else ''} ago"
        elif delta.seconds >= 60:
            m = delta.seconds // 60
            ago = f"{m} min ago"
        else:
            ago = "Just now"
        out.append({
            "id": e["id"],
            "type": (e.get("type") or "emergency").replace("_", " ").title(),
            "status": e.get("status"),
            "time_ago": ago,
        })
    return jsonify({"success": True, "emergencies": out})


@app.route("/api/patient/request/status")
@role_required("citizen")
def api_patient_request_status():
    _run_escalations()
    rid = request.args.get("id", type=int)
    edata = load_emergencies()
    em = None
    if rid:
        em, _ = get_emergency_by_id(rid)
    else:
        for e in reversed(edata["emergencies"]):
            if e.get("user_id") == session.get("user_id"):
                em = e
                break
    if not em or em.get("user_id") != session.get("user_id"):
        return jsonify({"success": True, "active": False})
    hdata = hl.load_hospitals(read_json, save_json)
    hospital = hl.get_hospital_by_id(hdata, em.get("assigned_hospital_id"))
    dist = em.get("hospital_distance_km")
    if dist is None and hospital and em.get("latitude") is not None:
        dist = round(
            hl.haversine_km(
                em["latitude"], em["longitude"],
                hospital["latitude"], hospital["longitude"],
            ),
            2,
        )
    eta_min = max(3, int((float(dist or 5) / 40) * 60)) if dist else None
    team_label = em.get("assigned_team_label") or TEAM_LABELS.get(em.get("assigned_to"), "Emergency Response Team")
    return jsonify({
        "success": True,
        "active": True,
        "request": {
            "id": em["id"],
            "type": em.get("type"),
            "status": em.get("status"),
            "status_history": em.get("status_history", []),
            "team_label": team_label,
            "assigned_to": em.get("assigned_to"),
            "location": em.get("location"),
            "latitude": em.get("latitude"),
            "longitude": em.get("longitude"),
            "accuracy_m": em.get("accuracy_m"),
            "tracking_active": em.get("tracking_active", False),
            "last_location_update": em.get("last_location_update"),
            "location_trail": em.get("location_history", [])[-15:],
        },
    })


@app.route("/api/hospital/request/<int:eid>/accept", methods=["POST"])
@role_required("hospital")
def hospital_accept_request(eid):
    user = current_user()
    hid, _ = _get_user_hospital(user)
    if not hid:
        return jsonify({"success": False, "message": "Hospital not registered"}), 403
    em, edata = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False}), 404
    if em.get("assigned_hospital_id") != hid:
        return jsonify({"success": False, "message": "Not assigned to your hospital"}), 403
    _append_status(em, "accepted", "Hospital accepted request")
    em["accepted_at"] = now_str()
    em["assigned_to"] = "hospital"
    hdata = hl.load_hospitals(read_json, save_json)
    hospital = hl.get_hospital_by_id(hdata, hid)
    if hospital:
        em["responder_latitude"] = hospital["latitude"]
        em["responder_longitude"] = hospital["longitude"]
    save_emergencies(edata)
    _notify("patient", em.get("user_id"), "Your emergency request has been accepted.", eid, "request_accepted")
    append_audit("hospital_accept", "emergency", eid)
    return jsonify({"success": True, "status": em["status"]})


@app.route("/api/hospital/request/<int:eid>/reject", methods=["POST"])
@role_required("hospital")
def hospital_reject_request(eid):
    user = current_user()
    hid, _ = _get_user_hospital(user)
    if not hid:
        return jsonify({"success": False, "message": "Hospital not registered"}), 403
    em, edata = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False}), 404
    if em.get("assigned_hospital_id") != hid:
        return jsonify({"success": False, "message": "Forbidden"}), 403
    settings = load_settings()
    timeout = int(settings.get("hospital_response_timeout_sec", 120))
    hdata = hl.load_hospitals(read_json, save_json)
    _append_status(em, "rejected_by_hospital", "Hospital rejected — escalating")
    em["escalation_index"] = em.get("escalation_index", 0) + 1
    hospital = hl.assign_next_hospital(em, hdata, timeout)
    if hospital:
        _notify("hospital", hospital["id"], f"Escalated emergency #{eid}", eid)
        _notify("patient", em.get("user_id"), f"Request forwarded to {hospital['name']}", eid)
    else:
        _append_status(em, "no_hospital_available", "No more hospitals")
    save_emergencies(edata)
    return jsonify({"success": True, "status": em["status"], "hospital": em.get("assigned_hospital_name")})


@app.route("/api/notifications")
@login_required
def api_notifications():
    role = session.get("role")
    unread_only = request.args.get("unread") == "1"
    if role == "citizen":
        notes = hl.get_notifications_for(read_json, "patient", session.get("user_id"), unread_only=unread_only)
    elif role == "hospital":
        user = current_user()
        hid, _ = _get_user_hospital(user)
        notes = hl.get_notifications_for(read_json, "hospital", hid, unread_only=unread_only) if hid else []
    elif role == "admin":
        notes = hl.get_notifications_for(read_json, "admin", session.get("user_id"), unread_only=unread_only)
    elif role in ("police", "fire", "call_center"):
        notes = hl.get_notifications_for(read_json, role, session.get("user_id"), unread_only=unread_only)
    else:
        notes = []
    return jsonify({"notifications": notes, "unread_count": sum(1 for n in notes if not n.get("read"))})


@app.route("/api/notifications/read", methods=["POST"])
@login_required
def api_notifications_read():
    data = request.get_json(silent=True) or {}
    role = session.get("role")
    target_type = "patient" if role == "citizen" else role
    target_id = session.get("user_id")
    if role == "hospital":
        target_id = _user_hospital_id(current_user()) or target_id
    hl.mark_notifications_read(read_json, save_json, target_type, target_id, data.get("ids"))
    return jsonify({"success": True})


@app.route("/api/messages/<int:request_id>", methods=["GET", "POST"])
@login_required
def api_messages(request_id):
    em, _ = get_emergency_by_id(request_id)
    if not em:
        return jsonify({"success": False}), 404
    if not _can_access_emergency(em, session.get("role")):
        return jsonify({"success": False, "message": "Forbidden"}), 403
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        msg_type = data.get("msg_type", "text")
        text = (data.get("text") or "").strip()
        audio = (data.get("audio") or "").strip()
        if msg_type == "voice":
            if not audio:
                return jsonify({"success": False, "message": "No audio data"}), 400
            text = "[Voice message]"
        elif not text:
            return jsonify({"success": False, "message": "Empty message"}), 400
        msg = hl.add_message(
            read_json, save_json, request_id, session.get("role"), session.get("user_id"),
            text, msg_type=msg_type, audio_data=audio,
        )
        em_chat, edata = get_emergency_by_id(request_id)
        if em_chat:
            em_chat.pop("chat_typing", None)
            save_emergencies(edata)
        if session.get("role") == "citizen":
            if em.get("assigned_hospital_id"):
                _notify("hospital", em.get("assigned_hospital_id"), f"New message on request #{request_id}", request_id)
            else:
                _notify_admins(f"New citizen message on emergency #{request_id}", request_id, "system_alert")
        else:
            _notify("patient", em.get("user_id"), "New message from response team", request_id, "system_alert")
        return jsonify({"success": True, "message": msg})
    msgs = hl.get_messages_for_request(read_json, save_json, request_id, session.get("role"))
    typing = em.get("chat_typing")
    show_typing = False
    if typing and typing.get("role") != session.get("role"):
        show_typing = (datetime.now() - parse_dt(typing.get("at"))).total_seconds() < 8
    return jsonify({"success": True, "messages": msgs, "typing": show_typing})


@app.route("/api/emergencies/<int:eid>/typing", methods=["POST"])
@login_required
def emergency_typing(eid):
    em, edata = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False}), 404
    if not _can_access_emergency(em, session.get("role")):
        return jsonify({"success": False, "message": "Forbidden"}), 403
    data = request.get_json(silent=True) or {}
    if data.get("typing"):
        em["chat_typing"] = {"role": session.get("role"), "at": now_str()}
    else:
        em.pop("chat_typing", None)
    save_emergencies(edata)
    return jsonify({"success": True})


@app.route("/api/user/profile", methods=["GET", "PUT"])
@role_required("citizen")
def api_user_profile():
    user, udata = get_user_by_id(session.get("user_id"))
    if not user:
        return jsonify({"success": False}), 404
    if request.method == "GET":
        safe = {k: user.get(k) for k in (
            "id", "name", "email", "phone", "profile_photo", "emergency_contact_name",
            "emergency_contact_phone", "emergency_contact_relation", "address", "city",
            "date_of_birth", "blood_type", "medical_notes", "created_at", "last_login",
            "status", "saved_locations",
        )}
        return jsonify({"success": True, "profile": safe})
    data = request.get_json(silent=True) or {}
    allowed = (
        "name", "phone", "profile_photo", "emergency_contact_name", "emergency_contact_phone",
        "emergency_contact_relation", "address", "city", "date_of_birth", "blood_type", "medical_notes",
        "saved_locations",
    )
    for key in allowed:
        if key in data:
            user[key] = data[key]
    if data.get("profile_photo") and len(str(data["profile_photo"])) > 120000:
        return jsonify({"success": False, "message": "Photo too large"}), 400
    save_users(udata)
    return jsonify({"success": True, "profile": prepare_user_for_template(user)})


@app.route("/api/user/dashboard")
@role_required("citizen")
def api_user_dashboard():
    _run_escalations()
    uid = session.get("user_id")
    edata = load_emergencies()
    mine = [e for e in edata["emergencies"] if e.get("user_id") == uid]
    mine.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    active = [e for e in mine if e.get("status") not in COMPLETED_STATUSES]
    completed = [e for e in mine if e.get("status") in COMPLETED_STATUSES]
    notes = hl.get_notifications_for(read_json, "patient", uid, limit=30)
    unread = sum(1 for n in notes if not n.get("read"))
    announcements = load_announcements().get("announcements", [])[:10]
    user, _ = get_user_by_id(uid)
    active_chat = None
    if active:
        em = active[0]
        msgs = hl.get_messages_for_request(read_json, save_json, em["id"], "citizen")
        active_chat = {"request_id": em["id"], "message_count": len(msgs)}
    return jsonify({
        "success": True,
        "profile_summary": {
            "name": user.get("name") if user else "",
            "email": user.get("email") if user else "",
            "phone": user.get("phone") if user else "",
            "profile_photo": user.get("profile_photo", "") if user else "",
            "emergency_contact_name": user.get("emergency_contact_name", "") if user else "",
            "emergency_contact_phone": user.get("emergency_contact_phone", "") if user else "",
            "account_status": user.get("status", "active") if user else "active",
            "saved_locations": user.get("saved_locations", []) if user else [],
        },
        "active_emergency": _emergency_summary(active[0]) if active else None,
        "active_count": len(active),
        "completed_count": len(completed),
        "recent_emergencies": [_emergency_summary(e) for e in mine[:8]],
        "notifications": notes[:12],
        "unread_notifications": unread,
        "announcements": announcements,
        "active_chat": active_chat,
    })


TIMELINE_STEPS = (
    ("request_submitted", "Request Submitted"),
    ("request_accepted", "Request Accepted"),
    ("team_assigned", "Team Assigned"),
    ("en_route", "Team En Route"),
    ("arrived", "Team Arrived"),
    ("completed", "Case Completed"),
)

DISPLAY_STAGE_LABELS = {
    "request_received": "Request Received",
    "team_assigned": "Team Assigned",
    "on_the_way": "On The Way",
    "arrived": "Arrived",
    "assistance_complete": "Assistance Complete",
    "standby": "Ready — No Active Emergency",
}


def _distance_remaining_km(em, responder=None):
    victim_lat, victim_lng = hl.best_emergency_coords(em)
    if not hl.is_in_somalia(victim_lat, victim_lng):
        return None
    dist = None
    if responder and responder.get("latitude") is not None:
        if hl.is_in_somalia(responder["latitude"], responder["longitude"]):
            dist = hl.haversine_km(
                responder["latitude"], responder["longitude"], victim_lat, victim_lng
            )
    if dist is None:
        base = _responder_base_location(em)
        if base and hl.is_in_somalia(base["latitude"], base["longitude"]):
            dist = hl.haversine_km(base["latitude"], base["longitude"], victim_lat, victim_lng)
    return hl.cap_local_distance_km(dist)


def _dispatch_unit_info(em):
    """Assigned response unit details shown to the citizen."""
    eid = em["id"]
    team_label = em.get("assigned_team_label") or TEAM_LABELS.get(em.get("assigned_to"), "Emergency Response Team")
    base = _responder_base_location(em)
    contact = "+252 61 500 0000"
    unit_name = team_label
    if base:
        unit_name = base.get("name", team_label)
        hdata = hl.load_hospitals(read_json, save_json)
        hospital = hl.get_hospital_by_id(hdata, em.get("assigned_hospital_id")) if em.get("assigned_hospital_id") else None
        if hospital and hospital.get("phone"):
            contact = hospital["phone"]
    drivers = ("Ahmed Noor", "Fatima Ali", "Hassan Mohamed", "Amina Yusuf", "Omar Diini")
    vehicles = ("AMB", "RSC", "EMR")
    prefix = vehicles[eid % len(vehicles)]
    assigned = em.get("assigned_to", "hospital")
    team_code = {"hospital": "MED", "fire": "FIR", "police": "POL"}.get(assigned, "ERS")
    return {
        "team_name": unit_name,
        "team_id": f"ERS-{team_code}-{eid:04d}",
        "vehicle_number": f"{prefix}-{1000 + (eid % 900)}",
        "driver_name": drivers[eid % len(drivers)],
        "contact_number": contact,
    }


def _emergency_display_stage(em):
    if em.get("status") in COMPLETED_STATUSES:
        return "assistance_complete", DISPLAY_STAGE_LABELS["assistance_complete"]
    rs = em.get("responder_status", {})
    if rs.get("arrived_at_scene") or em.get("status") == "in_progress":
        return "arrived", DISPLAY_STAGE_LABELS["arrived"]
    if rs.get("en_route") or em.get("status") == "dispatched":
        return "on_the_way", DISPLAY_STAGE_LABELS["on_the_way"]
    if em.get("assigned_hospital_id") or em.get("assigned_to") in RESPONSE_STATIONS:
        return "team_assigned", DISPLAY_STAGE_LABELS["team_assigned"]
    if em.get("status") in ("pending", "pending_hospital", "accepted"):
        return "request_received", DISPLAY_STAGE_LABELS["request_received"]
    return "request_received", DISPLAY_STAGE_LABELS["request_received"]


def _build_emergency_timeline(em):
    rs = em.get("responder_status", {})
    history = {h.get("status"): h.get("timestamp") for h in em.get("status_history", [])}
    steps = []
    for key, label in TIMELINE_STEPS:
        ts = None
        done = False
        if key == "request_submitted":
            ts = em.get("timestamp")
            done = bool(ts)
        elif key == "request_accepted":
            ts = em.get("accepted_at") or history.get("accepted")
            done = bool(ts) or em.get("status") not in ("pending",)
        elif key == "team_assigned":
            ts = history.get("pending_hospital") or history.get("pending")
            done = bool(em.get("assigned_hospital_id")) or em.get("assigned_to") in RESPONSE_STATIONS
            if done and not ts:
                ts = em.get("timestamp")
        elif key == "en_route":
            ts = rs.get("en_route")
            done = bool(ts) or em.get("status") in ("dispatched", "in_progress", "completed", "resolved")
        elif key == "arrived":
            ts = rs.get("arrived_at_scene")
            done = bool(ts) or em.get("status") in ("in_progress", "completed", "resolved")
        elif key == "completed":
            ts = rs.get("reached_victim") or history.get("completed") or history.get("resolved")
            done = em.get("status") in COMPLETED_STATUSES
        steps.append({"key": key, "label": label, "timestamp": ts, "completed": done})
    return steps


def _emergency_summary(em):
    team = em.get("assigned_team_label") or TEAM_LABELS.get(em.get("assigned_to"), "Response Team")
    victim_lat, victim_lng, _ = _emergency_coords_view(em)
    em_view = dict(em)
    em_view["latitude"] = victim_lat
    em_view["longitude"] = victim_lng
    responder = _compute_responder_location(em_view)
    stage_key, stage_label = _emergency_display_stage(em)
    unit = _dispatch_unit_info(em)
    hospital_info = None
    if em.get("assigned_hospital_id"):
        hdata = hl.load_hospitals(read_json, save_json)
        hospital = hl.get_hospital_by_id(hdata, em.get("assigned_hospital_id"))
        coords = hl.resolve_hospital_coords(hospital) if hospital else None
        if hospital and coords:
            hospital_info = {
                "id": hospital["id"],
                "name": hospital["name"],
                "latitude": coords[0],
                "longitude": coords[1],
            }
    return {
        "id": em["id"],
        "type": em.get("type", "emergency"),
        "status": em.get("status"),
        "display_stage": stage_key,
        "display_stage_label": stage_label,
        "team": team,
        "timestamp": em.get("timestamp"),
        "last_update": em.get("last_location_update") or em.get("accepted_at") or em.get("timestamp"),
        "location": em.get("location"),
        "latitude": victim_lat,
        "longitude": victim_lng,
        "tracking_active": em.get("tracking_active", False),
        "eta_minutes": _compute_eta_minutes(em_view, responder),
        "distance_km": _distance_remaining_km(em_view, responder),
        "responder_assigned": responder is not None or bool(em.get("assigned_hospital_id")),
        "hospital": hospital_info,
        "assigned_to": em.get("assigned_to"),
        "dispatch_unit": unit,
        "timeline": _build_emergency_timeline(em),
        "responder_status": em.get("responder_status", {}),
    }


def _responder_base_location(em):
    assigned = em.get("assigned_to", "hospital")
    if assigned == "hospital" and em.get("assigned_hospital_id"):
        hdata = hl.load_hospitals(read_json, save_json)
        hospital = hl.get_hospital_by_id(hdata, em.get("assigned_hospital_id"))
        if hospital:
            coords = hl.resolve_hospital_coords(hospital)
            if coords:
                return {
                    "latitude": coords[0],
                    "longitude": coords[1],
                    "name": hospital["name"],
                }
    station = RESPONSE_STATIONS.get(assigned)
    if station and hl.is_in_somalia(station["latitude"], station["longitude"]):
        return dict(station)
    return None


def _compute_responder_location(em):
    """Simulate live responder GPS advancing toward the emergency scene."""
    if em.get("status") in COMPLETED_STATUSES:
        return None
    victim_lat, victim_lng = hl.best_emergency_coords(em)
    if not hl.is_in_somalia(victim_lat, victim_lng):
        return None
    base = _responder_base_location(em)
    if not base and not em.get("assigned_hospital_id") and em.get("assigned_to") not in RESPONSE_STATIONS:
        return None
    if not base:
        return None

    start_lat, start_lng = base["latitude"], base["longitude"]
    rs = em.get("responder_status", {})
    if rs.get("reached_victim") or em.get("status") in ("completed", "resolved"):
        progress = 1.0
    elif rs.get("arrived_at_scene") or em.get("status") == "in_progress":
        progress = 0.92
    elif rs.get("en_route") or em.get("status") == "dispatched":
        anchor = rs.get("en_route") or em.get("accepted_at") or em.get("timestamp")
        elapsed = (datetime.now() - parse_dt(anchor)).total_seconds()
        progress = min(0.88, max(0.08, elapsed / 420))
    elif em.get("status") in ("accepted", "pending_hospital"):
        progress = 0.0
    elif em.get("assigned_hospital_id") or em.get("assigned_to") in RESPONSE_STATIONS:
        progress = 0.04
    else:
        return None

    lat = start_lat + (victim_lat - start_lat) * progress
    lng = start_lng + (victim_lng - start_lng) * progress
    return {
        "latitude": round(lat, 6),
        "longitude": round(lng, 6),
        "name": base.get("name", em.get("assigned_team_label", "Response Team")),
        "progress_pct": round(progress * 100),
    }


def _compute_eta_minutes(em, responder=None):
    dist = _distance_remaining_km(em, responder)
    if dist is None:
        stored = em.get("hospital_distance_km")
        dist = hl.cap_local_distance_km(stored)
    if dist is None:
        return None
    eta = max(2, int((float(dist) / 35) * 60))
    return min(eta, 120)


def _emergency_coords_view(em):
    """Sanitized Somalia coordinates for map and distance calculations."""
    lat, lng = hl.best_emergency_coords(em)
    stored_valid = (
        em.get("latitude") is not None
        and em.get("longitude") is not None
        and hl.is_in_somalia(em["latitude"], em["longitude"])
    )
    return lat, lng, stored_valid


def _emergency_tracking_payload(em):
    victim_lat, victim_lng, coords_valid = _emergency_coords_view(em)
    em_view = dict(em)
    em_view["latitude"] = victim_lat
    em_view["longitude"] = victim_lng
    responder = _compute_responder_location(em_view)
    trail = hl.filter_somalia_trail(em.get("location_history") or [])
    team_label = em.get("assigned_team_label") or TEAM_LABELS.get(em.get("assigned_to"), "Response Team")

    hospital_payload = None
    station_payload = None
    assigned = em.get("assigned_to", "hospital")
    if assigned == "hospital" and em.get("assigned_hospital_id"):
        hdata = hl.load_hospitals(read_json, save_json)
        hospital = hl.get_hospital_by_id(hdata, em.get("assigned_hospital_id"))
        coords = hl.resolve_hospital_coords(hospital) if hospital else None
        if hospital and coords:
            hospital_payload = {
                "id": hospital["id"],
                "name": hospital["name"],
                "latitude": coords[0],
                "longitude": coords[1],
                "phone": hospital.get("phone"),
            }
    elif assigned in RESPONSE_STATIONS:
        station = RESPONSE_STATIONS[assigned]
        station_payload = {
            "type": assigned,
            "name": station["name"],
            "latitude": station["latitude"],
            "longitude": station["longitude"],
        }

    district = em.get("district") or ""
    if not coords_valid:
        district = district or "Mogadishu, Somalia"
    location_label = district + " (" + str(victim_lat) + ", " + str(victim_lng) + ")"

    return {
        "emergency_id": em["id"],
        "latitude": victim_lat,
        "longitude": victim_lng,
        "coords_valid": coords_valid,
        "coords_corrected": not coords_valid,
        "accuracy_m": em.get("accuracy_m"),
        "district": district,
        "location": location_label,
        "status": em.get("status"),
        "type": em.get("type"),
        "assigned_to": assigned,
        "tracking_active": em.get("tracking_active", False),
        "last_location_update": em.get("last_location_update"),
        "caller_name": em.get("caller_name"),
        "phone": em.get("phone"),
        "trail": trail[-30:],
        "trail_count": len(trail),
        "team_label": team_label,
        "team_assigned": responder is not None or bool(em.get("assigned_hospital_id")),
        "hospital": hospital_payload,
        "station": station_payload,
        "responder": responder,
        "eta_minutes": _compute_eta_minutes(em_view, responder),
        "distance_km": _distance_remaining_km(em_view, responder),
        "responder_status": em.get("responder_status", {}),
        "display_stage": _emergency_display_stage(em)[0],
        "display_stage_label": _emergency_display_stage(em)[1],
        "dispatch_unit": _dispatch_unit_info(em),
        "timeline": _build_emergency_timeline(em),
        "last_update": em.get("last_location_update") or em.get("accepted_at") or em.get("timestamp"),
        "typing": em.get("chat_typing"),
    }


@app.route("/api/announcements")
@login_required
def api_announcements():
    return jsonify({"announcements": load_announcements().get("announcements", [])})


@app.route("/api/send_alert", methods=["POST"])
@role_required("citizen")
def send_alert():
    settings = load_settings()
    if not settings.get("sos_enabled", True):
        return jsonify({"success": False, "message": "SOS system is currently disabled."}), 403
    if settings.get("maintenance_mode"):
        return jsonify({"success": False, "message": "System under maintenance."}), 503
    if emergencies_today_count() >= settings.get("max_emergencies_per_day", 100):
        return jsonify({"success": False, "message": "Daily emergency limit reached."}), 429

    data = request.get_json(silent=True) or {}
    edata = load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    etype = normalize_type(data.get("type", "medical"))
    assign_map = {
        "medical": "hospital",
        "family_help": "hospital",
        "fire": "fire",
        "security": "police",
        "accident": "police",
    }
    lat = data.get("latitude")
    lng = data.get("longitude")
    district = data.get("district") or ""
    location_text = data.get("location") or "Location unavailable"
    if lat is not None and lng is not None:
        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            lat, lng = None, None

    fix = build_location_fix(data)
    fix["latitude"] = fix["latitude"] if fix["latitude"] is not None else lat
    fix["longitude"] = fix["longitude"] if fix["longitude"] is not None else lng
    if fix["latitude"] is not None:
        try:
            fix["latitude"], fix["longitude"] = hl.validate_coordinates(
                fix["latitude"], fix["longitude"]
            )
        except ValueError as exc:
            return jsonify({"success": False, "message": str(exc)}), 400
    elif lat is not None and lng is not None:
        return jsonify({
            "success": False,
            "message": "Coordinates must be within Somalia.",
        }), 400

    emergency = {
        "id": eid,
        "user_id": session.get("user_id"),
        "type": etype,
        "location": location_text,
        "district": district or fix.get("district", ""),
        "latitude": fix["latitude"],
        "longitude": fix["longitude"],
        "building": fix.get("building", ""),
        "floor": fix.get("floor", ""),
        "room": fix.get("room", ""),
        "accuracy_m": fix.get("accuracy_m"),
        "method": fix.get("method", "gps"),
        "confidence": fix.get("confidence"),
        "timestamp": now_iso(),
        "status": "pending",
        "caller_name": data.get("name") or session.get("name", "Anonymous"),
        "phone": data.get("phone") or "Not provided",
        "notes": (data.get("notes") or "").strip()[:2000],
        "assigned_to": assign_map.get(etype, "hospital"),
        "location_history": [fix],
        "responder_status": {},
    }
    _apply_tracking_fields(emergency, fix)
    _auto_dispatch_emergency(emergency)
    edata["emergencies"].append(emergency)
    save_emergencies(edata)
    _run_escalations()
    # AI analyzes in parallel — never delays or replaces SOS auto-dispatch
    _schedule_ai_analysis(emergency, source="sos")
    append_audit("emergency_created", "emergency", eid, {"type": etype}, session.get("user_id"))
    return jsonify({
        "success": True,
        "id": eid,
        "status": emergency.get("status"),
        "team": emergency.get("assigned_team_label"),
        "assigned_hospital": emergency.get("assigned_hospital_name"),
        "hospital_distance_km": emergency.get("hospital_distance_km"),
        "message": "Emergency dispatched to response team.",
    })


@app.route("/api/route/osrm")
@login_required
def api_osrm_route():
    """Proxy OSRM driving directions (Somalia coordinates only)."""
    from_param = request.args.get("from", "")
    to_param = request.args.get("to", "")
    try:
        lat1, lng1 = [float(x) for x in from_param.split(",")]
        lat2, lng2 = [float(x) for x in to_param.split(",")]
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid from/to coordinates."}), 400
    for lat, lng in ((lat1, lng1), (lat2, lng2)):
        if not hl.is_in_somalia(lat, lng):
            return jsonify({"success": False, "message": "Route points must be within Somalia."}), 400
    path = f"{lng1},{lat1};{lng2},{lat2}"
    url = (
        "https://router.project-osrm.org/route/v1/driving/"
        + urllib.parse.quote(path, safe=";,")
        + "?overview=full&geometries=geojson&steps=false"
    )
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return jsonify({"success": False, "message": "Routing service unavailable."}), 502
    routes = payload.get("routes") or []
    if not routes:
        return jsonify({"success": False, "message": "No route found."}), 404
    route = routes[0]
    geom = route.get("geometry") or {}
    coords = geom.get("coordinates") or []
    return jsonify({
        "success": True,
        "coordinates": coords,
        "distance_km": round((route.get("distance") or 0) / 1000, 2),
        "duration_minutes": max(1, int((route.get("duration") or 0) / 60)),
    })


@app.route("/api/emergencies/<int:eid>/tracking", methods=["GET"])
@login_required
def emergency_tracking(eid):
    """Real-time location snapshot + trail for citizen and assigned hospital."""
    em, _ = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False, "message": "Not found"}), 404
    role = session.get("role")
    if not _can_access_emergency(em, role):
        return jsonify({"success": False, "message": "Forbidden"}), 403
    payload = _emergency_tracking_payload(em)
    return jsonify({"success": True, **payload})


@app.route("/api/emergencies/<int:eid>/location", methods=["POST"])
@login_required
def append_emergency_location(eid):
    data = request.get_json(silent=True) or {}
    em, edata = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False, "message": "Not found"}), 404
    role = session.get("role")
    if not _can_access_emergency(em, role):
        return jsonify({"success": False, "message": "Forbidden"}), 403

    fix = build_location_fix(data)
    if fix["latitude"] is None:
        return jsonify({
            "success": False,
            "message": "Coordinates must be within Somalia.",
        }), 400
    try:
        fix["latitude"], fix["longitude"] = hl.validate_coordinates(
            fix["latitude"], fix["longitude"]
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    em.setdefault("location_history", [])
    em["location_history"].append(fix)
    if fix["latitude"] is not None:
        em["latitude"] = fix["latitude"]
        em["longitude"] = fix["longitude"]
    for key in ("building", "floor", "room", "district", "accuracy_m", "method", "confidence"):
        if fix.get(key):
            em[key] = fix[key]
    em["tracking_active"] = True
    em["last_location_update"] = now_str()
    if em.get("location") and fix.get("district"):
        em["location"] = fix["district"] + " (" + str(fix["latitude"]) + ", " + str(fix["longitude"]) + ")"
    save_emergencies(edata)
    append_audit("location_update", "emergency", eid, fix)
    return jsonify({
        "success": True,
        "fix": fix,
        "count": len(em["location_history"]),
        "latitude": em.get("latitude"),
        "longitude": em.get("longitude"),
    })


@app.route("/api/emergencies/<int:eid>/location/stop", methods=["POST"])
@login_required
def stop_emergency_tracking(eid):
    em, edata = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False}), 404
    if session.get("role") == "citizen" and em.get("user_id") != session.get("user_id"):
        return jsonify({"success": False, "message": "Forbidden"}), 403
    em["tracking_active"] = False
    save_emergencies(edata)
    return jsonify({"success": True, "tracking_active": False})


@app.route("/api/emergencies/<int:eid>/locations", methods=["GET"])
@login_required
def get_emergency_locations(eid):
    em, _ = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False}), 404
    if not _can_access_emergency(em, session.get("role")):
        return jsonify({"success": False, "message": "Forbidden"}), 403
    return jsonify({
        "success": True,
        "emergency_id": eid,
        "locations": em.get("location_history", []),
        "latitude": em.get("latitude"),
        "longitude": em.get("longitude"),
        "tracking_active": em.get("tracking_active", False),
    })


@app.route("/api/emergencies/<int:eid>/responder", methods=["POST"])
@login_required
def responder_status_update(eid):
    if session.get("role") not in ROLE_API_TYPE:
        return jsonify({"success": False, "message": "Forbidden"}), 403
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    em, edata = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False}), 404
    if not matches_filter(em["type"], ROLE_API_TYPE[session.get("role")]):
        return jsonify({"success": False, "message": "Forbidden"}), 403

    valid_actions = ("arrived_at_scene", "reached_victim", "en_route")
    if action not in valid_actions:
        return jsonify({"success": False, "message": "Invalid action"}), 400

    em.setdefault("responder_status", {})
    em["responder_status"][action] = now_str()
    uid = em.get("user_id")
    if action == "en_route":
        em["status"] = "dispatched"
        _notify("patient", uid, "Your emergency response team is on the way.", eid, "team_dispatched")
    elif action == "arrived_at_scene":
        em["status"] = "in_progress"
        _notify("patient", uid, "The response team has arrived at your location.", eid, "team_arrived")
    elif action == "reached_victim":
        em["status"] = "completed"
        _notify("patient", uid, "Your emergency has been resolved.", eid, "emergency_completed")
    save_emergencies(edata)
    append_audit(action, "emergency", eid)
    return jsonify({"success": True, "responder_status": em["responder_status"], "status": em["status"]})


@app.route("/api/emergencies/<int:eid>/route", methods=["GET"])
@login_required
def emergency_route(eid):
    em, _ = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False}), 404
    coords = EmergencyLocation_parse(em)
    return jsonify(
        {
            "success": True,
            "victim": {
                "lat": coords["lat"],
                "lng": coords["lng"],
                "label": _location_label(em),
                "building": em.get("building"),
                "floor": em.get("floor"),
                "room": em.get("room"),
            },
            "instructions": _simple_navigation_instructions(em),
            "eta_note": "Use OSRM/Mapbox for production turn-by-turn; outdoor routing only in MVP.",
        }
    )


def EmergencyLocation_parse(em):
    lat = em.get("latitude")
    lng = em.get("longitude")
    if lat is not None and lng is not None:
        return {"lat": float(lat), "lng": float(lng)}
    import re

    m = re.search(r"\((-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\)", em.get("location", ""))
    if m:
        return {"lat": float(m.group(1)), "lng": float(m.group(2))}
    return {"lat": 2.0469, "lng": 45.3182}


def _location_label(em):
    parts = [p for p in [em.get("building"), em.get("floor") and f"Floor {em['floor']}", em.get("room") and f"Room {em['room']}"] if p]
    if parts:
        return ", ".join(parts) + " — " + (em.get("district") or em.get("location", ""))
    return em.get("district") or em.get("location", "Unknown")


def _simple_navigation_instructions(em):
    label = _location_label(em)
    return [
        {"step": 1, "text": "Navigate to " + (em.get("district") or "victim coordinates") + " using the map."},
        {"step": 2, "text": "Use GPS coordinates: " + label + "."},
        {"step": 3, "text": "Call the victim on arrival to confirm exact spot."},
    ]


@app.route("/api/get_emergencies", methods=["GET"])
@login_required
def get_emergencies():
    _run_escalations()
    role = session.get("role")
    filter_type = request.args.get("type", "").lower()
    status_filter = request.args.get("status", "").lower()

    if role in ROLE_API_TYPE:
        allowed = ROLE_API_TYPE[role]
        if filter_type and filter_type != allowed:
            return jsonify({"success": False, "message": "Forbidden"}), 403
        filter_type = allowed
    elif role != "admin":
        return jsonify({"success": False, "message": "Forbidden"}), 403

    edata = load_emergencies()
    result = []
    user = current_user()
    hospital_id = _user_hospital_id(user) if role == "hospital" else None
    if role == "hospital" and not hospital_id:
        return jsonify({
            "emergencies": [],
            "count": 0,
            "refresh_interval": load_settings().get("refresh_interval", 5),
            "message": "Complete hospital registration to receive dispatch requests.",
        })
    for em in edata["emergencies"]:
        if role != "admin" and not matches_filter(em["type"], filter_type):
            continue
        if role == "hospital":
            if em.get("assigned_hospital_id") != hospital_id:
                continue
        if status_filter and em["status"] != status_filter:
            continue
        row = dict(em)
        row["tracking_active"] = em.get("tracking_active", False)
        row["last_location_update"] = em.get("last_location_update")
        result.append(row)
    result.sort(key=lambda x: x["timestamp"], reverse=True)
    settings = load_settings()
    return jsonify(
        {
            "emergencies": result,
            "count": len(result),
            "refresh_interval": settings.get("refresh_interval", 5),
        }
    )


@app.route("/api/update_status", methods=["POST"])
@login_required
def update_status():
    role = session.get("role")
    if role not in ROLE_API_TYPE and role != "admin":
        return jsonify({"success": False, "message": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    eid = int(data.get("id", 0))
    new_status = data.get("status", "dispatched")
    if new_status not in STATUS_VALUES:
        return jsonify({"success": False, "message": "Invalid status"}), 400

    edata = load_emergencies()
    for em in edata["emergencies"]:
        if em["id"] == eid:
            if role in ROLE_API_TYPE and not matches_filter(em["type"], ROLE_API_TYPE[role]):
                return jsonify({"success": False, "message": "Forbidden"}), 403
            em["status"] = new_status
            uid = em.get("user_id")
            if new_status == "dispatched":
                _notify("patient", uid, "Your emergency response team has been dispatched.", eid, "team_dispatched")
            elif new_status in ("completed", "resolved"):
                _notify("patient", uid, "Your emergency has been completed.", eid, "emergency_completed")
                _ai_record_outcome(em)
            save_emergencies(edata)
            append_audit("status_update", "emergency", eid, {"status": new_status})
            return jsonify({"success": True, "emergency": em})
    return jsonify({"success": False, "message": "Not found"}), 404


# ---------- ADMIN API ----------


@app.route("/api/admin/backup", methods=["POST"])
@admin_required
def admin_backup():
    backup_dir = os.path.join(DATABASE_DIR, "backups", datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(backup_dir, exist_ok=True)
    ms = _mysql_backend()
    if ms:
        for name, payload in ms.export_all().items():
            with open(os.path.join(backup_dir, f"{name}.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
    else:
        import shutil

        for name in os.listdir(DATABASE_DIR):
            if name.endswith(".json"):
                shutil.copy2(os.path.join(DATABASE_DIR, name), os.path.join(backup_dir, name))
    append_audit("database_backup", "system", 0, {"path": backup_dir})
    return jsonify({"success": True, "backup_path": backup_dir})

@app.route("/api/admin/stats")
@admin_required
def admin_stats():
    udata = load_users()
    edata = load_emergencies()
    users = udata["users"]
    emergencies = edata["emergencies"]
    now = datetime.now()
    today = now.date()
    week_start = today - timedelta(days=7)

    by_role = {r: 0 for r in VALID_ROLES}
    for u in users:
        by_role[u["role"]] = by_role.get(u["role"], 0) + 1

    today_count = sum(1 for e in emergencies if parse_dt(e["timestamp"]).date() == today)
    week_count = sum(1 for e in emergencies if parse_dt(e["timestamp"]).date() >= week_start)

    by_type = {}
    by_location = {}
    for e in emergencies:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        loc = (e.get("location") or "Unknown").split(",")[0].strip()
        by_location[loc] = by_location.get(loc, 0) + 1

    settings = load_settings()
    avg = (
        settings.get("ambulance_response_time", 8)
        + settings.get("police_response_time", 6)
        + settings.get("fire_response_time", 9)
    ) / 3

    active_sessions = 1 if session.get("user_id") else 0

    return jsonify(
        {
            "total_users": len(users),
            "users_by_role": by_role,
            "emergencies_today": today_count,
            "emergencies_week": week_count,
            "emergencies_total": len(emergencies),
            "avg_response_time": round(avg, 1),
            "active_sessions": active_sessions,
            "by_type": by_type,
            "by_location": dict(sorted(by_location.items(), key=lambda x: -x[1])[:8]),
            "blocked_users": sum(1 for u in users if u.get("status") == "blocked"),
        }
    )


@app.route("/api/admin/users")
@admin_required
def admin_users():
    udata = load_users()
    q = request.args.get("q", "").lower()
    role = request.args.get("role", "")
    users = udata["users"]
    if q:
        users = [
            u
            for u in users
            if q in user_name(u).lower() or q in u.get("email", "").lower()
        ]
    if role:
        users = [u for u in users if u.get("role") == role]
    safe = []
    for u in users:
        safe.append(
            {
                "id": u["id"],
                "name": user_name(u),
                "email": u["email"],
                "phone": u.get("phone", ""),
                "role": u["role"],
                "status": u.get("status", "active"),
                "created_at": u.get("created_at"),
                "last_login": u.get("last_login"),
                "activity": u.get("activity", []),
            }
        )
    return jsonify({"users": safe})


@app.route("/api/admin/users/block", methods=["POST"])
@admin_required
def admin_block_user():
    data = request.get_json(silent=True) or {}
    uid = int(data.get("id", 0))
    udata = load_users()
    for u in udata["users"]:
        if u["id"] == uid:
            if u["role"] == "admin":
                return jsonify({"success": False, "message": "Cannot block admin"}), 400
            u["status"] = "blocked" if u.get("status") != "blocked" else "active"
            log_activity(u, "Status changed to " + u["status"])
            save_users(udata)
            return jsonify({"success": True, "status": u["status"]})
    return jsonify({"success": False}), 404


@app.route("/api/admin/users/delete", methods=["POST"])
@admin_required
def admin_delete_user():
    data = request.get_json(silent=True) or {}
    uid = int(data.get("id", 0))
    udata = load_users()
    for i, u in enumerate(udata["users"]):
        if u["id"] == uid:
            if u["role"] == "admin":
                return jsonify({"success": False, "message": "Cannot delete admin"}), 400
            udata["users"].pop(i)
            save_users(udata)
            return jsonify({"success": True})
    return jsonify({"success": False}), 404


@app.route("/api/admin/users/edit", methods=["PUT", "POST"])
@admin_required
def admin_edit_user():
    data = request.get_json(silent=True) or {}
    uid = int(data.get("id", 0))
    udata = load_users()
    for u in udata["users"]:
        if u["id"] == uid:
            if data.get("name"):
                u["name"] = data["name"]
            elif data.get("full_name"):
                u["name"] = data["full_name"]
            if data.get("email"):
                u["email"] = data["email"]
            if data.get("phone"):
                u["phone"] = data["phone"]
            if data.get("role") and data["role"] in VALID_ROLES:
                if u["role"] == "admin" and data["role"] != "admin":
                    return jsonify({"success": False, "message": "Cannot change admin role"}), 400
                u["role"] = data["role"]
            if data.get("password"):
                u["password_hash"] = generate_password_hash(data["password"])
            log_activity(u, "Profile updated by admin")
            save_users(udata)
            return jsonify({"success": True, "user": {k: u[k] for k in u if k != "password_hash"}})
    return jsonify({"success": False}), 404


@app.route("/api/admin/users/create", methods=["POST"])
@admin_required
def admin_create_user():
    data = request.get_json(silent=True) or {}
    udata = load_users()
    uid = udata["next_id"]
    udata["next_id"] += 1
    role = data.get("role", "citizen")
    if role not in VALID_ROLES:
        return jsonify({"success": False, "message": "Invalid role"}), 400
    user = {
        "id": uid,
        "name": data.get("name") or data.get("full_name", "New User"),
        "email": data.get("email", f"user{uid}@example.com"),
        "phone": data.get("phone", ""),
        "password_hash": generate_password_hash(data.get("password", "123456")),
        "role": role,
        "status": "active",
        "created_at": now_str(),
        "last_login": None,
        "activity": [{"action": "Created by admin", "timestamp": now_str()}],
    }
    udata["users"].append(user)
    save_users(udata)
    return jsonify({"success": True, "id": uid})


@app.route("/api/admin/content")
@admin_required
def admin_content():
    return jsonify(load_content())


@app.route("/api/admin/content/update", methods=["POST"])
@admin_required
def admin_content_update():
    data = request.get_json(silent=True) or {}
    key = data.get("key")
    value = data.get("value")
    if not key:
        return jsonify({"success": False}), 400
    content = load_content()
    if key in DEFAULT_CONTENT or key in content:
        content[key] = value
        save_content(content)
        return jsonify({"success": True, "content": content})
    return jsonify({"success": False, "message": "Unknown key"}), 400


@app.route("/api/admin/content/reset", methods=["POST"])
@admin_required
def admin_content_reset():
    data = request.get_json(silent=True) or {}
    key = data.get("key")
    content = load_content()
    if key and key in DEFAULT_CONTENT:
        content[key] = DEFAULT_CONTENT[key]
        save_content(content)
    else:
        content = DEFAULT_CONTENT.copy()
        save_content(content)
    return jsonify({"success": True, "content": content})


@app.route("/api/admin/settings")
@admin_required
def admin_settings():
    return jsonify(load_settings())


@app.route("/api/admin/settings/update", methods=["POST"])
@admin_required
def admin_settings_update():
    data = request.get_json(silent=True) or {}
    settings = load_settings()
    for key in DEFAULT_SETTINGS:
        if key in data:
            settings[key] = data[key]
    save_settings(settings)
    return jsonify({"success": True, "settings": settings})


@app.route("/api/admin/emergencies")
@admin_required
def admin_emergencies():
    edata = load_emergencies()
    return jsonify({"emergencies": edata["emergencies"]})


@app.route("/api/admin/emergencies/update", methods=["POST"])
@admin_required
def admin_emergencies_update():
    data = request.get_json(silent=True) or {}
    eid = int(data.get("id", 0))
    edata = load_emergencies()
    for em in edata["emergencies"]:
        if em["id"] == eid:
            if "status" in data:
                em["status"] = data["status"]
            if "assigned_to" in data:
                em["assigned_to"] = data["assigned_to"]
            save_emergencies(edata)
            return jsonify({"success": True, "emergency": em})
    return jsonify({"success": False}), 404


@app.route("/api/admin/emergencies/delete", methods=["POST"])
@admin_required
def admin_emergencies_delete():
    data = request.get_json(silent=True) or {}
    eid = int(data.get("id", 0))
    edata = load_emergencies()
    edata["emergencies"] = [e for e in edata["emergencies"] if e["id"] != eid]
    save_emergencies(edata)
    return jsonify({"success": True})


@app.route("/api/admin/emergencies/export")
@admin_required
def admin_emergencies_export():
    edata = load_emergencies()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["ID", "Type", "Location", "Caller", "Phone", "Timestamp", "Status", "Assigned To"]
    )
    for e in edata["emergencies"]:
        writer.writerow(
            [
                e["id"],
                e["type"],
                e["location"],
                e.get("caller_name", ""),
                e.get("phone", ""),
                e["timestamp"],
                e["status"],
                e.get("assigned_to", ""),
            ]
        )
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=emergencies.csv"},
    )


def _create_emergency_from_call(call, etype, team, operator, notes=""):
    """Create a standard emergency record from a call-center session (reuses auto-dispatch)."""
    edata = load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    fix = build_location_fix({
        "latitude": call.get("latitude"),
        "longitude": call.get("longitude"),
        "district": call.get("district") or call.get("address"),
        "method": "gps",
        "accuracy_m": call.get("accuracy_m"),
        "confidence": 90,
    })
    emergency = {
        "id": eid,
        "user_id": call.get("user_id"),
        "type": normalize_type(etype),
        "location": call.get("address") or call.get("district") or "Call Center GPS",
        "district": call.get("district") or "",
        "latitude": call.get("latitude"),
        "longitude": call.get("longitude"),
        "accuracy_m": call.get("accuracy_m"),
        "method": "gps",
        "confidence": 90,
        "timestamp": now_iso(),
        "status": "pending",
        "caller_name": call.get("caller_name") or "Unknown",
        "phone": call.get("phone") or "Not provided",
        "notes": (notes or call.get("notes") or "").strip()[:2000],
        "assigned_to": team,
        "location_history": [fix] if fix.get("latitude") is not None else [],
        "responder_status": {},
        "source": "call_center",
        "call_id": call.get("id"),
        "operator_id": operator.get("id") if operator else None,
        "operator_name": (operator or {}).get("name", ""),
        "request_mode": "call_center",
    }
    _apply_tracking_fields(emergency, fix if fix.get("latitude") is not None else None)
    _auto_dispatch_emergency(emergency)
    edata["emergencies"].append(emergency)
    save_emergencies(edata)
    append_audit(
        "call_center_dispatch",
        "emergency",
        eid,
        {"call_id": call.get("id"), "type": etype, "team": team},
        (operator or {}).get("id"),
    )
    # Parallel AI memory for learning; operator already approved via Call Center UI
    _schedule_ai_analysis(emergency, source="call_center")
    return emergency


def _notify_call_dispatch(call, emergency, teams):
    """Notify citizen, responders, and admins after call-center dispatch."""
    eid = emergency["id"]
    etype = emergency.get("type")
    gps_line = (
        f"{call.get('caller_name')} — GPS {call.get('latitude')}, {call.get('longitude')} "
        f"— {call.get('address')}"
    )
    _notify(
        "patient",
        call.get("user_id"),
        f"Call Center dispatched {TYPE_LABELS.get(etype, etype)} help for your call #{call.get('id')}.",
        eid,
        "team_assigned",
    )
    if "hospital" in teams and emergency.get("assigned_hospital_id"):
        _notify(
            "hospital",
            emergency["assigned_hospital_id"],
            f"CALL CENTER: {gps_line}",
            eid,
            "team_assigned",
        )
    # Notify all active police / fire user accounts for those teams
    if "police" in teams or "fire" in teams:
        udata = load_users()
        for u in udata["users"]:
            if u.get("status") == "blocked":
                continue
            if "police" in teams and u.get("role") == "police":
                _notify("police", u["id"], f"CALL CENTER: {gps_line}", eid, "team_assigned")
            if "fire" in teams and u.get("role") == "fire":
                _notify("fire", u["id"], f"CALL CENTER: {gps_line}", eid, "team_assigned")
    _notify_admins(
        f"Call Center dispatched emergency #{eid} ({etype}) from call #{call.get('id')}.",
        eid,
        "system_alert",
    )


def _notify_role_users(role, message, request_id=None, ntype="system_alert"):
    udata = load_users()
    for u in udata["users"]:
        if u.get("role") == role and u.get("status") != "blocked":
            _notify(role, u["id"], message, request_id, ntype)


# ---------- CALL CENTER (Method 2) ----------


@app.route("/call-center/login", methods=["GET", "POST"])
def call_center_login():
    if session.get("user_id") and session.get("role") == "call_center":
        return redirect(url_for("call_center_dashboard"))
    if session.get("user_id") and session.get("role") != "call_center":
        flash("Please use the Call Center login for operator accounts.", "warning")

    if request.method == "POST":
        login_id = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user, udata = get_user_by_login(login_id)
        if user and user.get("status") == "blocked":
            flash("Your account has been blocked. Contact admin.", "error")
        elif user and user.get("role") != "call_center":
            flash("This login is for Call Center Operators only.", "error")
        elif user and check_password_hash(user["password_hash"], password):
            user["last_login"] = now_str()
            user["last_seen_call_center"] = now_str()
            log_activity(user, "Call Center login")
            save_users(udata)
            login_user(user)
            flash("Welcome to the Emergency Call Center, " + user_name(user) + "!", "success")
            return redirect(url_for("call_center_dashboard"))
        else:
            flash("Invalid operator email or password.", "error")
    return render_template("call_center_login.html")


@app.route("/call-center")
@call_center_required
def call_center_dashboard():
    user = current_user()
    settings = load_settings()
    return render_template(
        "call_center_dashboard.html",
        user=user,
        call_center_phone=settings.get("call_center_phone", "+252612000999"),
        type_options=cc.EMERGENCY_TYPE_OPTIONS,
    )


@app.route("/call-center/history")
@call_center_required
def call_center_history_page():
    return render_template("call_center_history.html", user=current_user())


@app.route("/api/call-center/initiate", methods=["POST"])
@role_required("citizen")
def api_call_center_initiate():
    """Citizen presses Call Emergency Center — silent GPS + open tel: link."""
    settings = load_settings()
    if not settings.get("call_center_enabled", True):
        return jsonify({"success": False, "message": "Call Center is currently unavailable."}), 403
    if settings.get("maintenance_mode"):
        return jsonify({"success": False, "message": "System under maintenance."}), 503

    data = request.get_json(silent=True) or {}
    user = current_user()
    payload = {
        "user_id": session.get("user_id"),
        "name": data.get("name") or (user.get("name") if user else "") or session.get("name"),
        "phone": data.get("phone") or (user.get("phone") if user else "") or "",
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "address": data.get("address") or data.get("district") or "",
        "district": data.get("district") or "",
        "accuracy_m": data.get("accuracy_m"),
        "device_info": data.get("device_info") or {
            "user_agent": request.headers.get("User-Agent", "")[:300],
        },
    }
    try:
        call = cc.create_incoming_call(payload, read_json, save_json)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    phone = settings.get("call_center_phone") or cc.default_call_center_settings()["phone_primary"]
    _notify_admins(
        f"Incoming Call Center call #{call['id']} from {call['caller_name']} ({call['phone']})",
        None,
        "system_alert",
    )
    append_audit("call_center_incoming", "call", call["id"], {"user_id": payload["user_id"]})
    return jsonify({
        "success": True,
        "call_id": call["id"],
        "call_center_phone": phone,
        "tel_href": "tel:" + phone.replace(" ", ""),
        "message": "Connecting to Emergency Call Center. Your location was shared with the operator.",
        "call": {
            "id": call["id"],
            "status": call["status"],
            "latitude": call["latitude"],
            "longitude": call["longitude"],
            "address": call["address"],
        },
    })


@app.route("/api/call-center/live")
@call_center_required
def api_call_center_live():
    """Live queue for operators + heartbeat."""
    user = current_user()
    udata = load_users()
    for u in udata["users"]:
        if u.get("id") == user["id"]:
            u["last_seen_call_center"] = now_str()
            save_users(udata)
            break
    settings = load_settings()
    heartbeat = int(settings.get("call_center_heartbeat_sec", 45))
    online = cc.online_operators(load_users, heartbeat)
    active = cc.active_calls(read_json, save_json)
    stats = cc.call_stats(read_json, save_json, [o["id"] for o in online])
    return jsonify({
        "success": True,
        "calls": active,
        "stats": stats,
        "operators_online": online,
        "refresh_interval": settings.get("refresh_interval", 5),
        "call_center_phone": settings.get("call_center_phone"),
    })


@app.route("/api/call-center/history")
@login_required
def api_call_center_history():
    role = session.get("role")
    if role not in ("call_center", "admin"):
        return jsonify({"success": False, "message": "Forbidden"}), 403
    limit = request.args.get("limit", 100, type=int)
    return jsonify({"success": True, "calls": cc.call_history(read_json, save_json, limit=limit)})


@app.route("/api/call-center/calls/<int:call_id>")
@call_center_required
def api_call_center_get(call_id):
    data = cc.load_calls(read_json, save_json)
    call = cc.get_call_by_id(data, call_id)
    if not call:
        return jsonify({"success": False, "message": "Not found"}), 404
    if call.get("latitude") and call.get("longitude"):
        call["nearest"] = cc.find_nearest_responders(
            call["latitude"], call["longitude"], read_json, save_json, RESPONSE_STATIONS
        )
    return jsonify({"success": True, "call": call})


@app.route("/api/call-center/calls/<int:call_id>/answer", methods=["POST"])
@call_center_required
def api_call_center_answer(call_id):
    try:
        call = cc.answer_call(call_id, current_user(), read_json, save_json)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify({"success": True, "call": call})


@app.route("/api/call-center/calls/<int:call_id>/status", methods=["POST"])
@call_center_required
def api_call_center_status(call_id):
    data = request.get_json(silent=True) or {}
    try:
        call = cc.set_call_status(
            call_id,
            data.get("status", "in_progress"),
            read_json,
            save_json,
            notes=data.get("notes"),
            operator=current_user(),
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify({"success": True, "call": call})


@app.route("/api/call-center/calls/<int:call_id>/nearest", methods=["GET"])
@call_center_required
def api_call_center_nearest(call_id):
    data = cc.load_calls(read_json, save_json)
    call = cc.get_call_by_id(data, call_id)
    if not call:
        return jsonify({"success": False, "message": "Not found"}), 404
    nearest = cc.find_nearest_responders(
        call["latitude"], call["longitude"], read_json, save_json, RESPONSE_STATIONS
    )
    call["nearest"] = nearest
    cc.save_calls(data, save_json)
    return jsonify({"success": True, "nearest": nearest})


@app.route("/api/call-center/calls/<int:call_id>/dispatch", methods=["POST"])
@call_center_required
def api_call_center_dispatch(call_id):
    """Dispatch to one or more responder teams. Body: { types: ["medical","fire"], notes }"""
    payload = request.get_json(silent=True) or {}
    types = payload.get("types") or payload.get("emergency_types") or []
    if isinstance(types, str):
        types = [types]
    if not types:
        return jsonify({"success": False, "message": "Select at least one emergency type."}), 400

    data = cc.load_calls(read_json, save_json)
    call = cc.get_call_by_id(data, call_id)
    if not call:
        return jsonify({"success": False, "message": "Call not found"}), 404

    operator = current_user()
    if not call.get("operator_id"):
        try:
            call = cc.answer_call(call_id, operator, read_json, save_json)
        except ValueError:
            pass
        data = cc.load_calls(read_json, save_json)
        call = cc.get_call_by_id(data, call_id)

    notes = (payload.get("notes") or "").strip()
    if notes:
        call["notes"] = notes
        cc.save_calls(data, save_json)

    pairs = cc.resolve_dispatch_types(types)
    if not pairs:
        return jsonify({"success": False, "message": "Invalid emergency types."}), 400

    created = []
    teams = []
    for etype, team in pairs:
        em = _create_emergency_from_call(call, etype, team, operator, notes)
        created.append(em)
        teams.append(team)
        _notify_call_dispatch(call, em, [team])

    call = cc.record_dispatch(
        call_id,
        [e.get("type") for e in created],
        [e["id"] for e in created],
        teams,
        read_json,
        save_json,
    )

    if payload.get("complete_call"):
        call = cc.set_call_status(call_id, "completed", read_json, save_json, operator=operator)

    return jsonify({
        "success": True,
        "call": call,
        "emergencies": [
            {
                "id": e["id"],
                "type": e.get("type"),
                "status": e.get("status"),
                "assigned_to": e.get("assigned_to"),
                "assigned_hospital_id": e.get("assigned_hospital_id"),
                "assigned_hospital_name": e.get("assigned_hospital_name"),
            }
            for e in created
        ],
        "message": f"Dispatched to {', '.join(teams)}.",
    })


@app.route("/api/call-center/calls/<int:call_id>/send-gps", methods=["POST"])
@call_center_required
def api_call_center_send_gps(call_id):
    """Push GPS packet to selected responder dashboards."""
    payload = request.get_json(silent=True) or {}
    targets = payload.get("targets") or payload.get("dispatched_to") or []
    if isinstance(targets, str):
        targets = [targets]
    type_map = {
        "hospital": "medical",
        "medical": "medical",
        "police": "security",
        "fire": "fire",
    }
    types = []
    for t in targets:
        mapped = type_map.get(str(t).lower())
        if mapped:
            types.append(mapped)
    if not types and payload.get("types"):
        types = payload.get("types")
    if not types:
        return jsonify({"success": False, "message": "Select Hospital, Police, and/or Fire."}), 400

    call_data = cc.load_calls(read_json, save_json)
    call = cc.get_call_by_id(call_data, call_id)
    if not call:
        return jsonify({"success": False, "message": "Call not found"}), 404
    operator = current_user()
    pairs = cc.resolve_dispatch_types(types)
    created = []
    existing_ids = set(call.get("emergency_ids") or [])
    existing_teams = set(call.get("dispatched_to") or [])
    for etype, team in pairs:
        if team in existing_teams:
            continue
        em = _create_emergency_from_call(
            call, etype, team, operator, payload.get("notes") or "GPS shared by Call Center"
        )
        created.append(em)
        existing_ids.add(em["id"])
        existing_teams.add(team)
        _notify_call_dispatch(call, em, [team])

    all_types = list(dict.fromkeys((call.get("emergency_types") or []) + [e.get("type") for e in created]))
    call = cc.record_dispatch(
        call_id,
        all_types,
        list(existing_ids),
        list(existing_teams),
        read_json,
        save_json,
    )
    return jsonify({
        "success": True,
        "call": call,
        "emergencies": [{"id": e["id"], "assigned_to": e.get("assigned_to")} for e in created],
        "message": "GPS sent to selected responders.",
    })


@app.route("/api/call-center/calls/<int:call_id>/transfer", methods=["POST"])
@call_center_required
def api_call_center_transfer(call_id):
    """Transfer an active call to another Call Center operator."""
    payload = request.get_json(silent=True) or {}
    target_id = payload.get("operator_id")
    if not target_id:
        return jsonify({"success": False, "message": "Select a target operator_id."}), 400
    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid operator_id."}), 400

    me = current_user()
    if target_id == me.get("id"):
        return jsonify({"success": False, "message": "Cannot transfer a call to yourself."}), 400

    target, udata = get_user_by_id(target_id)
    if not target or target.get("role") != "call_center" or target.get("status") == "blocked":
        return jsonify({"success": False, "message": "Target must be an active Call Center operator."}), 400

    data = cc.load_calls(read_json, save_json)
    call = cc.get_call_by_id(data, call_id)
    if not call:
        return jsonify({"success": False, "message": "Call not found"}), 404
    if call.get("status") in ("completed", "cancelled", "missed"):
        return jsonify({"success": False, "message": "Cannot transfer a closed call."}), 400

    prev_op = call.get("operator_name") or me.get("name")
    call["operator_id"] = target["id"]
    call["operator_name"] = target.get("name") or "Operator"
    call["transferred_from"] = me.get("id")
    call["transferred_at"] = now_str()
    call["status"] = "answered" if call.get("status") == "ringing" else call.get("status")
    note = (payload.get("notes") or "").strip()
    if note:
        call["notes"] = ((call.get("notes") or "") + "\n[Transfer] " + note).strip()
    cc.save_calls(data, save_json)

    _notify(
        "call_center",
        target["id"],
        f"Call #{call_id} transferred to you from {prev_op}: {call.get('caller_name')} ({call.get('phone')})",
        None,
        "system_alert",
    )
    append_audit(
        "call_center_transfer",
        "call",
        call_id,
        {"from": me.get("id"), "to": target_id},
        me.get("id"),
    )
    return jsonify({
        "success": True,
        "call": call,
        "message": f"Call transferred to {call['operator_name']}.",
    })


@app.route("/api/call-center/calls/<int:call_id>/cancel", methods=["POST"])
@call_center_required
def api_call_center_cancel(call_id):
    try:
        call = cc.set_call_status(
            call_id, "cancelled", read_json, save_json, operator=current_user()
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    _notify(
        "patient",
        call.get("user_id"),
        f"Your Call Center session #{call_id} was cancelled by the operator.",
        None,
        "system_alert",
    )
    return jsonify({"success": True, "call": call})


@app.route("/api/call-center/calls/<int:call_id>/complete", methods=["POST"])
@call_center_required
def api_call_center_complete(call_id):
    try:
        call = cc.set_call_status(
            call_id, "completed", read_json, save_json, operator=current_user()
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify({"success": True, "call": call})


@app.route("/api/call-center/settings", methods=["GET"])
@login_required
def api_call_center_settings():
    if session.get("role") not in ("admin", "call_center"):
        return jsonify({"success": False, "message": "Forbidden"}), 403
    settings = load_settings()
    return jsonify({
        "success": True,
        "settings": {
            "enabled": settings.get("call_center_enabled", True),
            "phone_primary": settings.get("call_center_phone", "+252612000999"),
            "phone_secondary": settings.get("call_center_phone_secondary", ""),
            "auto_nearest": settings.get("call_center_auto_nearest", True),
            "heartbeat_sec": settings.get("call_center_heartbeat_sec", 45),
            "priority_medical": settings.get("call_center_priority_medical", 1),
            "priority_fire": settings.get("call_center_priority_fire", 1),
            "priority_police": settings.get("call_center_priority_police", 1),
        },
    })


@app.route("/api/admin/call-center/stats")
@admin_required
def api_admin_call_center_stats():
    settings = load_settings()
    online = cc.online_operators(load_users, int(settings.get("call_center_heartbeat_sec", 45)))
    stats = cc.call_stats(read_json, save_json, [o["id"] for o in online])
    udata = load_users()
    operators = [
        {
            "id": u["id"],
            "name": u.get("name"),
            "email": u.get("email"),
            "phone": u.get("phone"),
            "status": u.get("status"),
            "last_seen": u.get("last_seen_call_center") or u.get("last_login"),
        }
        for u in udata["users"]
        if u.get("role") == "call_center"
    ]
    return jsonify({
        "success": True,
        "stats": stats,
        "operators": operators,
        "operators_online": online,
        "settings": {
            "enabled": settings.get("call_center_enabled", True),
            "phone_primary": settings.get("call_center_phone"),
            "phone_secondary": settings.get("call_center_phone_secondary"),
        },
        "recent_calls": cc.call_history(read_json, save_json, limit=20),
    })


@app.route("/api/admin/call-center/settings", methods=["POST"])
@admin_required
def api_admin_call_center_settings():
    data = request.get_json(silent=True) or {}
    settings = load_settings()
    mapping = {
        "enabled": "call_center_enabled",
        "phone_primary": "call_center_phone",
        "phone_secondary": "call_center_phone_secondary",
        "auto_nearest": "call_center_auto_nearest",
        "heartbeat_sec": "call_center_heartbeat_sec",
        "priority_medical": "call_center_priority_medical",
        "priority_fire": "call_center_priority_fire",
        "priority_police": "call_center_priority_police",
    }
    for src, dest in mapping.items():
        if src in data:
            settings[dest] = data[src]
        if dest in data:
            settings[dest] = data[dest]
    save_settings(settings)
    append_audit("call_center_settings_updated", "settings", 0, data)
    return jsonify({"success": True, "settings": settings})


if __name__ == "__main__":
    ensure_database_dir()
    seed_defaults()
    app.run(debug=True, host="127.0.0.1", port=5000)
