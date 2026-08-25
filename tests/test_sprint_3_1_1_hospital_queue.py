"""Sprint 3.1.1 — Hospital Command pending queue (accept/reject/call/chat, no assign)."""
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
    now = "2026-08-06 12:00:00"

    app_module.save_json(
        "hospitals",
        {
            "hospitals": [
                {
                    "id": 1,
                    "name": "HCC Test Hospital",
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
                    "name": "Citizen HCC",
                    "email": "citizen.hcc@example.com",
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
                    "name": "Hospital HCC",
                    "email": "hospital.hcc@example.com",
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


def test_queue_ui_has_sprint_311_fields_and_actions():
    js = (ROOT / "static" / "js" / "hospital_command.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "hospital_command.css").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "hospital_dashboard.html").read_text(encoding="utf-8")

    assert "Sprint 3.1.1" in js
    assert "hcc-q-fields" in js and "hcc-q-fields" in css
    assert ">Priority<" in js
    assert ">Received<" in js
    assert ">Distance<" in js
    assert ">Type<" in js
    assert "hcc-q-citizen" in js
    assert 'class="hcc-btn hcc-btn-success hcc-btn-sm q-accept"' in js
    assert 'class="hcc-btn hcc-btn-danger hcc-btn-sm q-reject"' in js
    assert 'class="hcc-btn hcc-btn-ghost hcc-btn-sm q-call"' in js
    assert 'class="hcc-btn hcc-btn-primary hcc-btn-sm q-chat"' in js
    assert "/api/hospital/request/" in js and "/accept" in js
    assert 'openAssignModal(parseInt(b.getAttribute("data-id"), 10), "accept")' not in js
    assert "q-assign" not in js.split("function bindQueueActions")[1].split("function renderQueue")[0]
    assert "hospital_command.js" in html and (
        "v=hcc-311" in html or "v=hcc-312" in html or "v=hcc-313" in html
    )


def test_pending_queue_accept_reject_chat_without_ambulance(hcc):
    c, app = hcc

    _session(c, 1, "citizen", "Citizen HCC")
    r = c.post(
        "/api/send_alert",
        json={
            "type": "medical",
            "latitude": 2.05,
            "longitude": 45.32,
            "location": "Hodan market",
            "name": "Citizen HCC",
            "phone": "612000001",
            "notes": "Chest pain — queue sprint",
        },
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    eid = body["id"]
    em, _ = app.get_emergency_by_id(eid)
    assert em["status"] in ("pending_hospital", "pending")
    assert em.get("assigned_hospital_id") == 1

    _session(c, 2, "hospital", "Hospital HCC")
    q = c.get("/api/get_emergencies?type=medical")
    assert q.status_code == 200
    ids = [e["id"] for e in (q.get_json() or {}).get("emergencies") or []]
    assert eid in ids

    pending = [
        e
        for e in (q.get_json() or {}).get("emergencies") or []
        if e["id"] == eid
    ][0]
    assert pending.get("type") == "medical"
    assert pending.get("phone") == "612000001"
    assert pending.get("timestamp")
    assert pending.get("latitude") is not None

    # Accept without ambulance unit (Sprint 3.1.1)
    r = c.post(f"/api/hospital/request/{eid}/accept", json={})
    assert r.status_code == 200, r.get_json()
    em, _ = app.get_emergency_by_id(eid)
    assert em["status"] == "accepted"
    assert not em.get("assigned_ambulance_id")

    # Chat via existing messages API
    r = c.post(f"/api/messages/{eid}", json={"text": "Hospital desk: we accepted your request"})
    assert r.status_code == 200, r.get_json()
    r = c.get(f"/api/messages/{eid}")
    assert r.status_code == 200
    msgs = (r.get_json() or {}).get("messages") or r.get_json() or []
    if isinstance(msgs, dict):
        msgs = msgs.get("messages") or []
    assert any("accepted your request" in str(m.get("text") or m.get("message") or "") for m in msgs)

    # Fresh pending case for reject
    _session(c, 1, "citizen", "Citizen HCC")
    r = c.post(
        "/api/send_alert",
        json={
            "type": "medical",
            "latitude": 2.046,
            "longitude": 45.318,
            "location": "Waberi",
            "notes": "reject queue case",
        },
    )
    eid2 = r.get_json()["id"]
    _session(c, 2, "hospital", "Hospital HCC")
    r = c.post(f"/api/hospital/request/{eid2}/reject", json={})
    assert r.status_code == 200, r.get_json()
    assert r.get_json().get("success") is True
    em2, _ = app.get_emergency_by_id(eid2)
    # Reject either escalates away from this hospital or closes the pending accept path
    assert em2.get("status") != "accepted" or em2.get("assigned_hospital_id") != 1
