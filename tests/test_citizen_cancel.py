"""Citizen can cancel their own active emergency (JSON isolation)."""
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
    now = "2026-08-02 12:00:00"
    app_module.save_json(
        "hospitals",
        {
            "hospitals": [
                {
                    "id": 1,
                    "name": "Cancel Test Hospital",
                    "city": "Mogadishu",
                    "region": "Banadir",
                    "district": "Hodan",
                    "address": "Hodan",
                    "latitude": 2.0469,
                    "longitude": 45.3182,
                    "phone": "612000100",
                    "services": ["Emergency"],
                    "specialties": ["Emergency"],
                    "ambulance_available": True,
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


def test_citizen_can_cancel_own_emergency(client):
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
            "location": "Cancel test Mogadishu",
            "district": "Hodan",
            "name": "Cancel Tester",
            "phone": "0612222222",
            "notes": "Citizen cancel flow test",
        },
    )
    body = r.get_json() or {}
    assert r.status_code == 200, body
    assert body.get("success") is True
    eid = body.get("id")
    assert eid

    cancel = c.post(
        f"/api/patient/request/{eid}/cancel",
        json={"reason": "False alarm"},
    )
    cbody = cancel.get_json() or {}
    assert cancel.status_code == 200, cbody
    assert cbody.get("success") is True
    assert cbody.get("status") == "cancelled"

    em, _ = ers.get_emergency_by_id(eid)
    assert em is not None
    assert (em.get("status") or "").lower() == "cancelled"
    assert em.get("cancelled_by") == "citizen"
    assert em.get("tracking_active") is False

    again = c.post(f"/api/patient/request/{eid}/cancel", json={})
    again_body = again.get_json() or {}
    assert again.status_code == 400
    assert again_body.get("success") is False
