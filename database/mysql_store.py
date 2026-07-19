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


# Full-document wipe of these tables is refused unless keep_ids is non-empty
# (prevents accidental mass delete from a stale/empty in-memory snapshot).
_PROTECTED_WIPE_TABLES = frozenset({"users", "hospitals", "emergencies"})


def _delete_stale_ids(cur, table, existing_ids, keep_ids):
    """Delete rows present in DB but missing from the in-memory document list."""
    allowed = {
        "users", "hospitals", "emergencies", "notifications", "messages",
        "announcements", "audit_logs", "call_center_calls",
        "ai_analysis", "ai_recommendation", "ai_dispatch_log", "ai_memory",
    }
    if table not in allowed:
        raise ValueError(f"Refusing delete on unexpected table: {table}")
    stale = set(existing_ids) - set(keep_ids)
    if not stale:
        return []
    if table in _PROTECTED_WIPE_TABLES and not keep_ids and existing_ids:
        raise ValueError(
            f"Refusing to wipe all {len(existing_ids)} rows from {table} "
            "(empty document snapshot). Reload and retry."
        )
    ids = list(stale)
    cur.execute(
        f"DELETE FROM {table} WHERE id IN ({', '.join(['%s'] * len(ids))})",
        ids,
    )
    return ids


def _cleanup_before_emergency_delete(cur, emergency_ids):
    """Detach/remove dependents before deleting emergencies (FK-safe)."""
    if not emergency_ids:
        return
    ids = list(emergency_ids)
    ph = ", ".join(["%s"] * len(ids))
    # CASCADE FKs also cover these; explicit delete keeps JSON-mode parity
    cur.execute(f"DELETE FROM notifications WHERE request_id IN ({ph})", ids)
    cur.execute(f"DELETE FROM messages WHERE request_id IN ({ph})", ids)
    for table in ("ai_analysis", "ai_recommendation", "ai_dispatch_log", "ai_memory"):
        try:
            cur.execute(
                f"UPDATE {table} SET emergency_id = NULL WHERE emergency_id IN ({ph})",
                ids,
            )
        except Exception:
            pass


def _cleanup_before_user_delete(cur, user_ids):
    """Null/delete references before deleting users (FK-safe)."""
    if not user_ids:
        return
    ids = list(user_ids)
    ph = ", ".join(["%s"] * len(ids))
    cur.execute(
        f"DELETE FROM notifications WHERE target_type IN "
        f"('patient','citizen','user','hospital','police','fire','admin','call_center') "
        f"AND target_id IN ({ph})",
        ids,
    )
    try:
        cur.execute(
            f"UPDATE hospitals SET owner_user_id = NULL WHERE owner_user_id IN ({ph})",
            ids,
        )
    except Exception:
        pass
    try:
        cur.execute(f"UPDATE messages SET sender_id = NULL WHERE sender_id IN ({ph})", ids)
    except Exception:
        pass
    try:
        cur.execute(
            f"UPDATE call_center_calls SET operator_id = NULL, operator_name = '' "
            f"WHERE operator_id IN ({ph})",
            ids,
        )
    except Exception:
        pass
    try:
        cur.execute(
            f"UPDATE call_center_calls SET user_id = NULL WHERE user_id IN ({ph})",
            ids,
        )
    except Exception:
        pass
    try:
        cur.execute(f"UPDATE audit_logs SET user_id = NULL WHERE user_id IN ({ph})", ids)
    except Exception:
        pass
    for table, col in (
        ("ai_recommendation", "operator_id"),
        ("ai_dispatch_log", "operator_id"),
    ):
        try:
            cur.execute(f"UPDATE {table} SET {col} = NULL WHERE {col} IN ({ph})", ids)
        except Exception:
            pass


# Back-compat aliases
_cleanup_after_emergency_delete = _cleanup_before_emergency_delete
_cleanup_after_user_delete = _cleanup_before_user_delete


@contextmanager
def _write_tx(lock_name=None):
    """Atomic write unit with optional named GET_LOCK."""
    with _db() as conn:
        prev_ac = True
        try:
            prev_ac = conn.get_autocommit()
        except Exception:
            prev_ac = True
        conn.autocommit(False)
        locked = False
        try:
            with conn.cursor() as cur:
                if lock_name:
                    if not _acquire_lock(cur, lock_name):
                        raise RuntimeError(f"Could not acquire lock: {lock_name}")
                    locked = True
                yield cur
                conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            if locked:
                try:
                    with conn.cursor() as cur:
                        _release_lock(cur, lock_name)
                except Exception:
                    pass
            try:
                conn.autocommit(prev_ac)
            except Exception:
                pass


def _acquire_lock(cur, name, timeout=10):
    cur.execute("SELECT GET_LOCK(%s, %s) AS ok", (f"gurmad:{name}", int(timeout)))
    row = cur.fetchone() or {}
    return bool(row.get("ok"))


def _release_lock(cur, name):
    try:
        cur.execute("SELECT RELEASE_LOCK(%s)", (f"gurmad:{name}",))
    except Exception:
        pass


def settings_row_exists():
    """True when MySQL settings singleton already has a row."""
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM settings WHERE id = 1")
                return cur.fetchone() is not None
    except Exception:
        return False


def content_row_exists():
    """True when MySQL CMS singleton already has a row."""
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM system_content WHERE id = 1")
                return cur.fetchone() is not None
    except Exception:
        return False


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
            u["email_verify_expires"] = _dt_str(u.get("email_verify_expires"))
            u["last_seen_call_center"] = _dt_str(u.get("last_seen_call_center"))
            if "email_verified" in u:
                u["email_verified"] = bool(u.get("email_verified"))
            else:
                u["email_verified"] = True
            if "notify_email_on_sos" in u:
                u["notify_email_on_sos"] = bool(u.get("notify_email_on_sos"))
            else:
                u["notify_email_on_sos"] = True
            if "notify_email_on_dispatch" in u:
                u["notify_email_on_dispatch"] = bool(u.get("notify_email_on_dispatch"))
            else:
                u["notify_email_on_dispatch"] = True
            users.append(u)
        max_id = max((u["id"] for u in users), default=0)
        return {"users": users, "next_id": max_id + 1}


def save_users(data):
    with _write_tx("users") as cur:
        cur.execute("SELECT id FROM users")
        existing = {r["id"] for r in cur.fetchall()}
        keep_ids = set()
        for u in data.get("users", []):
            row = dict(u)
            row["saved_locations"] = _json_dump(row.get("saved_locations", []))
            row["activity"] = _json_dump(row.get("activity", []))
            row.setdefault("username", (row.get("email") or "user").split("@")[0])
            # Normalize empty national_id_hash so UNIQUE allows multiple NULL
            nid = row.get("national_id_hash")
            if isinstance(nid, str) and not nid.strip():
                row["national_id_hash"] = None
            cols = [
                "id", "username", "name", "email", "phone", "password_hash", "role", "status",
                "profile_photo", "emergency_contact_name", "emergency_contact_phone",
                "emergency_contact_relation", "emergency_contact_email", "address", "city", "date_of_birth",
                "gender", "first_name", "middle_name", "last_name",
                "national_id_last4", "national_id_hash", "national_id_encrypted",
                "blood_type", "medical_notes", "allergies", "saved_locations", "hospital_id",
                "reset_token", "reset_expires",
                "email_verified", "email_verify_token", "email_verify_expires",
                "notify_email_on_sos", "notify_email_on_dispatch",
                "created_at", "last_login",
                "last_seen_call_center", "activity",
            ]
            row["email_verified"] = 1 if row.get("email_verified") else 0
            row["notify_email_on_sos"] = 1 if row.get("notify_email_on_sos", True) else 0
            row["notify_email_on_dispatch"] = 1 if row.get("notify_email_on_dispatch", True) else 0
            keep_ids.add(row["id"])
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
        stale = set(existing) - keep_ids
        if stale:
            _cleanup_before_user_delete(cur, stale)
        _delete_stale_ids(cur, "users", existing, keep_ids)


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
    with _write_tx("hospitals") as cur:
        cur.execute("SELECT id FROM hospitals")
        existing = {r["id"] for r in cur.fetchall()}
        keep_ids = set()
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
            keep_ids.add(row["id"])
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
        _delete_stale_ids(cur, "hospitals", existing, keep_ids)


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
    with _write_tx("emergencies") as cur:
        cur.execute("SELECT id FROM emergencies")
        existing = {r["id"] for r in cur.fetchall()}
        keep_ids = set()
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
            keep_ids.add(row["id"])
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
        stale = set(existing) - keep_ids
        if stale:
            _cleanup_before_emergency_delete(cur, stale)
        _delete_stale_ids(cur, "emergencies", existing, keep_ids)


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
    with _write_tx("notifications") as cur:
        cur.execute("SELECT id FROM notifications")
        existing = {r["id"] for r in cur.fetchall()}
        keep_ids = set()
        for n in data.get("notifications", []):
            req_id = n.get("request_id")
            if req_id in (0, "0", ""):
                req_id = None
            row = {
                "id": n["id"],
                "target_type": n["target_type"],
                "target_id": n["target_id"],
                "message": n["message"],
                "ntype": n.get("type") or n.get("ntype") or "system_alert",
                "request_id": req_id,
                "is_read": 1 if n.get("read") else 0,
                "timestamp": n.get("timestamp"),
            }
            cols = list(row.keys())
            keep_ids.add(row["id"])
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
        _delete_stale_ids(cur, "notifications", existing, keep_ids)


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
    with _write_tx("messages") as cur:
        cur.execute("SELECT id FROM messages")
        existing = {r["id"] for r in cur.fetchall()}
        keep_ids = set()
        for m in data.get("messages", []):
            sender_id = m.get("sender_id")
            if sender_id in (0, "0", ""):
                sender_id = None
            row = {
                "id": m["id"],
                "request_id": m["request_id"],
                "sender_role": m["sender_role"],
                "sender_id": sender_id,
                "text": m.get("text", ""),
                "msg_type": m.get("msg_type", "text"),
                "status": m.get("status", "sent"),
                "timestamp": m.get("timestamp"),
                "delivered_at": m.get("delivered_at"),
                "seen_at": m.get("seen_at"),
            }
            cols = list(row.keys())
            keep_ids.add(row["id"])
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
        _delete_stale_ids(cur, "messages", existing, keep_ids)


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
            keep_ids = set()
            for a in data.get("announcements", []):
                row = {
                    "id": a["id"],
                    "title": a["title"],
                    "body": a["body"],
                    "priority": a.get("priority", "info"),
                    "timestamp": a.get("timestamp"),
                }
                cols = list(row.keys())
                keep_ids.add(row["id"])
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
            _delete_stale_ids(cur, "announcements", existing, keep_ids)


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
            keep_ids = set()
            # Cap growth: keep newest 5000 in DB when document is trimmed
            entries = data.get("entries", [])[:5000]
            for e in entries:
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
                keep_ids.add(row["id"])
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
            _delete_stale_ids(cur, "audit_logs", existing, keep_ids)


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
    try:
        with _write_tx("call_center_calls") as cur:
            cur.execute("SELECT id FROM call_center_calls")
            existing = {r["id"] for r in cur.fetchall()}
            keep_ids = set()
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
                keep_ids.add(row["id"])
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
            _delete_stale_ids(cur, "call_center_calls", existing, keep_ids)
    except Exception:
        # Table may not exist yet on first boot before ensure_call_center_schema
        return


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
            if "call_center" not in type_str.lower() or "super_admin" not in type_str.lower():
                cur.execute(
                    "ALTER TABLE users MODIFY COLUMN role "
                    "ENUM('citizen','hospital','police','fire','admin','super_admin','call_center') "
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


def ensure_super_admin_role():
    """Idempotent: add super_admin to users.role ENUM."""
    if not available():
        return {"ok": False, "reason": "pymysql unavailable"}
    changes = []
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM users LIKE 'role'")
            role_col = cur.fetchone() or {}
            type_str = str(role_col.get("Type") or role_col.get("type") or "")
            if "super_admin" not in type_str.lower():
                cur.execute(
                    "ALTER TABLE users MODIFY COLUMN role "
                    "ENUM('citizen','hospital','police','fire','admin','super_admin','call_center') "
                    "NOT NULL DEFAULT 'citizen'"
                )
                changes.append("users.role_enum_super_admin")
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
    """Upsert AI list tables from {items, next_id} — never wipe the whole table."""
    allowed = {"ai_analysis", "ai_recommendation", "ai_dispatch_log", "ai_memory"}
    if table not in allowed:
        return
    items = data.get("items") or []
    with _db() as conn:
        with conn.cursor() as cur:
            _acquire_lock(cur, table)
            try:
                cur.execute(f"SELECT id FROM {table}")
                existing = {r["id"] for r in cur.fetchall()}
                keep_ids = set()
                for item in items:
                    payload = dict(item)
                    item_id = int(payload.get("id") or 0)
                    if item_id <= 0:
                        continue
                    keep_ids.add(item_id)
                    if table == "ai_memory":
                        cur.execute(
                            f"""
                            INSERT INTO {table}
                              (id, event_type, emergency_id, call_id, analysis_id,
                               recommendation_id, dispatch_log_id, payload, timestamp)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON DUPLICATE KEY UPDATE
                              event_type=VALUES(event_type), emergency_id=VALUES(emergency_id),
                              call_id=VALUES(call_id), analysis_id=VALUES(analysis_id),
                              recommendation_id=VALUES(recommendation_id),
                              dispatch_log_id=VALUES(dispatch_log_id),
                              payload=VALUES(payload), timestamp=VALUES(timestamp)
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
                            ON DUPLICATE KEY UPDATE
                              emergency_id=VALUES(emergency_id), call_id=VALUES(call_id),
                              category=VALUES(category), gurmad_type=VALUES(gurmad_type),
                              priority=VALUES(priority), risk_level=VALUES(risk_level),
                              confidence=VALUES(confidence), provider=VALUES(provider),
                              source=VALUES(source), payload=VALUES(payload)
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
                            ON DUPLICATE KEY UPDATE
                              analysis_id=VALUES(analysis_id), emergency_id=VALUES(emergency_id),
                              call_id=VALUES(call_id), status=VALUES(status),
                              confidence=VALUES(confidence),
                              estimated_arrival_minutes=VALUES(estimated_arrival_minutes),
                              provider=VALUES(provider), human_decision=VALUES(human_decision),
                              operator_id=VALUES(operator_id), payload=VALUES(payload),
                              updated_at=VALUES(updated_at)
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
                            ON DUPLICATE KEY UPDATE
                              emergency_id=VALUES(emergency_id), call_id=VALUES(call_id),
                              recommendation_id=VALUES(recommendation_id),
                              analysis_id=VALUES(analysis_id),
                              human_decision=VALUES(human_decision),
                              operator_id=VALUES(operator_id), payload=VALUES(payload)
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
                _delete_stale_ids(cur, table, existing, keep_ids)
            finally:
                _release_lock(cur, table)


def ensure_email_verification_schema():
    """Idempotent columns for signup email verification."""
    if not available():
        return {"ok": False, "reason": "pymysql unavailable"}
    changes = []
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM users LIKE 'email_verified'")
            if not cur.fetchone():
                cur.execute(
                    "ALTER TABLE users ADD COLUMN email_verified TINYINT(1) NOT NULL DEFAULT 0 "
                    "AFTER reset_expires"
                )
                # Existing accounts remain usable
                cur.execute("UPDATE users SET email_verified = 1")
                changes.append("users.email_verified")
            cur.execute("SHOW COLUMNS FROM users LIKE 'email_verify_token'")
            if not cur.fetchone():
                cur.execute(
                    "ALTER TABLE users ADD COLUMN email_verify_token VARCHAR(128) NULL "
                    "AFTER email_verified"
                )
                changes.append("users.email_verify_token")
            cur.execute("SHOW COLUMNS FROM users LIKE 'email_verify_expires'")
            if not cur.fetchone():
                cur.execute(
                    "ALTER TABLE users ADD COLUMN email_verify_expires DATETIME NULL "
                    "AFTER email_verify_token"
                )
                changes.append("users.email_verify_expires")
    return {"ok": True, "changes": changes}


def ensure_citizen_profile_schema():
    """Idempotent columns for citizen registration profile fields."""
    if not available():
        return {"ok": False, "reason": "pymysql unavailable"}
    changes = []
    specs = [
        ("first_name", "VARCHAR(60) DEFAULT '' AFTER name"),
        ("middle_name", "VARCHAR(60) DEFAULT '' AFTER first_name"),
        ("last_name", "VARCHAR(60) DEFAULT '' AFTER middle_name"),
        ("gender", "VARCHAR(20) DEFAULT '' AFTER date_of_birth"),
        ("national_id_last4", "VARCHAR(4) DEFAULT '' AFTER gender"),
        ("national_id_hash", "VARCHAR(64) NULL AFTER national_id_last4"),
        ("national_id_encrypted", "TEXT NULL AFTER national_id_hash"),
        ("emergency_contact_email", "VARCHAR(180) DEFAULT '' AFTER emergency_contact_relation"),
        ("allergies", "VARCHAR(500) DEFAULT '' AFTER medical_notes"),
    ]
    with _db() as conn:
        with conn.cursor() as cur:
            for col, ddl in specs:
                cur.execute(f"SHOW COLUMNS FROM users LIKE '{col}'")
                if not cur.fetchone():
                    cur.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
                    changes.append(f"users.{col}")
    return {"ok": True, "changes": changes}


def ensure_admin_profile_schema():
    """Idempotent columns for admin My Profile notification preferences."""
    if not available():
        return {"ok": False, "reason": "pymysql unavailable"}
    changes = []
    specs = [
        ("notify_email_on_sos", "TINYINT(1) NOT NULL DEFAULT 1"),
        ("notify_email_on_dispatch", "TINYINT(1) NOT NULL DEFAULT 1"),
    ]
    with _db() as conn:
        with conn.cursor() as cur:
            for col, ddl in specs:
                cur.execute(f"SHOW COLUMNS FROM users LIKE '{col}'")
                if not cur.fetchone():
                    cur.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
                    changes.append(f"users.{col}")
    return {"ok": True, "changes": changes}


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
        ("hospitals", "fk_hospitals_owner"),
        ("notifications", "fk_notif_request"),
        ("messages", "fk_messages_request"),
        ("messages", "fk_messages_sender"),
        ("audit_logs", "fk_audit_user"),
        ("call_center_calls", "fk_cc_user"),
        ("call_center_calls", "fk_cc_operator"),
        ("ai_analysis", "fk_ai_analysis_emergency"),
        ("ai_analysis", "fk_ai_analysis_call"),
        ("ai_recommendation", "fk_ai_rec_emergency"),
        ("ai_recommendation", "fk_ai_rec_call"),
        ("ai_dispatch_log", "fk_ai_dlog_emergency"),
        ("ai_memory", "fk_ai_mem_emergency"),
    }
    expected_columns = {
        ("users", "notify_email_on_sos"),
        ("users", "notify_email_on_dispatch"),
        ("users", "email_verified"),
        ("users", "last_seen_call_center"),
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
                SELECT TABLE_NAME, COLUMN_NAME
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                """,
                (load_config()["database"],),
            )
            columns = {(r["TABLE_NAME"], r["COLUMN_NAME"]) for r in cur.fetchall()}
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
    missing_columns = expected_columns - columns
    return {
        "tables_ok": not missing_tables,
        "missing_tables": sorted(missing_tables),
        "fks_ok": not missing_fks,
        "missing_fks": [f"{t}.{c}" for t, c in sorted(missing_fks)],
        "columns_ok": not missing_columns,
        "missing_columns": [f"{t}.{c}" for t, c in sorted(missing_columns)],
        "index_count": len(indexes),
        "tables": sorted(tables & expected_tables),
        "ok": not missing_tables and not missing_fks and not missing_columns,
    }


def _index_exists(cur, table, index_name):
    cur.execute(
        """
        SELECT 1 FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s
        LIMIT 1
        """,
        (table, index_name),
    )
    return cur.fetchone() is not None


def _fk_exists(cur, table, constraint_name):
    cur.execute(
        """
        SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
          AND CONSTRAINT_NAME = %s AND CONSTRAINT_TYPE = 'FOREIGN KEY'
        LIMIT 1
        """,
        (table, constraint_name),
    )
    return cur.fetchone() is not None


def ensure_production_integrity():
    """
    Idempotent production hardening:
    - column alignment (notify prefs, nullable message sender)
    - indexes for uniqueness / lookups
    - orphan cleanup
    - missing foreign keys
    - role ENUM includes super_admin / call_center
    """
    if not available():
        return {"ok": False, "reason": "pymysql unavailable", "changes": []}
    changes = []
    with _db() as conn:
        with conn.cursor() as cur:
            # Role ENUM alignment
            try:
                cur.execute("SHOW COLUMNS FROM users LIKE 'role'")
                col = cur.fetchone() or {}
                type_str = str(col.get("Type") or "")
                if "super_admin" not in type_str.lower() or "call_center" not in type_str.lower():
                    cur.execute(
                        "ALTER TABLE users MODIFY COLUMN role "
                        "ENUM('citizen','hospital','police','fire','admin','super_admin','call_center') "
                        "NOT NULL DEFAULT 'citizen'"
                    )
                    changes.append("users.role_enum")
            except Exception:
                pass

            # Columns that must exist for app saves
            for col, ddl in (
                ("notify_email_on_sos", "TINYINT(1) NOT NULL DEFAULT 1"),
                ("notify_email_on_dispatch", "TINYINT(1) NOT NULL DEFAULT 1"),
            ):
                try:
                    cur.execute(f"SHOW COLUMNS FROM users LIKE '{col}'")
                    if not cur.fetchone():
                        cur.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
                        changes.append(f"users.{col}")
                except Exception:
                    pass

            # messages.sender_id must be nullable for ON DELETE SET NULL
            try:
                cur.execute(
                    "UPDATE messages m LEFT JOIN users u ON u.id = m.sender_id "
                    "SET m.sender_id = NULL WHERE m.sender_id = 0 OR u.id IS NULL"
                )
            except Exception:
                pass
            try:
                cur.execute("SHOW COLUMNS FROM messages LIKE 'sender_id'")
                col = cur.fetchone() or {}
                if str(col.get("Null") or "").upper() != "YES":
                    cur.execute("ALTER TABLE messages MODIFY COLUMN sender_id INT NULL")
                    changes.append("messages.sender_id_nullable")
            except Exception:
                pass

            # Unique / lookup indexes
            index_specs = [
                ("users", "uq_users_national_id_hash",
                 "CREATE UNIQUE INDEX uq_users_national_id_hash ON users (national_id_hash)"),
                ("users", "idx_users_username",
                 "CREATE INDEX idx_users_username ON users (username)"),
                ("users", "idx_users_email_verify_token",
                 "CREATE INDEX idx_users_email_verify_token ON users (email_verify_token)"),
                ("users", "idx_users_reset_token",
                 "CREATE INDEX idx_users_reset_token ON users (reset_token)"),
                ("users", "idx_users_role_status",
                 "CREATE INDEX idx_users_role_status ON users (role, status)"),
                ("emergencies", "idx_emergencies_status_ts",
                 "CREATE INDEX idx_emergencies_status_ts ON emergencies (status, timestamp)"),
                ("emergencies", "idx_emergencies_type_status",
                 "CREATE INDEX idx_emergencies_type_status ON emergencies (type, status)"),
                ("messages", "idx_messages_request_ts",
                 "CREATE INDEX idx_messages_request_ts ON messages (request_id, timestamp)"),
                ("notifications", "idx_notif_target_read",
                 "CREATE INDEX idx_notif_target_read ON notifications (target_type, target_id, is_read)"),
                ("audit_logs", "idx_audit_user",
                 "CREATE INDEX idx_audit_user ON audit_logs (user_id)"),
                ("call_center_calls", "idx_cc_status_start",
                 "CREATE INDEX idx_cc_status_start ON call_center_calls (status, start_time)"),
                ("ai_recommendation", "idx_ai_rec_analysis",
                 "CREATE INDEX idx_ai_rec_analysis ON ai_recommendation (analysis_id)"),
                ("ai_recommendation", "idx_ai_rec_operator",
                 "CREATE INDEX idx_ai_rec_operator ON ai_recommendation (operator_id)"),
                ("ai_dispatch_log", "idx_ai_dlog_call",
                 "CREATE INDEX idx_ai_dlog_call ON ai_dispatch_log (call_id)"),
                ("ai_dispatch_log", "idx_ai_dlog_recommendation",
                 "CREATE INDEX idx_ai_dlog_recommendation ON ai_dispatch_log (recommendation_id)"),
                ("ai_dispatch_log", "idx_ai_dlog_analysis",
                 "CREATE INDEX idx_ai_dlog_analysis ON ai_dispatch_log (analysis_id)"),
                ("ai_dispatch_log", "idx_ai_dlog_operator",
                 "CREATE INDEX idx_ai_dlog_operator ON ai_dispatch_log (operator_id)"),
                ("ai_memory", "idx_ai_mem_call",
                 "CREATE INDEX idx_ai_mem_call ON ai_memory (call_id)"),
                ("ai_memory", "idx_ai_mem_analysis",
                 "CREATE INDEX idx_ai_mem_analysis ON ai_memory (analysis_id)"),
                ("ai_memory", "idx_ai_mem_recommendation",
                 "CREATE INDEX idx_ai_mem_recommendation ON ai_memory (recommendation_id)"),
            ]
            for table, name, ddl in index_specs:
                try:
                    if not _index_exists(cur, table, name):
                        if name == "uq_users_national_id_hash":
                            cur.execute(
                                "UPDATE users SET national_id_hash = NULL "
                                "WHERE national_id_hash IS NOT NULL AND TRIM(national_id_hash) = ''"
                            )
                        cur.execute(ddl)
                        changes.append(f"index:{name}")
                except Exception:
                    pass

            # Orphan cleanup before FKs
            orphan_sql = [
                """
                UPDATE hospitals h
                LEFT JOIN users u ON u.id = h.owner_user_id
                SET h.owner_user_id = NULL
                WHERE h.owner_user_id IS NOT NULL AND u.id IS NULL
                """,
                """
                UPDATE call_center_calls c
                LEFT JOIN users u ON u.id = c.operator_id
                SET c.operator_id = NULL
                WHERE c.operator_id IS NOT NULL AND u.id IS NULL
                """,
                """
                UPDATE call_center_calls c
                LEFT JOIN users u ON u.id = c.user_id
                SET c.user_id = NULL
                WHERE c.user_id IS NOT NULL AND u.id IS NULL
                """,
                """
                UPDATE emergencies e
                LEFT JOIN users u ON u.id = e.user_id
                SET e.user_id = NULL
                WHERE e.user_id IS NOT NULL AND u.id IS NULL
                """,
                """
                UPDATE emergencies e
                LEFT JOIN hospitals h ON h.id = e.assigned_hospital_id
                SET e.assigned_hospital_id = NULL
                WHERE e.assigned_hospital_id IS NOT NULL AND h.id IS NULL
                """,
                """
                UPDATE messages m
                LEFT JOIN users u ON u.id = m.sender_id
                SET m.sender_id = NULL
                WHERE m.sender_id IS NOT NULL AND u.id IS NULL
                """,
                """
                UPDATE audit_logs a
                LEFT JOIN users u ON u.id = a.user_id
                SET a.user_id = NULL
                WHERE a.user_id IS NOT NULL AND u.id IS NULL
                """,
            ]
            for sql in orphan_sql:
                try:
                    cur.execute(sql)
                except Exception:
                    pass

            # Delete orphans that cannot be nulled
            for label, sql in (
                (
                    "prune_orphan_notifications",
                    """
                    DELETE n FROM notifications n
                    LEFT JOIN emergencies e ON e.id = n.request_id
                    WHERE n.request_id IS NOT NULL AND e.id IS NULL
                    """,
                ),
                (
                    "prune_orphan_messages",
                    """
                    DELETE m FROM messages m
                    LEFT JOIN emergencies e ON e.id = m.request_id
                    WHERE e.id IS NULL
                    """,
                ),
            ):
                try:
                    cur.execute(sql)
                    if cur.rowcount:
                        changes.append(f"{label}:{cur.rowcount}")
                except Exception:
                    pass

            # Detach AI orphans before AI FKs
            for table in ("ai_analysis", "ai_recommendation", "ai_dispatch_log", "ai_memory"):
                try:
                    cur.execute(
                        f"""
                        UPDATE {table} t
                        LEFT JOIN emergencies e ON e.id = t.emergency_id
                        SET t.emergency_id = NULL
                        WHERE t.emergency_id IS NOT NULL AND e.id IS NULL
                        """
                    )
                except Exception:
                    pass
                try:
                    cur.execute(
                        f"""
                        UPDATE {table} t
                        LEFT JOIN call_center_calls c ON c.id = t.call_id
                        SET t.call_id = NULL
                        WHERE t.call_id IS NOT NULL AND c.id IS NULL
                        """
                    )
                except Exception:
                    pass

            fk_specs = [
                (
                    "hospitals",
                    "fk_hospitals_owner",
                    "ALTER TABLE hospitals ADD CONSTRAINT fk_hospitals_owner "
                    "FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL",
                ),
                (
                    "notifications",
                    "fk_notif_request",
                    "ALTER TABLE notifications ADD CONSTRAINT fk_notif_request "
                    "FOREIGN KEY (request_id) REFERENCES emergencies(id) ON DELETE CASCADE",
                ),
                (
                    "messages",
                    "fk_messages_request",
                    "ALTER TABLE messages ADD CONSTRAINT fk_messages_request "
                    "FOREIGN KEY (request_id) REFERENCES emergencies(id) ON DELETE CASCADE",
                ),
                (
                    "messages",
                    "fk_messages_sender",
                    "ALTER TABLE messages ADD CONSTRAINT fk_messages_sender "
                    "FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE SET NULL",
                ),
                (
                    "audit_logs",
                    "fk_audit_user",
                    "ALTER TABLE audit_logs ADD CONSTRAINT fk_audit_user "
                    "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL",
                ),
                (
                    "call_center_calls",
                    "fk_cc_user",
                    "ALTER TABLE call_center_calls ADD CONSTRAINT fk_cc_user "
                    "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL",
                ),
                (
                    "call_center_calls",
                    "fk_cc_operator",
                    "ALTER TABLE call_center_calls ADD CONSTRAINT fk_cc_operator "
                    "FOREIGN KEY (operator_id) REFERENCES users(id) ON DELETE SET NULL",
                ),
                (
                    "ai_analysis",
                    "fk_ai_analysis_emergency",
                    "ALTER TABLE ai_analysis ADD CONSTRAINT fk_ai_analysis_emergency "
                    "FOREIGN KEY (emergency_id) REFERENCES emergencies(id) ON DELETE SET NULL",
                ),
                (
                    "ai_analysis",
                    "fk_ai_analysis_call",
                    "ALTER TABLE ai_analysis ADD CONSTRAINT fk_ai_analysis_call "
                    "FOREIGN KEY (call_id) REFERENCES call_center_calls(id) ON DELETE SET NULL",
                ),
                (
                    "ai_recommendation",
                    "fk_ai_rec_analysis",
                    "ALTER TABLE ai_recommendation ADD CONSTRAINT fk_ai_rec_analysis "
                    "FOREIGN KEY (analysis_id) REFERENCES ai_analysis(id) ON DELETE SET NULL",
                ),
                (
                    "ai_recommendation",
                    "fk_ai_rec_emergency",
                    "ALTER TABLE ai_recommendation ADD CONSTRAINT fk_ai_rec_emergency "
                    "FOREIGN KEY (emergency_id) REFERENCES emergencies(id) ON DELETE SET NULL",
                ),
                (
                    "ai_recommendation",
                    "fk_ai_rec_call",
                    "ALTER TABLE ai_recommendation ADD CONSTRAINT fk_ai_rec_call "
                    "FOREIGN KEY (call_id) REFERENCES call_center_calls(id) ON DELETE SET NULL",
                ),
                (
                    "ai_recommendation",
                    "fk_ai_rec_operator",
                    "ALTER TABLE ai_recommendation ADD CONSTRAINT fk_ai_rec_operator "
                    "FOREIGN KEY (operator_id) REFERENCES users(id) ON DELETE SET NULL",
                ),
                (
                    "ai_dispatch_log",
                    "fk_ai_dlog_emergency",
                    "ALTER TABLE ai_dispatch_log ADD CONSTRAINT fk_ai_dlog_emergency "
                    "FOREIGN KEY (emergency_id) REFERENCES emergencies(id) ON DELETE SET NULL",
                ),
                (
                    "ai_dispatch_log",
                    "fk_ai_dlog_call",
                    "ALTER TABLE ai_dispatch_log ADD CONSTRAINT fk_ai_dlog_call "
                    "FOREIGN KEY (call_id) REFERENCES call_center_calls(id) ON DELETE SET NULL",
                ),
                (
                    "ai_dispatch_log",
                    "fk_ai_dlog_recommendation",
                    "ALTER TABLE ai_dispatch_log ADD CONSTRAINT fk_ai_dlog_recommendation "
                    "FOREIGN KEY (recommendation_id) REFERENCES ai_recommendation(id) ON DELETE SET NULL",
                ),
                (
                    "ai_dispatch_log",
                    "fk_ai_dlog_analysis",
                    "ALTER TABLE ai_dispatch_log ADD CONSTRAINT fk_ai_dlog_analysis "
                    "FOREIGN KEY (analysis_id) REFERENCES ai_analysis(id) ON DELETE SET NULL",
                ),
                (
                    "ai_dispatch_log",
                    "fk_ai_dlog_operator",
                    "ALTER TABLE ai_dispatch_log ADD CONSTRAINT fk_ai_dlog_operator "
                    "FOREIGN KEY (operator_id) REFERENCES users(id) ON DELETE SET NULL",
                ),
                (
                    "ai_memory",
                    "fk_ai_mem_emergency",
                    "ALTER TABLE ai_memory ADD CONSTRAINT fk_ai_mem_emergency "
                    "FOREIGN KEY (emergency_id) REFERENCES emergencies(id) ON DELETE SET NULL",
                ),
                (
                    "ai_memory",
                    "fk_ai_mem_call",
                    "ALTER TABLE ai_memory ADD CONSTRAINT fk_ai_mem_call "
                    "FOREIGN KEY (call_id) REFERENCES call_center_calls(id) ON DELETE SET NULL",
                ),
                (
                    "ai_memory",
                    "fk_ai_mem_analysis",
                    "ALTER TABLE ai_memory ADD CONSTRAINT fk_ai_mem_analysis "
                    "FOREIGN KEY (analysis_id) REFERENCES ai_analysis(id) ON DELETE SET NULL",
                ),
                (
                    "ai_memory",
                    "fk_ai_mem_recommendation",
                    "ALTER TABLE ai_memory ADD CONSTRAINT fk_ai_mem_recommendation "
                    "FOREIGN KEY (recommendation_id) REFERENCES ai_recommendation(id) ON DELETE SET NULL",
                ),
                (
                    "ai_memory",
                    "fk_ai_mem_dispatch",
                    "ALTER TABLE ai_memory ADD CONSTRAINT fk_ai_mem_dispatch "
                    "FOREIGN KEY (dispatch_log_id) REFERENCES ai_dispatch_log(id) ON DELETE SET NULL",
                ),
            ]
            for table, name, ddl in fk_specs:
                try:
                    if not _fk_exists(cur, table, name):
                        cur.execute(ddl)
                        changes.append(f"fk:{name}")
                except Exception:
                    pass
        conn.commit()
    return {"ok": True, "changes": changes}
