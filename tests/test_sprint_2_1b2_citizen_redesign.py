"""Sprint 2.1B.2 — citizen experience redesign (presentation helpers)."""
import os

import pytest
from werkzeug.security import generate_password_hash

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
    import app as app_module

    app_module.DATABASE_DIR = str(tmp_path)
    app_module.configure_hospital_db(str(tmp_path))
    app_module.app.config["TESTING"] = True
    app_module.app.config["WTF_CSRF_ENABLED"] = False
    now = "2026-08-03 14:00:00"
    app_module.save_json(
        "hospitals",
        {
            "hospitals": [
                {
                    "id": 1,
                    "name": "Erdogan Hospital",
                    "city": "Mogadishu",
                    "region": "Banadir",
                    "district": "Hodan",
                    "address": "Hodan",
                    "latitude": 2.0469,
                    "longitude": 45.3182,
                    "phone": "612000777",
                    "services": ["Emergency"],
                    "specialties": ["Emergency"],
                    "ambulance_available": True,
                    "emergency_capacity": 10,
                    "operating_status": "open",
                    "created_at": now,
                    "updated_at": now,
                }
            ],
            "next_id": 2,
        },
    )
    app_module.save_json("response_stations", {"stations": [], "next_id": 1})
    app_module.save_json("ambulance_units", {"ambulances": [], "next_id": 1})
    app_module.save_json(
        "users",
        {
            "users": [
                {
                    "id": 1,
                    "name": "Citizen",
                    "email": "citizen@example.com",
                    "phone": "612000001",
                    "password_hash": generate_password_hash("Secret123!"),
                    "role": "citizen",
                    "status": "active",
                    "email_verified": True,
                    "created_at": now,
                    "activity": [],
                }
            ],
            "next_id": 2,
        },
    )
    app_module.save_json("emergencies", {"emergencies": [], "next_id": 1})
    app_module.save_json("notifications", {"notifications": [], "next_id": 1})
    with app_module.app.test_client() as c:
        yield c, app_module


def test_citizen_notify_message_is_calm(client):
    _, ers = client
    em = {
        "status": "accepted",
        "assigned_hospital_name": "Erdogan Hospital",
        "assigned_hospital_id": 1,
        "responder_status": {},
        "status_history": [],
    }
    msg = ers._citizen_status_notify_message(em)
    assert "accepted" in msg.lower()
    assert "Emergency update:" not in msg
    assert "pending_hospital" not in msg.lower()


def test_status_api_includes_completion_fields(client):
    c, ers = client
    with c.session_transaction() as s:
        s["user_id"] = 1
        s["role"] = "citizen"
        s["logged_in"] = True
        s["name"] = "Citizen"

    r = c.post(
        "/api/send_alert",
        json={
            "type": "medical",
            "latitude": 2.0469,
            "longitude": 45.3182,
            "location": "Hodan",
            "district": "Hodan",
            "name": "Citizen",
            "phone": "0612222222",
            "notes": "2.1B.2 redesign",
        },
    )
    body = r.get_json() or {}
    assert r.status_code == 200, body
    eid = body.get("id")
    assert eid

    em, edata = ers.get_emergency_by_id(eid)
    ers._append_status(em, "completed", "Care finished", notify_citizen=False)
    ers.save_emergencies(edata)

    status = c.get("/api/patient/request/status?id=" + str(eid))
    sbody = status.get_json() or {}
    assert status.status_code == 200, sbody
    req = sbody.get("request") or {}
    assert req.get("display_stage") == "completed"
    assert req.get("timestamp")
    assert "responder_status" in req
    assert req.get("display_stage_label") == "Emergency completed"
