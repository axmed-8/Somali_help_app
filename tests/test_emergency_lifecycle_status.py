"""Emergency status lifecycle: empty-queue close, complete paths, no stuck pending."""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

import pytest
from werkzeug.security import generate_password_hash

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def login(client, email, password):
    return client.post(
        "/login", data={"username": email, "password": password}, follow_redirects=True
    )


@pytest.fixture
def client():
    os.environ["GURMADNET_DB"] = "json"
    os.environ["EMAIL_PROVIDER"] = "memory"
    os.environ["TESTING"] = "1"
    import importlib
    import app as ers_app
    from email_service.factory import clear_email_provider_cache
    from email_service.memory_provider import clear_outbox
    import hospital_logic as hl

    clear_email_provider_cache()
    clear_outbox()
    importlib.reload(ers_app)

    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "database")
    os.makedirs(db, exist_ok=True)

    ers_app.DATABASE_DIR = db
    ers_app.USERS_FILE = os.path.join(db, "users.json")
    ers_app.EMERGENCIES_FILE = os.path.join(db, "emergencies.json")
    ers_app.CONTENT_FILE = os.path.join(db, "system_content.json")
    ers_app.SETTINGS_FILE = os.path.join(db, "settings.json")
    ers_app.AUDIT_FILE = os.path.join(db, "audit_log.json")
    ers_app.configure_hospital_db(db)
    ers_app.ANNOUNCEMENTS_FILE = os.path.join(db, "announcements.json")
    ers_app.seed_defaults()

    hdata = hl.load_hospitals(ers_app.read_json, ers_app.save_json)
    if not hdata.get("hospitals"):
        hdata["hospitals"] = [{
            "id": 1,
            "name": "Test Hospital",
            "city": "Mogadishu",
            "region": "Banadir",
            "district": "Hodan",
            "address": "Test Address",
            "latitude": 2.0469,
            "longitude": 45.3182,
            "phone": "0622222222",
            "emergency_contacts": ["0622222222"],
            "services": ["Emergency"],
            "specialties": ["Emergency"],
            "ambulance_available": True,
            "ambulance_count": 2,
            "emergency_capacity": 20,
            "rating": 4.5,
            "operating_status": "open",
            "contact_email": "amina@hospital.com",
            "owner_user_id": None,
            "location_verified": True,
            "created_at": ers_app.now_str(),
            "updated_at": ers_app.now_str(),
        }]
        hdata["next_id"] = 2
        hl.save_hospitals(hdata, ers_app.save_json)

    udata = ers_app.load_users()
    for name, email, password, role, phone in [
        ("Ahmed Ali", "ahmed@example.com", "123456", "citizen", "0611111111"),
        ("Dr. Amina", "amina@hospital.com", "123456", "hospital", "0622222222"),
    ]:
        uid = udata["next_id"]
        udata["next_id"] += 1
        udata["users"].append({
            "id": uid,
            "name": name,
            "email": email,
            "phone": phone,
            "password_hash": generate_password_hash(password),
            "role": role,
            "status": "active",
            "email_verified": True,
            "created_at": ers_app.now_str(),
            "last_login": None,
            "activity": [],
        })
    for u in udata["users"]:
        if u.get("email") == "amina@hospital.com":
            u["hospital_id"] = 1
        u["email_verified"] = True
    ers_app.save_users(udata)

    ers_app.app.config["TESTING"] = True
    ers_app.app.config["WTF_CSRF_ENABLED"] = False
    yield ers_app.app.test_client(), ers_app
    clear_outbox()
    clear_email_provider_cache()
    shutil.rmtree(tmp, ignore_errors=True)


def test_empty_hospital_queue_marks_no_hospital_available(client, monkeypatch):
    c, ers = client
    monkeypatch.setattr(ers.hl, "build_escalation_queue", lambda *a, **k: [])
    login(c, "ahmed@example.com", "123456")
    r = c.post(
        "/api/send_alert",
        json={
            "type": "medical",
            "latitude": 2.0469,
            "longitude": 45.3182,
            "location": "Mogadishu",
            "name": "Ahmed",
        },
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["success"] is True
    em, _ = ers.get_emergency_by_id(data["id"])
    assert em["status"] == "no_hospital_available"
    assert em.get("tracking_active") is False
    hist = em.get("status_history") or []
    assert hist
    assert hist[-1]["status"] == "no_hospital_available"
    assert em["status"] in ers.COMPLETED_STATUSES


def test_stuck_pending_hospital_closed_by_escalation_sweep(client):
    c, ers = client
    edata = ers.load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    edata["emergencies"].append({
        "id": eid,
        "user_id": 1,
        "type": "medical",
        "assigned_to": "hospital",
        "status": "pending",
        "escalation_queue": [],
        "escalation_index": 0,
        "tracking_active": True,
        "status_history": [],
        "latitude": 2.04,
        "longitude": 45.31,
        "timestamp": "2026-08-01 12:00:00",
    })
    ers.save_emergencies(edata)
    ers._run_escalations()
    em, _ = ers.get_emergency_by_id(eid)
    assert em["status"] == "no_hospital_available"
    assert em.get("tracking_active") is False
    assert any(h.get("status") == "no_hospital_available" for h in em.get("status_history") or [])


def test_responder_reached_victim_persists_completed_with_history(client):
    c, ers = client
    login(c, "ahmed@example.com", "123456")
    eid = c.post(
        "/api/send_alert",
        json={
            "type": "medical",
            "latitude": 2.0469,
            "longitude": 45.3182,
            "location": "x",
            "name": "A",
        },
    ).get_json()["id"]
    c.get("/logout", follow_redirects=True)
    login(c, "amina@hospital.com", "123456")
    assert c.post(f"/api/hospital/request/{eid}/accept").status_code == 200
    r = c.post(f"/api/emergencies/{eid}/responder", json={"action": "reached_victim"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "completed"
    em, _ = ers.get_emergency_by_id(eid)
    assert em["status"] == "completed"
    assert em.get("tracking_active") is False
    assert any(h.get("status") == "completed" for h in em.get("status_history") or [])


def test_hospital_accept_cannot_reopen_completed(client):
    c, ers = client
    login(c, "ahmed@example.com", "123456")
    eid = c.post(
        "/api/send_alert",
        json={
            "type": "medical",
            "latitude": 2.0469,
            "longitude": 45.3182,
            "location": "x",
            "name": "A",
        },
    ).get_json()["id"]
    c.get("/logout", follow_redirects=True)
    login(c, "amina@hospital.com", "123456")
    assert c.post(f"/api/hospital/request/{eid}/accept").status_code == 200
    assert c.post(f"/api/emergencies/{eid}/responder", json={"action": "reached_victim"}).status_code == 200
    reopen = c.post(f"/api/hospital/request/{eid}/accept")
    assert reopen.status_code == 400
    em, _ = ers.get_emergency_by_id(eid)
    assert em["status"] == "completed"


def test_heal_reached_victim_restores_completed_status(client):
    c, ers = client
    edata = ers.load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    edata["emergencies"].append({
        "id": eid,
        "user_id": 1,
        "type": "medical",
        "assigned_to": "hospital",
        "assigned_hospital_id": 1,
        "status": "accepted",
        "tracking_active": True,
        "responder_status": {"reached_victim": "2026-08-03 14:04:30"},
        "status_history": [{"status": "accepted", "timestamp": "2026-08-03 14:03:00", "note": "x"}],
        "latitude": 2.04,
        "longitude": 45.31,
        "timestamp": "2026-08-03 14:00:00",
    })
    ers.save_emergencies(edata)
    em, _ = ers.get_emergency_by_id(eid)
    assert em["status"] == "completed"
    assert em.get("tracking_active") is False


def test_escalation_save_does_not_stomp_completed(client):
    c, ers = client
    edata = ers.load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    edata["emergencies"].append({
        "id": eid,
        "user_id": 1,
        "type": "medical",
        "assigned_to": "hospital",
        "assigned_hospital_id": 1,
        "status": "pending_hospital",
        "escalation_queue": [1],
        "escalation_index": 0,
        "response_deadline": "2020-01-01 00:00:00",
        "tracking_active": True,
        "status_history": [],
        "latitude": 2.04,
        "longitude": 45.31,
        "timestamp": "2026-08-01 12:00:00",
    })
    ers.save_emergencies(edata)

    # Stale escalation list still sees pending_hospital; live DB already completed.
    stale = [dict(e) for e in ers.load_emergencies()["emergencies"]]
    for em in stale:
        if em["id"] == eid:
            em["status"] = "pending_hospital"
            em["response_deadline"] = "2020-01-01 00:00:00"

    live, edata2 = ers.get_emergency_by_id(eid)
    live["status"] = "completed"
    live["tracking_active"] = False
    live.setdefault("responder_status", {})["reached_victim"] = "2026-08-03 14:04:30"
    ers.save_emergencies(edata2)

    hdata = ers.hl.load_hospitals(ers.read_json, ers.save_json)
    ers.hl.process_escalations(
        stale,
        hdata,
        120,
        ers.save_emergencies,
        ers.load_emergencies,
        lambda *a, **k: None,
    )
    em, _ = ers.get_emergency_by_id(eid)
    assert em["status"] == "completed"


def test_stale_accepted_fire_auto_completes(client):
    c, ers = client
    edata = ers.load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    edata["emergencies"].append({
        "id": eid,
        "user_id": 1,
        "type": "fire",
        "assigned_to": "fire",
        "assigned_station_id": 1,
        "status": "accepted",
        "accepted_at": "2026-07-01 12:00:00",
        "tracking_active": True,
        "last_location_update": ers.now_str(),  # GPS must not block auto-close
        "status_history": [
            {"status": "pending", "timestamp": "2026-07-01 11:59:00", "note": "routed"},
            {"status": "accepted", "timestamp": "2026-07-01 12:00:00", "note": "accepted"},
        ],
        "responder_status": {},
        "latitude": 2.04,
        "longitude": 45.31,
        "timestamp": "2026-07-01 11:59:00",
    })
    ers.save_emergencies(edata)
    s = ers.load_settings()
    s["station_desk_stale_hours"] = 24
    s["emergency_absolute_timeout_hours"] = 9999
    s["post_accept_remind_sec"] = 10**9
    s["post_accept_escalate_sec"] = 10**9
    s["post_accept_reassign_sec"] = 10**9
    ers.save_settings(s)
    ers._run_escalations()
    em, _ = ers.get_emergency_by_id(eid)
    assert em["status"] == "completed"
    assert em.get("tracking_active") is False
    assert any("Auto-closed" in (h.get("note") or "") for h in em.get("status_history") or [])


def test_stale_pending_fire_auto_cancels(client):
    c, ers = client
    edata = ers.load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    edata["emergencies"].append({
        "id": eid,
        "user_id": 1,
        "type": "fire",
        "assigned_to": "fire",
        "assigned_station_id": 1,
        "status": "pending",
        "tracking_active": True,
        "status_history": [
            {"status": "pending", "timestamp": "2026-07-01 10:00:00", "note": "Nearest fire"},
        ],
        "responder_status": {},
        "latitude": 2.04,
        "longitude": 45.31,
        "timestamp": "2026-07-01 10:00:00",
    })
    ers.save_emergencies(edata)
    s = ers.load_settings()
    s["station_desk_stale_hours"] = 24
    s["emergency_absolute_timeout_hours"] = 9999
    s["post_accept_remind_sec"] = 10**9
    s["post_accept_escalate_sec"] = 10**9
    s["post_accept_reassign_sec"] = 10**9
    ers.save_settings(s)
    ers._run_escalations()
    em, _ = ers.get_emergency_by_id(eid)
    assert em["status"] == "no_responder_available"
    assert em.get("tracking_active") is False
    assert em["status"] in ers.COMPLETED_STATUSES


def test_post_accept_inactivity_remind_escalate_reassign_hospital(client):
    c, ers = client
    edata = ers.load_emergencies()
    # Second hospital for reassignment target
    hdata = ers.hl.load_hospitals(ers.read_json, ers.save_json)
    hdata["hospitals"].append({
        "id": 2,
        "name": "Second Hospital",
        "city": "Mogadishu",
        "region": "Banadir",
        "district": "Waberi",
        "address": "Addr 2",
        "latitude": 2.05,
        "longitude": 45.32,
        "phone": "0633333333",
        "emergency_contacts": ["0633333333"],
        "services": ["Emergency"],
        "specialties": ["Emergency"],
        "ambulance_available": True,
        "ambulance_count": 1,
        "emergency_capacity": 10,
        "rating": 4.0,
        "operating_status": "open",
        "contact_email": "",
        "owner_user_id": None,
        "location_verified": True,
        "created_at": ers.now_str(),
        "updated_at": ers.now_str(),
    })
    hdata["next_id"] = 3
    ers.hl.save_hospitals(hdata, ers.save_json)

    # Idle long enough for remind→escalate→reassign, but well under absolute timeout (999h).
    accepted_at = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    ts = (datetime.now() - timedelta(hours=1, minutes=1)).strftime("%Y-%m-%d %H:%M:%S")

    eid = edata["next_id"]
    edata["next_id"] += 1
    edata["emergencies"].append({
        "id": eid,
        "user_id": 1,
        "type": "medical",
        "assigned_to": "hospital",
        "assigned_hospital_id": 1,
        "assigned_hospital_name": "Test Hospital",
        "status": "accepted",
        "accepted_at": accepted_at,
        "escalation_queue": [1, 2],
        "escalation_index": 0,
        "tracking_active": True,
        "status_history": [
            {"status": "accepted", "timestamp": accepted_at, "note": "accepted"},
        ],
        "responder_status": {},
        "latitude": 2.04,
        "longitude": 45.31,
        "timestamp": ts,
        "lifecycle_timeout": {},
    })
    ers.save_emergencies(edata)
    s = ers.load_settings()
    s["post_accept_remind_sec"] = 60
    s["post_accept_escalate_sec"] = 120
    s["post_accept_reassign_sec"] = 180
    s["emergency_absolute_timeout_hours"] = 999
    s["station_desk_stale_hours"] = 999
    ers.save_settings(s)

    ers._run_escalations()
    em, _ = ers.get_emergency_by_id(eid)
    # Idle past reassign threshold → remind + escalate + reassign in one sweep
    assert em["status"] == "pending_hospital"
    assert em.get("assigned_hospital_id") == 2
    hist = em.get("status_history") or []
    assert any(h.get("status") == "lifecycle_remind" for h in hist)
    assert any(h.get("status") == "lifecycle_escalate" for h in hist)
    assert any(h.get("status") == "lifecycle_reassign" for h in hist)
    timeline = ers._build_emergency_timeline(em)
    assert any(s.get("key") == "lifecycle_remind" for s in timeline)


def test_post_accept_inactivity_closes_no_responder_when_queue_exhausted(client):
    c, ers = client
    edata = ers.load_emergencies()
    accepted_at = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    ts = (datetime.now() - timedelta(hours=1, minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    eid = edata["next_id"]
    edata["next_id"] += 1
    edata["emergencies"].append({
        "id": eid,
        "user_id": 1,
        "type": "medical",
        "assigned_to": "hospital",
        "assigned_hospital_id": 1,
        "assigned_hospital_name": "Test Hospital",
        "status": "accepted",
        "accepted_at": accepted_at,
        "escalation_queue": [1],
        "escalation_index": 0,
        "tracking_active": True,
        "status_history": [
            {"status": "accepted", "timestamp": accepted_at, "note": "accepted"},
        ],
        "responder_status": {},
        "latitude": 2.04,
        "longitude": 45.31,
        "timestamp": ts,
    })
    ers.save_emergencies(edata)
    s = ers.load_settings()
    s["post_accept_remind_sec"] = 60
    s["post_accept_escalate_sec"] = 120
    s["post_accept_reassign_sec"] = 180
    s["emergency_absolute_timeout_hours"] = 999
    s["station_desk_stale_hours"] = 999
    ers.save_settings(s)
    ers._run_escalations()
    em, _ = ers.get_emergency_by_id(eid)
    assert em["status"] == "no_responder_available"
    assert em.get("tracking_active") is False
    assert em["status"] in ers.COMPLETED_STATUSES
    assert any(
        h.get("status") == "no_responder_available" for h in (em.get("status_history") or [])
    )


def test_absolute_timeout_closes_active_emergency(client):
    c, ers = client
    edata = ers.load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    edata["emergencies"].append({
        "id": eid,
        "user_id": 1,
        "type": "medical",
        "assigned_to": "hospital",
        "assigned_hospital_id": 1,
        "status": "dispatched",
        "accepted_at": "2026-06-01 12:00:00",
        "tracking_active": True,
        "status_history": [
            {"status": "accepted", "timestamp": "2026-06-01 12:00:00", "note": "a"},
            {"status": "dispatched", "timestamp": "2026-06-01 12:05:00", "note": "d"},
        ],
        "responder_status": {"en_route": "2026-06-01 12:05:00"},
        "latitude": 2.04,
        "longitude": 45.31,
        "timestamp": "2026-06-01 11:00:00",
    })
    ers.save_emergencies(edata)
    s = ers.load_settings()
    s["emergency_absolute_timeout_hours"] = 24
    s["station_desk_stale_hours"] = 999
    s["post_accept_reassign_sec"] = 999999
    ers.save_settings(s)
    ers._run_escalations()
    em, _ = ers.get_emergency_by_id(eid)
    assert em["status"] == "timeout"
    assert em.get("tracking_active") is False
    assert em["status"] in ers.COMPLETED_STATUSES
    stage, label = ers._emergency_display_stage(em)
    assert stage == "timeout"
    assert "timeout" in label.lower() or "timed" in label.lower()


def test_citizen_dashboard_leaves_live_tracking_on_timeout(client):
    c, ers = client
    login(c, "ahmed@example.com", "123456")
    udata = ers.load_users()
    uid = next(u["id"] for u in udata["users"] if u["email"] == "ahmed@example.com")
    edata = ers.load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    edata["emergencies"].append({
        "id": eid,
        "user_id": uid,
        "type": "medical",
        "assigned_to": "hospital",
        "assigned_hospital_id": 1,
        "status": "timeout",
        "tracking_active": False,
        "status_history": [
            {"status": "timeout", "timestamp": "2026-08-01 12:00:00", "note": "timed out"},
        ],
        "responder_status": {},
        "latitude": 2.04,
        "longitude": 45.31,
        "timestamp": "2026-08-01 10:00:00",
    })
    ers.save_emergencies(edata)
    r = c.get("/api/user/dashboard")
    assert r.status_code == 200
    data = r.get_json()
    assert data["active_emergency"] is None
    assert data["completed_count"] >= 1
    recent = data["recent_emergencies"]
    assert any(e["id"] == eid and e["status"] == "timeout" for e in recent)
    timed = next(e for e in recent if e["id"] == eid)
    assert timed.get("timeline")
    assert any(s.get("key") == "timeout" for s in timed["timeline"])


def test_fresh_accepted_fire_not_auto_closed(client):
    c, ers = client
    edata = ers.load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    now = ers.now_str()
    edata["emergencies"].append({
        "id": eid,
        "user_id": 1,
        "type": "fire",
        "assigned_to": "fire",
        "assigned_station_id": 1,
        "status": "accepted",
        "accepted_at": now,
        "tracking_active": True,
        "status_history": [{"status": "accepted", "timestamp": now, "note": "accepted"}],
        "responder_status": {},
        "latitude": 2.04,
        "longitude": 45.31,
        "timestamp": now,
    })
    ers.save_emergencies(edata)
    s = ers.load_settings()
    s["station_desk_stale_hours"] = 24
    s["post_accept_remind_sec"] = 10**9
    s["post_accept_escalate_sec"] = 10**9
    s["post_accept_reassign_sec"] = 10**9
    s["emergency_absolute_timeout_hours"] = 9999
    ers.save_settings(s)
    ers._run_escalations()
    em, _ = ers.get_emergency_by_id(eid)
    assert em["status"] == "accepted"

