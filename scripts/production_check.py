"""
Production readiness checks for GurmadNet.
Run: python scripts/production_check.py
Exit 0 only when all critical checks pass.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load .env
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

passed = 0
failed = 0
warnings = []


def ok(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS - {name}")
    else:
        failed += 1
        print(f"FAIL - {name} :: {detail}")


def warn(name, msg):
    warnings.append(f"{name}: {msg}")
    print(f"WARN - {name} :: {msg}")


def main():
    secret = (os.environ.get("SECRET_KEY") or "").strip()
    ok(
        "SECRET_KEY set and strong",
        bool(secret)
        and len(secret) >= 32
        and secret not in {"change-me-in-production", "changeme", "secret"},
        "Set a long random SECRET_KEY in .env",
    )

    ok(
        "Flask-WTF installed",
        True,
    )
    try:
        import flask_wtf  # noqa: F401
    except ImportError:
        ok("Flask-WTF installed", False, "pip install Flask-WTF")

    csrf_flag = os.environ.get("WTF_CSRF_ENABLED", "true").lower()
    ok(
        "CSRF enabled in env",
        csrf_flag not in ("0", "false", "no", "off"),
        "Set WTF_CSRF_ENABLED=true",
    )

    db_mode = (os.environ.get("GURMADNET_DB") or "").lower()
    ok("GURMADNET_DB=mysql", db_mode == "mysql", f"got {db_mode!r}")

    cfg_path = ROOT / "database" / "db_config.env"
    ok("MySQL db_config.env exists", cfg_path.exists())

    mysql_ok = False
    try:
        from database.connection import load_config, reset_config
        from database import mysql_store

        reset_config()
        cfg = load_config()
        ok("MySQL password configured", bool(cfg.get("password")) and cfg.get("password") not in ("", "YOUR_PASSWORD", "your_password_here"))
        conn = mysql_store.connect()
        conn.close()
        mysql_ok = True
        ok("MySQL connection", True)
    except Exception as exc:
        ok("MySQL connection", False, str(exc))

    if mysql_ok:
        try:
            from database import mysql_store

            mysql_store.ensure_call_center_schema()
            mysql_store.ensure_ai_schema()
            mysql_store.ensure_email_verification_schema()
            ok("MySQL schema ensure", True)
        except Exception as exc:
            ok("MySQL schema ensure", False, str(exc))

    # SMTP
    provider = (os.environ.get("EMAIL_PROVIDER") or "").lower()
    user = (os.environ.get("SMTP_USER") or "").strip()
    password = (os.environ.get("SMTP_PASSWORD") or "").strip()
    from_addr = (os.environ.get("SMTP_FROM") or "").strip()
    ok("EMAIL_PROVIDER=smtp", provider in ("smtp", "gmail"), f"got {provider!r}")
    ok("SMTP_USER set", bool(user) and "your.address" not in user)
    ok("SMTP_FROM set", bool(from_addr) and "your.address" not in from_addr)
    ok(
        "SMTP_PASSWORD set (Gmail App Password)",
        bool(password) and "your_gmail" not in password.lower() and len(password) >= 8,
        "Paste a Gmail App Password into SMTP_PASSWORD in .env",
    )

    if password:
        try:
            from email_service.factory import get_email_provider, clear_email_provider_cache

            clear_email_provider_cache()
            result = get_email_provider("smtp").verify_connection()
            ok("SMTP connection verify", bool(result.get("success")), str(result.get("error") or result))
        except Exception as exc:
            ok("SMTP connection verify", False, str(exc))
    else:
        warn("SMTP", "Password empty — verification/reset emails will not send until configured")

    # App boots with CSRF + MySQL
    try:
        os.environ["GURMADNET_DB"] = "mysql"
        import importlib
        import app as ers

        importlib.reload(ers)
        ok("App imports with MySQL mode", True)
        ok("CSRFProtect attached", hasattr(ers, "csrf"))
        client = ers.app.test_client()
        # CSRF on for non-testing client
        ers.app.config["TESTING"] = False
        ers.app.config["WTF_CSRF_ENABLED"] = True
        r = client.get("/login")
        ok("Login page 200", r.status_code == 200)
        html = r.get_data(as_text=True)
        ok("CSRF token in login form", 'name="csrf_token"' in html)
        ok("CSRF meta injected", 'name="csrf-token"' in html)
        ok("csrf.js injected", "csrf.js" in html)
        # Admin page requires login — just ensure template has no Jinja-in-JS DEFAULTS
        admin_tpl = (ROOT / "templates" / "admin_dashboard.html").read_text(encoding="utf-8")
        ok(
            "Admin JS has no Jinja tojson in script",
            "const DEFAULTS = {{" not in admin_tpl and "admin-content-defaults" in admin_tpl,
        )
    except Exception as exc:
        ok("App production boot checks", False, str(exc))

    print("\n=== PRODUCTION CHECK SUMMARY ===")
    print(f"passed {passed} failed {failed} warnings {len(warnings)}")
    for w in warnings:
        print(" WARN:", w)
    if failed:
        print("\nProduction checks FAILED. Fix items above before deploy.")
        return 1
    print("\nAll critical production checks PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
