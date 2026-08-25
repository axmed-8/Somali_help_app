"""
End-to-End: Facility registries + Dispatch command workflow.

Flow:
  1. Create hospital
  2. Create ambulance linked to hospital
  3. Create police station
  4. Create police user linked to station
  5. Create SOS emergency
  6. Dispatch (hospital + ambulance)
  7. Status: pending → dispatched → in_progress → completed → resolved
  8. Assert audit_logs + status_history
"""
import os
import time

import pytest

os.environ["TESTING"] = "1"
os.environ["GURMADNET_DB"] = "json"
os.environ.setdefault("EMAIL_PROVIDER", "memory")
os.environ.setdefault("ALLOW_TEST_EMAILS", "1")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("GURMADNET_DB", "json")
    monkeypatch.setenv("DATABASE_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOW_TEST_EMAILS", "1")
    # Drop live MySQL credentials so reload cannot attach to Railway during suite runs
    for key in (
        "MYSQL_HOST",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "MYSQL_DATABASE",
        "DB_HOST",
        "DB_USER",
        "DB_PASSWORD",
        "DB_NAME",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    import importlib
    import app as app_module

    importlib.reload(app_module)
    app_module.DATABASE_DIR = str(tmp_path)
    app_module.USE_MYSQL = False
    app_module.configure_hospital_db(str(tmp_path))
    app_module.app.config["TESTING"] = True
    app_module.app.config["WTF_CSRF_ENABLED"] = False
    from werkzeug.security import generate_password_hash

    app_module.save_json(
        "users",
        {
            "users": [
                {
                    "id": 1,
                    "name": "Super Admin",
                    "email": "super@example.com",
                    "phone": "",
                    "password_hash": generate_password_hash("Secret123!"),
                    "role": "super_admin",
                    "status": "active",
                    "email_verified": True,
                    "hospital_id": None,
                    "station_id": None,
                    "call_center_id": None,
                    "created_at": "2026-01-01 00:00:00",
                    "activity": [],
                }
            ],
            "next_id": 2,
        },
    )
    app_module.save_json("hospitals", {"hospitals": [], "next_id": 1})
    app_module.save_json("response_stations", {"stations": [], "next_id": 1})
    app_module.save_json("ambulance_units", {"ambulances": [], "next_id": 1})
    app_module.save_json("call_centers", {"call_centers": [], "next_id": 1})
    app_module.save_json("emergencies", {"emergencies": [], "next_id": 1})
    app_module.save_json("audit_log", {"entries": [], "next_id": 1})
    app_module.save_json("notifications", {"notifications": [], "next_id": 1})
    with app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = 1
            sess["role"] = "super_admin"
            sess["name"] = "Super Admin"
            sess["email"] = "super@example.com"
        yield c, app_module


def _assert_ok(resp, step, expect_status=None):
    if expect_status is not None:
        assert resp.status_code == expect_status, f"{step}: HTTP {resp.status_code} body={resp.get_data(as_text=True)[:400]}"
    data = resp.get_json(silent=True) or {}
    assert data.get("success") is not False, f"{step}: {data}"
    return data


def test_e2e_facility_dispatch_full_flow(client):
    c, app_module = client
    stamp = str(int(time.time()))

    # 1) Hospital
    r = c.post(
        "/api/admin/hospitals",
        json={
            "name": f"E2E Hospital {stamp}",
            "region": "Banadir",
            "district": "Hodan",
            "city": "Mogadishu",
            "address": "Hodan District, Mogadishu",
            "phone": "612000001",
            "latitude": 2.0469,
            "longitude": 45.3182,
            "services": ["Emergency Care", "Trauma"],
            "operating_status": "open",
            "ambulance_available": True,
            "owner_name": f"E2E Hosp Admin {stamp}",
            "owner_email": f"e2e.hospital.{stamp}@example.com",
            "owner_password": "Secret123!",
        },
    )
    data = _assert_ok(r, "create hospital", 201)
    hid = data["hospital"]["id"]
    owner_id = data["owner"]["id"]
    assert data.get("owner", {}).get("role") == "hospital"
    assert data["hospital"].get("owner_user_id") == owner_id

    # 2) Hospital manages its own ambulance (dispatch essentials only)
    with c.session_transaction() as sess:
        sess["user_id"] = owner_id
        sess["role"] = "hospital"
        sess["name"] = f"E2E Hosp Admin {stamp}"
        sess["email"] = f"e2e.hospital.{stamp}@example.com"
    r = c.post(
        "/api/hospital/ambulances",
        json={
            "call_sign": f"AMB-E2E-{stamp}",
            "status": "available",
            "driver_name": "E2E Driver",
            "driver_phone": "612000010",
            "latitude": 2.047,
            "longitude": 45.319,
        },
    )
    data = _assert_ok(r, "hospital create ambulance", 201)
    aid = data["ambulance"]["id"]
    assert data["ambulance"]["hospital_id"] == hid
    assert data["ambulance"]["driver_phone"] == "612000010"

    with c.session_transaction() as sess:
        sess["user_id"] = 1
        sess["role"] = "super_admin"
        sess["name"] = "Super Admin"
        sess["email"] = "super@example.com"

    # Admin cannot manage fleet
    assert c.post("/api/admin/ambulances", json={"hospital_id": hid, "call_sign": "X"}).status_code == 403

    # 3) Police station
    r = c.post(
        "/api/admin/stations",
        json={
            "kind": "police",
            "name": f"E2E Police {stamp}",
            "city": "Mogadishu",
            "region": "Banadir",
            "district": "Wadajir",
            "address": "Wadajir, Mogadishu",
            "phone": "612000002",
            "latitude": 2.038,
            "longitude": 45.315,
            "operating_status": "open",
        },
    )
    data = _assert_ok(r, "create police station", 201)
    sid = data["station"]["id"]

    # 4) Police user linked to station
    r = c.post(
        "/api/admin/users/create",
        json={
            "name": f"E2E Officer {stamp}",
            "email": f"e2e.police.{stamp}@example.com",
            "phone": "612000003",
            "password": "Secret123!",
            "role": "police",
            "station_id": sid,
        },
    )
    data = _assert_ok(r, "create police user")
    assert data.get("role") == "police"
    assert data.get("station_id") == sid
    police_uid = data["id"]

    # 5) SOS / emergency
    edata = app_module.load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    edata["emergencies"].append(
        {
            "id": eid,
            "user_id": None,
            "type": "medical",
            "status": "pending",
            "request_mode": "sos",
            "location": "Mogadishu Hodan",
            "district": "Hodan",
            "caller_name": "E2E Citizen",
            "phone": "612000099",
            "latitude": 2.046,
            "longitude": 45.32,
            "assigned_to": "hospital",
            "timestamp": app_module.now_str(),
            "status_history": [],
            "escalation_queue": [],
            "escalation_index": 0,
            "tracking_active": True,
        }
    )
    app_module.save_emergencies(edata)
    app_module.append_audit("emergency_created", "emergency", eid, {"type": "medical", "source": "e2e"}, 1)

    # 6) Dispatch Center — assign hospital + ambulance
    r = c.post(
        "/api/admin/emergencies/dispatch",
        json={
            "id": eid,
            "assigned_to": "hospital",
            "assigned_hospital_id": hid,
            "ambulance_unit_id": aid,
            "notes": "E2E dispatch to hospital + ambulance",
        },
    )
    data = _assert_ok(r, "dispatch hospital")
    em = data["emergency"]
    assert em["assigned_hospital_id"] == hid
    assert em["status"] == "pending_hospital"
    assert em.get("assigned_ambulance_id") == aid

    # Also assign police station via update (multi-agency link)
    r = c.post(
        "/api/admin/emergencies/update",
        json={
            "id": eid,
            "assigned_to": "police",
            "assigned_station_id": sid,
            "status": "dispatched",
            "note": "E2E also assign police station",
        },
    )
    data = _assert_ok(r, "assign police + dispatched")
    em = data["emergency"]
    assert em["assigned_station_id"] == sid
    assert em["status"] == "dispatched"

    # 7) Status progression
    for status, note in (
        ("in_progress", "E2E in progress"),
        ("completed", "E2E completed"),
        ("resolved", "E2E resolved"),
    ):
        r = c.post(
            "/api/admin/emergencies/update",
            json={"id": eid, "status": status, "note": note},
        )
        data = _assert_ok(r, f"status → {status}")
        assert data["emergency"]["status"] == status

    # Final emergency record
    edata = app_module.load_emergencies()
    em = next(e for e in edata["emergencies"] if e["id"] == eid)
    history = em.get("status_history") or []
    statuses_seen = [h.get("status") for h in history]
    assert "dispatched" in statuses_seen, statuses_seen
    assert "in_progress" in statuses_seen, statuses_seen
    assert "completed" in statuses_seen, statuses_seen
    assert "resolved" in statuses_seen, statuses_seen
    assert em["status"] == "resolved"
    assert em.get("assigned_hospital_id") == hid or em.get("assigned_station_id") == sid

    # 8) Audit log coverage
    audit = app_module.read_json("audit_log", {"entries": []})
    actions = {(e.get("action"), e.get("entity_type"), e.get("entity_id")) for e in audit.get("entries") or []}
    assert ("hospital_created", "hospital", hid) in actions
    assert ("hospital_ambulance_created", "ambulance", aid) in actions
    assert ("station_created", "station", sid) in actions
    assert ("admin_user_created", "user", police_uid) in actions
    assert ("emergency_dispatch", "emergency", eid) in actions
    assert ("emergency_updated", "emergency", eid) in actions

    # Ambulance marked busy after dispatch
    amb_board = c.get("/api/admin/ambulances").get_json()["ambulances"]
    amb_row = next(a for a in amb_board if a["id"] == aid)
    assert amb_row["status"] == "busy"
    assert amb_row.get("driver_phone") == "612000010"

    # Registries still list created entities
    assert any(h["id"] == hid for h in c.get("/api/admin/hospitals").get_json()["hospitals"])
    assert any(a["id"] == aid for a in c.get("/api/admin/ambulances").get_json()["ambulances"])
    assert any(s["id"] == sid for s in c.get("/api/admin/stations?kind=police").get_json()["stations"])
