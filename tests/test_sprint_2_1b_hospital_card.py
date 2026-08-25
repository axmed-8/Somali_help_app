"""Sprint 2.1B — smart hospital recommendation card on citizen status."""
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
    now = "2026-08-03 10:00:00"
    app_module.save_json(
        "hospitals",
        {
            "hospitals": [
                {
                    "id": 1,
                    "name": "Banadir Recommended Hospital",
                    "city": "Mogadishu",
                    "region": "Banadir",
                    "district": "Hodan",
                    "address": "Hodan Rd",
                    "latitude": 2.0469,
                    "longitude": 45.3182,
                    "phone": "612000555",
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


def test_recommended_hospital_helper_uses_assignment(client):
    _, ers = client
    em = {
        "id": 7,
        "user_id": 1,
        "status": "pending_hospital",
        "assigned_hospital_id": 1,
        "assigned_hospital_name": "Banadir Recommended Hospital",
        "hospital_distance_km": 1.25,
        "latitude": 2.05,
        "longitude": 45.32,
        "timestamp": "2026-08-03 10:00:00",
        "status_history": [],
        "responder_status": {},
    }
    card = ers._citizen_recommended_hospital(em)
    assert card is not None
    assert card["name"] == "Banadir Recommended Hospital"
    assert card["district"] == "Hodan"
    assert card["phone"] == "612000555"
    assert card["distance_km"] == 1.25
    assert card["assignment_status"] == "assigned"
    assert "Hospital received" in card["assignment_status_en"]
    assert card["assignment_status_so"]


def test_recommended_hospital_none_without_assignment(client):
    _, ers = client
    em = {
        "status": "pending",
        "timestamp": "2026-08-03 10:00:00",
        "status_history": [],
        "responder_status": {},
    }
    assert ers._citizen_recommended_hospital(em) is None


def test_status_api_includes_hospital_card_after_medical_sos(client):
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
            "location": "Hodan test",
            "district": "Hodan",
            "name": "Citizen",
            "phone": "0612222222",
            "notes": "Sprint 2.1B hospital card",
        },
    )
    body = r.get_json() or {}
    assert r.status_code == 200, body
    assert body.get("success") is True
    eid = body.get("id")
    assert eid

    em, _ = ers.get_emergency_by_id(eid)
    assert em.get("assigned_hospital_id") == 1
    assert em.get("assigned_hospital_name")

    status = c.get("/api/patient/request/status")
    sbody = status.get_json() or {}
    assert status.status_code == 200, sbody
    assert sbody.get("active") is True
    req = sbody.get("request") or {}
    hosp = req.get("hospital") or req.get("recommended_hospital")
    assert hosp is not None
    assert hosp.get("name") == "Banadir Recommended Hospital"
    assert hosp.get("district") == "Hodan"
    assert hosp.get("phone") == "612000555"
    assert hosp.get("distance_km") is not None
    assert hosp.get("assignment_status_en")
    assert hosp.get("assignment_status_so")
    # ETA may be a number (existing helper) or None → UI shows Calculating...
    assert "eta_minutes" in hosp
    # assignment still medical/hospital — dispatch untouched
    assert em.get("assigned_to") == "hospital"
    assert (em.get("status") or "").lower() in ("pending_hospital", "pending", "accepted")
