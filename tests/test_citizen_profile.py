"""Citizen profile self-management: password change."""
import re

from tests.live_app import reload_live_app
from werkzeug.security import check_password_hash, generate_password_hash


def test_citizen_change_password():
    ers = reload_live_app()
    c = ers.app.test_client()
    users = (ers.load_users() or {}).get("users") or []
    citizen = next((u for u in users if u.get("role") == "citizen" and u.get("status") == "active"), None)
    assert citizen

    original_hash = citizen.get("password_hash")
    tmp_pw = "TempPass9!"
    udata = ers.load_users()
    for u in udata["users"]:
        if u["id"] == citizen["id"]:
            u["password_hash"] = generate_password_hash(tmp_pw)
            break
    ers.save_users(udata)

    try:
        with c.session_transaction() as s:
            s["user_id"] = citizen["id"]
            s["role"] = "citizen"
            s["logged_in"] = True
            s["name"] = citizen.get("name") or "Citizen"

        html = c.get("/dashboard").get_data(as_text=True)
        assert "password-form" in html
        assert "Change profile photo" in html
        assert "panel-profile" in html
        assert "Personal Information" in html
        token = re.search(r'csrf-token" content="([^"]+)"', html).group(1)

        bad = c.post(
            "/api/user/password",
            json={
                "current_password": "wrong",
                "new_password": "NewerPass9!",
                "confirm_password": "NewerPass9!",
            },
            headers={"X-CSRFToken": token},
        )
        assert bad.status_code == 400

        new_pw = "NewerPass9!"
        ok = c.post(
            "/api/user/password",
            json={
                "current_password": tmp_pw,
                "new_password": new_pw,
                "confirm_password": new_pw,
            },
            headers={"X-CSRFToken": token},
        )
        body = ok.get_json() or {}
        assert ok.status_code == 200, body
        assert body.get("success") is True

        refreshed = next(u for u in (ers.load_users() or {}).get("users") or [] if u["id"] == citizen["id"])
        assert check_password_hash(refreshed["password_hash"], new_pw)
    finally:
        udata = ers.load_users()
        for u in udata["users"]:
            if u["id"] == citizen["id"]:
                u["password_hash"] = original_hash
                break
        ers.save_users(udata)
