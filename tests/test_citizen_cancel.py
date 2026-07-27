"""Citizen can cancel their own active emergency."""
import re

from tests.live_app import reload_live_app


def test_citizen_can_cancel_own_emergency():
    ers = reload_live_app()
    c = ers.app.test_client()
    users = (ers.load_users() or {}).get("users") or []
    citizen = next((u for u in users if u.get("role") == "citizen" and u.get("status") == "active"), None)
    assert citizen, "need a citizen user"

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
            "type": "medical",
            "latitude": 2.0469,
            "longitude": 45.3182,
            "location": "Cancel test Mogadishu",
            "district": "Hodan",
            "name": "Cancel Tester",
            "phone": "0612222222",
            "notes": "Citizen cancel flow test",
        },
        headers={"X-CSRFToken": token},
    )
    body = r.get_json() or {}
    assert r.status_code == 200, body
    assert body.get("success") is True
    eid = body.get("id")
    assert eid

    cancel = c.post(
        f"/api/patient/request/{eid}/cancel",
        json={"reason": "False alarm"},
        headers={"X-CSRFToken": token},
    )
    cbody = cancel.get_json() or {}
    assert cancel.status_code == 200, cbody
    assert cbody.get("success") is True
    assert cbody.get("status") == "cancelled"

    em, _ = ers.get_emergency_by_id(eid)
    assert em is not None
    assert (em.get("status") or "").lower() == "cancelled"
    assert em.get("cancelled_by") == "citizen"
    assert em.get("tracking_active") is False

    again = c.post(
        f"/api/patient/request/{eid}/cancel",
        json={},
        headers={"X-CSRFToken": token},
    )
    again_body = again.get_json() or {}
    assert again.status_code == 400
    assert again_body.get("success") is False
