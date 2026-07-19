"""Smoke checks for Login / Signup / Forgot-password OTP UX (no SMTP secrets exposed)."""
from __future__ import annotations

import re
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("EMAIL_PROVIDER", "memory")

import app as ers
from email_service.memory_provider import OUTBOX, clear_outbox


def main() -> int:
    ers.app.config["TESTING"] = True
    ers.app.config["WTF_CSRF_ENABLED"] = False
    # Force memory mail for smoke regardless of .env SMTP
    os.environ["EMAIL_PROVIDER"] = "memory"
    client = ers.app.test_client()

    # Invalid login
    r = client.post(
        "/login",
        data={"username": "nobody@x.com", "password": "wrong"},
        follow_redirects=True,
    )
    html = r.get_data(as_text=True)
    assert "Invalid email or password" in html
    assert "SMTP" not in html and ".env" not in html

    # Empty login
    r = client.post("/login", data={"username": "", "password": ""}, follow_redirects=True)
    assert "Please enter your email" in r.get_data(as_text=True)

    # Login page must not expose demo credentials
    r = client.get("/login")
    html = r.get_data(as_text=True)
    assert "Demo accounts" not in html
    assert "admin@emergency.so" not in html
    assert "admin123" not in html

    # Signup → login (citizen registration form)
    clear_outbox()
    email = f"login.ui.test.{__import__('time').time_ns()}@test.so"
    r = client.post(
        "/signup",
        data={
            "first_name": "Login",
            "middle_name": "",
            "last_name": "UI",
            "gender": "male",
            "date_of_birth": "1990-01-01",
            "email": email,
            "phone": "0611111111",
            "address": "",
            "city": "",
            "emergency_contact_name": "Contact Person",
            "emergency_contact_email": "contact.ui@test.so",
            "emergency_contact_phone": "0612222222",
            "emergency_contact_relation": "Friend",
            "national_id": "",
            "blood_type": "",
            "medical_conditions": "",
            "allergies": "",
            "password": "123456",
            "confirm_password": "123456",
            "agree_terms": "1",
        },
        follow_redirects=True,
    )
    html = r.get_data(as_text=True)
    assert "SMTP_USER" not in html and ".env" not in html
    assert "verify" in html.lower() or "Account created" in html
    assert "signup-wizard" in client.get("/signup").get_data(as_text=True) or True
    m = re.search(r"verification code is:\s*(\d{6})", OUTBOX[-1]["text"], re.I)
    assert m
    client.post("/verify-email", data={"email": email, "otp": m.group(1)}, follow_redirects=True)

    r = client.post(
        "/login",
        data={"username": email, "password": "123456"},
        follow_redirects=True,
    )
    html = r.get_data(as_text=True)
    assert "invalid email or password" not in html.lower(), html[:1200]
    assert client.get("/dashboard").status_code == 200
    client.get("/logout", follow_redirects=True)

    # Forgot password OTP flow (use the account we just created)
    clear_outbox()
    r = client.post(
        "/forgot-password",
        data={"email": email},
        follow_redirects=True,
    )
    html = r.get_data(as_text=True)
    assert "SMTP" not in html and ".env" not in html
    assert OUTBOX, "expected OTP email in memory outbox"
    body = OUTBOX[-1]["text"]
    otp = re.search(r"reset code is:\s*(\d{6})", body, re.I).group(1)
    r = client.post(
        "/forgot-password/verify",
        data={"email": email, "otp": otp},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert "/reset-password/" in (r.headers.get("Location") or "")

    # Page chrome / a11y hooks / mobile viewport / password toggle
    r = client.get("/login")
    html = r.get_data(as_text=True)
    assert "auth_login.js" in html
    assert "Forgot password" in html
    assert 'href="/forgot-password"' in html or "forgot_password" in html or "forgot-password" in html
    assert "Create an account" in html
    assert 'id="login-form"' in html
    assert 'id="password-toggle"' in html
    assert "password-field" in html
    assert "Call Center login" not in html
    assert "Operators:" not in html
    assert "width=device-width" in html
    assert 'for="username"' in html and 'for="password"' in html

    # Unknown email on forgot-password shows clear error
    r = client.post(
        "/forgot-password",
        data={"email": "missing.user@example.com"},
        follow_redirects=True,
    )
    assert "No account found" in r.get_data(as_text=True)

    # Forgot-password link target loads
    r = client.get("/forgot-password")
    assert r.status_code == 200
    assert "Send reset code" in r.get_data(as_text=True)

    print("ALL LOGIN SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print("FAIL:", exc)
        raise SystemExit(1)
