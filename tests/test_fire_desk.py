"""Fire desk: citizen fire SOS appears on fire station queue."""
import re

from tests.live_app import reload_live_app


def test_citizen_fire_sos_appears_on_fire_desk():
    ers = reload_live_app()
    c = ers.app.test_client()
    users = (ers.load_users() or {}).get("users") or []
    citizen = next((u for u in users if u.get("role") == "citizen" and u.get("status") == "active"), None)
    fire = next(
        (u for u in users if u.get("role") == "fire" and u.get("station_id") and u.get("status") == "active"),
        None,
    )
    assert citizen and fire

    with c.session_transaction() as s:
        s["user_id"] = citizen["id"]
        s["role"] = "citizen"
        s["logged_in"] = True
        s["name"] = citizen.get("name") or "Citizen"

    html = c.get("/dashboard").get_data(as_text=True)
    token = re.search(r'csrf-token" content="([^"]+)"', html).group(1)

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
        headers={"X-CSRFToken": token},
    )
    body = r.get_json() or {}
    assert r.status_code == 200 and body.get("success")
    eid = body["id"]
    em, _ = ers.get_emergency_by_id(eid)
    assert em.get("assigned_to") == "fire"

    with c.session_transaction() as s:
        s["user_id"] = fire["id"]
        s["role"] = "fire"
        s["logged_in"] = True

    html2 = c.get("/fire").get_data(as_text=True)
    assert "Fire Command" in html2
    assert "fire_command.js" in html2
    assert "hcc-chat-shell" in html2
    token2 = re.search(r'csrf-token" content="([^"]+)"', html2).group(1)

    q = c.get("/api/get_emergencies?type=fire")
    ids = [e.get("id") for e in (q.get_json() or {}).get("emergencies") or []]
    assert eid in ids

    acc = c.post(f"/api/fire/request/{eid}/accept", json={}, headers={"X-CSRFToken": token2})
    assert acc.status_code == 200 and (acc.get_json() or {}).get("success")
