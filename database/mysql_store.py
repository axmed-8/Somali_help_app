"""MySQL storage backend for GurmadNet AI."""
import json
import os
from contextlib import contextmanager
from datetime import datetime

from database.connection import load_config

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError:
    pymysql = None
    DictCursor = None


EMERGENCY_COLUMNS = {
    "id", "user_id", "type", "status", "location", "district",
    "latitude", "longitude", "notes", "caller_name", "phone",
    "assigned_to", "assigned_team_label", "assigned_hospital_id",
    "assigned_hospital_name", "hospital_distance_km", "tracking_active",
    "last_location_update", "accepted_at", "timestamp",
}

_migration_conn = None


def begin_migration():
    """Use one connection with FK checks disabled for bulk JSON import."""
    global _migration_conn
    _migration_conn = connect()
    with _migration_conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS=0")


def end_migration():
    """Re-enable FK checks and close the migration connection."""
    global _migration_conn
    if _migration_conn:
        with _migration_conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
        _migration_conn.close()
        _migration_conn = None


@contextmanager
def _db():
    if _migration_conn is not None:
        yield _migration_conn
    else:
        conn = connect()
        try:
            yield conn
        finally:
            conn.close()


def available():
    return pymysql is not None


def connect(database=None):
    if not available():
        raise RuntimeError("PyMySQL not installed. Run: pip install PyMySQL")
    cfg = load_config()
    if database is not None:
        cfg = {**cfg, "database": database}
    cfg["cursorclass"] = DictCursor
    return pymysql.connect(**cfg)


def _json_load(val, default=None):
    if default is None:
        default = {}
    if val is None or val == "":
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (TypeError, json.JSONDecodeError):
        return default


def _json_dump(val):
    return json.dumps(val, ensure_ascii=False) if val is not None else None


def _dt_str(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    return str(val)[:19]


def _entity_key(name):
    """Normalize store key (supports legacy *.json basenames for tests)."""
    key = os.path.basename(str(name))
    if key.endswith(".json"):
        key = key[:-5]
    return key


def read_store(entity, default):
    """Load entity data from MySQL."""
    loaders = {
        "users": lambda: load_users(),
        "emergencies": lambda: load_emergencies(),
        "hospitals": lambda: load_hospitals(),
        "notifications": lambda: load_notifications(),
        "messages": lambda: load_messages(),
        "announcements": lambda: load_announcements(),
        "settings": lambda: load_settings_dict(default),
        "system_content": lambda: load_content_dict(default),
        "audit_log": lambda: load_audit_log(),
        "call_center_calls": lambda: load_call_center_calls(),
        "ai_analysis": lambda: load_ai_list_store("ai_analysis"),
        "ai_recommendation": lambda: load_ai_list_store("ai_recommendation"),
        "ai_dispatch_log": lambda: load_ai_list_store("ai_dispatch_log"),
        "ai_memory": lambda: load_ai_list_store("ai_memory"),
    }
    loader = loaders.get(_entity_key(entity))
    if loader:
        return loader()
    return json.loads(json.dumps(default))


def save_store(entity, data):
    """Persist entity data to MySQL."""
    savers = {
        "users": save_users,
        "emergencies": save_emergencies,
        "hospitals": save_hospitals,
        "notifications": save_notifications,
        "messages": save_messages,
        "announcements": save_announcements,
        "settings": save_settings_dict,
        "system_content": save_content_dict,
        "audit_log": save_audit_log,
        "call_center_calls": save_call_center_calls,
        "ai_analysis": lambda d: save_ai_list_store("ai_analysis", d),
        "ai_recommendation": lambda d: save_ai_list_store("ai_recommendation", d),
        "ai_dispatch_log": lambda d: save_ai_list_store("ai_dispatch_log", d),
        "ai_memory": lambda d: save_ai_list_store("ai_memory", d),
    }
    saver = savers.get(_entity_key(entity))
    if saver:
        saver(data)


def read_by_path(path, default):
    return read_store(path, default)


def save_by_path(path, data):
    save_store(path, data)


def export_all():
    """Export all tables to dicts (admin backup)."""
    return {
        "users": load_users(),
        "emergencies": load_emergencies(),
        "hospitals": load_hospitals(),
        "notifications": load_notifications(),
        "messages": load_messages(),
        "announcements": load_announcements(),
        "settings": load_settings_dict({}),
        "system_content": load_content_dict({}),
        "audit_log": load_audit_log(),
        "call_center_calls": load_call_center_calls(),
        "ai_analysis": load_ai_list_store("ai_analysis"),
        "ai_recommendation": load_ai_list_store("ai_recommendation"),
        "ai_dispatch_log": load_ai_list_store("ai_dispatch_log"),
        "ai_memory": load_ai_list_store("ai_memory"),
    }


def export_all_json():
    return export_all()


def load_users():
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users ORDER BY id")
            rows = cur.fetchall()
        users = []
        for r in rows:
            u = dict(r)
            u["saved_locations"] = _json_load(u.pop("saved_locations", None), [])
            u["activity"] = _json_load(u.pop("activity", None), [])
            u["created_at"] = _dt_str(u.get("created_at"))
            u["last_login"] = _dt_str(u.get("last_login"))
            u["reset_expires"] = _dt_str(u.get("reset_expires"))
            u["last_seen_call_center"] = _dt_str(u.get("last_seen_call_center"))
            users.append(u)
        max_id = max((u["id"] for u in users), default=0)
        return {"users": users, "next_id": max_id + 1}


def save_users(data):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users")
            existing = {r["id"] for r in cur.fetchall()}
            for u in data.get("users", []):
                row = dict(u)
                row["saved_locations"] = _json_dump(row.get("saved_locations", []))
                row["activity"] = _json_dump(row.get("activity", []))
                row.setdefault("username", (row.get("email") or "user").split("@")[0])
                cols = [
                    "id", "username", "name", "email", "phone", "password_hash", "role", "status",
                    "profile_photo", "emergency_contact_name", "emergency_contact_phone",
                    "emergency_contact_relation", "address", "city", "date_of_birth",
                    "blood_type", "medical_notes", "saved_locations", "hospital_id",
                    "reset_token", "reset_expires", "created_at", "last_login",
                    "last_seen_call_center", "activity",
                ]
                if row["id"] in existing:
                    sets = ", ".join(f"{c}=%s" for c in cols if c != "id")
                    cur.execute(
                        f"UPDATE users SET {sets} WHERE id=%s",
                        [row.get(c) for c in cols if c != "id"] + [row["id"]],
                    )
                else:
                    ph = ", ".join(["%s"] * len(cols))
                    cur.execute(
                        f"INSERT INTO users ({', '.join(cols)}) VALUES ({ph})",
                        [row.get(c) for c in cols],
                    )


def load_hospitals():
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM hospitals ORDER BY id")
            rows = cur.fetchall()
        hospitals = []
        for r in rows:
            h = dict(r)
            h["emergency_contacts"] = _json_load(h.pop("emergency_contacts", None), [])
            h["services"] = _json_load(h.pop("services", None), [])
            h["specialties"] = _json_load(h.pop("specialties", None), [])
            h["ambulance_available"] = bool(h.get("ambulance_available"))
            h["location_verified"] = bool(h.get("location_verified"))
            h["created_at"] = _dt_str(h.get("created_at"))
            h["updated_at"] = _dt_str(h.get("updated_at"))
            hospitals.append(h)
        max_id = max((h["id"] for h in hospitals), default=0)
        return {"hospitals": hospitals, "next_id": max_id + 1}


def save_hospitals(data):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM hospitals")
            existing = {r["id"] for r in cur.fetchall()}
            for h in data.get("hospitals", []):
                row = dict(h)
                row["emergency_contacts"] = _json_dump(row.get("emergency_contacts", []))
                row["services"] = _json_dump(row.get("services", []))
                row["specialties"] = _json_dump(row.get("specialties", []))
                row["ambulance_available"] = 1 if row.get("ambulance_available") else 0
                row["location_verified"] = 1 if row.get("location_verified") else 0
                cols = [
                    "id", "name", "city", "region", "district", "address",
                    "latitude", "longitude", "phone", "emergency_contacts", "services",
                    "specialties", "ambulance_available", "ambulance_count",
                    "emergency_capacity", "rating", "operating_status", "contact_email",
                    "owner_user_id", "location_verified", "created_at", "updated_at",
                ]
                if row["id"] in existing:
                    sets = ", ".join(f"{c}=%s" for c in cols if c != "id")
                    cur.execute(
                        f"UPDATE hospitals SET {sets} WHERE id=%s",
                        [row.get(c) for c in cols if c != "id"] + [row["id"]],
                    )
                else:
                    ph = ", ".join(["%s"] * len(cols))
                    cur.execute(
                        f"INSERT INTO hospitals ({', '.join(cols)}) VALUES ({ph})",
                        [row.get(c) for c in cols],
                    )


def load_emergencies():
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM emergencies ORDER BY id")
            rows = cur.fetchall()
        out = []
        for r in rows:
            row = dict(r)
            payload = _json_load(row.pop("payload", None), {})
            em = {**payload, **{k: row[k] for k in row}}
            em["tracking_active"] = bool(em.get("tracking_active"))
            em["timestamp"] = _dt_str(em.get("timestamp"))
            em["last_location_update"] = _dt_str(em.get("last_location_update"))
            em["accepted_at"] = _dt_str(em.get("accepted_at"))
            out.append(em)
        max_id = max((e["id"] for e in out), default=0)
        return {"emergencies": out, "next_id": max_id + 1}


def save_emergencies(data):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM emergencies")
            existing = {r["id"] for r in cur.fetchall()}
            for em in data.get("emergencies", []):
                payload = {k: v for k, v in em.items() if k not in EMERGENCY_COLUMNS}
                row = {
                    "id": em["id"],
                    "user_id": em.get("user_id"),
                    "type": em.get("type", "medical"),
                    "status": em.get("status", "pending"),
                    "location": em.get("location", ""),
                    "district": em.get("district", ""),
                    "latitude": em.get("latitude"),
                    "longitude": em.get("longitude"),
                    "notes": em.get("notes", ""),
                    "caller_name": em.get("caller_name", ""),
                    "phone": em.get("phone", ""),
                    "assigned_to": em.get("assigned_to", "hospital"),
                    "assigned_team_label": em.get("assigned_team_label", ""),
                    "assigned_hospital_id": em.get("assigned_hospital_id"),
                    "assigned_hospital_name": em.get("assigned_hospital_name", ""),
                    "hospital_distance_km": em.get("hospital_distance_km"),
                    "tracking_active": 1 if em.get("tracking_active") else 0,
                    "last_location_update": em.get("last_location_update"),
                    "accepted_at": em.get("accepted_at"),
                    "timestamp": em.get("timestamp"),
                    "payload": _json_dump(payload),
                }
                cols = list(row.keys())
                if row["id"] in existing:
                    sets = ", ".join(f"{c}=%s" for c in cols if c != "id")
                    cur.execute(
                        f"UPDATE emergencies SET {sets} WHERE id=%s",
                        [row[c] for c in cols if c != "id"] + [row["id"]],
                    )
                else:
                    ph = ", ".join(["%s"] * len(cols))
                    cur.execute(
                        f"INSERT INTO emergencies ({', '.join(cols)}) VALUES ({ph})",
                        [row[c] for c in cols],
                    )


def load_notifications():
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM notifications ORDER BY id")
            rows = cur.fetchall()
        notifications = []
        for r in rows:
            n = {
                "id": r["id"],
                "timestamp": _dt_str(r.get("timestamp")),
                "target_type": r["target_type"],
                "target_id": r["target_id"],
                "message": r["message"],
                "request_id": r.get("request_id"),
                "type": r.get("ntype") or "system_alert",
                "read": bool(r.get("is_read")),
            }
            notifications.append(n)
        max_id = max((n["id"] for n in notifications), default=0)
        return {"notifications": notifications, "next_id": max_id + 1}


def save_notifications(data):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM notifications")
            existing = {r["id"] for r in cur.fetchall()}
            for n in data.get("notifications", []):
                row = {
                    "id": n["id"],
                    "target_type": n["target_type"],
                    "target_id": n["target_id"],
                    "message": n["message"],
                    "ntype": n.get("type") or n.get("ntype") or "system_alert",
                    "request_id": n.get("request_id"),
                    "is_read": 1 if n.get("read") else 0,
                    "timestamp": n.get("timestamp"),
                }
                cols = list(row.keys())
                if row["id"] in existing:
                    sets = ", ".join(f"{c}=%s" for c in cols if c != "id")
                    cur.execute(
                        f"UPDATE notifications SET {sets} WHERE id=%s",
                        [row[c] for c in cols if c != "id"] + [row["id"]],
                    )
                else:
                    ph = ", ".join(["%s"] * len(cols))
                    cur.execute(
                        f"INSERT INTO notifications ({', '.join(cols)}) VALUES ({ph})",
                        [row[c] for c in cols],
                    )


def load_messages():
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM messages ORDER BY id")
            rows = cur.fetchall()
        messages = []
        for r in rows:
            m = dict(r)
            m["timestamp"] = _dt_str(m.get("timestamp"))
            m["delivered_at"] = _dt_str(m.get("delivered_at"))
            m["seen_at"] = _dt_str(m.get("seen_at"))
            m.setdefault("msg_type", "text")
            messages.append(m)
        max_id = max((m["id"] for m in messages), default=0)
        return {"messages": messages, "next_id": max_id + 1}


def save_messages(data):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM messages")
            existing = {r["id"] for r in cur.fetchall()}
            for m in data.get("messages", []):
                row = {
                    "id": m["id"],
                    "request_id": m["request_id"],
                    "sender_role": m["sender_role"],
                    "sender_id": m["sender_id"],
                    "text": m.get("text", ""),
                    "msg_type": m.get("msg_type", "text"),
                    "status": m.get("status", "sent"),
                    "timestamp": m.get("timestamp"),
                    "delivered_at": m.get("delivered_at"),
                    "seen_at": m.get("seen_at"),
                }
                cols = list(row.keys())
                if row["id"] in existing:
                    sets = ", ".join(f"{c}=%s" for c in cols if c != "id")
                    cur.execute(
                        f"UPDATE messages SET {sets} WHERE id=%s",
                        [row[c] for c in cols if c != "id"] + [row["id"]],
                    )
                else:
                    ph = ", ".join(["%s"] * len(cols))
                    cur.execute(
                        f"INSERT INTO messages ({', '.join(cols)}) VALUES ({ph})",
                        [row[c] for c in cols],
                    )


def load_announcements():
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM announcements ORDER BY id")
            rows = cur.fetchall()
        announcements = []
        for r in rows:
            announcements.append({
                "id": r["id"],
                "title": r["title"],
                "body": r["body"],
                "priority": r.get("priority") or "info",
                "timestamp": _dt_str(r.get("timestamp")),
            })
        max_id = max((a["id"] for a in announcements), default=0)
        return {"announcements": announcements, "next_id": max_id + 1}


def save_announcements(data):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM announcements")
            existing = {r["id"] for r in cur.fetchall()}
            for a in data.get("announcements", []):
                row = {
                    "id": a["id"],
                    "title": a["title"],
                    "body": a["body"],
                    "priority": a.get("priority", "info"),
                    "timestamp": a.get("timestamp"),
                }
                cols = list(row.keys())
                if row["id"] in existing:
                    sets = ", ".join(f"{c}=%s" for c in cols if c != "id")
                    cur.execute(
                        f"UPDATE announcements SET {sets} WHERE id=%s",
                        [row[c] for c in cols if c != "id"] + [row["id"]],
                    )
                else:
                    ph = ", ".join(["%s"] * len(cols))
                    cur.execute(
                        f"INSERT INTO announcements ({', '.join(cols)}) VALUES ({ph})",
                        [row[c] for c in cols],
                    )


def load_settings_dict(default=None):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT payload FROM settings WHERE id = 1")
            row = cur.fetchone()
        if row:
            return _json_load(row["payload"], default or {})
        return json.loads(json.dumps(default or {}))


def save_settings_dict(data):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO settings (id, payload) VALUES (1, %s)
                ON DUPLICATE KEY UPDATE payload = VALUES(payload)
                """,
                (_json_dump(data),),
            )


def load_content_dict(default=None):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT payload FROM system_content WHERE id = 1")
            row = cur.fetchone()
        if row:
            return _json_load(row["payload"], default or {})
        return json.loads(json.dumps(default or {}))


def save_content_dict(data):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO system_content (id, payload) VALUES (1, %s)
                ON DUPLICATE KEY UPDATE payload = VALUES(payload)
                """,
                (_json_dump(data),),
            )


def load_audit_log():
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM audit_logs ORDER BY id DESC")
            rows = cur.fetchall()
        entries = []
        for r in rows:
            entries.append({
                "id": r["id"],
                "timestamp": _dt_str(r.get("timestamp")),
                "action": r["action"],
                "entity_type": r["entity_type"],
                "entity_id": r.get("entity_id"),
                "user_id": r.get("user_id"),
                "details": _json_load(r.get("details"), {}),
            })
        max_id = max((e["id"] for e in entries), default=0)
        return {"entries": entries, "next_id": max_id + 1}


def save_audit_log(data):
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM audit_logs")
            existing = {r["id"] for r in cur.fetchall()}
            for e in data.get("entries", []):
                row = {
                    "id": e["id"],
                    "action": e["action"],
                    "entity_type": e["entity_type"],
                    "entity_id": e.get("entity_id"),
                    "user_id": e.get("user_id"),
                    "details": _json_dump(e.get("details", {})),
                    "timestamp": e.get("timestamp"),
                }
                cols = list(row.keys())
                if row["id"] in existing:
                    sets = ", ".join(f"{c}=%s" for c in cols if c != "id")
                    cur.execute(
                        f"UPDATE audit_logs SET {sets} WHERE id=%s",
                        [row[c] for c in cols if c != "id"] + [row["id"]],
                    )
                else:
                    ph = ", ".join(["%s"] * len(cols))
                    cur.execute(
                        f"INSERT INTO audit_logs ({', '.join(cols)}) VALUES ({ph})",
                        [row[c] for c in cols],
                    )


CALL_CENTER_COLUMNS = {
    "id", "user_id", "caller_name", "phone", "latitude", "longitude",
    "address", "district", "status", "operator_id", "operator_name",
    "emergency_type", "emergency_types", "dispatched_to", "emergency_ids",
    "nearest", "device_info", "notes", "accuracy_m", "start_time",
    "answered_at", "dispatched_at", "end_time", "duration_sec",
    "final_status", "source",
}


def load_call_center_calls():
    with _db() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT * FROM call_center_calls ORDER BY id")
                rows = cur.fetchall()
            except Exception:
                return {"calls": [], "next_id": 1}
        calls = []
        for r in rows:
            c = dict(r)
            c["emergency_types"] = _json_load(c.pop("emergency_types", None), [])
            c["dispatched_to"] = _json_load(c.pop("dispatched_to", None), [])
            c["emergency_ids"] = _json_load(c.pop("emergency_ids", None), [])
            c["nearest"] = _json_load(c.pop("nearest", None), {})
            c["device_info"] = _json_load(c.pop("device_info", None), {})
            for k in ("start_time", "answered_at", "dispatched_at", "end_time"):
                c[k] = _dt_str(c.get(k))
            calls.append(c)
        max_id = max((c["id"] for c in calls), default=0)
        return {"calls": calls, "next_id": max_id + 1}


def save_call_center_calls(data):
    with _db() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT id FROM call_center_calls")
                existing = {r["id"] for r in cur.fetchall()}
            except Exception:
                return
            for c in data.get("calls", []):
                row = {
                    "id": c["id"],
                    "user_id": c.get("user_id"),
                    "caller_name": c.get("caller_name", ""),
                    "phone": c.get("phone", ""),
                    "latitude": c.get("latitude"),
                    "longitude": c.get("longitude"),
                    "address": c.get("address", ""),
                    "district": c.get("district", ""),
                    "status": c.get("status", "ringing"),
                    "operator_id": c.get("operator_id"),
                    "operator_name": c.get("operator_name", ""),
                    "emergency_type": c.get("emergency_type", ""),
                    "emergency_types": _json_dump(c.get("emergency_types", [])),
                    "dispatched_to": _json_dump(c.get("dispatched_to", [])),
                    "emergency_ids": _json_dump(c.get("emergency_ids", [])),
                    "nearest": _json_dump(c.get("nearest", {})),
                    "device_info": _json_dump(c.get("device_info", {})),
                    "notes": c.get("notes", ""),
                    "accuracy_m": c.get("accuracy_m"),
                    "start_time": c.get("start_time"),
                    "answered_at": c.get("answered_at"),
                    "dispatched_at": c.get("dispatched_at"),
                    "end_time": c.get("end_time"),
                    "duration_sec": c.get("duration_sec", 0),
                    "final_status": c.get("final_status", ""),
                    "source": c.get("source", "call_center"),
                }
                cols = list(row.keys())
                if row["id"] in existing:
                    sets = ", ".join(f"{col}=%s" for col in cols if col != "id")
                    cur.execute(
                        f"UPDATE call_center_calls SET {sets} WHERE id=%s",
                        [row[col] for col in cols if col != "id"] + [row["id"]],
                    )
                else:
                    ph = ", ".join(["%s"] * len(cols))
                    cur.execute(
                        f"INSERT INTO call_center_calls ({', '.join(cols)}) VALUES ({ph})",
                        [row[col] for col in cols],
                    )


def ensure_call_center_schema():
    """
    Idempotent MySQL migration for Call Center.
    Fixes Data truncated for column 'role' when ENUM lacks call_center.
    Safe to run on every startup.
    """
    if not available():
        return {"ok": False, "reason": "pymysql unavailable"}
    changes = []
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM users LIKE 'role'")
            role_col = cur.fetchone() or {}
            type_str = str(role_col.get("Type") or role_col.get("type") or "")
            if "call_center" not in type_str.lower():
                cur.execute(
                    "ALTER TABLE users MODIFY COLUMN role "
                    "ENUM('citizen','hospital','police','fire','admin','call_center') "
                    "NOT NULL DEFAULT 'citizen'"
                )
                changes.append("users.role_enum")

            cur.execute("SHOW COLUMNS FROM users LIKE 'last_seen_call_center'")
            if not cur.fetchone():
                cur.execute(
                    "ALTER TABLE users ADD COLUMN last_seen_call_center DATETIME NULL "
                    "AFTER last_login"
                )
                changes.append("users.last_seen_call_center")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS call_center_calls (
                  id INT AUTO_INCREMENT PRIMARY KEY,
                  user_id INT NULL,
                  caller_name VARCHAR(120) NOT NULL,
                  phone VARCHAR(40) DEFAULT '',
                  latitude DOUBLE NULL,
                  longitude DOUBLE NULL,
                  address TEXT,
                  district VARCHAR(120) DEFAULT '',
                  status VARCHAR(40) NOT NULL DEFAULT 'ringing',
                  operator_id INT NULL,
                  operator_name VARCHAR(120) DEFAULT '',
                  emergency_type VARCHAR(40) DEFAULT '',
                  emergency_types JSON,
                  dispatched_to JSON,
                  emergency_ids JSON,
                  nearest JSON,
                  device_info JSON,
                  notes TEXT,
                  accuracy_m DOUBLE NULL,
                  start_time DATETIME NOT NULL,
                  answered_at DATETIME NULL,
                  dispatched_at DATETIME NULL,
                  end_time DATETIME NULL,
                  duration_sec INT DEFAULT 0,
                  final_status VARCHAR(40) DEFAULT '',
                  source VARCHAR(40) DEFAULT 'call_center',
                  INDEX idx_cc_status (status),
                  INDEX idx_cc_operator (operator_id),
                  INDEX idx_cc_user (user_id),
                  INDEX idx_cc_start (start_time)
                )
                """
            )
            changes.append("call_center_calls")
    return {"ok": True, "changes": changes}


def load_ai_list_store(table):
    """Load ai_analysis / ai_recommendation / ai_dispatch_log / ai_memory as {items, next_id}."""
    allowed = {"ai_analysis", "ai_recommendation", "ai_dispatch_log", "ai_memory"}
    if table not in allowed:
        return {"items": [], "next_id": 1}
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id, payload FROM {table} ORDER BY id")
            rows = cur.fetchall()
        items = []
        max_id = 0
        for r in rows:
            payload = _json_load(r.get("payload"), {})
            if not isinstance(payload, dict):
                payload = {"value": payload}
            payload["id"] = r["id"]
            items.append(payload)
            max_id = max(max_id, int(r["id"] or 0))
        return {"items": items, "next_id": max_id + 1}


def save_ai_list_store(table, data):
    """Replace-store AI list tables from {items, next_id} shape used by ai_engine.storage."""
    allowed = {"ai_analysis", "ai_recommendation", "ai_dispatch_log", "ai_memory"}
    if table not in allowed:
        return
    items = data.get("items") or []
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {table}")
            for item in items:
                payload = dict(item)
                item_id = int(payload.get("id") or 0)
                if table == "ai_memory":
                    cur.execute(
                        f"""
                        INSERT INTO {table}
                          (id, event_type, emergency_id, call_id, analysis_id,
                           recommendation_id, dispatch_log_id, payload, timestamp)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            item_id,
                            payload.get("event_type") or "unknown",
                            payload.get("emergency_id"),
                            payload.get("call_id"),
                            payload.get("analysis_id"),
                            payload.get("recommendation_id"),
                            payload.get("dispatch_log_id"),
                            _json_dump(payload),
                            _dt_str(payload.get("timestamp")) or _dt_str(datetime.now()),
                        ),
                    )
                elif table == "ai_analysis":
                    cur.execute(
                        f"""
                        INSERT INTO {table}
                          (id, emergency_id, call_id, category, gurmad_type, priority,
                           risk_level, confidence, provider, source, payload, created_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            item_id,
                            payload.get("emergency_id"),
                            payload.get("call_id"),
                            payload.get("category"),
                            payload.get("gurmad_type"),
                            payload.get("priority"),
                            payload.get("risk_level"),
                            payload.get("confidence"),
                            payload.get("provider"),
                            payload.get("source"),
                            _json_dump(payload),
                            _dt_str(payload.get("created_at")) or _dt_str(datetime.now()),
                        ),
                    )
                elif table == "ai_recommendation":
                    cur.execute(
                        f"""
                        INSERT INTO {table}
                          (id, analysis_id, emergency_id, call_id, status, confidence,
                           estimated_arrival_minutes, provider, human_decision, operator_id,
                           payload, created_at, updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            item_id,
                            payload.get("analysis_id"),
                            payload.get("emergency_id"),
                            payload.get("call_id"),
                            payload.get("status") or "pending",
                            payload.get("confidence"),
                            payload.get("estimated_arrival_minutes"),
                            payload.get("provider"),
                            payload.get("human_decision") or "",
                            payload.get("operator_id"),
                            _json_dump(payload),
                            _dt_str(payload.get("created_at")) or _dt_str(datetime.now()),
                            _dt_str(payload.get("updated_at")),
                        ),
                    )
                else:  # ai_dispatch_log
                    cur.execute(
                        f"""
                        INSERT INTO {table}
                          (id, emergency_id, call_id, recommendation_id, analysis_id,
                           human_decision, operator_id, payload, created_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            item_id,
                            payload.get("emergency_id"),
                            payload.get("call_id"),
                            payload.get("recommendation_id"),
                            payload.get("analysis_id"),
                            payload.get("human_decision") or "",
                            payload.get("operator_id"),
                            _json_dump(payload),
                            _dt_str(payload.get("created_at")) or _dt_str(datetime.now()),
                        ),
                    )
        conn.commit()


def ensure_ai_schema():
    """Idempotent MySQL migration for AI Emergency Engine stores."""
    if not available():
        return {"ok": False, "reason": "pymysql unavailable"}
    changes = []
    ddl = {
        "ai_analysis": """
            CREATE TABLE IF NOT EXISTS ai_analysis (
              id INT AUTO_INCREMENT PRIMARY KEY,
              emergency_id INT NULL,
              call_id INT NULL,
              category VARCHAR(40) DEFAULT '',
              gurmad_type VARCHAR(40) DEFAULT '',
              priority VARCHAR(20) DEFAULT '',
              risk_level VARCHAR(20) DEFAULT '',
              confidence DOUBLE DEFAULT 0,
              provider VARCHAR(40) DEFAULT 'rule_based',
              source VARCHAR(40) DEFAULT '',
              payload JSON,
              created_at DATETIME NOT NULL,
              INDEX idx_ai_analysis_emergency (emergency_id),
              INDEX idx_ai_analysis_call (call_id),
              INDEX idx_ai_analysis_created (created_at)
            )
        """,
        "ai_recommendation": """
            CREATE TABLE IF NOT EXISTS ai_recommendation (
              id INT AUTO_INCREMENT PRIMARY KEY,
              analysis_id INT NULL,
              emergency_id INT NULL,
              call_id INT NULL,
              status VARCHAR(20) DEFAULT 'pending',
              confidence DOUBLE DEFAULT 0,
              estimated_arrival_minutes INT NULL,
              provider VARCHAR(40) DEFAULT 'rule_based',
              human_decision VARCHAR(40) DEFAULT '',
              operator_id INT NULL,
              payload JSON,
              created_at DATETIME NOT NULL,
              updated_at DATETIME NULL,
              INDEX idx_ai_rec_emergency (emergency_id),
              INDEX idx_ai_rec_call (call_id),
              INDEX idx_ai_rec_status (status),
              INDEX idx_ai_rec_created (created_at)
            )
        """,
        "ai_dispatch_log": """
            CREATE TABLE IF NOT EXISTS ai_dispatch_log (
              id INT AUTO_INCREMENT PRIMARY KEY,
              emergency_id INT NULL,
              call_id INT NULL,
              recommendation_id INT NULL,
              analysis_id INT NULL,
              human_decision VARCHAR(40) DEFAULT '',
              operator_id INT NULL,
              payload JSON,
              created_at DATETIME NOT NULL,
              INDEX idx_ai_dlog_emergency (emergency_id),
              INDEX idx_ai_dlog_created (created_at)
            )
        """,
        "ai_memory": """
            CREATE TABLE IF NOT EXISTS ai_memory (
              id INT AUTO_INCREMENT PRIMARY KEY,
              event_type VARCHAR(40) NOT NULL,
              emergency_id INT NULL,
              call_id INT NULL,
              analysis_id INT NULL,
              recommendation_id INT NULL,
              dispatch_log_id INT NULL,
              payload JSON,
              timestamp DATETIME NOT NULL,
              INDEX idx_ai_mem_type (event_type),
              INDEX idx_ai_mem_emergency (emergency_id),
              INDEX idx_ai_mem_ts (timestamp)
            )
        """,
    }
    with _db() as conn:
        with conn.cursor() as cur:
            for name, sql in ddl.items():
                cur.execute(sql)
                changes.append(name)
        conn.commit()
    return {"ok": True, "changes": changes}


def table_counts():
    """Return row counts for all application tables."""
    tables = (
        "hospitals", "users", "emergencies", "notifications", "messages",
        "announcements", "settings", "system_content", "audit_logs",
        "call_center_calls",
        "ai_analysis", "ai_recommendation", "ai_dispatch_log", "ai_memory",
    )
    counts = {}
    with _db() as conn:
        with conn.cursor() as cur:
            for table in tables:
                try:
                    cur.execute(f"SELECT COUNT(*) AS c FROM {table}")
                    counts[table] = cur.fetchone()["c"]
                except Exception:
                    counts[table] = None
    return counts


def verify_schema():
    """Confirm expected tables, indexes, and foreign keys exist."""
    expected_tables = {
        "hospitals", "users", "emergencies", "notifications", "messages",
        "announcements", "settings", "system_content", "audit_logs",
        "call_center_calls",
        "ai_analysis", "ai_recommendation", "ai_dispatch_log", "ai_memory",
    }
    expected_fks = {
        ("users", "fk_users_hospital"),
        ("emergencies", "fk_emergencies_user"),
        ("emergencies", "fk_emergencies_hospital"),
    }
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TABLE_NAME FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s
                """,
                (load_config()["database"],),
            )
            tables = {r["TABLE_NAME"] for r in cur.fetchall()}
            cur.execute(
                """
                SELECT TABLE_NAME, CONSTRAINT_NAME
                FROM information_schema.TABLE_CONSTRAINTS
                WHERE TABLE_SCHEMA = %s AND CONSTRAINT_TYPE = 'FOREIGN KEY'
                """,
                (load_config()["database"],),
            )
            fks = {(r["TABLE_NAME"], r["CONSTRAINT_NAME"]) for r in cur.fetchall()}
            cur.execute(
                """
                SELECT TABLE_NAME, INDEX_NAME
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = %s
                """,
                (load_config()["database"],),
            )
            indexes = {(r["TABLE_NAME"], r["INDEX_NAME"]) for r in cur.fetchall()}
    missing_tables = expected_tables - tables
    missing_fks = expected_fks - fks
    return {
        "tables_ok": not missing_tables,
        "missing_tables": sorted(missing_tables),
        "fks_ok": not missing_fks,
        "missing_fks": sorted(missing_fks),
        "index_count": len(indexes),
        "tables": sorted(tables & expected_tables),
    }
