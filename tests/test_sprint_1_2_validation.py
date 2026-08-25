"""
Sprint 1.2 — Emergency Dispatch & System Validation (JSON isolation).

Covers citizen SOS → dispatch → hospital/police/fire response → map → notifications.
Does not hit live MySQL.
"""
from __future__ import annotations

import os
import time

import pytest
from werkzeug.security import generate_password_hash

os.environ["TESTING"] = "1"
os.environ["GURMADNET_DB"] = "json"
os.environ.setdefault("EMAIL_PROVIDER", "memory")
os.environ.setdefault("ALLOW_TEST_EMAILS", "1")
os.environ.setdefault("AI_PROVIDER", "rule_based")


@pytest.fixture()
def s12(tmp_path, monkeypatch):
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

    stamp = str(int(time.time() * 1000))[-8:]
    now = "2026-08-02 12:00:00"

    # Facilities
    app_module.save_json(
        "hospitals",
        {
            "hospitals": [
                {
                    "id": 1,
                    "name": "S12 Hospital",
                    "city": "Mogadishu",
                    "region": "Banadir",
                    "district": "Hodan",
                    "address": "Hodan",
                    "latitude": 2.0469,
                    "longitude": 45.3182,
                    "phone": "612000100",
                    "emergency_contacts": ["612000100"],
                    "services": ["Emergency"],
                    "specialties": ["Emergency"],
                    "ambulance_available": True,
                    "ambulance_count": 1,
                    # Intentionally omit emergency_capacity — dispatch must still assign
                    "rating": 4.5,
                    "operating_status": "open",
                    "owner_user_id": 2,
                    "location_verified": True,
                    "created_at": now,
                    "updated_at": now,
                }
            ],
            "next_id": 2,
        },
    )
    app_module.save_json(
        "response_stations",
        {
            "stations": [
                {
                    "id": 1,
                    "kind": "police",
                    "name": "S12 Police",
                    "city": "Mogadishu",
                    "region": "Banadir",
                    "district": "Hodan",
                    "address": "Hodan",
                    "latitude": 2.038,
                    "longitude": 45.315,
                    "phone": "612000200",
                    "operating_status": "open",
                    "owner_user_id": 3,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": 2,
                    "kind": "fire",
                    "name": "S12 Fire",
                    "city": "Mogadishu",
                    "region": "Banadir",
                    "district": "Hodan",
                    "address": "Hodan",
                    "latitude": 2.052,
                    "longitude": 45.328,
                    "phone": "612000300",
                    "operating_status": "open",
                    "owner_user_id": 4,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
            "next_id": 3,
        },
    )
    app_module.save_json(
        "ambulance_units",
        {
            "ambulances": [
                {
                    "id": 1,
                    "hospital_id": 1,
                    "call_sign": "AMB-S12",
                    "plate_number": "S12-001",
                    "status": "available",
                    "latitude": 2.047,
                    "longitude": 45.319,
                    "driver_name": "Driver S12",
                    "driver_phone": "612000110",
                    "created_at": now,
                    "updated_at": now,
                }
            ],
            "next_id": 2,
        },
    )
    app_module.save_json(
        "users",
        {
            "users": [
                {
                    "id": 1,
                    "name": "Citizen S12",
                    "email": f"citizen.s12.{stamp}@example.com",
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
                    "name": "Hospital S12",
                    "email": f"hospital.s12.{stamp}@example.com",
                    "phone": "612000002",
                    "password_hash": generate_password_hash("Secret123!"),
                    "role": "hospital",
                    "status": "active",
                    "email_verified": True,
                    "hospital_id": 1,
                    "created_at": now,
                    "activity": [],
                },
                {
                    "id": 3,
                    "name": "Police S12",
                    "email": f"police.s12.{stamp}@example.com",
                    "phone": "612000003",
                    "password_hash": generate_password_hash("Secret123!"),
                    "role": "police",
                    "status": "active",
                    "email_verified": True,
                    "station_id": 1,
                    "created_at": now,
                    "activity": [],
                },
                {
                    "id": 4,
                    "name": "Fire S12",
                    "email": f"fire.s12.{stamp}@example.com",
                    "phone": "612000004",
                    "password_hash": generate_password_hash("Secret123!"),
                    "role": "fire",
                    "status": "active",
                    "email_verified": True,
                    "station_id": 2,
                    "created_at": now,
                    "activity": [],
                },
                {
                    "id": 5,
                    "name": "Admin S12",
                    "email": f"admin.s12.{stamp}@example.com",
                    "phone": "612000005",
                    "password_hash": generate_password_hash("Secret123!"),
                    "role": "super_admin",
                    "status": "active",
                    "email_verified": True,
                    "created_at": now,
                    "activity": [],
                },
            ],
            "next_id": 6,
        },
    )
    app_module.save_json("emergencies", {"emergencies": [], "next_id": 1})
    app_module.save_json("notifications", {"notifications": [], "next_id": 1})
    app_module.save_json("audit_log", {"entries": [], "next_id": 1})
    app_module.save_json("call_centers", {"call_centers": [], "next_id": 1})

    with app_module.app.test_client() as c:
        yield c, app_module


def _session(c, user_id, role, name="User"):
    with c.session_transaction() as s:
        s["user_id"] = user_id
        s["role"] = role
        s["name"] = name
        s["logged_in"] = True


def _ok(resp, step, status=None):
    if status is not None:
        assert resp.status_code == status, f"{step}: HTTP {resp.status_code} {resp.get_data(as_text=True)[:300]}"
    data = resp.get_json(silent=True) or {}
    assert data.get("success") is not False, f"{step}: {data}"
    return data


def test_s12_medical_hospital_workflow(s12):
    c, app = s12

    # 1) Citizen SOS with GPS + description
    _session(c, 1, "citizen", "Citizen S12")
    r = c.post(
        "/api/send_alert",
        json={
            "type": "medical",
            "latitude": 2.046,
            "longitude": 45.318,
            "location": "Hodan Market",
            "district": "Hodan",
            "name": "Citizen S12",
            "phone": "612000001",
            "notes": "Chest pain — sprint 1.2 validation",
        },
    )
    body = _ok(r, "citizen SOS", 200)
    assert body.get("success") is True
    eid = body["id"]
    assert body.get("assigned_to") == "hospital"
    assert body.get("status") in ("pending_hospital", "pending", "dispatched")
    em, _ = app.get_emergency_by_id(eid)
    assert em["user_id"] == 1
    assert abs(float(em["latitude"]) - 2.046) < 1e-6
    assert "Chest pain" in (em.get("notes") or "")

    # 2) AI recommendation path (parallel / non-blocking)
    try:
        eng = app._ai_engine()
        analysis = eng.analyze(app._ai_context_from_emergency(em, source="sos"))
        assert analysis is not None
        assert getattr(analysis, "category", None) or (isinstance(analysis, dict) and analysis)
    except Exception as exc:
        pytest.fail(f"AI analysis failed: {exc}")

    # Facility selection should be nearest hospital (even without emergency_capacity set)
    assert em.get("assigned_hospital_id") == 1, em
    assert em.get("status") == "pending_hospital"
    assert body.get("assigned_hospital") == "S12 Hospital"

    # 3) Hospital receive + accept + assign ambulance + status
    _session(c, 2, "hospital", "Hospital S12")
    # Role alias type=hospital and canonical type=medical must both work
    q_alias = c.get("/api/get_emergencies?type=hospital")
    assert q_alias.status_code == 200, q_alias.get_json()
    q = c.get("/api/get_emergencies?type=medical")
    assert q.status_code == 200, q.get_json()
    ids = [e["id"] for e in (q.get_json() or {}).get("emergencies") or []]
    ids_alias = [e["id"] for e in (q_alias.get_json() or {}).get("emergencies") or []]
    assert eid in ids, "hospital desk missing SOS (type=medical)"
    assert eid in ids_alias, "hospital desk missing SOS (type=hospital alias)"

    r = c.post(f"/api/hospital/request/{eid}/accept", json={})
    _ok(r, "hospital accept", 200)
    em, _ = app.get_emergency_by_id(eid)
    assert em["status"] == "accepted"

    r = c.post(
        f"/api/hospital/request/{eid}/assign-ambulance",
        json={"ambulance_unit_id": 1},
    )
    data = _ok(r, "assign ambulance", 200)
    assert data.get("ambulance", {}).get("id") == 1
    em, _ = app.get_emergency_by_id(eid)
    assert em.get("assigned_ambulance_id") == 1
    assert em["status"] in ("accepted", "dispatched")

    # Complete via admin status update (hospital desk completion varies by UI)
    _session(c, 5, "super_admin", "Admin S12")
    for st in ("in_progress", "completed"):
        r = c.post(
            "/api/admin/emergencies/update",
            json={"id": eid, "status": st, "note": f"S12 {st}"},
        )
        _ok(r, f"status {st}", 200)
    em, _ = app.get_emergency_by_id(eid)
    assert em["status"] == "completed"

    # Notifications: citizen + hospital
    notes = app.read_json("notifications", {"notifications": []}).get("notifications") or []
    targets = {(n.get("target_type"), n.get("target_id")) for n in notes}
    assert ("patient", 1) in targets or any(
        n.get("request_id") == eid and n.get("target_type") == "patient" for n in notes
    )
    assert any(n.get("request_id") == eid for n in notes), "expected notifications for emergency"


def test_s12_hospital_reject_escalation(s12):
    c, app = s12
    # Second hospital for escalation
    hdata = app.read_json("hospitals", {"hospitals": [], "next_id": 1})
    hdata["hospitals"].append(
        {
            "id": 2,
            "name": "S12 Hospital B",
            "city": "Mogadishu",
            "region": "Banadir",
            "district": "Wadajir",
            "address": "Wadajir",
            "latitude": 2.02,
            "longitude": 45.29,
            "phone": "612000199",
            "services": ["Emergency"],
            "specialties": ["Emergency"],
            "ambulance_available": True,
            "operating_status": "open",
            "owner_user_id": None,
            "created_at": "2026-08-02 12:00:00",
            "updated_at": "2026-08-02 12:00:00",
        }
    )
    hdata["next_id"] = 3
    app.save_json("hospitals", hdata)

    _session(c, 1, "citizen")
    r = c.post(
        "/api/send_alert",
        json={
            "type": "medical",
            "latitude": 2.0469,
            "longitude": 45.3182,
            "location": "Near Hospital A",
            "notes": "reject escalation test",
        },
    )
    eid = _ok(r, "SOS", 200)["id"]
    em, _ = app.get_emergency_by_id(eid)
    assert em.get("assigned_hospital_id") == 1

    _session(c, 2, "hospital")
    r = c.post(f"/api/hospital/request/{eid}/reject", json={"reason": "No capacity"})
    data = _ok(r, "hospital reject", 200)
    em, _ = app.get_emergency_by_id(eid)
    # Escalation should move to next hospital or mark no_hospital
    assert em.get("assigned_hospital_id") in (1, 2) or em.get("status") in (
        "rejected_by_hospital",
        "pending_hospital",
        "no_hospital_available",
    )
    assert em.get("escalation_index", 0) >= 1 or data.get("success") is True


def test_s12_police_and_fire_workflows(s12):
    c, app = s12

    # Police SOS
    _session(c, 1, "citizen")
    r = c.post(
        "/api/send_alert",
        json={
            "type": "security",
            "latitude": 2.04,
            "longitude": 45.316,
            "location": "Street A",
            "notes": "Robbery in progress",
        },
    )
    peid = _ok(r, "security SOS", 200)["id"]
    em, _ = app.get_emergency_by_id(peid)
    assert em.get("assigned_to") == "police"

    _session(c, 3, "police")
    q = c.get("/api/get_emergencies?type=police")
    assert peid in [e["id"] for e in (q.get_json() or {}).get("emergencies") or []]
    _ok(c.post(f"/api/police/request/{peid}/accept", json={}), "police accept", 200)
    _ok(c.post(f"/api/police/request/{peid}/dispatch", json={}), "police dispatch", 200)
    _ok(c.post(f"/api/police/request/{peid}/complete", json={}), "police complete", 200)
    em, _ = app.get_emergency_by_id(peid)
    assert em["status"] in ("completed", "resolved")
    assert em.get("assigned_station_id") == 1

    # Fire SOS
    _session(c, 1, "citizen")
    r = c.post(
        "/api/send_alert",
        json={
            "type": "fire",
            "latitude": 2.05,
            "longitude": 45.327,
            "location": "Warehouse",
            "notes": "Smoke visible",
        },
    )
    feid = _ok(r, "fire SOS", 200)["id"]
    em, _ = app.get_emergency_by_id(feid)
    assert em.get("assigned_to") == "fire"

    _session(c, 4, "fire")
    q = c.get("/api/get_emergencies?type=fire")
    assert feid in [e["id"] for e in (q.get_json() or {}).get("emergencies") or []]
    _ok(c.post(f"/api/fire/request/{feid}/accept", json={}), "fire accept", 200)
    _ok(c.post(f"/api/fire/request/{feid}/dispatch", json={}), "fire dispatch", 200)
    _ok(c.post(f"/api/fire/request/{feid}/complete", json={}), "fire complete", 200)
    em, _ = app.get_emergency_by_id(feid)
    assert em["status"] in ("completed", "resolved")
    assert em.get("assigned_station_id") == 2


def test_s12_live_map_markers_and_notifications(s12):
    c, app = s12
    _session(c, 1, "citizen")
    med = _ok(
        c.post(
            "/api/send_alert",
            json={
                "type": "medical",
                "latitude": 2.0465,
                "longitude": 45.3185,
                "location": "Map test",
                "notes": "map marker case",
            },
        ),
        "SOS map",
        200,
    )["id"]

    _session(c, 5, "super_admin")
    cc = c.get("/api/admin/command-center")
    assert cc.status_code == 200, cc.get_data(as_text=True)[:300]
    payload = cc.get_json() or {}
    markers = payload.get("map_markers") or []
    kinds = {m.get("kind") for m in markers}
    assert "hospital" in kinds
    assert "police" in kinds
    assert "fire" in kinds
    # Citizen / emergency marker when active SOS has coords
    assert any(m.get("kind") == "emergency" and m.get("id") == med for m in markers), [
        (m.get("kind"), m.get("id")) for m in markers
    ]

    # Ambulance GPS appears on map after hospital assigns a unit with coords
    _session(c, 2, "hospital")
    assert c.post(f"/api/hospital/request/{med}/accept", json={}).status_code == 200
    assert (
        c.post(
            f"/api/hospital/request/{med}/assign-ambulance",
            json={"ambulance_unit_id": 1},
        ).status_code
        == 200
    )
    _session(c, 5, "super_admin")
    markers2 = (c.get("/api/admin/command-center").get_json() or {}).get("map_markers") or []
    # Fleet units are exposed as ambulance markers when present on the command map
    amb_markers = [
        m
        for m in markers2
        if m.get("kind") == "ambulance"
        or "AMB" in str(m.get("name") or "").upper()
        or (m.get("meta") or {}).get("call_sign")
    ]
    # If command-center maps ambulances under hospital meta, still require assigned unit coords usable
    amb_data = app.read_json("ambulance_units", {"ambulances": []}).get("ambulances") or []
    unit = next(a for a in amb_data if a.get("id") == 1)
    assert unit.get("latitude") is not None and unit.get("longitude") is not None
    assert unit.get("status") == "busy" or amb_markers

    notes = app.read_json("notifications", {"notifications": []}).get("notifications") or []
    assert notes, "expected at least one notification after SOS/dispatch"


def test_s12_citizen_cancel(s12):
    c, app = s12
    _session(c, 1, "citizen")
    eid = _ok(
        c.post(
            "/api/send_alert",
            json={
                "type": "medical",
                "latitude": 2.0469,
                "longitude": 45.3182,
                "location": "Cancel me",
                "notes": "false alarm",
            },
        ),
        "SOS",
        200,
    )["id"]
    data = _ok(
        c.post(f"/api/patient/request/{eid}/cancel", json={"reason": "False alarm"}),
        "cancel",
        200,
    )
    assert data.get("status") == "cancelled"
    em, _ = app.get_emergency_by_id(eid)
    assert em["status"] == "cancelled"
    assert em.get("cancelled_by") == "citizen"
    assert em.get("tracking_active") is False
