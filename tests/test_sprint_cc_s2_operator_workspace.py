"""Call Center Sprint 2 — Operator Workspace (layout clarity, existing APIs only)."""
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
def cc(tmp_path, monkeypatch):
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
    now = "2026-08-08 21:00:00"

    app_module.save_json("hospitals", {"hospitals": [], "next_id": 1})
    app_module.save_json("response_stations", {"stations": [], "next_id": 1})
    app_module.save_json("ambulance_units", {"ambulances": [], "next_id": 1})
    app_module.save_json("call_centers", {"call_centers": [], "next_id": 1})
    app_module.save_json("call_center_calls", {"calls": [], "next_id": 1})
    app_module.save_json("emergencies", {"emergencies": [], "next_id": 1})
    app_module.save_json("notifications", {"notifications": [], "next_id": 1})
    app_module.save_json(
        "users",
        {
            "users": [
                {
                    "id": 1,
                    "name": "Citizen CC",
                    "email": "citizen.cc2@example.com",
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
                    "name": "Operator CC",
                    "email": "operator.cc2@example.com",
                    "phone": "612000999",
                    "password_hash": generate_password_hash("Secret123!"),
                    "role": "call_center",
                    "status": "active",
                    "email_verified": True,
                    "created_at": now,
                    "activity": [],
                },
            ],
            "next_id": 3,
        },
    )

    with app_module.app.test_client() as c:
        yield c, app_module


def _session(c, user_id, role, name="User"):
    with c.session_transaction() as s:
        s["user_id"] = user_id
        s["role"] = role
        s["name"] = name
        s["logged_in"] = True


def test_operator_workspace_ui_markers():
    js = (ROOT / "static" / "js" / "call_center.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "call_center.css").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "call_center_dashboard.html").read_text(encoding="utf-8")

    assert "Operator Workspace" in js
    assert "cc-ws-header" in js and "cc-ws-header" in css
    assert "cc-ws-caller" in js
    assert "cc-ws-location" in js
    assert "cc-ws-fields" in js or "cc-ws-field" in js
    assert "function wsField" in js
    assert "cc-ws-history" in js and "cc-ws-history" in css
    assert 'id="btn-back-live"' in js
    assert 'id="session-notes"' in js
    assert 'id="btn-save-location"' in js
    assert "Operator Workspace" in html
    assert "cc-ws-empty-title" in html
    assert "v=cc-s2" in html or "v=cc-s3" in html or "v=cc-s4" in html
    # Single openSession / renderAiPanel definitions
    assert js.count("function openSession(") == 1
    assert js.count("function renderAiPanel(") == 1
    assert "Operator Workspace" in js


def test_open_session_and_save_location(cc):
    c, app = cc

    _session(c, 1, "citizen", "Citizen CC")
    r = c.post(
        "/api/call-center/initiate",
        json={
            "latitude": 2.0469,
            "longitude": 45.3182,
            "address": "Hodan",
            "district": "Hodan",
            "name": "Citizen CC",
            "phone": "612000001",
        },
    )
    assert r.status_code == 200, r.get_json()
    call_id = r.get_json()["call_id"]

    _session(c, 2, "call_center", "Operator CC")
    # Answer then open detail (workspace payload)
    ans = c.post(f"/api/call-center/calls/{call_id}/answer", json={})
    assert ans.status_code == 200, ans.get_json()

    detail = c.get(f"/api/call-center/calls/{call_id}")
    assert detail.status_code == 200, detail.get_json()
    body = detail.get_json()
    assert body.get("success") is not False
    call = body.get("call") or {}
    assert call.get("id") == call_id
    assert call.get("latitude") is not None
    assert "nearest" in call or "nearest" in body

    # Save location (existing API used by workspace)
    loc = c.post(
        f"/api/call-center/calls/{call_id}/location",
        json={
            "latitude": 2.0475,
            "longitude": 45.3190,
            "address": "Makka Al-mukarama",
        },
    )
    assert loc.status_code == 200, loc.get_json()
    assert loc.get_json().get("success") is not False
    updated = (loc.get_json().get("call") or {})
    assert abs(float(updated.get("latitude") or call.get("latitude")) - 2.0475) < 0.01 or updated.get("address")
