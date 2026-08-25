"""Sprint 2.1B.1 — unique system chat messages (no duplicate auto welcomes)."""
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
    import hospital_logic as hl

    app_module.DATABASE_DIR = str(tmp_path)
    app_module.configure_hospital_db(str(tmp_path))
    app_module.app.config["TESTING"] = True
    app_module.app.config["WTF_CSRF_ENABLED"] = False
    now = "2026-08-03 12:00:00"
    app_module.save_json("hospitals", {"hospitals": [], "next_id": 1})
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
                },
                {
                    "id": 2,
                    "name": "Hospital Desk",
                    "email": "hospital@example.com",
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
    app_module.save_json(
        "emergencies",
        {
            "emergencies": [
                {
                    "id": 1,
                    "user_id": 1,
                    "type": "medical",
                    "status": "accepted",
                    "assigned_to": "hospital",
                    "assigned_hospital_id": 1,
                    "timestamp": now,
                    "latitude": 2.0469,
                    "longitude": 45.3182,
                    "status_history": [],
                    "responder_status": {},
                }
            ],
            "next_id": 2,
        },
    )
    app_module.save_json("messages", {"messages": [], "next_id": 1})
    app_module.save_json("notifications", {"notifications": [], "next_id": 1})
    with app_module.app.test_client() as c:
        yield c, app_module, hl


def test_unique_system_message_dedupes(client):
    _, ers, hl = client
    welcome = "Hospital-ku waa aqbalay. / We accepted your request."
    m1 = hl.add_message(
        ers.read_json,
        ers.save_json,
        1,
        "hospital",
        2,
        welcome,
        unique_system=True,
    )
    m2 = hl.add_message(
        ers.read_json,
        ers.save_json,
        1,
        "hospital",
        2,
        welcome,
        unique_system=True,
    )
    assert m1["id"] == m2["id"]
    msgs = hl.get_messages_for_request(ers.read_json, ers.save_json, 1)
    assert len(msgs) == 1


def test_user_messages_are_never_deduped(client):
    _, ers, hl = client
    text = "I need help now"
    hl.add_message(ers.read_json, ers.save_json, 1, "citizen", 1, text)
    hl.add_message(ers.read_json, ers.save_json, 1, "citizen", 1, text)
    msgs = hl.get_messages_for_request(ers.read_json, ers.save_json, 1)
    assert len(msgs) == 2


def test_api_unique_system_flag_dedupes_welcome(client):
    c, ers, _ = client
    welcome = (
        "Hospital-ku waa aqbalay codsigaaga. Halkan nala soo hadal. "
        "/ We accepted your request — message us here."
    )
    with c.session_transaction() as s:
        s["user_id"] = 2
        s["role"] = "hospital"
        s["logged_in"] = True
        s["name"] = "Hospital Desk"

    r1 = c.post("/api/messages/1", json={"text": welcome, "unique_system": True})
    r2 = c.post("/api/messages/1", json={"text": welcome, "unique_system": True})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert (r1.get_json() or {}).get("message", {}).get("id") == (
        r2.get_json() or {}
    ).get("message", {}).get("id")

    with c.session_transaction() as s:
        s["user_id"] = 1
        s["role"] = "citizen"
        s["logged_in"] = True
        s["name"] = "Citizen"

    listed = c.get("/api/messages/1")
    body = listed.get_json() or {}
    assert listed.status_code == 200
    assert len(body.get("messages") or []) == 1
