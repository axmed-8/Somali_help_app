"""Call Center Sprint 1 — Live Emergency Queue (cards + KPIs, existing APIs only)."""
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
    now = "2026-08-06 17:00:00"

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
                    "email": "citizen.cc@example.com",
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
                    "email": "operator.cc@example.com",
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


def test_live_queue_ui_has_sprint1_fields_and_actions():
    js = (ROOT / "static" / "js" / "call_center.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "call_center.css").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "call_center_dashboard.html").read_text(encoding="utf-8")

    assert "cc-q-fields" in js and "cc-q-fields" in css
    assert "cc-q-citizen" in js
    assert ">Priority<" in js
    assert ">Received<" in js
    assert ">Wait<" in js
    assert "data-voice-accept" in js
    assert "data-voice-reject" in js
    assert 'data-open="' in js
    assert 'data-cancel="' in js
    assert "markSelectedQueueCard" in js
    assert "is-selected" in css
    assert "cc-kpi-strip" in html and "cc-kpi-strip" in css
    assert 'id="kpi-waiting"' in html
    assert "Live Emergency Queue" in html
    assert "call_center.js" in html and any(v in html for v in ("v=cc-s1", "v=cc-s2", "v=cc-s3", "v=cc-s4"))
    assert "call_center.css" in html and any(v in html for v in ("v=cc-s1", "v=cc-s2", "v=cc-s3", "v=cc-s4"))


def test_live_queue_api_accept_open_cancel(cc):
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
    body = r.get_json()
    assert body.get("success") is True
    call_id = body["call_id"]

    _session(c, 2, "call_center", "Operator CC")
    live = c.get("/api/call-center/live")
    assert live.status_code == 200, live.get_json()
    live_body = live.get_json()
    assert live_body.get("success") is not False
    calls = live_body.get("calls") or []
    ids = [x.get("id") for x in calls]
    assert call_id in ids

    hit = next(x for x in calls if x.get("id") == call_id)
    assert hit.get("status") in ("ringing", "accepted", "connecting", "connected")
    assert hit.get("caller_name") or hit.get("phone")
    assert hit.get("latitude") is not None
    assert "stats" in live_body
    stats = live_body["stats"] or {}
    for key in (
        "operators_online",
        "incoming_calls",
        "calls_waiting",
        "calls_in_progress",
        "resolved_today",
        "avg_response_minutes",
    ):
        assert key in stats

    # Answer (Accept path) — existing API
    ans = c.post(f"/api/call-center/calls/{call_id}/answer", json={})
    assert ans.status_code == 200, ans.get_json()
    assert (ans.get_json().get("call") or {}).get("status") in (
        "answered",
        "accepted",
        "connecting",
        "connected",
        "in_progress",
    )

    # Open detail (used by Open button)
    detail = c.get(f"/api/call-center/calls/{call_id}")
    assert detail.status_code == 200, detail.get_json()
    assert detail.get_json().get("success") is not False
    assert (detail.get_json().get("call") or {}).get("id") == call_id

    # Cancel
    cancel = c.post(f"/api/call-center/calls/{call_id}/cancel", json={})
    assert cancel.status_code == 200, cancel.get_json()
    assert cancel.get_json().get("success") is not False
