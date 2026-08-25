"""Sprint 3.1.2 — Hospital Command Operator Workspace + busy ambulance guard."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

os.environ["TESTING"] = "1"
os.environ["GURMADNET_DB"] = "json"
os.environ.setdefault("EMAIL_PROVIDER", "memory")
os.environ.setdefault("ALLOW_TEST_EMAILS", "1")
os.environ.setdefault("AI_PROVIDER", "rule_based")

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def hcc(tmp_path, monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("GURMADNET_DB", "json")
    monkeypatch.setenv("DATABASE_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOW_TEST_EMAILS", "1")
    monkeypatch.setenv("AI_PROVIDER", "rule_based")

    import app as app_module

    app_module.DATABASE_DIR = str(tmp_path)
    app_module.configure_hospital_db(str(tmp_path))
    app_module.app.config["TESTING"] = True
    app_module.app.config["WTF_CSRF_ENABLED"] = False
    now = "2026-08-10 12:00:00"

    app_module.save_json(
        "hospitals",
        {
            "hospitals": [
                {
                    "id": 1,
                    "name": "HCC Workspace Hospital",
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
                    "owner_user_id": 2,
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
                    "name": "Citizen WS",
                    "email": "citizen.ws@example.com",
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
                    "name": "Hospital WS",
                    "email": "hospital.ws@example.com",
                    "phone": "612000002",
                    "password_hash": generate_password_hash("Secret123!"),
                    "role": "hospital",
                    "status": "active",
                    "email_verified": True,
                    "hospital_id": 1,
                    "created_at": now,
                    "activity": [],
                },
            ],
            "next_id": 3,
        },
    )
    app_module.save_json("emergencies", {"emergencies": [], "next_id": 1})
    app_module.save_json("notifications", {"notifications": [], "next_id": 1})
    app_module.save_json("messages", {"messages": [], "next_id": 1})

    with app_module.app.test_client() as c:
        yield c, app_module


def _session(c, user_id, role, name="User"):
    with c.session_transaction() as s:
        s["user_id"] = user_id
        s["role"] = role
        s["name"] = name
        s["logged_in"] = True


def _create_medical(c, app, *, lat=2.05, lng=45.32, notes="workspace case"):
    _session(c, 1, "citizen", "Citizen WS")
    r = c.post(
        "/api/send_alert",
        json={
            "type": "medical",
            "latitude": lat,
            "longitude": lng,
            "location": "Hodan market",
            "name": "Citizen WS",
            "phone": "612000001",
            "notes": notes,
        },
    )
    assert r.status_code == 200, r.get_json()
    eid = r.get_json()["id"]
    em, _ = app.get_emergency_by_id(eid)
    assert em.get("assigned_hospital_id") == 1
    return eid


def _add_ambulance(c, *, call_sign="AMB-WS", status="available"):
    _session(c, 2, "hospital", "Hospital WS")
    r = c.post(
        "/api/hospital/ambulances",
        json={
            "call_sign": call_sign,
            "status": status,
            "driver_name": "Driver WS",
            "driver_phone": "617000111",
            "latitude": 2.047,
            "longitude": 45.319,
        },
    )
    assert r.status_code in (200, 201), r.get_json()
    return r.get_json()["ambulance"]["id"]


def test_workspace_ui_markers():
    js = (ROOT / "static" / "js" / "hospital_command.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "hospital_command.css").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "hospital_dashboard.html").read_text(encoding="utf-8")

    assert "Sprint 3.1.2" in js or "Operator Workspace (Sprint 3.1.2)" in js
    assert "function renderWorkspace" in js
    assert "function wsStage" in js
    assert "hcc-workspace" in html and 'data-sprint="3.1.2"' in html
    assert "Operator Workspace" in html
    assert "btn-ws-close" in html
    assert "hcc-ws-body" in html and "hcc-ws-body" in css
    assert "hcc-ws-actions" in js and "hcc-ws-actions" in css
    assert "hcc-ws-amb-list" in js and "hcc-ws-amb" in css
    assert "hcc-ws-life" in js and "hcc-ws-life" in css
    assert "rail-has-workspace" in js and "rail-has-workspace" in css
    assert 'availableUnits()' in js
    assert 'action: "en_route"' in js
    assert 'action: "arrived_at_scene"' in js
    assert 'action: "reached_victim"' in js
    assert "/api/hospital/request/" in js and "/assign-ambulance" in js
    assert "v=hcc-312" in html or "v=hcc-313" in html
    # Pending workspace actions (not Complete / En Route)
    assert "ws-accept" in js and "ws-reject" in js
    assert "ws-enroute" in js and "ws-arrived" in js and "ws-complete" in js
    # Queue Accept-without-ambulance contract preserved
    assert 'class="hcc-btn hcc-btn-success hcc-btn-sm q-accept"' in js


def test_lifecycle_accept_assign_enroute_arrived_complete(hcc):
    c, app = hcc
    eid = _create_medical(c, app, notes="full lifecycle")
    aid = _add_ambulance(c, call_sign="AMB-LIFE")

    _session(c, 2, "hospital", "Hospital WS")

    # 3) Accept without ambulance
    r = c.post(f"/api/hospital/request/{eid}/accept", json={})
    assert r.status_code == 200, r.get_json()
    em, _ = app.get_emergency_by_id(eid)
    assert em["status"] == "accepted"
    assert not em.get("assigned_ambulance_id")

    # 4) Fleet list via existing API
    fleet = c.get("/api/hospital/ambulances")
    assert fleet.status_code == 200
    units = (fleet.get_json() or {}).get("ambulances") or []
    assert any(u["id"] == aid and u.get("status") == "available" for u in units)

    # 5) Assign available unit
    r = c.post(
        f"/api/hospital/request/{eid}/assign-ambulance",
        json={"ambulance_unit_id": aid},
    )
    assert r.status_code == 200, r.get_json()
    em, _ = app.get_emergency_by_id(eid)
    assert em.get("assigned_ambulance_id") == aid
    assert em["status"] == "dispatched"

    # 7) En Route
    r = c.post(f"/api/emergencies/{eid}/responder", json={"action": "en_route"})
    assert r.status_code == 200, r.get_json()
    em, _ = app.get_emergency_by_id(eid)
    assert em["status"] == "dispatched"
    assert em.get("responder_status", {}).get("en_route")

    # 8) Arrived
    r = c.post(f"/api/emergencies/{eid}/responder", json={"action": "arrived_at_scene"})
    assert r.status_code == 200, r.get_json()
    em, _ = app.get_emergency_by_id(eid)
    assert em["status"] == "in_progress"
    assert em.get("responder_status", {}).get("arrived_at_scene")

    # 9) Complete
    r = c.post(f"/api/emergencies/{eid}/responder", json={"action": "reached_victim"})
    assert r.status_code == 200, r.get_json()
    em, _ = app.get_emergency_by_id(eid)
    assert em["status"] == "completed"

    units2 = c.get("/api/hospital/ambulances").get_json()["ambulances"]
    assert next(u for u in units2 if u["id"] == aid)["status"] == "available"

    # 11) Terminal cannot reopen
    r = c.post(f"/api/hospital/request/{eid}/accept", json={})
    assert r.status_code in (400, 403, 409) or (r.get_json() or {}).get("success") is False
    r = c.post(
        f"/api/hospital/request/{eid}/assign-ambulance",
        json={"ambulance_unit_id": aid},
    )
    assert r.status_code == 400
    r = c.post(f"/api/emergencies/{eid}/responder", json={"action": "en_route"})
    assert r.status_code == 400


def test_busy_and_offline_ambulance_assign_guard(hcc):
    """Busy/offline unit cannot be assigned to a *different* emergency; same-case rebind ok."""
    c, app = hcc
    eid1 = _create_medical(c, app, notes="case A", lat=2.05, lng=45.32)
    eid2 = _create_medical(c, app, notes="case B", lat=2.051, lng=45.321)
    aid_busy = _add_ambulance(c, call_sign="AMB-BUSY", status="available")
    aid_offline = _add_ambulance(c, call_sign="AMB-OFF", status="offline")

    _session(c, 2, "hospital", "Hospital WS")
    assert c.post(f"/api/hospital/request/{eid1}/accept", json={}).status_code == 200
    assert c.post(f"/api/hospital/request/{eid2}/accept", json={}).status_code == 200

    r = c.post(
        f"/api/hospital/request/{eid1}/assign-ambulance",
        json={"ambulance_unit_id": aid_busy},
    )
    assert r.status_code == 200, r.get_json()
    em1, _ = app.get_emergency_by_id(eid1)
    assert em1.get("assigned_ambulance_id") == aid_busy

    # Busy on case A → reject for case B
    r = c.post(
        f"/api/hospital/request/{eid2}/assign-ambulance",
        json={"ambulance_unit_id": aid_busy},
    )
    assert r.status_code == 400, r.get_json()
    body = r.get_json() or {}
    assert body.get("success") is False
    assert "busy" in (body.get("message") or "").lower()
    em2, _ = app.get_emergency_by_id(eid2)
    assert not em2.get("assigned_ambulance_id")

    # Offline → reject
    r = c.post(
        f"/api/hospital/request/{eid2}/assign-ambulance",
        json={"ambulance_unit_id": aid_offline},
    )
    assert r.status_code == 400, r.get_json()
    assert "offline" in ((r.get_json() or {}).get("message") or "").lower()

    # Same-case rebind of already-assigned busy unit remains allowed
    r = c.post(
        f"/api/hospital/request/{eid1}/assign-ambulance",
        json={"ambulance_unit_id": aid_busy},
    )
    assert r.status_code == 200, r.get_json()
    em1, _ = app.get_emergency_by_id(eid1)
    assert em1.get("assigned_ambulance_id") == aid_busy


def test_pending_accept_without_ambulance_preserved(hcc):
    """Sprint 3.1.1 contract: Pending → Accept with empty body still works."""
    c, app = hcc
    eid = _create_medical(c, app, notes="accept empty")
    _session(c, 2, "hospital", "Hospital WS")
    r = c.post(f"/api/hospital/request/{eid}/accept", json={})
    assert r.status_code == 200, r.get_json()
    em, _ = app.get_emergency_by_id(eid)
    assert em["status"] == "accepted"
    assert not em.get("assigned_ambulance_id")


def test_citizen_files_untouched_marker():
    """Smoke: Sprint 3.1.2 must not invent citizen workspace markers."""
    patient = (ROOT / "static" / "js" / "patient.js").read_text(encoding="utf-8")
    assert "hcc-workspace" not in patient
    assert "Sprint 3.1.2" not in patient
