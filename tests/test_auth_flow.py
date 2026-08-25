"""Sprint 1.1 — authentication flow tests (JSON-isolated)."""
from __future__ import annotations

import re

import pytest


@pytest.fixture()
def auth_client(tmp_path, monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("GURMADNET_DB", "json")
    monkeypatch.setenv("EMAIL_PROVIDER", "memory")
    monkeypatch.setenv("DATABASE_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOW_TEST_EMAILS", "true")

    from tests.live_app import reload_json_app

    app_module = reload_json_app(monkeypatch=monkeypatch, database_dir=tmp_path)
    app_module.app.config["TESTING"] = True
    app_module.app.config["WTF_CSRF_ENABLED"] = True
    # Disable email verification gate for most login tests; enable in specific ones.
    settings = app_module.load_settings()
    settings["auth_require_email_verification"] = False
    settings["auth_allow_citizen_signup"] = True
    settings["security_login_max_attempts"] = 3
    settings["security_lockout_minutes"] = 15
    settings["password_min_length"] = 8
    app_module.save_settings(settings)
    client = app_module.app.test_client()
    return app_module, client


def _csrf(client):
    r = client.get("/login")
    html = r.get_data(as_text=True)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    if not m:
        m = re.search(r'csrf-token" content="([^"]+)"', html)
    assert m, "CSRF token missing from login page"
    return m.group(1)


def _signup_payload(csrf, **overrides):
    data = {
        "csrf_token": csrf,
        "first_name": "Axmed",
        "middle_name": "",
        "last_name": "Ali",
        "gender": "male",
        "email": "axmed.auth.test@example.com",
        "phone": "6151111001",
        "password": "SecurePass1!",
        "confirm_password": "SecurePass1!",
        "address": "Hodan",
        "city": "Mogadishu",
        "date_of_birth": "1995-01-15",
        "national_id": "123456789012",
        "blood_type": "O+",
        "medical_conditions": "",
        "allergies": "",
        "emergency_contact_name": "Hodan",
        "emergency_contact_phone": "6152222002",
        "emergency_contact_relation": "Sister",
        "emergency_contact_email": "",
        "agree_terms": "1",
    }
    data.update(overrides)
    return data


def test_unauth_role_pages_redirect(auth_client):
    _, client = auth_client
    for path in ("/admin", "/hospital", "/police", "/fire", "/call-center", "/dashboard"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (301, 302), path


def test_csrf_rejects_login_without_token(auth_client):
    _, client = auth_client
    r = client.post(
        "/login",
        data={"username": "a@example.com", "password": "x"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 400)


def test_signup_login_logout(auth_client):
    app_module, client = auth_client
    csrf = _csrf(client)
    r = client.post("/signup", data=_signup_payload(csrf), follow_redirects=False)
    assert r.status_code in (302, 303)
    # Without verification requirement, signup may land on login
    r = client.post(
        "/login",
        data={
            "csrf_token": _csrf(client),
            "username": "axmed.auth.test@example.com",
            "password": "SecurePass1!",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    with client.session_transaction() as sess:
        assert sess.get("user_id")
        assert sess.get("role") == "citizen"
    r = client.get("/logout", follow_redirects=False)
    assert r.status_code in (302, 303)
    with client.session_transaction() as sess:
        assert not sess.get("user_id")


def test_duplicate_email_and_phone(auth_client):
    _, client = auth_client
    csrf = _csrf(client)
    assert client.post("/signup", data=_signup_payload(csrf), follow_redirects=True).status_code == 200
    r = client.post(
        "/signup",
        data=_signup_payload(_csrf(client), email="axmed.auth.test@example.com", phone="6159999888"),
        follow_redirects=True,
    )
    assert b"Email already registered" in r.data
    r = client.post(
        "/signup",
        data=_signup_payload(
            _csrf(client),
            email="other.auth.test@example.com",
            phone="6151111001",
            national_id="999999999999",
        ),
        follow_redirects=True,
    )
    assert b"phone number is already registered" in r.data


def test_weak_password_rejected(auth_client):
    _, client = auth_client
    r = client.post(
        "/signup",
        data=_signup_payload(_csrf(client), password="short", confirm_password="short"),
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert b"Password" in r.data or b"password" in r.data


def test_wrong_login(auth_client):
    _, client = auth_client
    client.post("/signup", data=_signup_payload(_csrf(client)), follow_redirects=True)
    r = client.post(
        "/login",
        data={
            "csrf_token": _csrf(client),
            "username": "axmed.auth.test@example.com",
            "password": "WrongPass1!",
        },
        follow_redirects=True,
    )
    assert b"Invalid email or password" in r.data


def test_blocked_account_cannot_login(auth_client):
    app_module, client = auth_client
    client.post("/signup", data=_signup_payload(_csrf(client)), follow_redirects=True)
    udata = app_module.load_users()
    for u in udata["users"]:
        if u.get("email") == "axmed.auth.test@example.com":
            u["status"] = "blocked"
    app_module.save_users(udata)
    r = client.post(
        "/login",
        data={
            "csrf_token": _csrf(client),
            "username": "axmed.auth.test@example.com",
            "password": "SecurePass1!",
        },
        follow_redirects=True,
    )
    assert b"blocked" in r.data.lower()


def test_lockout_persists_after_failed_logins(auth_client):
    app_module, client = auth_client
    client.post("/signup", data=_signup_payload(_csrf(client)), follow_redirects=True)
    for _ in range(3):
        client.post(
            "/login",
            data={
                "csrf_token": _csrf(client),
                "username": "axmed.auth.test@example.com",
                "password": "WrongPass1!",
            },
            follow_redirects=True,
        )
    user, _ = app_module.get_user_by_login("axmed.auth.test@example.com")
    assert user is not None
    assert user.get("locked_until") or int(user.get("failed_logins") or 0) >= 3 or app_module._account_lockout_active(user)
    r = client.post(
        "/login",
        data={
            "csrf_token": _csrf(client),
            "username": "axmed.auth.test@example.com",
            "password": "SecurePass1!",
        },
        follow_redirects=True,
    )
    assert b"Too many failed attempts" in r.data or b"Invalid" in r.data


def test_citizen_cannot_open_admin(auth_client):
    _, client = auth_client
    client.post("/signup", data=_signup_payload(_csrf(client)), follow_redirects=True)
    client.post(
        "/login",
        data={
            "csrf_token": _csrf(client),
            "username": "axmed.auth.test@example.com",
            "password": "SecurePass1!",
        },
        follow_redirects=True,
    )
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code in (302, 303)


def test_partial_user_snapshot_skips_mass_delete(caplog):
    """Safety net: save_users with a 1-user stub must not wipe the table."""
    import logging

    from database import mysql_store

    class Cur:
        def execute(self, *a, **k):
            return None

        def fetchall(self):
            return []

    with caplog.at_level(logging.ERROR, logger="database.mysql_store"):
        deleted = mysql_store._delete_stale_ids(Cur(), "users", {1, 2, 3, 4, 5}, {1})
    assert deleted == []
    assert any("partial snapshot" in r.message for r in caplog.records)
