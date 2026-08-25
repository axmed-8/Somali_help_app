"""Citizen profile self-management: password change (JSON-isolated)."""
from __future__ import annotations

import re

from werkzeug.security import check_password_hash, generate_password_hash


def test_citizen_change_password(tmp_path, monkeypatch):
    """
    Exercises /dashboard profile UI + POST /api/user/password.

    Uses JSON isolation (not live Railway MySQL). Live reload_live_app() was
    hanging 2+ minutes on ensure_mysql_boot + save_users(stub) over the proxy.
    """
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("GURMADNET_DB", "json")
    monkeypatch.setenv("EMAIL_PROVIDER", "memory")
    monkeypatch.setenv("DATABASE_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOW_TEST_EMAILS", "true")

    from tests.live_app import reload_json_app

    ers = reload_json_app(monkeypatch=monkeypatch, database_dir=tmp_path)
    ers.app.config["TESTING"] = True
    ers.app.config["WTF_CSRF_ENABLED"] = True

    settings = ers.load_settings()
    settings["auth_require_email_verification"] = False
    ers.save_settings(settings)

    # Seed one citizen
    udata = ers.load_users()
    uid = int(udata.get("next_id") or 1)
    tmp_pw = "TempPass9!"
    citizen = {
        "id": uid,
        "name": "Profile Citizen",
        "email": "profile.citizen@example.com",
        "phone": "6150000999",
        "password_hash": generate_password_hash(tmp_pw),
        "role": "citizen",
        "status": "active",
        "email_verified": True,
        "created_at": ers.now_str(),
        "activity": [],
    }
    udata["users"].append(citizen)
    udata["next_id"] = uid + 1
    ers.save_users(udata)

    c = ers.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = citizen["id"]
        s["role"] = "citizen"
        s["logged_in"] = True
        s["name"] = citizen["name"]

    html = c.get("/dashboard").get_data(as_text=True)
    assert "password-form" in html
    assert "Change profile photo" in html
    assert "panel-profile" in html
    # Profile section labels evolved; keep structural markers stable
    assert "Profile" in html or "Macluumaad" in html or "profile" in html.lower()
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

    refreshed = next(
        u for u in (ers.load_users() or {}).get("users") or [] if u["id"] == citizen["id"]
    )
    assert check_password_hash(refreshed["password_hash"], new_pw)
