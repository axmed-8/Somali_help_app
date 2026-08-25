"""Sprint 2.1A — smart emergency status stages, timeline, and citizen notify."""
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
                    "name": "Smart Status Hospital",
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


def test_display_stage_mapping(client):
    _, ers = client
    cases = [
        ({"status": "pending", "timestamp": "t0"}, "finding_nearest", "Finding nearest hospital"),
        (
            {
                "status": "pending_hospital",
                "assigned_hospital_id": 1,
                "timestamp": "t0",
            },
            "assigned",
            "Hospital received your request",
        ),
        (
            {
                "status": "pending",
                "assigned_station_id": 9,
                "timestamp": "t0",
            },
            "assigned",
            "Hospital received your request",
        ),
        ({"status": "accepted", "accepted_at": "t1"}, "accepted", "Hospital accepted your request"),
        ({"status": "dispatched"}, "responder_dispatched", "Ambulance dispatched"),
        (
            {"status": "dispatched", "responder_status": {"en_route": "t2"}},
            "on_the_way",
            "Ambulance is on the way",
        ),
        (
            {"status": "in_progress", "responder_status": {"arrived_at_scene": "t3"}},
            "arrived",
            "Ambulance arrived",
        ),
        ({"status": "completed"}, "completed", "Emergency completed"),
        ({"status": "cancelled"}, "cancelled", "Request cancelled"),
        ({"status": "no_hospital_available"}, "no_facility", "No hospital available"),
    ]
    for em, key, label in cases:
        got_key, got_label = ers._emergency_display_stage(em)
        assert got_key == key, em
        assert got_label == label, em


def test_timeline_has_eight_smart_steps(client):
    _, ers = client
    em = {
        "status": "accepted",
        "timestamp": "2026-08-02 12:00:00",
        "accepted_at": "2026-08-02 12:05:00",
        "assigned_hospital_id": 1,
        "status_history": [
            {"status": "pending_hospital", "timestamp": "2026-08-02 12:01:00"},
            {"status": "accepted", "timestamp": "2026-08-02 12:05:00"},
        ],
        "responder_status": {},
    }
    timeline = ers._build_emergency_timeline(em)
    assert len(timeline) == 8
    keys = [s["key"] for s in timeline]
    assert keys == [
        "submitted",
        "finding_nearest",
        "assigned",
        "accepted",
        "responder_dispatched",
        "on_the_way",
        "arrived",
        "completed",
    ]
    progress = ers._build_status_progress(em)
    current = [p for p in progress if p.get("current")]
    assert len(current) == 1
    assert current[0]["key"] == "accepted"
    assert progress[0]["completed"] is True
    assert progress[3]["completed"] is False  # current not marked completed


def test_patient_status_api_includes_smart_fields(client):
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
            "location": "Smart status Mogadishu",
            "district": "Hodan",
            "name": "Citizen",
            "phone": "0612222222",
            "notes": "Sprint 2.1A status API",
        },
    )
    body = r.get_json() or {}
    assert r.status_code == 200, body
    assert body.get("success") is True

    status = c.get("/api/patient/request/status")
    sbody = status.get_json() or {}
    assert status.status_code == 200, sbody
    assert sbody.get("active") is True
    req = sbody.get("request") or {}
    assert req.get("display_stage")
    assert req.get("display_stage_label")
    assert isinstance(req.get("timeline"), list)
    assert len(req["timeline"]) == 8
    assert isinstance(req.get("progress"), list)
    assert len(req["progress"]) == 8
    # existing fields preserved
    assert "status" in req
    assert "status_history" in req


def test_append_status_notifies_citizen_on_change(client):
    _, ers = client
    em = {
        "id": 99,
        "user_id": 1,
        "status": "pending_hospital",
        "assigned_hospital_id": 1,
        "status_history": [],
        "responder_status": {},
        "timestamp": "2026-08-02 12:00:00",
    }
    ers._append_status(em, "accepted", "Hospital accepted")
    assert em["status"] == "accepted"
    notes = ers.read_json("notifications", {"notifications": []}).get("notifications") or []
    patient_notes = [
        n
        for n in notes
        if n.get("target_type") == "patient"
        and n.get("target_id") == 1
        and n.get("request_id") == 99
    ]
    assert patient_notes, notes
    assert any("accepted" in (n.get("message") or "").lower() for n in patient_notes)

    before = len(patient_notes)
    ers._append_status(em, "accepted", "Same status again")
    notes2 = ers.read_json("notifications", {"notifications": []}).get("notifications") or []
    patient_notes2 = [
        n
        for n in notes2
        if n.get("target_type") == "patient"
        and n.get("target_id") == 1
        and n.get("request_id") == 99
    ]
    assert len(patient_notes2) == before


def test_append_status_notify_citizen_false_skips(client):
    _, ers = client
    em = {
        "id": 100,
        "user_id": 1,
        "status": "pending_hospital",
        "assigned_hospital_id": 1,
        "status_history": [],
        "responder_status": {},
        "timestamp": "2026-08-02 12:00:00",
    }
    ers._append_status(em, "accepted", "Quiet accept", notify_citizen=False)
    notes = ers.read_json("notifications", {"notifications": []}).get("notifications") or []
    patient_notes = [
        n
        for n in notes
        if n.get("target_type") == "patient"
        and n.get("target_id") == 1
        and n.get("request_id") == 100
    ]
    assert patient_notes == []
