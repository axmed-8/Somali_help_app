"""E2E: citizen security SOS must appear on police desk queue."""
import re

from tests.live_app import reload_live_app


def test_citizen_security_sos_appears_on_police_desk():
    ers = reload_live_app()
    c = ers.app.test_client()
    users = (ers.load_users() or {}).get("users") or []
    citizen = next((u for u in users if u.get("role") == "citizen" and u.get("status") == "active"), None)
    police = next(
        (u for u in users if u.get("role") == "police" and u.get("station_id") and u.get("status") == "active"),
        None,
    )
    assert citizen, "need a citizen user"
    assert police, "need a police user with station_id"

    with c.session_transaction() as s:
        s["user_id"] = citizen["id"]
        s["role"] = "citizen"
        s["logged_in"] = True
        s["name"] = citizen.get("name") or "Citizen"

    html = c.get("/dashboard").get_data(as_text=True)
    m = re.search(r'csrf-token" content="([^"]+)"', html)
    token = m.group(1) if m else ""
    assert token

    r = c.post(
        "/api/send_alert",
        json={
            "type": "security",
            "latitude": 2.0469,
            "longitude": 45.3182,
            "location": "Test Hodan Security SOS",
            "district": "Hodan",
            "name": "Test Caller",
            "phone": "0611111111",
            "notes": "Police desk visibility test",
        },
        headers={"X-CSRFToken": token},
    )
    body = r.get_json() or {}
    assert r.status_code == 200, body
    assert body.get("success") is True
    eid = body.get("id")
    assert eid

    em, _ = ers.get_emergency_by_id(eid)
    assert em is not None
    assert em.get("assigned_to") == "police"
    assert (em.get("type") or "") == "security"
    assert (em.get("status") or "") in ("pending", "accepted", "dispatched")

    with c.session_transaction() as s:
        s["user_id"] = police["id"]
        s["role"] = "police"
        s["logged_in"] = True
        s["name"] = police.get("name") or "Police"

    html2 = c.get("/police").get_data(as_text=True)
    m2 = re.search(r'csrf-token" content="([^"]+)"', html2)
    token2 = m2.group(1) if m2 else ""

    q = c.get("/api/get_emergencies?type=police")
    qbody = q.get_json() or {}
    assert q.status_code == 200, qbody
    ids = [e.get("id") for e in (qbody.get("emergencies") or [])]
    assert eid in ids, {
        "eid": eid,
        "ids": ids,
        "em_sid": em.get("assigned_station_id"),
        "police_sid": police.get("station_id"),
        "message": qbody.get("message"),
    }

    acc = c.post(
        f"/api/police/request/{eid}/accept",
        json={},
        headers={"X-CSRFToken": token2},
    )
    assert acc.status_code == 200, acc.get_json()
    assert (acc.get_json() or {}).get("success") is True
