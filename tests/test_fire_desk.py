"""Fire desk: citizen fire SOS appears on fire station queue (JSON isolation)."""
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
        "response_stations",
        {
            "stations": [
                {
                    "id": 2,
                    "kind": "fire",
                    "name": "Test Fire Station",
                    "city": "Mogadishu",
                    "region": "Banadir",
                    "district": "Hodan",
                    "address": "Hodan",
                    "latitude": 2.052,
                    "longitude": 45.328,
                    "phone": "612000300",
                    "operating_status": "open",
                    "created_at": now,
                    "updated_at": now,
                }
            ],
            "next_id": 3,
        },
    )
    app_module.save_json("hospitals", {"hospitals": [], "next_id": 1})
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
                },
                {
                    "id": 2,
                    "name": "Fire",
                    "email": "fire@example.com",
                    "phone": "612000002",
                    "password_hash": generate_password_hash("Secret123!"),
                    "role": "fire",
                    "status": "active",
                    "email_verified": True,
                    "station_id": 2,
                    "created_at": now,
                    "activity": [],
                },
            ],
            "next_id": 3,
        },
    )
    app_module.save_json("emergencies", {"emergencies": [], "next_id": 1})
    app_module.save_json("notifications", {"notifications": [], "next_id": 1})
    with app_module.app.test_client() as c:
        yield c, app_module


def test_citizen_fire_sos_appears_on_fire_desk(client):
    c, ers = client
    with c.session_transaction() as s:
        s["user_id"] = 1
        s["role"] = "citizen"
        s["logged_in"] = True
        s["name"] = "Citizen"

    r = c.post(
        "/api/send_alert",
        json={
            "type": "fire",
            "latitude": 2.0469,
            "longitude": 45.3182,
            "location": "Test Fire SOS Banadir",
            "district": "Hodan",
            "name": "Fire Caller",
            "phone": "0612222222",
            "notes": "Fire desk visibility test",
        },
    )
    body = r.get_json() or {}
    assert r.status_code == 200 and body.get("success")
    eid = body["id"]
    em, _ = ers.get_emergency_by_id(eid)
    assert em.get("assigned_to") == "fire"

    with c.session_transaction() as s:
        s["user_id"] = 2
        s["role"] = "fire"
        s["logged_in"] = True

    html2 = c.get("/fire").get_data(as_text=True)
    assert "Fire Command" in html2
    assert "fire_command.js" in html2
    assert "hcc-chat-shell" in html2

    q = c.get("/api/get_emergencies?type=fire")
    ids = [e.get("id") for e in (q.get_json() or {}).get("emergencies") or []]
    assert eid in ids

    acc = c.post(f"/api/fire/request/{eid}/accept", json={})
    assert acc.status_code == 200 and (acc.get_json() or {}).get("success")
