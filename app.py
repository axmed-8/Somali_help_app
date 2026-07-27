import csv
import base64
import hashlib
import hmac
import importlib.util
import io
import json
import logging
import os
import re
import secrets
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from functools import wraps

# Load .env (SMTP_*) before email_service / other config readers
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Email/SMTP only — never force-overwrite GURMADNET_DB here (breaks JSON smoke/e2e scripts).
_EMAIL_ENV_KEYS = (
    "EMAIL_PROVIDER",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "SMTP_USE_TLS",
    "EMAIL_VERIFICATION_HOURS",
    "EMAIL_VERIFICATION_MINUTES",
)


def _apply_email_env_from_dotenv(*, force=False):
    """
    Apply email/SMTP keys from project .env.
    force=True overwrites stale shell vars (e.g. EMAIL_PROVIDER=memory left by pytest).
    """
    env_path = os.path.join(BASE_DIR, ".env")
    try:
        from dotenv import dotenv_values
    except ImportError:
        return
    vals = dotenv_values(env_path) or {}
    for key in _EMAIL_ENV_KEYS:
        raw = vals.get(key)
        if raw is None:
            continue
        val = str(raw).strip()
        if not val:
            continue
        if force or not str(os.environ.get(key) or "").strip():
            os.environ[key] = val


try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE_DIR, ".env"), override=False)
except ImportError:
    pass

# Local `python app.py` must use .env SMTP even if the shell still has
# EMAIL_PROVIDER=memory from a previous pytest run. Pytest / TESTING=1 keep test providers.
_testing_flag = (os.environ.get("TESTING") or "").strip().lower() in ("1", "true", "yes")
if "pytest" not in sys.modules and not _testing_flag:
    _apply_email_env_from_dotenv(force=True)
from flask import (
    Flask,
    Response,
    flash,
    has_request_context,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf
from werkzeug.security import check_password_hash, generate_password_hash

import hospital_logic as hl
import call_center_logic as cc
from ai_engine import get_ai_engine, call_center_ai
from email_service import send_verification_email
from email_service.service import (
    allow_test_email_domains,
    is_valid_email_format,
    normalize_email,
    send_email_verification_otp_email,
    send_emergency_contact_alert_email,
    send_password_reset_otp_email,
    signup_email_rejection_reason,
)

_logger = logging.getLogger(__name__)
DATABASE_DIR = os.path.join(BASE_DIR, "database")
STORE_USERS = "users"
STORE_EMERGENCIES = "emergencies"
STORE_CONTENT = "system_content"
STORE_SETTINGS = "settings"
STORE_AUDIT = "audit_log"
STORE_ANNOUNCEMENTS = "announcements"
STORE_CALL_CENTER = "call_center_calls"
# Legacy aliases (tests write temp JSON files using these paths)
USERS_FILE = STORE_USERS
EMERGENCIES_FILE = STORE_EMERGENCIES
CONTENT_FILE = STORE_CONTENT
SETTINGS_FILE = STORE_SETTINGS
AUDIT_FILE = STORE_AUDIT
ANNOUNCEMENTS_FILE = STORE_ANNOUNCEMENTS
CALL_CENTER_FILE = STORE_CALL_CENTER

TEAM_LABELS = {
    "hospital": "Medical Response Team",
    "police": "Police Emergency Team",
    "fire": "Fire & Rescue Team",
    "admin": "Emergency Dispatch Center",
    "call_center": "Emergency Call Center",
}

COMPLETED_STATUSES = ("resolved", "completed", "cancelled", "no_hospital_available")

# Seeded into MySQL settings.response_stations once; runtime reads via get_response_stations().
DEFAULT_RESPONSE_STATIONS = {
    "fire": {
        "latitude": 2.052,
        "longitude": 45.328,
        "name": "Fire & Rescue Station",
        "phone": "",
    },
    "police": {
        "latitude": 2.038,
        "longitude": 45.315,
        "name": "Police Response Unit",
        "phone": "",
    },
}
# Backward-compatible name; prefer get_response_stations() for live MySQL values.
RESPONSE_STATIONS = DEFAULT_RESPONSE_STATIONS


def configure_hospital_db(db_dir):
    """Keep hospital_logic store keys aligned with app (tests use temp JSON dir)."""
    hl.DATABASE_DIR = db_dir
    hl.HOSPITALS_STORE = "hospitals"
    hl.NOTIFICATIONS_STORE = "notifications"
    hl.MESSAGES_STORE = "messages"


configure_hospital_db(DATABASE_DIR)

_file_locks = {}
_file_locks_guard = threading.Lock()


def _path_lock(path):
    with _file_locks_guard:
        if path not in _file_locks:
            _file_locks[path] = threading.Lock()
        return _file_locks[path]


app = Flask(__name__)
_secret = (os.environ.get("SECRET_KEY") or "").strip()
if not _secret:
    _secret = secrets.token_hex(32)
    _logger.warning(
        "SECRET_KEY is not set — using an ephemeral key. "
        "Set a strong SECRET_KEY in .env for production."
    )
elif _secret in {"change-me-in-production", "changeme", "secret"}:
    _logger.warning(
        "SECRET_KEY is insecure (%r). Replace it with a long random value before production.",
        _secret,
    )
app.secret_key = _secret
# Harden session cookies for production-quality defaults
_csrf_enabled = os.environ.get("WTF_CSRF_ENABLED", "true").lower() not in (
    "0",
    "false",
    "no",
    "off",
)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").lower()
    in ("1", "true", "yes", "on"),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    WTF_CSRF_ENABLED=_csrf_enabled,
    WTF_CSRF_TIME_LIMIT=None,
    WTF_CSRF_HEADERS=["X-CSRFToken", "X-CSRF-Token"],
    WTF_CSRF_SSL_STRICT=False,
)
# HTTPS tunnels (cloudflared / ngrok) send X-Forwarded-* headers.
if os.environ.get("TRUST_PROXY", "1").strip().lower() not in ("0", "false", "no", "off"):
    try:
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    except Exception:
        _logger.exception("ProxyFix not applied")
csrf = CSRFProtect(app)


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    _logger.warning("CSRF rejection path=%s reason=%s", request.path, getattr(e, "description", e))
    wants_json = (request.path or "").startswith("/api/") or (
        request.accept_mimetypes.best == "application/json"
    )
    if wants_json:
        return jsonify({
            "success": False,
            "message": "Your session expired or the request was invalid. Refresh and try again.",
            "csrf_error": True,
        }), 400
    flash("Your session expired. Please try again.", "error")
    return redirect(request.referrer or url_for("login"))

TYPE_MAP = {
    "medical": ["medical", "family_help", "family help"],
    "police": ["police", "security", "accident"],
    "fire": ["fire"],
}

TYPE_LABELS = {
    "medical": "Medical",
    "accident": "Accident",
    "fire": "Fire",
    "security": "Security",
    "family_help": "Family Help",
    "other": "Other",
}

STATUS_VALUES = ["pending", "dispatched", "in_progress", "completed", "cancelled", "resolved", "pending_hospital", "accepted"]
VALID_ROLES = [
    "citizen",
    "hospital",
    "police",
    "fire",
    "admin",
    "super_admin",
    "call_center",
]

# Staff who can access /admin (permissions differ by role)
STAFF_ADMIN_ROLES = frozenset({"super_admin", "admin"})
PRIVILEGED_ROLES = frozenset({"super_admin", "admin"})
OPS_USER_ROLES = frozenset({"citizen", "hospital", "police", "fire", "call_center"})
# Ops staff any Admin can create; privileged Admin accounts need Super Admin.
OPS_CREATE_ROLES = frozenset({"hospital", "police", "fire", "call_center"})
# Super Admin "Create staff" button may only create these roles
SUPER_CREATE_ROLES = frozenset({"admin", "hospital", "police", "fire", "call_center"})

# Role → capability set for the admin dashboard / APIs
ADMIN_PERMISSIONS = {
    "super_admin": frozenset({
        "dashboard",
        "users_ops",
        "users_admins",
        "emergencies_view",
        "emergencies_update",
        "emergencies_delete",
        "emergencies_export",
        "settings_ops",
        "settings_system",
        "content_edit",
        "content_reset",
        "appearance",
        "audit",
        "backup",
        "call_center",
        "ai",
        "reports",
        "monitoring",
    }),
    "admin": frozenset({
        "dashboard",
        "users_ops",
        "emergencies_view",
        "emergencies_update",
        "emergencies_export",
        "settings_ops",
        "content_edit",
        "appearance",
        "call_center",
        "ai",
        "reports",
        "monitoring",
        # Regular Admin cannot: users_admins, emergencies_delete,
        # settings_system, content_reset, audit, backup
    }),
}

# Settings keys only Super Admin may change (ops Admin uses a small allow-list)
SUPER_ONLY_SETTINGS = frozenset({
    "sos_enabled",
    "maintenance_mode",
    "max_emergencies_per_day",
    "ai_enabled",
    "ai_provider",
    "google_maps_api_key",
    "hospital_response_timeout_sec",
    "app_name",
    "app_description",
    "app_logo_url",
    "app_favicon_url",
    "default_language",
    "timezone",
    "contact_phone",
    "contact_email",
    "contact_address",
    "contact_website",
    "emergency_hotline",
    "smtp_enabled",
    "smtp_host",
    "smtp_port",
    "smtp_user",
    "smtp_password",
    "smtp_from",
    "smtp_use_tls",
    "email_verification_minutes",
    "security_force_https",
    "security_login_max_attempts",
    "security_lockout_minutes",
    "auth_require_email_verification",
    "auth_allow_citizen_signup",
    "password_min_length",
    "password_require_upper",
    "password_require_digit",
    "password_require_special",
    "session_timeout_minutes",
    "ai_confidence_threshold",
    "ai_auto_suggest",
    "priority_medical",
    "priority_fire",
    "priority_police",
    "priority_accident",
    "priority_family_help",
    "notify_email_enabled",
    "notify_admin_on_sos",
    "notify_citizen_status",
    "sms_provider",
    "sms_api_key",
    "sms_api_url",
    "sms_sender_id",
    "maps_provider",
    "maps_default_lat",
    "maps_default_lng",
    "maps_default_zoom",
    "openai_api_key",
    "external_api_key",
    "upload_max_mb",
    "upload_allowed_extensions",
    "db_backup_retention_days",
    "theme_mode",
    "brand_primary_color",
    "brand_accent_color",
})

ROLE_HOME = {
    "citizen": "/",
    "hospital": "/hospital",
    "police": "/police",
    "fire": "/fire",
    "admin": "/admin",
    "super_admin": "/admin",
    "call_center": "/call-center",
}

ROLE_API_TYPE = {"hospital": "medical", "police": "police", "fire": "fire"}


def _session_role():
    return session.get("role")


def _is_staff_admin(role=None):
    return (role if role is not None else _session_role()) in STAFF_ADMIN_ROLES


def _is_super_admin(role=None):
    return (role if role is not None else _session_role()) == "super_admin"


def _admin_permissions(role=None):
    role = role if role is not None else _session_role()
    return ADMIN_PERMISSIONS.get(role) or frozenset()


def _has_admin_perm(perm, role=None):
    return perm in _admin_permissions(role)


def _forbid_admin(message="You do not have permission for this action."):
    if _wants_json_response():
        return jsonify({"success": False, "message": message}), 403
    flash(message, "error")
    return redirect(ROLE_HOME.get(_session_role(), "/login"))


def _require_admin_perm(perm):
    """Return a Flask response if the current admin lacks `perm`, else None."""
    if not _is_staff_admin():
        return _forbid_admin()
    if not _has_admin_perm(perm):
        return _forbid_admin("Super Admin access is required for this action.")
    return None

def _json_store_allowed():
    """
    JSON file store is allowed ONLY under automated tests.
    GURMADNET_DB=json alone is not enough for gunicorn/production.
    """
    mode = (os.environ.get("GURMADNET_DB") or "").strip().lower()
    if mode != "json":
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    if "pytest" in sys.modules:
        return True
    if (os.environ.get("TESTING") or "").strip().lower() in ("1", "true", "yes"):
        return True
    return False


def _resolve_use_mysql():
    """
    MySQL is the only production store.
    JSON is permitted solely when GURMADNET_DB=json AND tests (pytest/TESTING=1).
    """
    mode = (os.environ.get("GURMADNET_DB") or "").strip().lower()
    if mode == "json":
        if _json_store_allowed():
            return False
        raise RuntimeError(
            "GURMADNET_DB=json is blocked outside tests. "
            "Remove it and configure database/db_config.env for MySQL."
        )

    cfg_path = os.path.join(DATABASE_DIR, "db_config.env")
    if not os.path.exists(cfg_path) and mode != "mysql":
        if _json_store_allowed():
            return False
        raise RuntimeError(
            "MySQL is required. Create database/db_config.env "
            "(copy from db_config.env.example) with live credentials."
        )
    try:
        from database.connection import load_config
        from database import mysql_store

        if not mysql_store.available():
            raise RuntimeError("PyMySQL is not installed. Run: pip install PyMySQL")
        cfg = load_config()
        if cfg.get("password") in ("", "YOUR_PASSWORD", "CHANGE_ME_STRONG_PASSWORD"):
            raise RuntimeError(
                "Set a real DB_PASSWORD in database/db_config.env before starting the app."
            )
        conn = mysql_store.connect()
        conn.close()
        return True
    except RuntimeError:
        raise
    except Exception as exc:
        if _json_store_allowed():
            logging.getLogger(__name__).warning(
                "MySQL unavailable (%s); using JSON because TESTING/pytest is active.",
                exc,
            )
            return False
        raise RuntimeError(
            f"Cannot connect to MySQL ({exc}). Fix database/db_config.env — "
            "the app will not fall back to JSON files."
        ) from exc


USE_MYSQL = _resolve_use_mysql()
logging.getLogger(__name__).info(
    "Storage backend: %s",
    "MySQL (live)" if USE_MYSQL else "JSON (test only)",
)

# Ensure Call Center + AI + email verification MySQL schema before seeding
if USE_MYSQL:
    try:
        from database import mysql_store as _ms_boot

        _ms_boot.ensure_call_center_schema()
        _ms_boot.ensure_ai_schema()
        _ms_boot.ensure_email_verification_schema()
        _ms_boot.ensure_citizen_profile_schema()
        _ms_boot.ensure_admin_profile_schema()
        _ms_boot.ensure_hospital_logo_schema()
        _ms_boot.ensure_ambulance_gps_share_schema()
    except Exception as _cc_schema_exc:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "Call Center/AI/email MySQL schema ensure skipped: %s", _cc_schema_exc
        )


DEFAULT_CONTENT = {
    "app_name": "GurmadNet AI",
    "sos_button_text": "SOS",
    "sos_subtitle": "Tap SOS button to start emergency request",
    "confirmation_message": "Help is on the way!",
    "hospital_dashboard_title": "Hospital Dashboard",
    "police_dashboard_title": "Police Operations Dashboard",
    "fire_dashboard_title": "Fire & Rescue Dashboard",
    "admin_dashboard_title": "GurmadNet AI — Admin Control Panel",
    "call_center_dashboard_title": "GurmadNet AI — Emergency Call Center",
    "welcome_citizen": "Mogadishu & Somalia — 24/7 Emergency Response",
    "emergency_type_medical": "Medical",
    "emergency_type_accident": "Accident",
    "emergency_type_fire": "Fire",
    "emergency_type_security": "Security",
    "emergency_type_family": "Family Help",
    "call_center_button_text": "Call Emergency Center",
    "call_center_hint": "Speak with an operator — your GPS is shared automatically",
}

DEFAULT_SETTINGS = {
    # --- legacy / ops ---
    "sos_enabled": True,
    "ambulance_response_time": 8,
    "police_response_time": 6,
    "fire_response_time": 9,
    "refresh_interval": 5,
    "max_emergencies_per_day": 100,
    "maintenance_mode": False,
    "sms_notifications": False,
    "color_hospital": "#2E7D32",
    "color_police": "#1565C0",
    "color_fire": "#C62828",
    "dark_mode": False,
    "hospital_response_timeout_sec": 120,
    "google_maps_api_key": os.environ.get("GOOGLE_MAPS_API_KEY", ""),
    "call_center_enabled": True,
    "call_center_phone": "",
    "call_center_phone_secondary": "",
    "call_center_priority_medical": 1,
    "call_center_priority_fire": 1,
    "call_center_priority_police": 1,
    "call_center_auto_nearest": True,
    "call_center_heartbeat_sec": 45,
    "ai_enabled": True,
    "ai_provider": "rule_based",
    # --- general / branding ---
    "app_name": "GurmadNet AI",
    "app_description": "National Emergency Response Platform for Somalia",
    "app_logo_url": "",
    "app_favicon_url": "",
    "default_language": "en",
    "timezone": "Africa/Mogadishu",
    # --- contact ---
    "contact_phone": "",
    "contact_email": "",
    "contact_address": "",
    "contact_website": "",
    "emergency_hotline": "",
    # --- SMTP / email (runtime override; .env remains fallback) ---
    "smtp_enabled": True,
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from": "",
    "smtp_use_tls": True,
    "email_verification_minutes": 30,
    # --- security / auth / password / session ---
    "security_force_https": False,
    "security_login_max_attempts": 5,
    "security_lockout_minutes": 15,
    "auth_require_email_verification": True,
    "auth_allow_citizen_signup": True,
    "password_min_length": 6,
    "password_require_upper": False,
    "password_require_digit": False,
    "password_require_special": False,
    "session_timeout_minutes": 120,
    # --- AI ---
    "ai_confidence_threshold": 0.55,
    "ai_auto_suggest": True,
    # --- emergency priorities ---
    "priority_medical": 1,
    "priority_fire": 1,
    "priority_police": 1,
    "priority_accident": 2,
    "priority_family_help": 3,
    # --- notifications ---
    "notify_email_enabled": True,
    "notify_admin_on_sos": True,
    "notify_citizen_status": True,
    # --- SMS gateway ---
    "sms_provider": "none",
    "sms_api_key": "",
    "sms_api_url": "",
    "sms_sender_id": "GurmadNet",
    # --- maps ---
    "maps_provider": "google",
    "maps_default_lat": 2.0469,
    "maps_default_lng": 45.3182,
    "maps_default_zoom": 14,
    # --- API keys (masked in UI) ---
    "openai_api_key": "",
    "external_api_key": "",
    # --- uploads ---
    "upload_max_mb": 5,
    "upload_allowed_extensions": "jpg,jpeg,png,gif,webp,pdf",
    # --- database (read-only display mostly) ---
    "db_backup_retention_days": 30,
    # --- theme ---
    "theme_mode": "dark",
    "brand_primary_color": "#2563eb",
    "brand_accent_color": "#22c55e",
}

# Keys that must never be returned in plain text to the client
SECRET_SETTING_KEYS = frozenset({
    "smtp_password",
    "google_maps_api_key",
    "sms_api_key",
    "openai_api_key",
    "external_api_key",
})

# Super Admin System Configuration form definition (UI schema)
SYSTEM_SETTINGS_GROUPS = [
    {
        "id": "general",
        "title": "General System Settings",
        "description": "Application identity and defaults",
        "fields": [
            {"key": "app_name", "label": "App Name", "type": "text"},
            {"key": "app_description", "label": "Description", "type": "textarea"},
            {"key": "app_logo_url", "label": "Logo URL / path", "type": "text", "hint": "e.g. /static/uploads/logo.png"},
            {"key": "app_favicon_url", "label": "Favicon URL / path", "type": "text"},
            {"key": "default_language", "label": "Default language", "type": "select",
             "options": [{"value": "en", "label": "English"}, {"value": "so", "label": "Somali"}]},
            {"key": "timezone", "label": "Timezone", "type": "text"},
        ],
    },
    {
        "id": "contact",
        "title": "Contact Information",
        "description": "Public contact details shown across the platform",
        "fields": [
            {"key": "emergency_hotline", "label": "Emergency hotline", "type": "tel"},
            {"key": "contact_phone", "label": "Phone", "type": "tel"},
            {"key": "contact_email", "label": "Email", "type": "email"},
            {"key": "contact_address", "label": "Address", "type": "textarea"},
            {"key": "contact_website", "label": "Website", "type": "text"},
        ],
    },
    {
        "id": "smtp",
        "title": "SMTP / Email Settings",
        "description": "Overrides .env when filled. Leave password blank to keep the current value.",
        "fields": [
            {"key": "smtp_enabled", "label": "Enable email sending", "type": "checkbox"},
            {"key": "smtp_host", "label": "SMTP host", "type": "text"},
            {"key": "smtp_port", "label": "SMTP port", "type": "number", "min": 1},
            {"key": "smtp_user", "label": "SMTP username", "type": "text"},
            {"key": "smtp_password", "label": "SMTP password", "type": "password"},
            {"key": "smtp_from", "label": "From address", "type": "email"},
            {"key": "smtp_use_tls", "label": "Use TLS", "type": "checkbox"},
            {"key": "email_verification_minutes", "label": "Email OTP validity (minutes)", "type": "number", "min": 5},
        ],
    },
    {
        "id": "security",
        "title": "Security Settings",
        "fields": [
            {"key": "security_force_https", "label": "Prefer HTTPS links", "type": "checkbox"},
            {"key": "security_login_max_attempts", "label": "Max login attempts", "type": "number", "min": 3},
            {"key": "security_lockout_minutes", "label": "Lockout duration (minutes)", "type": "number", "min": 1},
            {"key": "maintenance_mode", "label": "Maintenance mode", "type": "checkbox"},
            {"key": "sos_enabled", "label": "Enable SOS system", "type": "checkbox"},
        ],
    },
    {
        "id": "auth",
        "title": "Authentication Settings",
        "fields": [
            {"key": "auth_allow_citizen_signup", "label": "Allow citizen self-registration", "type": "checkbox"},
            {"key": "auth_require_email_verification", "label": "Require email verification for citizens", "type": "checkbox"},
        ],
    },
    {
        "id": "password",
        "title": "Password Policy",
        "fields": [
            {"key": "password_min_length", "label": "Minimum length", "type": "number", "min": 4},
            {"key": "password_require_upper", "label": "Require uppercase letter", "type": "checkbox"},
            {"key": "password_require_digit", "label": "Require digit", "type": "checkbox"},
            {"key": "password_require_special", "label": "Require special character", "type": "checkbox"},
        ],
    },
    {
        "id": "session",
        "title": "Session Timeout",
        "fields": [
            {"key": "session_timeout_minutes", "label": "Session timeout (minutes)", "type": "number", "min": 15},
        ],
    },
    {
        "id": "ai",
        "title": "AI Configuration",
        "fields": [
            {"key": "ai_enabled", "label": "Enable AI assistance", "type": "checkbox"},
            {"key": "ai_provider", "label": "AI provider", "type": "select",
             "options": [
                 {"value": "rule_based", "label": "Rule-based"},
                 {"value": "openai", "label": "OpenAI"},
             ]},
            {"key": "ai_confidence_threshold", "label": "Confidence threshold (0–1)", "type": "number", "min": 0, "max": 1, "step": 0.05},
            {"key": "ai_auto_suggest", "label": "Auto-suggest recommendations", "type": "checkbox"},
            {"key": "openai_api_key", "label": "OpenAI API key", "type": "password"},
        ],
    },
    {
        "id": "emergency",
        "title": "Emergency Priority Settings",
        "description": "1 = highest priority",
        "fields": [
            {"key": "priority_medical", "label": "Medical priority", "type": "number", "min": 1, "max": 5},
            {"key": "priority_fire", "label": "Fire priority", "type": "number", "min": 1, "max": 5},
            {"key": "priority_police", "label": "Police / security priority", "type": "number", "min": 1, "max": 5},
            {"key": "priority_accident", "label": "Accident priority", "type": "number", "min": 1, "max": 5},
            {"key": "priority_family_help", "label": "Family help priority", "type": "number", "min": 1, "max": 5},
            {"key": "ambulance_response_time", "label": "Target ambulance response (min)", "type": "number", "min": 1},
            {"key": "police_response_time", "label": "Target police response (min)", "type": "number", "min": 1},
            {"key": "fire_response_time", "label": "Target fire response (min)", "type": "number", "min": 1},
            {"key": "hospital_response_timeout_sec", "label": "Hospital accept timeout (sec)", "type": "number", "min": 30},
            {"key": "max_emergencies_per_day", "label": "Max emergencies / day", "type": "number", "min": 1},
            {"key": "refresh_interval", "label": "Dashboard refresh (sec)", "type": "number", "min": 3},
        ],
    },
    {
        "id": "notifications",
        "title": "Notification Settings",
        "fields": [
            {"key": "notify_email_enabled", "label": "Email notifications", "type": "checkbox"},
            {"key": "notify_admin_on_sos", "label": "Notify admins on new SOS", "type": "checkbox"},
            {"key": "notify_citizen_status", "label": "Notify citizens on status changes", "type": "checkbox"},
        ],
    },
    {
        "id": "sms",
        "title": "SMS Settings",
        "fields": [
            {"key": "sms_notifications", "label": "Enable SMS notifications", "type": "checkbox"},
            {"key": "sms_provider", "label": "SMS provider", "type": "select",
             "options": [
                 {"value": "none", "label": "None / disabled"},
                 {"value": "custom", "label": "Custom HTTP API"},
             ]},
            {"key": "sms_api_url", "label": "SMS API URL", "type": "text"},
            {"key": "sms_api_key", "label": "SMS API key", "type": "password"},
            {"key": "sms_sender_id", "label": "Sender ID", "type": "text"},
        ],
    },
    {
        "id": "maps",
        "title": "Maps & Location Settings",
        "description": "Google Maps is the primary live source for GPS maps, addresses, places, routes, and distances. Enable Maps JavaScript, Geocoding, Places, and Directions APIs on your Google Cloud key.",
        "fields": [
            {"key": "maps_provider", "label": "Maps provider", "type": "select",
             "options": [
                 {"value": "google", "label": "Google Maps (recommended)"},
                 {"value": "leaflet", "label": "Leaflet fallback (only if no Google key)"},
             ]},
            {"key": "google_maps_api_key", "label": "Google Maps API key", "type": "password",
             "hint": "Required for live Google geocoding, routes, and map tiles"},
            {"key": "maps_default_lat", "label": "Default map latitude (initial view only)", "type": "number", "step": 0.0001},
            {"key": "maps_default_lng", "label": "Default map longitude (initial view only)", "type": "number", "step": 0.0001},
            {"key": "maps_default_zoom", "label": "Default zoom", "type": "number", "min": 1, "max": 20},
        ],
    },
    {
        "id": "api_keys",
        "title": "API Keys Management",
        "description": "Sensitive values are masked. Leave blank to keep the existing secret.",
        "fields": [
            {"key": "external_api_key", "label": "External integrations API key", "type": "password"},
        ],
    },
    {
        "id": "call_center",
        "title": "Call Center Settings",
        "fields": [
            {"key": "call_center_enabled", "label": "Enable Call Center", "type": "checkbox"},
            {"key": "call_center_phone", "label": "Primary phone", "type": "tel"},
            {"key": "call_center_phone_secondary", "label": "Secondary phone", "type": "tel"},
            {"key": "call_center_priority_medical", "label": "Priority medical", "type": "number", "min": 1, "max": 5},
            {"key": "call_center_priority_fire", "label": "Priority fire", "type": "number", "min": 1, "max": 5},
            {"key": "call_center_priority_police", "label": "Priority police", "type": "number", "min": 1, "max": 5},
            {"key": "call_center_auto_nearest", "label": "Auto-select nearest unit", "type": "checkbox"},
            {"key": "call_center_heartbeat_sec", "label": "Operator heartbeat (sec)", "type": "number", "min": 15},
        ],
    },
    {
        "id": "branding",
        "title": "System Branding & Theme",
        "fields": [
            {"key": "theme_mode", "label": "Default theme", "type": "select",
             "options": [
                 {"value": "dark", "label": "Dark"},
                 {"value": "light", "label": "Light"},
             ]},
            {"key": "dark_mode", "label": "Prefer dark mode (legacy flag)", "type": "checkbox"},
            {"key": "brand_primary_color", "label": "Primary brand color", "type": "color"},
            {"key": "brand_accent_color", "label": "Accent color", "type": "color"},
            {"key": "color_hospital", "label": "Hospital role color", "type": "color"},
            {"key": "color_police", "label": "Police role color", "type": "color"},
            {"key": "color_fire", "label": "Fire role color", "type": "color"},
        ],
    },
    {
        "id": "uploads",
        "title": "File Upload Settings",
        "fields": [
            {"key": "upload_max_mb", "label": "Max upload size (MB)", "type": "number", "min": 1, "max": 50},
            {"key": "upload_allowed_extensions", "label": "Allowed extensions (comma-separated)", "type": "text"},
        ],
    },
    {
        "id": "database",
        "title": "Database & Backup Settings",
        "description": "Operational controls. Create backups from Backup & Restore.",
        "fields": [
            {"key": "db_backup_retention_days", "label": "Backup retention (days)", "type": "number", "min": 1},
        ],
    },
]


def ensure_database_dir():
    os.makedirs(DATABASE_DIR, exist_ok=True)


def _json_file_path(entity):
    """Map store entity to temp JSON file path (test mode only)."""
    key = os.path.basename(str(entity))
    if not key.endswith(".json"):
        key = f"{key}.json"
    return os.path.join(DATABASE_DIR, key)


def read_store(entity, default):
    """Read entity document from MySQL (production) or JSON (tests only)."""
    ms = _mysql_backend()
    if ms:
        return ms.read_store(entity, default)
    if not _json_store_allowed():
        raise RuntimeError(
            f"Refusing JSON read for '{entity}' — MySQL is required. "
            "Check database/db_config.env and restart the server."
        )
    path = _json_file_path(entity)
    ensure_database_dir()
    if not os.path.exists(path):
        save_store(entity, default)
        return json.loads(json.dumps(default))
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        save_store(entity, default)
        return json.loads(json.dumps(default))


def save_store(entity, data):
    """Persist to MySQL (production) or temp JSON (tests only). Never dual-write."""
    ms = _mysql_backend()
    if ms:
        ms.save_store(entity, data)
        return
    if not _json_store_allowed():
        raise RuntimeError(
            f"Refusing JSON write for '{entity}' — MySQL is required. "
            "Check database/db_config.env and restart the server."
        )
    path = _json_file_path(entity)
    ensure_database_dir()
    with _path_lock(path):
        fd, tmp_path = tempfile.mkstemp(dir=DATABASE_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise


def read_json(entity, default):
    return read_store(entity, default)


def save_json(entity, data):
    save_store(entity, data)


def append_audit(action, entity_type, entity_id, details=None, user_id=None):
    try:
        log = read_json(AUDIT_FILE, {"entries": [], "next_id": 1})
        entries = log.get("entries") or []
        max_existing = 0
        for e in entries:
            try:
                max_existing = max(max_existing, int(e.get("id") or 0))
            except (TypeError, ValueError):
                pass
        next_id = int(log.get("next_id") or 1)
        if next_id <= max_existing:
            next_id = max_existing + 1
        entry = {
            "id": next_id,
            "timestamp": now_str(),
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "user_id": user_id or session.get("user_id"),
            "details": details or {},
        }
        log["next_id"] = next_id + 1
        log["entries"] = ([entry] + entries)[:5000]
        save_json(AUDIT_FILE, log)
    except Exception:
        logging.getLogger(__name__).exception(
            "append_audit failed action=%s entity=%s/%s",
            action,
            entity_type,
            entity_id,
        )


def normalize_emergency_record(em):
    em.setdefault("location_history", [])
    em.setdefault("responder_status", {})
    em.setdefault("user_id", None)
    em.setdefault("request_mode", "legacy")
    em.setdefault("escalation_queue", [])
    em.setdefault("escalation_index", 0)
    em.setdefault("status_history", [])
    em.setdefault("assigned_hospital_id", None)
    em.setdefault("assigned_hospital_name", "")
    # Terminal SOS must never keep live tracking / map "live" flags
    if (em.get("status") or "").lower() in COMPLETED_STATUSES:
        em["tracking_active"] = False
    return em


ACTIVE_SOS_STATUSES = frozenset({
    "pending",
    "dispatched",
    "in_progress",
    "pending_hospital",
    "accepted",
})


def _stop_sos_tracking(em):
    """Clear live GPS tracking when an SOS reaches a terminal state."""
    if not em:
        return
    em["tracking_active"] = False


def _is_active_sos(em):
    return (em.get("status") or "").lower() in ACTIVE_SOS_STATUSES


def _ai_engine():
    """Provider-agnostic AI Emergency Engine (never talks to a vendor SDK directly)."""
    settings = load_settings()
    provider = (settings.get("ai_provider") or os.environ.get("AI_PROVIDER") or "rule_based")
    provider = str(provider).strip().lower()
    eng = get_ai_engine(read_json, save_json, provider_name=provider)
    if getattr(eng, "provider_name", None) != provider:
        eng = get_ai_engine(read_json, save_json, provider_name=provider, reset=True)
    return eng


def _ai_context_from_emergency(emergency, source="sos", extra=None):
    """Build analysis context from an emergency record without mutating dispatch."""
    edata = load_emergencies()
    hdata = hl.load_hospitals(read_json, save_json)
    uid = emergency.get("user_id")
    history = [
        {
            "id": e.get("id"),
            "type": e.get("type"),
            "status": e.get("status"),
            "timestamp": e.get("timestamp"),
        }
        for e in edata.get("emergencies", [])
        if uid and e.get("user_id") == uid and e.get("id") != emergency.get("id")
    ][-10:]
    active = [
        e for e in edata.get("emergencies", [])
        if e.get("status") not in COMPLETED_STATUSES
    ]
    ctx = {
        "emergency_id": emergency.get("id"),
        "call_id": emergency.get("call_id"),
        "type": emergency.get("type"),
        "notes": emergency.get("notes") or "",
        "description": emergency.get("notes") or "",
        "latitude": emergency.get("latitude"),
        "longitude": emergency.get("longitude"),
        "address": emergency.get("location") or emergency.get("district") or "",
        "district": emergency.get("district") or "",
        "caller_name": emergency.get("caller_name"),
        "phone": emergency.get("phone"),
        "source": source,
        "emergency_history": history,
        "active_emergencies": active,
        "hospitals": hdata.get("hospitals", []),
        "police_station": get_response_stations().get("police"),
        "fire_station": get_response_stations().get("fire"),
    }
    if emergency.get("latitude") is not None and emergency.get("longitude") is not None:
        try:
            ctx["nearest"] = cc.find_nearest_responders(
                emergency["latitude"],
                emergency["longitude"],
                read_json,
                save_json,
                get_response_station_list(),
            )
        except Exception:
            ctx["nearest"] = {}
    if extra:
        ctx.update(extra)
    return ctx


def _schedule_ai_analysis(emergency, source="sos"):
    """
    Run AI Decision + Smart Dispatch recommendation in parallel.
    Never blocks or alters SOS auto-dispatch. AI never dispatches.
    """
    settings = load_settings()
    if not settings.get("ai_enabled", True):
        return None
    try:
        engine = _ai_engine()
        context = _ai_context_from_emergency(emergency, source=source)
        eid = emergency.get("id")
        assigned_to = emergency.get("assigned_to")
        assigned_hid = emergency.get("assigned_hospital_id")
        assigned_hname = emergency.get("assigned_hospital_name")
        call_id = emergency.get("call_id")

        def _on_done(result):
            try:
                analysis = (result or {}).get("analysis") or {}
                recommendation = (result or {}).get("recommendation") or {}
                decision = "auto_sos" if source == "sos" else (
                    "auto_healthcare" if source == "healthcare" else "queued"
                )
                if source == "call_center":
                    decision = "call_center_created"
                engine.record_dispatch_result({
                    "emergency_id": eid,
                    "call_id": call_id,
                    "recommendation_id": recommendation.get("id"),
                    "analysis_id": analysis.get("id"),
                    "human_decision": decision,
                    "dispatched_to": [assigned_to] if assigned_to else [],
                    "emergency_ids": [eid] if eid else [],
                    "assigned_hospital_id": assigned_hid,
                    "assigned_hospital_name": assigned_hname,
                    "notes": f"Parallel AI analysis after {source} dispatch path",
                })
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "AI dispatch_result memory write failed for emergency %s", eid
                )

        # Deterministic sync path in tests — avoids flaky async timing races
        if app.config.get("TESTING") or os.environ.get("AI_SYNC", "").lower() in (
            "1",
            "true",
            "yes",
        ):
            try:
                return _run_ai_analysis_now(engine, context, _on_done)
            except Exception:
                import logging
                logging.getLogger(__name__).exception(
                    "AI sync analysis failed for emergency %s", eid
                )
                return None

        return engine.analyze_emergency_async(context, on_done=_on_done)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "AI schedule failed for emergency %s", emergency.get("id")
        )
        return None


def _run_ai_analysis_now(engine, context, on_done):
    """Synchronous AI path used under TESTING for deterministic results."""
    result = engine.analyze_and_recommend(context)
    if on_done:
        on_done(result)
    return result


def _ai_record_outcome(emergency):
    """Persist final outcome into AI Memory for future strategic modules."""
    try:
        settings = load_settings()
        if not settings.get("ai_enabled", True):
            return
        _ai_engine().record_outcome({
            "emergency_id": emergency.get("id"),
            "call_id": emergency.get("call_id"),
            "status": emergency.get("status"),
            "final_status": emergency.get("status"),
            "assigned_to": emergency.get("assigned_to"),
            "type": emergency.get("type"),
            "district": emergency.get("district"),
            "latitude": emergency.get("latitude"),
            "longitude": emergency.get("longitude"),
            "notes": emergency.get("notes"),
        })
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "AI outcome memory write failed for emergency %s", emergency.get("id")
        )


def _citizen_emergency_history(user_id, limit=10):
    """Previous emergencies for Call Center AI context (read-only)."""
    if not user_id:
        return []
    edata = load_emergencies()
    rows = [
        {
            "id": e.get("id"),
            "type": e.get("type"),
            "status": e.get("status"),
            "timestamp": e.get("timestamp"),
            "location": e.get("location") or e.get("district"),
        }
        for e in edata.get("emergencies", [])
        if e.get("user_id") == user_id
    ]
    rows.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return rows[:limit]


def _ai_context_from_call(call, notes=None):
    """Build AI context for an open Call Center session (does not dispatch)."""
    call = dict(call or {})
    history = _citizen_emergency_history(call.get("user_id"))
    call["emergency_history"] = history
    if call.get("latitude") is not None and call.get("longitude") is not None:
        try:
            call["nearest"] = cc.find_nearest_responders(
                call["latitude"],
                call["longitude"],
                read_json,
                save_json,
                get_response_station_list(),
            )
        except Exception:
            call.setdefault("nearest", {})
    hdata = hl.load_hospitals(read_json, save_json)
    edata = load_emergencies()
    active = [
        e for e in edata.get("emergencies", [])
        if e.get("status") not in COMPLETED_STATUSES
    ]
    ctx = call_center_ai.build_call_context(call, notes=notes, extra={
        "hospitals": hdata.get("hospitals", []),
        "police_station": get_response_stations().get("police"),
        "fire_station": get_response_stations().get("fire"),
        "active_emergencies": active,
        "emergency_history": history,
    })
    return ctx


def _ai_panel_for_call(call_id):
    """Latest analysis + recommendation flattened for the AI Assistant Panel."""
    packed = _ai_engine().get_latest_for_call(call_id)
    return {
        "analysis": packed.get("analysis"),
        "recommendation": packed.get("recommendation"),
        "panel": call_center_ai.panel_from_result(packed),
    }


def _run_call_center_ai(call, notes=None):
    """
    Synchronous Call Center AI (rule_based is fast).
    Recommendation-only — never creates emergencies or dispatches.
    """
    settings = load_settings()
    if not settings.get("ai_enabled", True):
        return {"success": False, "message": "AI is disabled", "panel": None}
    engine = _ai_engine()
    ctx = _ai_context_from_call(call, notes=notes)
    result = engine.summarize_call(ctx)
    panel = call_center_ai.panel_from_result(result)
    return {
        "success": True,
        "analysis": result.get("analysis"),
        "recommendation": result.get("recommendation"),
        "panel": panel,
        "emergency_history": ctx.get("emergency_history") or [],
    }


def _notify(target_type, target_id, message, request_id=None, ntype="system_alert"):
    hl.add_notification(read_json, save_json, target_type, target_id, message, request_id, ntype)


def _notify_admins(message, request_id=None, ntype="system_alert"):
    udata = load_users()
    for u in udata["users"]:
        if u.get("role") in STAFF_ADMIN_ROLES and u.get("status") == "active":
            _notify(u.get("role") or "admin", u["id"], message, request_id, ntype)


def _auto_dispatch_emergency(emergency):
    """Route emergency to the correct response team automatically."""
    eid = emergency["id"]
    uid = emergency.get("user_id")
    etype = emergency.get("type", "medical")
    assign_map = {
        "medical": "hospital",
        "family_help": "hospital",
        "fire": "fire",
        "security": "police",
        "accident": "police",
    }
    team = assign_map.get(etype, "hospital")
    emergency["assigned_to"] = team
    emergency["assigned_team_label"] = TEAM_LABELS.get(team, "Emergency Response Team")
    emergency.setdefault("status_history", [])

    _notify("patient", uid, "Your emergency request has been received.", eid, "request_received")
    _notify_admins(f"New emergency #{eid} ({etype}) — auto-dispatch initiated.", eid, "system_alert")

    if team == "hospital" and emergency.get("latitude") and emergency.get("longitude"):
        settings = load_settings()
        timeout = int(settings.get("hospital_response_timeout_sec", 120))
        hdata = hl.seed_hospitals_if_empty(read_json, save_json)
        emergency["escalation_queue"] = hl.build_escalation_queue(
            emergency["latitude"], emergency["longitude"], hdata
        )
        emergency["escalation_index"] = 0
        if emergency["escalation_queue"]:
            hospital = hl.assign_next_hospital(emergency, hdata, timeout)
            if hospital:
                dist = emergency.get("hospital_distance_km")
                dist_txt = f" ({dist} km)" if dist is not None else ""
                _append_status(
                    emergency,
                    "pending_hospital",
                    f"Nearest hospital: {hospital['name']}{dist_txt}",
                )
                _notify("hospital", hospital["id"], f"URGENT: Emergency #{eid} — respond now", eid, "team_assigned")
                _notify(
                    "patient",
                    uid,
                    f"Nearest hospital {hospital['name']}{dist_txt} has been assigned to your request.",
                    eid,
                    "team_assigned",
                )
        else:
            emergency["status"] = "pending"
            _append_status(emergency, "pending", "Awaiting dispatch assignment")
    else:
        emergency["status"] = "pending"
        # Soft-assign nearest open police/fire station when coordinates exist
        if team in ("police", "fire") and emergency.get("latitude") and emergency.get("longitude"):
            import police_logic as pl

            nearest = pl.nearest_open_station(
                team, emergency.get("latitude"), emergency.get("longitude"), read_json
            )
            if nearest:
                emergency["assigned_station_id"] = nearest.get("id")
                emergency["assigned_team_label"] = nearest.get("name") or emergency["assigned_team_label"]
                if nearest.get("phone"):
                    emergency["contact_number"] = nearest.get("phone")
                dist = nearest.get("_distance_km")
                dist_txt = f" ({dist} km)" if dist is not None else ""
                _append_status(
                    emergency,
                    "pending",
                    f"Nearest {team} station: {nearest.get('name')}{dist_txt}",
                )
                _notify_role_operators(
                    team,
                    f"URGENT: Emergency #{eid} — respond now",
                    eid,
                    "team_assigned",
                    station_id=nearest.get("id"),
                )
                _notify(
                    "patient",
                    uid,
                    f"{nearest.get('name') or emergency['assigned_team_label']} has been assigned to your request.",
                    eid,
                    "team_assigned",
                )
            else:
                _append_status(emergency, "pending", f"Routed to {emergency['assigned_team_label']}")
                _notify_role_operators(
                    team, f"URGENT: Emergency #{eid} — open queue", eid, "team_assigned"
                )
                _notify("patient", uid, f"{emergency['assigned_team_label']} notified.", eid, "team_assigned")
        else:
            _append_status(emergency, "pending", f"Routed to {emergency['assigned_team_label']}")
            if team in ("police", "fire"):
                _notify_role_operators(
                    team, f"URGENT: Emergency #{eid} — open queue", eid, "team_assigned"
                )
            _notify("patient", uid, f"{emergency['assigned_team_label']} notified.", eid, "team_assigned")


def _user_hospital_id(user):
    if not user:
        return None
    return user.get("hospital_id")


def _get_user_hospital(user):
    hid = _user_hospital_id(user)
    if not hid:
        return None, None
    hdata = hl.load_hospitals(read_json, save_json)
    return hid, hl.get_hospital_by_id(hdata, hid)


def _user_station_id(user):
    if not user:
        return None
    sid = user.get("station_id")
    if sid in (None, ""):
        return None
    try:
        return int(sid)
    except (TypeError, ValueError):
        return None


def _get_user_station(user, kind=None):
    """Return (station_id, station_row) for police/fire operator."""
    import police_logic as pl

    role_kind = kind or ((user or {}).get("role") if user else None)
    if role_kind not in ("police", "fire"):
        role_kind = None
    return pl.get_user_station(user, role_kind, read_json)


def _notify_role_operators(role, message, request_id=None, ntype="system_alert", station_id=None):
    """Notify active operators of a role; optionally only those linked to a station."""
    udata = load_users()
    for u in udata.get("users") or []:
        if (u.get("role") or "") != role or (u.get("status") or "active") != "active":
            continue
        if station_id is not None:
            try:
                if int(u.get("station_id") or 0) != int(station_id):
                    continue
            except (TypeError, ValueError):
                continue
        _notify(role, u.get("id"), message, request_id, ntype)


def _hospital_name_map():
    """id → name for admin UI / API enrichment."""
    hdata = hl.load_hospitals(read_json, save_json)
    return {
        h["id"]: (h.get("name") or f"Hospital #{h['id']}")
        for h in hdata.get("hospitals") or []
    }


def _link_user_to_hospital(user_id, hospital_id, set_owner=True):
    """Bind hospital login account ↔ facility row (both directions)."""
    try:
        hospital_id = int(hospital_id)
    except (TypeError, ValueError):
        return False
    user, udata = get_user_by_id(user_id)
    if not user:
        return False
    hdata = hl.load_hospitals(read_json, save_json)
    hospital = hl.get_hospital_by_id(hdata, hospital_id)
    if not hospital:
        return False

    user["hospital_id"] = hospital_id
    if user.get("role") != "hospital":
        user["role"] = "hospital"
    save_users(udata)

    if set_owner:
        oid = hospital.get("owner_user_id")
        if not oid or int(oid) == int(user_id):
            hospital["owner_user_id"] = int(user_id)
            if not (hospital.get("contact_email") or "").strip():
                hospital["contact_email"] = user.get("email") or ""
            hl.save_hospitals(hdata, save_json)
    return True


def _unlink_user_from_hospital(user_id):
    """Clear user.hospital_id and hospital.owner_user_id when this user owned it."""
    user, udata = get_user_by_id(user_id)
    if not user:
        return
    hid = user.get("hospital_id")
    if hid:
        user["hospital_id"] = None
        save_users(udata)
    hdata = hl.load_hospitals(read_json, save_json)
    changed = False
    for h in hdata.get("hospitals") or []:
        if h.get("owner_user_id") == user_id:
            h["owner_user_id"] = None
            changed = True
        if hid and h.get("id") == hid and h.get("owner_user_id") == user_id:
            h["owner_user_id"] = None
            changed = True
    if changed:
        hl.save_hospitals(hdata, save_json)


def _sync_hospital_account_links():
    """
    Repair one-way / broken hospital↔user links:
    - owner_user_id → ensure that user.hospital_id matches
    - user.hospital_id → ensure owner_user_id if empty
    - match contact_email ↔ user.email when both sides unlinked
    """
    udata = load_users()
    hdata = hl.load_hospitals(read_json, save_json)
    users_by_id = {u["id"]: u for u in udata.get("users") or []}
    hospitals = hdata.get("hospitals") or []
    users_changed = False
    hospitals_changed = False

    # 1) Owner → user.hospital_id
    for h in hospitals:
        oid = h.get("owner_user_id")
        if not oid:
            continue
        u = users_by_id.get(oid)
        if not u:
            h["owner_user_id"] = None
            hospitals_changed = True
            continue
        if u.get("hospital_id") != h["id"]:
            u["hospital_id"] = h["id"]
            if u.get("role") != "hospital":
                u["role"] = "hospital"
            users_changed = True

    # 2) Hospital users with hospital_id → claim owner if empty
    for u in udata.get("users") or []:
        if str(u.get("role", "")).lower() != "hospital":
            continue
        hid = u.get("hospital_id")
        if not hid:
            continue
        h = hl.get_hospital_by_id(hdata, hid)
        if not h:
            u["hospital_id"] = None
            users_changed = True
            continue
        if not h.get("owner_user_id"):
            h["owner_user_id"] = u["id"]
            hospitals_changed = True

    def _digits(val):
        return "".join(ch for ch in str(val or "") if ch.isdigit())

    # 3) Email / phone match for fully unlinked pairs
    unlinked_hospital_users = [
        u
        for u in (udata.get("users") or [])
        if str(u.get("role", "")).lower() == "hospital" and not u.get("hospital_id")
    ]
    email_to_hospital_user = {
        (u.get("email") or "").strip().lower(): u
        for u in unlinked_hospital_users
        if (u.get("email") or "").strip()
    }

    for h in hospitals:
        if h.get("owner_user_id"):
            continue
        cem = (h.get("contact_email") or "").strip().lower()
        u = email_to_hospital_user.get(cem) if cem else None
        if not u:
            # Match user phone against hospital phone / emergency_contacts
            h_phones = {_digits(h.get("phone"))}
            for c in h.get("emergency_contacts") or []:
                d = _digits(c)
                if d:
                    h_phones.add(d)
            h_phones.discard("")
            for candidate in unlinked_hospital_users:
                if candidate.get("hospital_id"):
                    continue
                up = _digits(candidate.get("phone"))
                if not up:
                    continue
                if any(
                    up == hp or up in hp or hp in up
                    for hp in h_phones
                    if len(hp) >= 7 and len(up) >= 7
                ):
                    u = candidate
                    break
        if not u:
            continue
        u["hospital_id"] = h["id"]
        h["owner_user_id"] = u["id"]
        users_changed = True
        hospitals_changed = True
        email_to_hospital_user.pop((u.get("email") or "").strip().lower(), None)

    if users_changed:
        save_users(udata)
    if hospitals_changed:
        hl.save_hospitals(hdata, save_json)
    return {"users_changed": users_changed, "hospitals_changed": hospitals_changed}


def _sync_station_account_links():
    """
    Repair broken police/fire station↔operator links so desks receive SOS cases:
    - station.owner_user_id → user.station_id
    - user.station_id → owner_user_id if empty
    - phone match when both sides unlinked
    - if exactly one station of that kind exists, bind the unlinked operator(s) of that role
    """
    import facility_registry as fr

    udata = load_users()
    sdata = fr.load_stations(read_json)
    users_by_id = {u["id"]: u for u in udata.get("users") or []}
    stations = sdata.get("stations") or []
    users_changed = False
    stations_changed = False

    def _digits(val):
        return "".join(ch for ch in str(val or "") if ch.isdigit())

    # 1) Owner → user.station_id
    for st in stations:
        oid = st.get("owner_user_id")
        if not oid:
            continue
        u = users_by_id.get(oid)
        if u is None:
            try:
                u = users_by_id.get(int(oid))
            except (TypeError, ValueError):
                u = None
        if not u:
            st["owner_user_id"] = None
            stations_changed = True
            continue
        kind = (st.get("kind") or "").lower()
        if u.get("station_id") != st["id"]:
            u["station_id"] = st["id"]
            if kind in ("police", "fire") and u.get("role") != kind:
                u["role"] = kind
            users_changed = True

    # 2) Operators with station_id → claim owner if empty
    for u in udata.get("users") or []:
        role = str(u.get("role") or "").lower()
        if role not in ("police", "fire"):
            continue
        sid = u.get("station_id")
        if not sid:
            continue
        st = fr.get_station(sdata, sid)
        if not st:
            u["station_id"] = None
            users_changed = True
            continue
        if (st.get("kind") or "").lower() != role:
            u["station_id"] = None
            users_changed = True
            continue
        if not st.get("owner_user_id"):
            st["owner_user_id"] = u["id"]
            stations_changed = True

    # 3) Phone match for fully unlinked pairs
    for kind in ("police", "fire"):
        unlinked = [
            u
            for u in (udata.get("users") or [])
            if str(u.get("role") or "").lower() == kind and not u.get("station_id")
        ]
        for st in stations:
            if (st.get("kind") or "").lower() != kind or st.get("owner_user_id"):
                continue
            st_phone = _digits(st.get("phone"))
            if len(st_phone) < 7:
                continue
            for u in unlinked:
                if u.get("station_id"):
                    continue
                up = _digits(u.get("phone"))
                if len(up) < 7:
                    continue
                if up == st_phone or up in st_phone or st_phone in up:
                    u["station_id"] = st["id"]
                    st["owner_user_id"] = u["id"]
                    users_changed = True
                    stations_changed = True
                    break

    # 4) Single-station fallback: one open station of kind + unlinked operator(s)
    for kind in ("police", "fire"):
        kind_stations = [
            st
            for st in stations
            if (st.get("kind") or "").lower() == kind
            and (st.get("operating_status") or "open").lower() != "closed"
        ]
        if len(kind_stations) != 1:
            continue
        st = kind_stations[0]
        unlinked = [
            u
            for u in (udata.get("users") or [])
            if str(u.get("role") or "").lower() == kind and not u.get("station_id")
        ]
        if not unlinked:
            continue
        # Prefer empty-owner station; otherwise still bind operators so desks work
        for u in unlinked:
            u["station_id"] = st["id"]
            users_changed = True
        if not st.get("owner_user_id"):
            st["owner_user_id"] = unlinked[0]["id"]
            stations_changed = True

    if users_changed:
        save_users(udata)
    if stations_changed:
        fr.save_stations(sdata, save_json)
    return {"users_changed": users_changed, "stations_changed": stations_changed}


def _role_home(user):
    if user and user.get("role") == "hospital" and not user.get("hospital_id"):
        return url_for("hospital_register")
    return ROLE_HOME.get(user.get("role") if user else None, "/login")


def _run_escalations():
    settings = load_settings()
    timeout = int(settings.get("hospital_response_timeout_sec", 120))
    edata = load_emergencies()
    hdata = hl.load_hospitals(read_json, save_json)

    def _save(ed):
        save_emergencies(ed)

    def _load():
        return load_emergencies()

    hl.process_escalations(
        edata["emergencies"],
        hdata,
        timeout,
        _save,
        _load,
        lambda tt, tid, msg, rid=None: _notify(tt, tid, msg, rid),
    )


def _append_status(em, status, note=""):
    em.setdefault("status_history", [])
    em["status_history"].append({"status": status, "timestamp": now_str(), "note": note})
    em["status"] = status


def _create_healthcare_emergency(data, request_mode="emergency"):
    """Create emergency, route to nearest or preferred hospital."""
    settings = load_settings()
    timeout = int(settings.get("hospital_response_timeout_sec", 120))
    hdata = hl.seed_hospitals_if_empty(read_json, save_json)

    lat = data.get("latitude")
    lng = data.get("longitude")
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None, "Valid GPS location is required."

    edata = load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1

    fix = build_location_fix(data)
    fix["latitude"] = lat
    fix["longitude"] = lng

    emergency = {
        "id": eid,
        "user_id": session.get("user_id"),
        "type": normalize_type(data.get("type", "medical")),
        "request_mode": request_mode,
        "location": data.get("location") or fix.get("district", "Somalia"),
        "district": data.get("district") or fix.get("district", ""),
        "latitude": lat,
        "longitude": lng,
        "accuracy_m": fix.get("accuracy_m"),
        "method": fix.get("method", "gps"),
        "confidence": fix.get("confidence"),
        "timestamp": now_str(),
        "caller_name": data.get("name") or session.get("name", "Anonymous"),
        "phone": data.get("phone") or "Not provided",
        "assigned_to": "hospital",
        "location_history": [fix],
        "responder_status": {},
        "status_history": [],
        "notes": data.get("notes", ""),
    }
    _apply_tracking_fields(emergency, fix)

    if request_mode == "preferred":
        hid = int(data.get("hospital_id", 0))
        hospital = hl.get_hospital_by_id(hdata, hid)
        if not hospital:
            return None, "Hospital not found."
        emergency["escalation_queue"] = [hid]
        emergency["escalation_index"] = 0
        hl.assign_next_hospital(emergency, hdata, timeout)
        _append_status(emergency, "pending_hospital", f"Sent to chosen hospital: {hospital['name']}")
    else:
        emergency["escalation_queue"] = hl.build_escalation_queue(lat, lng, hdata)
        emergency["escalation_index"] = 0
        if not emergency["escalation_queue"]:
            emergency["status"] = "no_hospital_available"
            _stop_sos_tracking(emergency)
            _append_status(emergency, "no_hospital_available", "No hospitals available")
        else:
            hospital = hl.assign_next_hospital(emergency, hdata, timeout)
            if hospital:
                _append_status(emergency, "pending_hospital", f"Nearest: {hospital['name']}")

    edata["emergencies"].append(emergency)
    save_emergencies(edata)
    # Notify only after emergency row exists (MySQL FK on notifications.request_id)
    if emergency.get("assigned_hospital_id"):
        _notify(
            "hospital",
            emergency["assigned_hospital_id"],
            f"URGENT: Emergency request #{eid} — respond now",
            eid,
        )
    _notify("patient", session.get("user_id"), "Your emergency request has been submitted.", eid)
    append_audit("healthcare_request", "emergency", eid, {"mode": request_mode})
    _run_escalations()
    _schedule_ai_analysis(emergency, source="healthcare")
    return emergency, None


def normalize_user_record(user):
    """Ensure user matches the users table schema."""
    if "name" not in user and user.get("full_name"):
        user["name"] = user.pop("full_name")
    if "name" not in user:
        user["name"] = "Unknown User"
    user.pop("full_name", None)
    if "username" not in user:
        user["username"] = user.get("email", "user").split("@")[0]
    user.setdefault("phone", "")
    user.setdefault("status", "active")
    user.setdefault("activity", [])
    user.setdefault("profile_photo", "")
    user.setdefault("emergency_contact_name", "")
    user.setdefault("emergency_contact_phone", "")
    user.setdefault("emergency_contact_relation", "")
    user.setdefault("emergency_contact_email", "")
    user.setdefault("address", "")
    user.setdefault("city", "")
    user.setdefault("date_of_birth", "")
    user.setdefault("gender", "")
    user.setdefault("first_name", "")
    user.setdefault("middle_name", "")
    user.setdefault("last_name", "")
    user.setdefault("national_id_last4", "")
    user.setdefault("national_id_hash", None)
    user.setdefault("national_id_encrypted", None)
    user.setdefault("blood_type", "")
    user.setdefault("medical_notes", "")
    user.setdefault("allergies", "")
    user.setdefault("saved_locations", [])
    user.setdefault("notify_email_on_sos", True)
    user.setdefault("notify_email_on_dispatch", True)
    user["notify_email_on_sos"] = bool(user.get("notify_email_on_sos", True))
    user["notify_email_on_dispatch"] = bool(user.get("notify_email_on_dispatch", True))
    # Email verification (Step 1). Missing field → verified for legacy/seeded accounts.
    if "email_verified" not in user:
        user["email_verified"] = True
    else:
        user["email_verified"] = bool(user.get("email_verified"))
    user.setdefault("email_verify_token", None)
    user.setdefault("email_verify_expires", None)
    # Never expose raw national ID if somehow present
    user.pop("national_id", None)
    return user


def _compose_full_name(first_name, middle_name, last_name):
    parts = [p for p in (first_name, middle_name, last_name) if (p or "").strip()]
    return " ".join(parts).strip()


def _normalize_national_id(raw):
    return re.sub(r"\D+", "", (raw or "").strip())


def _hash_national_id(digits):
    key = (app.secret_key or "").encode("utf-8")
    return hmac.new(key, digits.encode("utf-8"), hashlib.sha256).hexdigest()


def _encrypt_national_id(digits):
    """Protect full national ID at rest (reversible with app secret)."""
    if not digits:
        return None
    key = hashlib.sha256((str(app.secret_key or "") + ":national-id").encode("utf-8")).digest()
    raw = digits.encode("utf-8")
    # Stream XOR with repeating key + HMAC tag for integrity
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
    tag = hmac.new(key, xored, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(tag + xored).decode("ascii")


def _national_id_fields(raw):
    """Return (last4, hash, encrypted) or (empty, None, None) when absent."""
    digits = _normalize_national_id(raw)
    if not digits:
        return "", None, None
    if len(digits) < 4:
        raise ValueError("National ID must contain at least 4 digits.")
    return digits[-4:], _hash_national_id(digits), _encrypt_national_id(digits)


def _parse_date_of_birth(value):
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Date of birth is required.")
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            dob = datetime.strptime(raw[:10], fmt)
            break
        except ValueError:
            dob = None
    if not dob:
        raise ValueError("Enter a valid date of birth.")
    if dob.date() > datetime.now().date():
        raise ValueError("Date of birth cannot be in the future.")
    if dob.year < 1900:
        raise ValueError("Enter a valid date of birth.")
    return dob.strftime("%Y-%m-%d")


def _validate_phone_required(phone, label="Phone number"):
    cleaned = (phone or "").strip()
    digits = re.sub(r"\D+", "", cleaned)
    if len(digits) < 7:
        raise ValueError(f"{label} is required.")
    return cleaned


def _notify_emergency_contact(user, emergency):
    """Email the registered emergency contact when a citizen submits SOS."""
    if not user:
        return
    to_email = normalize_email(user.get("emergency_contact_email") or "")
    if not to_email or not is_valid_email_format(to_email):
        _logger.warning(
            "No emergency contact email for user_id=%s emergency_id=%s phone=%s",
            user.get("id"),
            emergency.get("id"),
            user.get("emergency_contact_phone"),
        )
        return
    try:
        result = send_emergency_contact_alert_email(
            to_email=to_email,
            contact_name=(user.get("emergency_contact_name") or "").strip() or None,
            citizen_name=user_name(user),
            citizen_phone=user.get("phone") or emergency.get("phone"),
            emergency_type=emergency.get("type"),
            location=emergency.get("location") or emergency.get("district"),
            notes=emergency.get("notes"),
            emergency_id=emergency.get("id"),
            occurred_at=emergency.get("timestamp") or now_str(),
            latitude=emergency.get("latitude"),
            longitude=emergency.get("longitude"),
        )
        if not result.get("success"):
            _logger.error(
                "Emergency contact email failed to=%s error=%s",
                to_email,
                result.get("error"),
            )
        else:
            _logger.info(
                "Emergency contact notified to=%s emergency_id=%s",
                to_email,
                emergency.get("id"),
            )
    except Exception:
        _logger.exception("Emergency contact notify failed emergency_id=%s", emergency.get("id"))


def _user_is_email_verified(user):
    """Legacy users without the field are treated as verified."""
    if not user:
        return False
    if "email_verified" not in user:
        return True
    return bool(user.get("email_verified"))


def _email_verify_minutes():
    return int(os.environ.get("EMAIL_VERIFICATION_MINUTES", "30"))


def _issue_email_verification(user):
    """Attach a one-time 6-digit email verification OTP (hashed). Returns plain OTP."""
    minutes = _email_verify_minutes()
    otp = f"{secrets.randbelow(1_000_000):06d}"
    user["email_verified"] = False
    user["email_verify_token"] = _hash_password_otp(otp)
    user["email_verify_expires"] = (
        datetime.now() + timedelta(minutes=minutes)
    ).strftime("%Y-%m-%d %H:%M:%S")
    user["email_verify_attempts"] = 0
    return otp


def _verify_email_otp(user, otp_code):
    """Validate signup email OTP. Returns (ok, error_message)."""
    if not user:
        return False, "Invalid or expired code."
    stored = user.get("email_verify_token")
    expires = parse_dt(user.get("email_verify_expires"))
    if not stored or expires < datetime.now():
        user["email_verify_token"] = None
        user["email_verify_expires"] = None
        user.pop("email_verify_attempts", None)
        return False, "This code has expired. Request a new one."
    attempts = int(user.get("email_verify_attempts") or 0)
    if attempts >= 5:
        user["email_verify_token"] = None
        user["email_verify_expires"] = None
        user.pop("email_verify_attempts", None)
        return False, "Too many incorrect attempts. Request a new code."
    candidate = (otp_code or "").strip().replace(" ", "")
    if not candidate.isdigit() or len(candidate) != 6:
        user["email_verify_attempts"] = attempts + 1
        return False, "Enter the 6-digit code from your email."
    expected = _hash_password_otp(candidate)
    if len(str(stored)) != len(expected) or not hmac.compare_digest(str(stored), expected):
        user["email_verify_attempts"] = attempts + 1
        left = 5 - int(user.get("email_verify_attempts") or 0)
        if left <= 0:
            user["email_verify_token"] = None
            user["email_verify_expires"] = None
            user.pop("email_verify_attempts", None)
            return False, "Too many incorrect attempts. Request a new code."
        return False, f"Incorrect code. {left} attempt(s) remaining."
    user["email_verified"] = True
    user["email_verify_token"] = None
    user["email_verify_expires"] = None
    user.pop("email_verify_attempts", None)
    return True, None


def _send_user_verification_email(user, otp_code):
    """Send verification OTP email. Returns send() result."""
    result = send_email_verification_otp_email(
        to_email=user.get("email"),
        otp_code=otp_code,
        user_name=user_name(user),
        minutes=_email_verify_minutes(),
    )
    if not result.get("success"):
        _logger.error(
            "Verification email failed to=%s provider=%s error=%s",
            user.get("email"),
            result.get("provider"),
            result.get("error"),
        )
    return result


def _flash_email_send_failure(context="verification"):
    """User-facing email failure only — never expose SMTP/.env/exception details."""
    if context == "signup":
        flash(
            "Account created, but we could not send the verification code. "
            "Please use Resend code on the verification page, or try again later.",
            "warning",
        )
    else:
        flash(
            "We could not send the verification code right now. "
            "Please try again in a few minutes or contact support.",
            "error",
        )


def _render_login_page(pending_email=None, needs_verification=False):
    """Render login with verification UX flags (keeps existing auth routes)."""
    pending = (pending_email if pending_email is not None else request.args.get("pending_email") or "").strip()
    return render_template(
        "login.html",
        pending_email=pending,
        needs_verification=bool(needs_verification or pending),
    )


def user_name(user):
    if not user:
        return "User"
    return user.get("name") or user.get("full_name", "User")


def prepare_user_for_template(user):
    if not user:
        return None
    u = normalize_user_record(dict(user))
    return u


def public_user_profile(user):
    """Safe user dict for API/JSON responses — never includes secrets."""
    if not user:
        return None
    u = normalize_user_record(dict(user))
    return {
        k: u.get(k)
        for k in (
            "id",
            "name",
            "email",
            "phone",
            "role",
            "status",
            "profile_photo",
            "emergency_contact_name",
            "emergency_contact_phone",
            "emergency_contact_relation",
            "emergency_contact_email",
            "address",
            "city",
            "date_of_birth",
            "gender",
            "first_name",
            "middle_name",
            "last_name",
            "national_id_last4",
            "blood_type",
            "medical_notes",
            "allergies",
            "created_at",
            "last_login",
            "saved_locations",
            "email_verified",
            "hospital_id",
        )
        if k in u or k in (
            "id", "name", "email", "phone", "role", "status", "email_verified",
            "national_id_last4", "first_name", "last_name", "gender", "allergies",
        )
    }


def _wants_json_response():
    if (request.path or "").startswith("/api/"):
        return True
    accept = request.accept_mimetypes
    return accept.best == "application/json"


def _safe_client_message(exc, fallback="Something went wrong. Please try again."):
    """Never expose stack traces, SMTP, SQL, or filesystem paths to clients."""
    msg = str(exc or "").strip()
    if not msg or len(msg) > 200:
        return fallback
    lowered = msg.lower()
    blocked = (
        "traceback",
        "smtp",
        ".env",
        "password",
        "pymysql",
        "operationalerror",
        "sql syntax",
        "file \"",
        "line ",
        "modulenotfound",
        "permissionerror",
        "secret",
    )
    if any(b in lowered for b in blocked):
        return fallback
    return msg


def _api_error(exc, fallback="Request failed. Please try again.", status=400):
    _logger.exception("API error: %s", exc)
    return jsonify({"success": False, "message": _safe_client_message(exc, fallback)}), status


def _auth_challenge(message="Please log in to continue.", category="warning"):
    if _wants_json_response():
        return jsonify({"success": False, "message": message, "auth_required": True}), 401
    flash(message, category)
    return redirect(url_for("login", next=request.path))


def _mysql_backend():
    if not USE_MYSQL:
        return None
    try:
        from database import mysql_store
        if mysql_store.available():
            return mysql_store
    except ImportError:
        pass
    return None


def _storage_status():
    """Live storage diagnostics for admin health / system settings."""
    info = {
        "backend": "mysql" if USE_MYSQL else "json",
        "mysql_required": not _json_store_allowed(),
        "live": False,
        "database": None,
        "user": None,
        "host": None,
        "port": None,
        "table_counts": {},
        "error": None,
    }
    if not USE_MYSQL:
        info["error"] = "Running on JSON test store — not production MySQL"
        return info
    try:
        from database.connection import load_config
        from database import mysql_store as _ms

        cfg = load_config()
        info["database"] = cfg.get("database")
        info["user"] = cfg.get("user")
        info["host"] = cfg.get("host")
        info["port"] = cfg.get("port")
        with _ms._db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DATABASE() AS db, USER() AS u")
                row = cur.fetchone() or {}
                info["database"] = row.get("db") or info["database"]
                info["user"] = row.get("u") or info["user"]
        info["table_counts"] = _ms.table_counts()
        info["live"] = True
    except Exception as exc:
        info["error"] = str(exc)
        info["live"] = False
    return info


def load_users():
    data = read_json(USERS_FILE, {"users": [], "next_id": 1})
    data["users"] = [normalize_user_record(u) for u in data.get("users", [])]
    return data


def save_users(data):
    data["users"] = [normalize_user_record(u) for u in data.get("users", [])]
    save_json(USERS_FILE, data)


def load_emergencies():
    data = read_json(EMERGENCIES_FILE, {"emergencies": [], "next_id": 1})
    data["emergencies"] = [normalize_emergency_record(e) for e in data.get("emergencies", [])]
    return data


def save_emergencies(data):
    data["emergencies"] = [normalize_emergency_record(e) for e in data.get("emergencies", [])]
    save_json(EMERGENCIES_FILE, data)


def get_emergency_by_id(eid):
    edata = load_emergencies()
    for em in edata["emergencies"]:
        if em["id"] == eid:
            return em, edata
    return None, edata


def _apply_tracking_fields(emergency, fix=None):
    """Mark emergency as actively tracked with GPS."""
    emergency["tracking_active"] = True
    emergency["last_location_update"] = now_str()
    if fix:
        emergency.setdefault("location_history", [])
        if not emergency["location_history"]:
            emergency["location_history"].append(fix)


def _can_access_emergency(em, role, user=None):
    """Citizen owner, assigned hospital/station, responder role, or admin."""
    if not em:
        return False
    if role in STAFF_ADMIN_ROLES:
        return True
    if role == "citizen":
        return em.get("user_id") == session.get("user_id")
    if role == "hospital":
        user = user or current_user()
        hid = _user_hospital_id(user)
        return hid and em.get("assigned_hospital_id") == hid
    if role in ("police", "fire"):
        user = user or current_user()
        sid = _user_station_id(user)
        if not sid:
            return False
        import police_logic as pl
        if not matches_filter(em.get("type"), ROLE_API_TYPE[role]):
            return False
        return pl.emergency_visible_to_station(em, sid, role)
    if role in ROLE_API_TYPE:
        return matches_filter(em.get("type"), ROLE_API_TYPE[role])
    return False


def _live_place_label(lat, lng, fallback=""):
    """Resolve a human place name from Google Maps reverse geocoding (live)."""
    if lat is None or lng is None:
        return fallback or ""
    api_key = _google_maps_api_key()
    if not (api_key and _use_google_maps()):
        return fallback or ""
    try:
        result = _google_geocode_reverse(float(lat), float(lng), api_key)
        if not result:
            return fallback or ""
        return (
            result.get("address")
            or result.get("display_name")
            or result.get("district")
            or fallback
            or ""
        )
    except Exception:
        return fallback or ""


def build_location_fix(data):
    lat = data.get("latitude")
    lng = data.get("longitude")
    if lat is not None and lng is not None:
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            lat, lng = None, None
    confidence = data.get("confidence")
    try:
        confidence = int(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    accuracy = data.get("accuracy_m") or data.get("accuracy")
    try:
        accuracy = float(accuracy) if accuracy is not None else None
    except (TypeError, ValueError):
        accuracy = None
    speed = data.get("speed_mps")
    heading = data.get("heading")
    try:
        speed = float(speed) if speed is not None else None
    except (TypeError, ValueError):
        speed = None
    try:
        heading = float(heading) if heading is not None else None
    except (TypeError, ValueError):
        heading = None
    if lat is not None and lng is not None and not hl.is_in_somalia(lat, lng):
        lat, lng = None, None
    district = (data.get("district") or "").strip()
    # Prefer live Google place names over bare coordinates / empty labels
    looks_like_coords = bool(re.match(r"^-?\d+\.?\d*\s*,\s*-?\d+\.?\d*$", district))
    if lat is not None and lng is not None and (not district or looks_like_coords):
        district = _live_place_label(lat, lng, district) or district
    return {
        "timestamp": now_str(),
        "latitude": lat,
        "longitude": lng,
        "district": district,
        "building": data.get("building") or "",
        "floor": data.get("floor") or "",
        "room": data.get("room") or "",
        "altitude_m": data.get("altitude_m"),
        "accuracy_m": accuracy,
        "uncertainty_m": accuracy,
        "speed_mps": speed,
        "heading": heading,
        "method": data.get("method") or "gps",
        "confidence": confidence,
    }


def load_content():
    stored = read_json(CONTENT_FILE, DEFAULT_CONTENT.copy())
    merged = DEFAULT_CONTENT.copy()
    merged.update(stored)
    return merged


def save_content(content):
    save_json(CONTENT_FILE, content)


def load_settings():
    stored = read_json(SETTINGS_FILE, DEFAULT_SETTINGS.copy())
    merged = DEFAULT_SETTINGS.copy()
    merged.update(stored)
    return merged


def save_settings(settings):
    save_json(SETTINGS_FILE, settings)
    _apply_runtime_settings(settings)


def get_response_stations():
    """Police/fire stations from MySQL response_stations table, then settings fallback."""
    try:
        import facility_registry as fr
        from_table = fr.stations_as_settings_map(read_json)
        if from_table:
            return from_table
    except Exception:
        logging.getLogger(__name__).exception("Failed loading response_stations table")
    try:
        stored = load_settings().get("response_stations")
        if isinstance(stored, dict) and stored:
            out = {}
            for key in ("police", "fire"):
                row = stored.get(key)
                if isinstance(row, dict) and row.get("latitude") is not None and row.get("longitude") is not None:
                    out[key] = row
            if out:
                return out
    except Exception:
        logging.getLogger(__name__).exception("Failed loading response_stations from MySQL settings")
    return {k: dict(v) for k, v in DEFAULT_RESPONSE_STATIONS.items()}


def get_response_station_list():
    """All open police/fire stations with coords (for true nearest ranking)."""
    try:
        import facility_registry as fr
        rows = fr.open_stations_with_coords(read_json)
        if rows:
            return rows
    except Exception:
        logging.getLogger(__name__).exception("Failed loading open stations list")
    # Fallback: legacy single-station map → list
    out = []
    for kind, row in (get_response_stations() or {}).items():
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item.setdefault("kind", kind)
        if item.get("latitude") is not None and item.get("longitude") is not None:
            out.append(item)
    return out


def _apply_runtime_settings(settings=None):
    """Push selected settings into process env so email/AI pick them up without restart."""
    settings = settings or load_settings()
    # SMTP — only override when host/user provided from dashboard
    if settings.get("smtp_host"):
        os.environ["SMTP_HOST"] = str(settings.get("smtp_host") or "")
    if settings.get("smtp_port") is not None:
        os.environ["SMTP_PORT"] = str(settings.get("smtp_port") or 587)
    if settings.get("smtp_user"):
        os.environ["SMTP_USER"] = str(settings.get("smtp_user") or "")
    if settings.get("smtp_password"):
        os.environ["SMTP_PASSWORD"] = str(settings.get("smtp_password") or "")
    if settings.get("smtp_from"):
        os.environ["SMTP_FROM"] = str(settings.get("smtp_from") or "")
    if "smtp_use_tls" in settings:
        os.environ["SMTP_USE_TLS"] = "true" if settings.get("smtp_use_tls") else "false"
    if settings.get("smtp_enabled") is False:
        os.environ["EMAIL_PROVIDER"] = "memory"
    elif settings.get("smtp_host"):
        os.environ["EMAIL_PROVIDER"] = "smtp"
    if settings.get("ai_provider"):
        os.environ["AI_PROVIDER"] = str(settings.get("ai_provider") or "rule_based")
    if settings.get("openai_api_key"):
        os.environ["OPENAI_API_KEY"] = str(settings.get("openai_api_key") or "")
    if settings.get("google_maps_api_key"):
        os.environ["GOOGLE_MAPS_API_KEY"] = str(settings.get("google_maps_api_key") or "")
    if settings.get("app_name"):
        os.environ["APP_NAME"] = str(settings.get("app_name") or "GurmadNet AI")
    try:
        minutes = int(settings.get("session_timeout_minutes") or 120)
    except (TypeError, ValueError):
        minutes = 120
    app.permanent_session_lifetime = timedelta(minutes=max(15, minutes))
    try:
        from email_service.factory import clear_email_provider_cache

        clear_email_provider_cache()
    except Exception:
        pass


def _public_settings_view(settings):
    """Settings for API responses — secrets masked."""
    out = dict(settings)
    for key in SECRET_SETTING_KEYS:
        if out.get(key):
            out[key] = ""
            out[key + "_set"] = True
        else:
            out[key + "_set"] = False
    return out


def _coerce_setting_value(key, value):
    default = DEFAULT_SETTINGS.get(key)
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if value is None:
        return ""
    return value


def _sync_branding_to_content(settings):
    """Keep CMS app_name in sync with system settings when Super Admin saves."""
    name = (settings.get("app_name") or "").strip()
    if not name:
        return
    content = load_content()
    if content.get("app_name") != name:
        content["app_name"] = name
        save_content(content)


def _password_policy_error(password):
    """Return an error message if password fails configured policy, else None."""
    settings = load_settings()
    pw = password or ""
    try:
        min_len = int(settings.get("password_min_length") or 6)
    except (TypeError, ValueError):
        min_len = 6
    min_len = max(4, min_len)
    if len(pw) < min_len:
        return f"Password must be at least {min_len} characters."
    if settings.get("password_require_upper") and not any(c.isupper() for c in pw):
        return "Password must include an uppercase letter."
    if settings.get("password_require_digit") and not any(c.isdigit() for c in pw):
        return "Password must include a digit."
    if settings.get("password_require_special") and not re.search(r"[^A-Za-z0-9]", pw):
        return "Password must include a special character."
    return None


def _account_lockout_active(user):
    locked_until = user.get("locked_until")
    if not locked_until:
        return False
    try:
        return parse_dt(locked_until) > datetime.now()
    except Exception:
        return False


def _register_failed_login(user, udata):
    settings = load_settings()
    try:
        max_attempts = int(settings.get("security_login_max_attempts") or 5)
    except (TypeError, ValueError):
        max_attempts = 5
    try:
        lock_mins = int(settings.get("security_lockout_minutes") or 15)
    except (TypeError, ValueError):
        lock_mins = 15
    fails = int(user.get("failed_logins") or 0) + 1
    user["failed_logins"] = fails
    if fails >= max(1, max_attempts):
        user["locked_until"] = (datetime.now() + timedelta(minutes=max(1, lock_mins))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        user["failed_logins"] = 0
    save_users(udata)


def _clear_failed_logins(user):
    user["failed_logins"] = 0
    user.pop("locked_until", None)


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_iso():
    return now_str()


def parse_dt(value):
    if not value:
        return datetime.min
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(str(value)[:26], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.min


def _avg_response_minutes(emergencies):
    """Measured dispatch latency from status_history. None when no samples exist."""
    samples = []
    for e in emergencies or []:
        hist = e.get("status_history") or []
        start = parse_dt(e.get("timestamp")) if e.get("timestamp") else None
        if not start or start == datetime.min:
            continue
        dispatched_at = None
        for h in hist:
            st = (h.get("status") or "").lower()
            if st in ("dispatched", "accepted", "in_progress") and h.get("timestamp"):
                dispatched_at = parse_dt(h.get("timestamp"))
                break
        if dispatched_at and dispatched_at != datetime.min and dispatched_at >= start:
            mins = (dispatched_at - start).total_seconds() / 60.0
            if 0 <= mins <= 180:
                samples.append(mins)
    if not samples:
        return None
    return round(sum(samples) / len(samples), 1)


def seed_defaults():
    """Initialize schema/content only — never creates demo or seed user accounts."""
    if USE_MYSQL:
        try:
            from database import mysql_store as _ms

            _ms.ensure_call_center_schema()
            _ms.ensure_email_verification_schema()
            _ms.ensure_citizen_profile_schema()
            _ms.ensure_admin_profile_schema()
            _ms.ensure_hospital_logo_schema()
            _ms.ensure_ambulance_gps_share_schema()
            _ms.ensure_ai_schema()
            integrity = _ms.ensure_production_integrity()
            if integrity.get("changes"):
                logging.getLogger(__name__).info(
                    "MySQL integrity changes: %s", integrity.get("changes")
                )
        except Exception:
            logging.getLogger(__name__).exception("MySQL schema ensure failed")

    try:
        sync = _sync_hospital_account_links()
        if sync.get("users_changed") or sync.get("hospitals_changed"):
            logging.getLogger(__name__).info("Hospital↔user link sync: %s", sync)
    except Exception:
        logging.getLogger(__name__).exception("Hospital account link sync failed")

    try:
        sync_st = _sync_station_account_links()
        if sync_st.get("users_changed") or sync_st.get("stations_changed"):
            logging.getLogger(__name__).info("Station↔user link sync: %s", sync_st)
    except Exception:
        logging.getLogger(__name__).exception("Station account link sync failed")

    # Seed CMS / settings only when empty — never overwrite live MySQL data
    if USE_MYSQL:
        try:
            from database import mysql_store as _ms

            if not _ms.content_row_exists():
                save_content(DEFAULT_CONTENT.copy())
            if not _ms.settings_row_exists():
                save_settings(DEFAULT_SETTINGS.copy())
        except Exception:
            logging.getLogger(__name__).exception("MySQL content/settings seed check failed")
    else:
        content_path = _json_file_path(CONTENT_FILE)
        settings_path = _json_file_path(SETTINGS_FILE)
        if not os.path.exists(content_path):
            save_content(DEFAULT_CONTENT.copy())
        if not os.path.exists(settings_path):
            save_settings(DEFAULT_SETTINGS.copy())

    # Drop legacy demo branding left in CMS from earlier seed content
    try:
        content = load_content()
        demo_titles = {
            "hospital_dashboard_title": "Aamin Ambulance - Hospital Dashboard",
            "police_dashboard_title": "Hamar Police - Police Dashboard",
            "fire_dashboard_title": "KM4 Fire Station - Fire Dashboard",
        }
        changed = False
        for key, old in demo_titles.items():
            if content.get(key) == old:
                content[key] = DEFAULT_CONTENT[key]
                changed = True
        if changed:
            save_content(content)
    except Exception:
        pass
    hl.seed_hospitals_if_empty(read_json, save_json)
    hl.migrate_all_hospitals(read_json, save_json)
    seed_announcements_if_empty()
    # Persist police/fire stations into MySQL settings (single source of truth)
    try:
        settings = load_settings()
        stations = settings.get("response_stations")
        if not isinstance(stations, dict) or not stations:
            settings["response_stations"] = {
                k: dict(v) for k, v in DEFAULT_RESPONSE_STATIONS.items()
            }
            save_settings(settings)
    except Exception:
        logging.getLogger(__name__).exception("response_stations MySQL seed failed")
    if USE_MYSQL:
        try:
            from database import mysql_store as _ms

            _ms.ensure_super_admin_role()
        except Exception:
            logging.getLogger(__name__).exception("MySQL super_admin role ensure failed")
    _migrate_legacy_admins_to_super()
    try:
        _apply_runtime_settings()
    except Exception:
        logging.getLogger(__name__).exception("Runtime settings apply failed")


# Gunicorn / import path: ensure MySQL schema + settings seed (not only __main__)
_BOOT_SEEDED = False


def ensure_mysql_boot():
    """Idempotent boot for gunicorn workers — MySQL single source of truth."""
    global _BOOT_SEEDED
    if _BOOT_SEEDED:
        return
    if not USE_MYSQL:
        _BOOT_SEEDED = True
        return
    try:
        seed_defaults()
    except Exception:
        logging.getLogger(__name__).exception("MySQL boot seed_defaults failed")
    _BOOT_SEEDED = True


@app.before_request
def _mysql_boot_before_request():
    ensure_mysql_boot()


@app.before_request
def _ensure_valid_session():
    """Drop stale sessions after user purge/delete so links don't dump into wrong roles."""
    uid = session.get("user_id")
    if not uid:
        return None
    # Skip static assets
    if (request.path or "").startswith("/static/"):
        return None
    user, _ = get_user_by_id(uid)
    if not user:
        session.clear()
        return None
    status = (user.get("status") or "active").lower()
    if status in ("blocked", "disabled", "inactive", "deleted"):
        session.clear()
        return None
    # Keep session role/name in sync with DB
    if user.get("role") and user.get("role") != session.get("role"):
        session["role"] = user["role"]
    if user.get("name"):
        session["name"] = user["name"]
    if user.get("email"):
        session["email"] = user["email"]
    return None


def get_user_by_login(login):
    key = (login or "").strip().lower()
    udata = load_users()
    for user in udata["users"]:
        if user["email"].lower() == key:
            return user, udata
        if user.get("username", "").lower() == key:
            return user, udata
    return None, udata


def get_user_by_id(uid):
    udata = load_users()
    try:
        uid_int = int(uid)
    except (TypeError, ValueError):
        uid_int = None
    for user in udata["users"]:
        if user.get("id") == uid or (uid_int is not None and user.get("id") == uid_int):
            return user, udata
    return None, udata


def log_activity(user, action):
    entry = {"action": action, "timestamp": now_str()}
    user.setdefault("activity", [])
    user["activity"] = ([entry] + user["activity"])[:50]


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    user, _ = get_user_by_id(uid)
    return prepare_user_for_template(user)


def login_user(user):
    user = normalize_user_record(user)
    session.permanent = True
    session["user_id"] = user["id"]
    session["role"] = user["role"]
    session["name"] = user["name"]
    session["email"] = user["email"]
    return True


def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return _auth_challenge("Please log in to continue.")
        settings = load_settings()
        if settings.get("maintenance_mode") and not _is_staff_admin():
            if _wants_json_response():
                return jsonify({"success": False, "message": "System is under maintenance."}), 503
            flash("System is under maintenance. Try again later.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return wrapped


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                return _auth_challenge("Please log in to continue.")
            user = current_user()
            if not user:
                session.clear()
                return _auth_challenge("Please log in again.")
            # Prefer DB role over stale session role
            role = user.get("role") or session.get("role")
            if role and role != session.get("role"):
                session["role"] = role
            settings = load_settings()
            if settings.get("maintenance_mode") and not _is_staff_admin(role):
                if _wants_json_response():
                    return jsonify({"success": False, "message": "System is under maintenance."}), 503
                flash("System is under maintenance.", "error")
                return redirect(url_for("login"))
            if role not in roles:
                if _wants_json_response():
                    return jsonify({"success": False, "message": "Forbidden"}), 403
                # Staff hitting the wrong desk → quiet redirect home (no scary flash)
                if role in ROLE_HOME:
                    return redirect(_role_home(user))
                flash("You do not have permission to access that page.", "error")
                return redirect(url_for("login"))
            return f(*args, **kwargs)

        return wrapped

    return decorator


def _sync_session_role():
    """Refresh session role from DB (e.g. after admin → super_admin migration)."""
    uid = session.get("user_id")
    if not uid:
        return
    user, _ = get_user_by_id(uid)
    if not user:
        return
    if user.get("role") and user.get("role") != session.get("role"):
        session["role"] = user["role"]
    if user.get("name"):
        session["name"] = user["name"]
    if user.get("email"):
        session["email"] = user["email"]


def admin_required(f):
    """Allow Super Admin or regular Admin into the admin area."""
    guarded = role_required("super_admin", "admin")(f)

    @wraps(f)
    def wrapped(*args, **kwargs):
        _sync_session_role()
        return guarded(*args, **kwargs)

    return wrapped


def super_admin_required(f):
    """Restrict a route to Super Admin only."""
    return role_required("super_admin")(f)


def call_center_required(f):
    return role_required("call_center")(f)


def normalize_type(raw_type):
    if not raw_type:
        return "medical"
    t = str(raw_type).strip().lower().replace(" ", "_")
    mapping = {
        "medical": "medical",
        "accident": "accident",
        "fire": "fire",
        "security": "security",
        "family_help": "family_help",
        "family": "family_help",
        "other": "medical",
    }
    return mapping.get(t, t)


def matches_filter(emergency_type, filter_type):
    if not filter_type:
        return True
    allowed = TYPE_MAP.get(filter_type.lower(), [filter_type.lower()])
    return emergency_type in allowed


def emergencies_today_count():
    edata = load_emergencies()
    today = datetime.now().date()
    return sum(
        1
        for e in edata["emergencies"]
        if parse_dt(e["timestamp"]).date() == today
    )


def _google_maps_api_key():
    settings = load_settings()
    return (
        (settings.get("google_maps_api_key") or "").strip()
        or (os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()
    )


def _use_google_maps(settings=None):
    """Google Maps is primary whenever an API key is configured."""
    settings = settings or load_settings()
    key = (settings.get("google_maps_api_key") or "").strip() or (
        os.environ.get("GOOGLE_MAPS_API_KEY") or ""
    ).strip()
    if not key:
        return False
    provider = str(settings.get("maps_provider") or "google").strip().lower()
    return provider != "leaflet"


@app.context_processor
def inject_globals():
    settings = load_settings()
    content = load_content()
    # Prefer system setting app_name when set
    if settings.get("app_name"):
        content = dict(content)
        content["app_name"] = settings.get("app_name") or content.get("app_name")
    gmaps_key = _google_maps_api_key()
    use_gmaps = _use_google_maps(settings)
    try:
        default_lat = float(settings.get("maps_default_lat") or 2.0469)
        default_lng = float(settings.get("maps_default_lng") or 45.3182)
        default_zoom = int(settings.get("maps_default_zoom") or 14)
    except (TypeError, ValueError):
        default_lat, default_lng, default_zoom = 2.0469, 45.3182, 14
    maps_config = {
        "provider": "google" if use_gmaps else "leaflet",
        "apiKeyPresent": bool(gmaps_key),
        "defaultLat": default_lat,
        "defaultLng": default_lng,
        "defaultZoom": default_zoom,
    }
    return {
        "content": content,
        "settings": settings,
        "auth_user": current_user(),
        "google_maps_key": gmaps_key,
        "use_google_maps": use_gmaps,
        "maps_config": maps_config,
        "csrf_token": generate_csrf,
        "app_logo_url": settings.get("app_logo_url") or "",
        "app_favicon_url": settings.get("app_favicon_url") or "",
        "brand_primary_color": settings.get("brand_primary_color") or "#2563eb",
    }


@app.after_request
def _inject_csrf_assets(response):
    """Ensure every HTML page has CSRF meta + fetch helper (does not alter APIs)."""
    if not app.config.get("WTF_CSRF_ENABLED", True):
        return response
    ctype = (response.headers.get("Content-Type") or "").lower()
    if "text/html" not in ctype:
        return response
    try:
        html = response.get_data(as_text=True)
    except Exception:
        return response
    if not html or "csrf-token" in html:
        return response
    token = generate_csrf()
    meta = f'<meta name="csrf-token" content="{token}">'
    script = f'<script src="{url_for("static", filename="js/csrf.js")}"></script>'
    if "</head>" in html:
        html = html.replace("</head>", meta + "\n</head>", 1)
    if "</body>" in html:
        html = html.replace("</body>", script + "\n</body>", 1)
    response.set_data(html)
    return response


def _password_otp_minutes():
    try:
        return max(2, min(30, int(os.environ.get("PASSWORD_OTP_MINUTES", "10"))))
    except ValueError:
        return 10


def _hash_password_otp(otp_code):
    """HMAC-SHA256 of OTP bound to app secret — never store raw OTP."""
    key = (app.secret_key or "").encode("utf-8")
    return hmac.new(key, str(otp_code).encode("utf-8"), hashlib.sha256).hexdigest()


def _clear_password_otp(user):
    user["email_verify_token"] = None
    user["email_verify_expires"] = None
    user.pop("reset_otp_attempts", None)


def _issue_password_otp(user):
    """Create a one-time 6-digit OTP for password reset. Returns plain OTP."""
    minutes = _password_otp_minutes()
    otp = f"{secrets.randbelow(1_000_000):06d}"
    user["email_verify_token"] = _hash_password_otp(otp)
    user["email_verify_expires"] = (
        datetime.now() + timedelta(minutes=minutes)
    ).strftime("%Y-%m-%d %H:%M:%S")
    user["reset_otp_attempts"] = 0
    # Invalidate any prior reset link token
    user["reset_token"] = None
    user["reset_expires"] = None
    return otp, minutes


def _verify_password_otp(user, otp_code):
    """
    Validate OTP. Returns (ok, error_message).
    On success: OTP is consumed and a short-lived reset_token is issued.
    """
    if not user:
        return False, "Invalid or expired code."
    stored = user.get("email_verify_token")
    expires = parse_dt(user.get("email_verify_expires"))
    if not stored or expires < datetime.now():
        _clear_password_otp(user)
        return False, "This code has expired. Request a new one."
    attempts = int(user.get("reset_otp_attempts") or 0)
    if attempts >= 5:
        _clear_password_otp(user)
        return False, "Too many incorrect attempts. Request a new code."
    candidate = (otp_code or "").strip().replace(" ", "")
    if not candidate.isdigit() or len(candidate) != 6:
        user["reset_otp_attempts"] = attempts + 1
        return False, "Enter the 6-digit code from your email."
    expected = _hash_password_otp(candidate)
    if len(str(stored)) != len(expected) or not hmac.compare_digest(str(stored), expected):
        user["reset_otp_attempts"] = attempts + 1
        left = 5 - int(user.get("reset_otp_attempts") or 0)
        if left <= 0:
            _clear_password_otp(user)
            return False, "Too many incorrect attempts. Request a new code."
        return False, f"Incorrect code. {left} attempt(s) remaining."
    # Consume OTP (one-time) and issue password-reset token
    _clear_password_otp(user)
    token = secrets.token_urlsafe(32)
    user["reset_token"] = token
    user["reset_expires"] = (
        datetime.now() + timedelta(minutes=15)
    ).strftime("%Y-%m-%d %H:%M:%S")
    return True, token


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        user = current_user()
        return redirect(_role_home(user))

    if request.method == "POST":
        login_id = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not login_id:
            flash("Please enter your email address.", "error")
            return _render_login_page()
        if not password:
            flash("Please enter your password.", "error")
            return _render_login_page(pending_email=login_id)
        if "@" in login_id and not is_valid_email_format(login_id):
            flash("Please enter a valid email address.", "error")
            return _render_login_page(pending_email=login_id)

        user, udata = get_user_by_login(login_id)

        if user and user.get("status") == "blocked":
            flash("Your account has been blocked. Contact admin.", "error")
        elif user and _account_lockout_active(user):
            flash("Too many failed attempts. Try again later.", "error")
        elif user and check_password_hash(user["password_hash"], password):
            if (
                user.get("role") == "citizen"
                and load_settings().get("auth_require_email_verification", True)
                and not _user_is_email_verified(user)
            ):
                flash(
                    "Please verify your email before logging in. "
                    "Enter the code we sent to your inbox.",
                    "error",
                )
                return redirect(url_for("verify_email_code", email=user.get("email")))
            _clear_failed_logins(user)
            user["last_login"] = now_str()
            log_activity(user, "Logged in")
            save_users(udata)
            login_user(user)
            flash("Welcome back, " + user_name(user) + "!", "success")
            nxt = request.args.get("next")
            if nxt and nxt.startswith("/"):
                return redirect(nxt)
            return redirect(_role_home(user))
        else:
            if user:
                _register_failed_login(user, udata)
            flash("Invalid email or password. Please try again.", "error")

    return _render_login_page()


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Step 1: request a one-time password reset code by email."""
    if request.method == "POST":
        email = normalize_email(request.form.get("email", ""))
        if not is_valid_email_format(email):
            flash("Please enter a valid email address.", "error")
            return render_template("forgot_password.html", email=email)

        user, udata = get_user_by_login(email)
        if not user:
            flash(
                "No account found with that email address. "
                "Check the spelling or create a new account.",
                "error",
            )
            return render_template("forgot_password.html", email=email)

        otp, minutes = _issue_password_otp(user)
        save_users(udata)
        result = send_password_reset_otp_email(
            to_email=email,
            otp_code=otp,
            user_name=user_name(user),
            minutes=minutes,
        )
        if not result.get("success"):
            err = (result.get("error") or "").lower()
            _logger.error(
                "Password reset OTP email failed to=%s error=%s",
                email,
                result.get("error"),
            )
            # Roll back OTP so a failed send cannot leave a usable code
            _clear_password_otp(user)
            save_users(udata)
            if "smtp not configured" in err or "smtp_password" in err:
                flash(
                    "Email delivery is not configured yet. "
                    "Ask the administrator to set SMTP_PASSWORD in .env "
                    "(Gmail App Password), or reset the password from Admin.",
                    "error",
                )
            else:
                flash(
                    "We could not send a reset code right now. Please try again later.",
                    "error",
                )
            return render_template("forgot_password.html", email=email)

        _logger.info("Password reset OTP sent to %s (expires in %s min)", email, minutes)
        flash(
            "A one-time reset code has been sent to your email. Check your inbox.",
            "success",
        )
        return redirect(url_for("verify_reset_otp", email=email))

    return render_template("forgot_password.html")


@app.route("/forgot-password/verify", methods=["GET", "POST"])
def verify_reset_otp():
    """Step 2: verify the emailed OTP, then continue to set a new password."""
    email = normalize_email(
        request.values.get("email") or request.args.get("email") or ""
    )
    if request.method == "GET":
        return render_template("verify_reset_otp.html", email=email)

    if not is_valid_email_format(email):
        flash("Please enter a valid email address.", "error")
        return redirect(url_for("forgot_password"))

    otp_code = request.form.get("otp") or request.form.get("code") or ""
    user, udata = get_user_by_login(email)
    # Constant-ish messaging when user missing
    if not user:
        flash("Invalid or expired code.", "error")
        return render_template("verify_reset_otp.html", email=email)

    ok, payload = _verify_password_otp(user, otp_code)
    save_users(udata)
    if not ok:
        flash(payload, "error")
        return render_template("verify_reset_otp.html", email=email)

    token = payload
    flash("Code verified. Choose a new password.", "success")
    return redirect(url_for("reset_password", token=token))


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    """Step 3: set a new password after OTP verification (one-time token)."""
    udata = load_users()
    user = None
    for u in udata["users"]:
        if u.get("reset_token") == token:
            user = u
            break
    if not user:
        flash("Invalid or expired reset session. Request a new code.", "error")
        return redirect(url_for("forgot_password"))
    expires = parse_dt(user.get("reset_expires"))
    if expires < datetime.now():
        user.pop("reset_token", None)
        user.pop("reset_expires", None)
        save_users(udata)
        flash("Reset session expired. Request a new code.", "error")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        pw = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        pw_err = _password_policy_error(pw)
        if pw_err:
            flash(pw_err, "error")
        elif pw != confirm:
            flash("Passwords do not match.", "error")
        else:
            user["password_hash"] = generate_password_hash(pw)
            user.pop("reset_token", None)
            user.pop("reset_expires", None)
            _clear_password_otp(user)
            log_activity(user, "Password reset via OTP")
            save_users(udata)
            flash("Password updated. You can log in now.", "success")
            return redirect(url_for("login"))
    return render_template("reset_password.html", token=token)


VALID_EMERGENCY_RELATIONS = (
    "Father", "Mother", "Brother", "Sister", "Spouse", "Friend", "Other",
)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if session.get("user_id"):
        user = current_user()
        return redirect(_role_home(user))

    if not load_settings().get("auth_allow_citizen_signup", True):
        flash("Citizen registration is currently disabled.", "error")
        return redirect(url_for("login"))

    if request.method == "POST":
        first_name = (request.form.get("first_name") or "").strip()
        middle_name = (request.form.get("middle_name") or "").strip()
        last_name = (request.form.get("last_name") or "").strip()
        gender = (request.form.get("gender") or "").strip().lower()
        email = normalize_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        address = (request.form.get("address") or "").strip()
        city = (request.form.get("city") or "").strip()
        blood_type = (request.form.get("blood_type") or "").strip()
        medical_conditions = (request.form.get("medical_conditions") or "").strip()
        allergies = (request.form.get("allergies") or "").strip()
        agree = request.form.get("agree_terms") in ("1", "on", "true", "yes")
        role = "citizen"
        email_reject = signup_email_rejection_reason(email)
        contact_email = normalize_email(request.form.get("emergency_contact_email", ""))
        contact_name = (request.form.get("emergency_contact_name") or "").strip()
        contact_relation = (request.form.get("emergency_contact_relation") or "").strip()

        try:
            phone = _validate_phone_required(request.form.get("phone"), "Phone number")
            contact_phone = _validate_phone_required(
                request.form.get("emergency_contact_phone"),
                "Emergency contact phone",
            )
            dob = _parse_date_of_birth(request.form.get("date_of_birth"))
            nid_last4, nid_hash, nid_enc = _national_id_fields(request.form.get("national_id"))
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("signup.html")

        if not first_name or not last_name:
            flash("First name and last name are required.", "error")
        elif gender not in ("male", "female"):
            flash("Please select a gender.", "error")
        elif not email or email_reject:
            flash(email_reject or "Please enter a valid email address.", "error")
        elif not contact_name:
            flash("Emergency contact name is required.", "error")
        elif contact_relation not in VALID_EMERGENCY_RELATIONS:
            flash("Please select a valid emergency contact relationship.", "error")
        elif contact_email and not is_valid_email_format(contact_email):
            flash("Please enter a valid emergency contact email, or leave it blank.", "error")
        elif not agree:
            flash("Please agree to the Terms and Privacy Policy.", "error")
        else:
            pw_err = _password_policy_error(password)
            if pw_err:
                flash(pw_err, "error")
            elif password != confirm:
                flash("Passwords do not match.", "error")
            else:
                udata = load_users()
                if any(u["email"].lower() == email for u in udata["users"]):
                    flash("Email already registered.", "error")
                elif nid_hash and any(u.get("national_id_hash") == nid_hash for u in udata["users"]):
                    flash("This National ID is already registered.", "error")
                else:
                    uid = udata["next_id"]
                    udata["next_id"] += 1
                    full_name = _compose_full_name(first_name, middle_name, last_name)
                    user = {
                        "id": uid,
                        "name": full_name,
                        "first_name": first_name,
                        "middle_name": middle_name,
                        "last_name": last_name,
                        "gender": gender,
                        "date_of_birth": dob,
                        "email": email,
                        "phone": phone,
                        "address": address,
                        "city": city,
                        "blood_type": blood_type,
                        "medical_notes": medical_conditions,
                        "allergies": allergies,
                        "emergency_contact_name": contact_name,
                        "emergency_contact_phone": contact_phone,
                        "emergency_contact_relation": contact_relation,
                        "emergency_contact_email": contact_email,
                        "national_id_last4": nid_last4,
                        "national_id_hash": nid_hash,
                        "national_id_encrypted": nid_enc,
                        "password_hash": generate_password_hash(password),
                        "role": role,
                        "status": "active",
                        "created_at": now_str(),
                        "last_login": None,
                        "activity": [{"action": "Account created", "timestamp": now_str()}],
                    }
                    settings = load_settings()
                    udata["users"].append(user)
                    if settings.get("auth_require_email_verification", True):
                        otp = _issue_email_verification(user)
                        save_users(udata)
                        result = _send_user_verification_email(user, otp)
                        if result.get("success"):
                            flash(
                                "Account created. Enter the verification code sent to your email.",
                                "success",
                            )
                        else:
                            _flash_email_send_failure("signup")
                        return redirect(url_for("verify_email_code", email=email))
                    user["email_verified"] = True
                    save_users(udata)
                    flash("Account created. You can log in now.", "success")
                    return redirect(url_for("login"))

    return render_template("signup.html")


@app.route("/verify-email", methods=["GET", "POST"])
def verify_email_code():
    """Verify citizen email with a one-time code after registration."""
    email = normalize_email(
        request.values.get("email") or request.args.get("email") or ""
    )
    if request.method == "GET":
        return render_template("verify_email.html", email=email)

    if not is_valid_email_format(email):
        flash("Please enter a valid email address.", "error")
        return redirect(url_for("login"))

    otp_code = request.form.get("otp") or request.form.get("code") or ""
    user, udata = get_user_by_login(email)
    if not user:
        flash("Invalid or expired code.", "error")
        return render_template("verify_email.html", email=email)
    if user.get("email_verified"):
        flash("This email is already verified. You can log in.", "success")
        return redirect(url_for("login"))

    ok, err = _verify_email_otp(user, otp_code)
    if not ok:
        save_users(udata)
        flash(err, "error")
        return render_template("verify_email.html", email=email)

    user["last_login"] = now_str()
    log_activity(user, "Email verified")
    log_activity(user, "Logged in")
    save_users(udata)
    login_user(user)
    flash("Email verified. Welcome, " + user_name(user) + "!", "success")
    return redirect(_role_home(user))

@app.route("/verify-email/<token>")
def verify_email(token):
    """Legacy link route — redirect users to the OTP verification page."""
    flash("Please enter the verification code from your email.", "success")
    return redirect(url_for("verify_email_code"))


@app.route("/resend-verification", methods=["POST"])
def resend_verification():
    email = normalize_email(request.form.get("email") or request.args.get("email") or "")
    if not is_valid_email_format(email):
        flash("Please enter a valid email address.", "error")
        return redirect(url_for("login"))
    user, udata = get_user_by_login(email)
    if not user:
        flash("If that email is registered, a verification code has been sent.", "success")
        return redirect(url_for("verify_email_code", email=email))
    if user.get("email_verified"):
        flash("This email is already verified. You can log in.", "success")
        return redirect(url_for("login"))
    otp = _issue_email_verification(user)
    save_users(udata)
    result = _send_user_verification_email(user, otp)
    if result.get("success"):
        flash("Verification code sent. Please check your inbox.", "success")
    else:
        _flash_email_send_failure("resend")
    return redirect(url_for("verify_email_code", email=email))


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/")
def index():
    """
    Citizen emergency home (SOS / Call Center).
    Guests must never see this app — send them to login/register.
    Staff opening / are routed to their role desk.
    """
    uid = session.get("user_id")
    if not uid:
        return redirect(url_for("login", next="/"))

    user = current_user()
    if not user:
        session.clear()
        return redirect(url_for("login", next="/"))

    # Blocked / inactive accounts cannot use the app
    status = (user.get("status") or "active").lower()
    if status in ("blocked", "disabled", "inactive", "deleted"):
        session.clear()
        flash("Your account is not allowed to access GurmadNet.", "error")
        return redirect(url_for("login"))

    role = user.get("role") or ""
    if role != "citizen":
        return redirect(_role_home(user))
    return render_template("index.html", user=user)


@app.route("/dashboard")
@role_required("citizen")
def user_dashboard():
    return render_template("user_dashboard.html", user=current_user())


def load_announcements():
    return read_json(ANNOUNCEMENTS_FILE, {"announcements": [], "next_id": 1})


def seed_announcements_if_empty():
    """No-op: never invent demo announcements — admins create them in CMS."""
    return


def _somalia_bounds_ok(lat, lng):
    return hl.is_in_somalia(lat, lng)


def _known_hospital_results(query):
    """Build geocode results from live MySQL hospitals (no hardcoded directory)."""
    out = []
    q = (query or "").strip().lower()
    if not q:
        return out
    try:
        hdata = hl.load_hospitals(read_json, save_json)
        for h in hdata.get("hospitals") or []:
            name = (h.get("name") or "").lower()
            addr = (h.get("address") or "").lower()
            city = (h.get("city") or "").lower()
            district = (h.get("district") or "").lower()
            if not (q in name or q in addr or q in city or q in district or name in q):
                continue
            lat, lng = h.get("latitude"), h.get("longitude")
            if lat is None or lng is None:
                continue
            out.append({
                "lat": float(lat),
                "lng": float(lng),
                "display_name": f"{h.get('name')}, {h.get('address') or h.get('city') or ''}".strip(", "),
                "name": h.get("name") or "",
                "address": h.get("address") or "",
                "city": h.get("city") or "",
                "district": h.get("district") or "",
                "region": h.get("region") or "",
                "source": "mysql_hospital",
                "hospital_id": h.get("id"),
                "match_score": 100,
            })
    except Exception:
        logging.getLogger(__name__).exception("MySQL hospital geocode search failed")
    return out


def _normalize_somalia_query(q):
    q = (q or "").strip()
    if not q:
        return q
    lower = q.lower()
    if "somalia" not in lower:
        q = f"{q}, Mogadishu, Somalia"
    elif "mogadishu" not in lower and any(
        kw in lower for kw in ("banadir", "digfeer", "digfer", "medina", "erdogan", "hospital")
    ):
        q = f"{q}, Mogadishu"
    return q


def _nominatim_in_somalia(row):
    addr = row.get("address") or {}
    country = (addr.get("country_code") or addr.get("country") or "").lower()
    if country and country not in ("so", "somalia", "somali"):
        return False
    try:
        lat, lng = float(row["lat"]), float(row["lon"])
    except (KeyError, TypeError, ValueError):
        return False
    return _somalia_bounds_ok(lat, lng)


def _score_geocode_match(query, row, parsed_name):
    q = query.lower().strip()
    name = (parsed_name or "").lower()
    display = (row.get("display_name") or "").lower()
    if name == q or q == name.replace(" hospital", ""):
        return 90
    if q in name or q in display:
        return 75
    if name in q:
        return 60
    return 10


def _parse_nominatim_address(addr):
    if not isinstance(addr, dict):
        return {"address": "", "city": "", "district": "", "region": ""}
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("municipality")
        or addr.get("state_district")
        or ""
    )
    district = (
        addr.get("suburb")
        or addr.get("neighbourhood")
        or addr.get("district")
        or addr.get("county")
        or addr.get("quarter")
        or ""
    )
    region = addr.get("state") or addr.get("region") or addr.get("county") or ""
    street = addr.get("road") or addr.get("pedestrian") or addr.get("footway") or ""
    parts = [p for p in (street, district, city) if p]
    return {
        "address": ", ".join(parts) if parts else "",
        "city": city,
        "district": district,
        "region": region,
    }


def _nominatim_request(path, params):
    query = urllib.parse.urlencode(params)
    url = f"https://nominatim.openstreetmap.org/{path}?{query}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "GurmadNetAI/1.0 (Somalia emergency response)"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode())


def _google_maps_http_get(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "GurmadNetAI/1.0 (Somalia emergency response)"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_google_address_components(components):
    """Extract city/district/region from Google Geocoding address_components."""
    by_type = {}
    for comp in components or []:
        for t in comp.get("types") or []:
            by_type[t] = comp.get("long_name") or ""
    city = (
        by_type.get("locality")
        or by_type.get("administrative_area_level_2")
        or by_type.get("postal_town")
        or ""
    )
    district = (
        by_type.get("sublocality")
        or by_type.get("sublocality_level_1")
        or by_type.get("neighborhood")
        or by_type.get("administrative_area_level_3")
        or ""
    )
    region = by_type.get("administrative_area_level_1") or ""
    street = by_type.get("route") or ""
    street_no = by_type.get("street_number") or ""
    road = f"{street_no} {street}".strip()
    parts = [p for p in (road, district, city) if p]
    return {
        "address": ", ".join(parts) if parts else "",
        "city": city,
        "district": district,
        "region": region,
    }


def _google_geocode_search(query, api_key):
    """Live Google Geocoding search restricted to Somalia."""
    params = urllib.parse.urlencode({
        "address": _normalize_somalia_query(query),
        "key": api_key,
        "region": "so",
        "components": "country:SO",
        "language": "en",
    })
    payload = _google_maps_http_get(
        f"https://maps.googleapis.com/maps/api/geocode/json?{params}"
    )
    status = payload.get("status")
    if status not in ("OK", "ZERO_RESULTS"):
        raise RuntimeError(payload.get("error_message") or f"Google Geocoding: {status}")
    results = []
    for row in payload.get("results") or []:
        loc = (row.get("geometry") or {}).get("location") or {}
        try:
            lat, lng = float(loc.get("lat")), float(loc.get("lng"))
        except (TypeError, ValueError):
            continue
        if not _somalia_bounds_ok(lat, lng):
            continue
        parsed = _parse_google_address_components(row.get("address_components"))
        formatted = row.get("formatted_address") or ""
        name = formatted.split(",")[0] if formatted else (parsed.get("district") or "Location")
        results.append({
            "lat": lat,
            "lng": lng,
            "display_name": formatted,
            "name": name,
            "address": parsed["address"] or formatted,
            "city": parsed["city"] or "Mogadishu",
            "district": parsed["district"],
            "region": parsed["region"] or "Banadir",
            "source": "google",
            "match_score": 95,
            "place_id": row.get("place_id") or "",
        })
    return results


def _google_geocode_reverse(lat, lng, api_key):
    """Live Google reverse geocoding for a coordinate pair."""
    params = urllib.parse.urlencode({
        "latlng": f"{lat},{lng}",
        "key": api_key,
        "language": "en",
        "result_type": "street_address|route|neighborhood|sublocality|locality",
    })
    payload = _google_maps_http_get(
        f"https://maps.googleapis.com/maps/api/geocode/json?{params}"
    )
    status = payload.get("status")
    if status == "ZERO_RESULTS":
        # Broader reverse lookup without result_type filter
        params = urllib.parse.urlencode({
            "latlng": f"{lat},{lng}",
            "key": api_key,
            "language": "en",
        })
        payload = _google_maps_http_get(
            f"https://maps.googleapis.com/maps/api/geocode/json?{params}"
        )
        status = payload.get("status")
    if status not in ("OK", "ZERO_RESULTS"):
        raise RuntimeError(payload.get("error_message") or f"Google Geocoding: {status}")
    rows = payload.get("results") or []
    if not rows:
        return None
    row = rows[0]
    parsed = _parse_google_address_components(row.get("address_components"))
    formatted = row.get("formatted_address") or ""
    return {
        "lat": lat,
        "lng": lng,
        "display_name": formatted,
        "name": formatted.split(",")[0] if formatted else (parsed.get("district") or "Selected location"),
        "address": parsed["address"] or formatted,
        "city": parsed["city"] or "Mogadishu",
        "district": parsed["district"],
        "region": parsed["region"] or "Banadir",
        "source": "google",
        "place_id": row.get("place_id") or "",
    }


def _google_directions_route(lat1, lng1, lat2, lng2, api_key):
    """Live Google Directions polyline + distance/duration."""
    params = urllib.parse.urlencode({
        "origin": f"{lat1},{lng1}",
        "destination": f"{lat2},{lng2}",
        "mode": "driving",
        "key": api_key,
        "region": "so",
        "language": "en",
    })
    payload = _google_maps_http_get(
        f"https://maps.googleapis.com/maps/api/directions/json?{params}"
    )
    status = payload.get("status")
    if status != "OK":
        raise RuntimeError(payload.get("error_message") or f"Google Directions: {status}")
    routes = payload.get("routes") or []
    if not routes:
        raise RuntimeError("No Google route found.")
    route = routes[0]
    leg = (route.get("legs") or [{}])[0]
    distance_m = float((leg.get("distance") or {}).get("value") or 0)
    duration_s = float((leg.get("duration") or {}).get("value") or 0)
    # Decode overview polyline for Leaflet fallback consumers ([lng, lat] pairs)
    overview = (route.get("overview_polyline") or {}).get("points") or ""
    coords = _decode_google_polyline(overview)
    return {
        "coordinates": coords,
        "distance_km": round(distance_m / 1000, 2),
        "duration_minutes": max(1, int(round(duration_s / 60))),
        "source": "google",
        "polyline": overview,
    }


def _decode_google_polyline(polyline_str):
    """Decode Google encoded polyline into [[lng, lat], ...] GeoJSON-style coords."""
    if not polyline_str:
        return []
    coordinates = []
    index = lat = lng = 0
    length = len(polyline_str)
    while index < length:
        for coord_name in ("lat", "lng"):
            result = shift = 0
            while True:
                if index >= length:
                    break
                b = ord(polyline_str[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if coord_name == "lat":
                lat += delta
            else:
                lng += delta
        coordinates.append([lng / 1e5, lat / 1e5])
    return coordinates


@app.route("/api/geocode/search")
@login_required
def geocode_search():
    """Search locations — Google Maps Geocoding first, then known hospitals / Nominatim."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"success": True, "results": [], "rejected": 0})
    results = []
    seen = set()
    rejected = 0
    api_key = _google_maps_api_key()
    if api_key and _use_google_maps():
        try:
            for row in _google_geocode_search(q, api_key):
                key = (round(row["lat"], 5), round(row["lng"], 5))
                if key in seen:
                    continue
                seen.add(key)
                results.append(row)
        except Exception:
            logging.getLogger(__name__).exception("Google geocode search failed")

    # Keep verified hospital directory as supplemental matches
    for row in _known_hospital_results(q):
        key = (round(row["lat"], 5), round(row["lng"], 5))
        if key in seen:
            continue
        seen.add(key)
        results.append(row)

    if not results:
        try:
            somalia_q = _normalize_somalia_query(q)
            rows = _nominatim_request("search", {
                "q": somalia_q,
                "format": "json",
                "addressdetails": 1,
                "countrycodes": "so",
                "limit": 15,
                "viewbox": f"{hl.SOMALIA_LNG_MIN},{hl.SOMALIA_LAT_MAX},{hl.SOMALIA_LNG_MAX},{hl.SOMALIA_LAT_MIN}",
                "bounded": 1,
            })
            nominatim_candidates = []
            for row in rows:
                if not _nominatim_in_somalia(row):
                    rejected += 1
                    continue
                lat, lng = float(row["lat"]), float(row["lon"])
                key = (round(lat, 5), round(lng, 5))
                if key in seen:
                    continue
                seen.add(key)
                parsed = _parse_nominatim_address(row.get("address", {}))
                name = row.get("name") or (row.get("display_name", "").split(",")[0])
                nominatim_candidates.append({
                    "lat": lat,
                    "lng": lng,
                    "display_name": row.get("display_name", ""),
                    "name": name,
                    "address": parsed["address"] or row.get("display_name", ""),
                    "city": parsed["city"] or "Mogadishu",
                    "district": parsed["district"],
                    "region": parsed["region"] or "Banadir",
                    "source": "nominatim",
                    "match_score": _score_geocode_match(q, row, name),
                })
            nominatim_candidates.sort(key=lambda x: -x["match_score"])
            results.extend(nominatim_candidates)
        except Exception as exc:
            if not results:
                return jsonify({
                    "success": False,
                    "message": _safe_client_message(exc),
                    "results": [],
                    "rejected": rejected,
                }), 502
    if not results:
        return jsonify({
            "success": False,
            "message": "No matching locations found in Somalia. Check your Google Maps API key and query.",
            "results": [],
            "rejected": rejected,
        })
    return jsonify({
        "success": True,
        "results": results[:12],
        "rejected": rejected,
        "provider": "google" if (results and results[0].get("source") == "google") else "fallback",
        "require_selection": len(results) > 1,
    })


@app.route("/api/geocode/reverse")
@login_required
def geocode_reverse():
    """Reverse geocode live coordinates via Google Maps (primary)."""
    try:
        lat = float(request.args.get("lat", ""))
        lng = float(request.args.get("lng", ""))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Valid lat/lng required."}), 400
    try:
        hl.validate_coordinates(lat, lng)
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400

    api_key = _google_maps_api_key()
    if api_key and _use_google_maps():
        try:
            result = _google_geocode_reverse(lat, lng, api_key)
            if result:
                return jsonify({"success": True, "result": result, "provider": "google"})
        except Exception:
            logging.getLogger(__name__).exception("Google reverse geocode failed")

    try:
        rows = _nominatim_request("reverse", {
            "lat": lat,
            "lon": lng,
            "format": "json",
            "addressdetails": 1,
            "zoom": 18,
        })
        if isinstance(rows, list):
            row = rows[0] if rows else {}
        else:
            row = rows
        addr = row.get("address", {}) if isinstance(row, dict) else {}
        country = (addr.get("country_code") or addr.get("country") or "").lower()
        if country and country not in ("so", "somalia", "somali"):
            return jsonify({"success": False, "message": "Location must be in Somalia."}), 400
        parsed = _parse_nominatim_address(addr)
        return jsonify({
            "success": True,
            "provider": "nominatim",
            "result": {
                "lat": lat,
                "lng": lng,
                "display_name": row.get("display_name", ""),
                "name": row.get("name") or parsed.get("city") or "Selected location",
                "address": parsed["address"] or row.get("display_name", ""),
                "city": parsed["city"] or "Mogadishu",
                "district": parsed["district"],
                "region": parsed["region"] or "Banadir",
                "source": "nominatim",
            },
        })
    except Exception as exc:
        return jsonify({
            "success": False,
            "message": _safe_client_message(exc, "Geocoding unavailable. Configure Google Maps API key."),
        }), 502


@app.route("/hospital")
@role_required("hospital")
def hospital_dashboard():
    user = current_user()
    hid, hospital = _get_user_hospital(user)
    if not hid or not hospital:
        return redirect(url_for("hospital_register"))
    return render_template(
        "hospital_dashboard.html",
        user=user,
        hospital=hospital,
    )


@app.route("/hospital/register", methods=["GET", "POST"])
@role_required("hospital")
def hospital_register():
    user = current_user()
    hid, hospital = _get_user_hospital(user)
    if hid and hospital:
        return redirect(url_for("hospital_dashboard"))

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        if not data:
            data = {
                "name": request.form.get("name", ""),
                "region": request.form.get("region", ""),
                "district": request.form.get("district", ""),
                "city": request.form.get("city", ""),
                "address": request.form.get("address", ""),
                "phone": request.form.get("phone", ""),
                "emergency_contacts": request.form.get("emergency_contacts", ""),
                "services": request.form.getlist("services"),
                "ambulance_available": request.form.get("ambulance_available"),
                "ambulance_count": request.form.get("ambulance_count", 0),
                "emergency_capacity": request.form.get("emergency_capacity", 10),
                "operating_status": request.form.get("operating_status", "open"),
                "latitude": request.form.get("latitude"),
                "longitude": request.form.get("longitude"),
                "contact_email": user.get("email") or request.form.get("contact_email", ""),
            }
        try:
            data["owner_user_id"] = user["id"]
            data["contact_email"] = data.get("contact_email") or user.get("email", "")
            if not data.get("phone"):
                data["phone"] = user.get("phone", "")
            lat, lng = hl.validate_coordinates(data.get("latitude"), data.get("longitude"))
            data["latitude"], data["longitude"] = lat, lng
            import logging
            logging.info(
                "Hospital registration location — name=%s address=%s city=%s district=%s lat=%s lng=%s user_id=%s",
                data.get("name"),
                data.get("address"),
                data.get("city"),
                data.get("district"),
                lat,
                lng,
                user.get("id"),
            )
            append_audit(
                "hospital_location_selected",
                "hospital",
                0,
                {
                    "name": data.get("name"),
                    "address": data.get("address"),
                    "city": data.get("city"),
                    "district": data.get("district"),
                    "latitude": lat,
                    "longitude": lng,
                },
                user.get("id"),
            )
            hospital = hl.create_hospital(data, read_json, save_json)
            _link_user_to_hospital(user["id"], hospital["id"])
            append_audit("hospital_registered", "hospital", hospital["id"], {"name": hospital["name"]})
            if request.is_json:
                return jsonify({"success": True, "hospital": hospital})
            flash(f"Hospital '{hospital['name']}' registered successfully.", "success")
            return redirect(url_for("hospital_dashboard"))
        except ValueError as exc:
            if request.is_json:
                return jsonify({"success": False, "message": _safe_client_message(exc)}), 400
            flash(_safe_client_message(exc, "Something went wrong. Please try again."), "error")

    return render_template(
        "hospital_register.html",
        user=user,
        service_options=hl.SERVICE_OPTIONS,
    )


@app.route("/api/hospital/profile", methods=["GET", "PUT"])
@role_required("hospital")
def api_hospital_profile():
    user = current_user()
    hid, hospital = _get_user_hospital(user)
    if not hid or not hospital:
        return jsonify({"success": False, "message": "Complete hospital registration first."}), 403

    if request.method == "GET":
        return jsonify({"success": True, "hospital": hospital})

    data = request.get_json(silent=True) or {}
    # Ambulance readiness comes from hospital-managed units — ignore manual fleet flags
    data.pop("ambulance_available", None)
    data.pop("ambulance_count", None)
    try:
        hospital = hl.update_hospital(hid, data, read_json, save_json)
        append_audit("hospital_profile_updated", "hospital", hid)
        return jsonify({"success": True, "hospital": hospital})
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400


@app.route("/api/hospital/logo", methods=["POST", "DELETE"])
@role_required("hospital")
def api_hospital_logo():
    """Upload or clear hospital logo under static/uploads/hospitals/."""
    user = current_user()
    hid, hospital = _get_user_hospital(user)
    if not hid or not hospital:
        return jsonify({"success": False, "message": "Complete hospital registration first."}), 403

    upload_dir = os.path.join(BASE_DIR, "static", "uploads", "hospitals")
    os.makedirs(upload_dir, exist_ok=True)

    if request.method == "DELETE":
        old = (hospital.get("logo_url") or "").strip()
        if old.startswith("/static/uploads/hospitals/"):
            old_path = os.path.join(BASE_DIR, old.lstrip("/").replace("/", os.sep))
            if os.path.isfile(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass
        hospital = hl.update_hospital(hid, {"logo_url": ""}, read_json, save_json)
        append_audit("hospital_logo_cleared", "hospital", hid)
        return jsonify({"success": True, "hospital": hospital, "logo_url": ""})

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"success": False, "message": "No file uploaded"}), 400
    settings = load_settings()
    allowed = {
        e.strip().lower()
        for e in str(settings.get("upload_allowed_extensions") or "jpg,jpeg,png,gif,webp").split(",")
        if e.strip()
    }
    ext = (f.filename.rsplit(".", 1)[-1] if "." in f.filename else "").lower()
    if ext not in allowed:
        return jsonify({
            "success": False,
            "message": "File type not allowed. Allowed: " + ", ".join(sorted(allowed)),
        }), 400
    max_mb = min(int(settings.get("upload_max_mb") or 5), 5)
    data = f.read()
    if len(data) > max_mb * 1024 * 1024:
        return jsonify({"success": False, "message": f"File exceeds {max_mb} MB limit"}), 400

    # Replace previous file if it was under our uploads folder
    old = (hospital.get("logo_url") or "").strip()
    if old.startswith("/static/uploads/hospitals/"):
        old_path = os.path.join(BASE_DIR, old.lstrip("/").replace("/", os.sep))
        if os.path.isfile(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    filename = f"hospital_{hid}.{ext}"
    path = os.path.join(upload_dir, filename)
    with open(path, "wb") as out:
        out.write(data)
    url = url_for("static", filename=f"uploads/hospitals/{filename}")
    hospital = hl.update_hospital(hid, {"logo_url": url}, read_json, save_json)
    append_audit("hospital_logo_uploaded", "hospital", hid, {"url": url})
    return jsonify({"success": True, "hospital": hospital, "logo_url": url})


def _hospital_sync_ambulance_counts(hid):
    import facility_registry as fr
    hdata = hl.load_hospitals(read_json, save_json)
    adata = fr.load_ambulances(read_json)
    if fr.sync_hospital_ambulance_counts(hdata, adata):
        hl.save_hospitals(hdata, save_json)
    return hl.get_hospital_by_id(hdata, hid)


def _assign_ambulance_to_emergency(em, aid, hospital_id):
    """Bind a hospital-owned unit to an emergency and mark it busy."""
    import facility_registry as fr
    try:
        aid = int(aid)
    except (TypeError, ValueError):
        raise ValueError("Invalid ambulance unit")
    adata = fr.load_ambulances(read_json)
    unit = fr.get_ambulance(adata, aid)
    if not unit or unit.get("hospital_id") != hospital_id:
        raise ValueError("Ambulance not found for your hospital")
    st = (unit.get("status") or "").lower()
    if st == "maintenance":
        st = "offline"
    if st == "offline":
        raise ValueError("Ambulance is offline")
    if st not in ("available", "busy"):
        raise ValueError("Ambulance is not available for dispatch")
    em["assigned_ambulance_id"] = aid
    em["assigned_ambulance_call_sign"] = unit.get("call_sign") or ""
    em["assigned_ambulance_driver_name"] = unit.get("driver_name") or ""
    em["assigned_ambulance_driver_phone"] = unit.get("driver_phone") or ""
    em["assigned_ambulance_latitude"] = unit.get("latitude")
    em["assigned_ambulance_longitude"] = unit.get("longitude")
    if unit.get("latitude") is not None and unit.get("longitude") is not None:
        em["responder_latitude"] = unit.get("latitude")
        em["responder_longitude"] = unit.get("longitude")
    if st == "available":
        fr.mark_ambulance_busy(aid, read_json, save_json)
        _hospital_sync_ambulance_counts(hospital_id)
    return unit


def _release_emergency_ambulance(em):
    """Return assigned unit to available when the case ends."""
    aid = em.get("assigned_ambulance_id")
    if not aid:
        return
    import facility_registry as fr
    try:
        aid = int(aid)
    except (TypeError, ValueError):
        return
    adata = fr.load_ambulances(read_json)
    unit = fr.get_ambulance(adata, aid)
    if not unit:
        return
    if (unit.get("status") or "").lower() == "busy":
        # Only release if driver phone still present (available requires it)
        try:
            fr.mark_ambulance_available(aid, read_json, save_json)
        except ValueError:
            # If driver phone missing, set offline rather than leave busy forever
            try:
                fr.update_ambulance(aid, {"status": "offline"}, read_json, save_json)
            except ValueError:
                return
        hid = unit.get("hospital_id") or em.get("assigned_hospital_id")
        if hid:
            _hospital_sync_ambulance_counts(hid)


@app.route("/api/hospital/ambulances", methods=["GET", "POST"])
@role_required("hospital")
def api_hospital_ambulances():
    """Hospital owns units; GurmadNet stores only dispatch essentials."""
    import facility_registry as fr
    user = current_user()
    hid, hospital = _get_user_hospital(user)
    if not hid or not hospital:
        return jsonify({"success": False, "message": "Complete hospital registration first."}), 403

    if request.method == "GET":
        data = fr.load_ambulances(read_json)
        rows = [
            fr.ambulance_dispatch_view(a, hospital.get("name") or "")
            for a in fr.list_hospital_ambulances(data, hid)
        ]
        rows.sort(key=lambda r: int(r.get("id") or 0))
        return jsonify({
            "success": True,
            "ambulances": rows,
            "count": len(rows),
            "available_count": sum(1 for r in rows if r.get("status") == "available"),
        })

    payload = request.get_json(silent=True) or {}
    payload["hospital_id"] = hid
    try:
        row = fr.create_ambulance(payload, read_json, save_json)
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400
    _hospital_sync_ambulance_counts(hid)
    append_audit(
        "hospital_ambulance_created",
        "ambulance",
        row["id"],
        {"call_sign": row.get("call_sign"), "hospital_id": hid},
        user.get("id"),
    )
    return jsonify({
        "success": True,
        "ambulance": fr.ambulance_dispatch_view(row, hospital.get("name") or ""),
    }), 201


@app.route("/api/hospital/ambulances/<int:aid>", methods=["GET", "PUT", "DELETE"])
@role_required("hospital")
def api_hospital_ambulance_item(aid):
    import facility_registry as fr
    user = current_user()
    hid, hospital = _get_user_hospital(user)
    if not hid or not hospital:
        return jsonify({"success": False, "message": "Complete hospital registration first."}), 403

    data = fr.load_ambulances(read_json)
    row = fr.get_ambulance(data, aid)
    if not row or row.get("hospital_id") != hid:
        return jsonify({"success": False, "message": "Ambulance not found"}), 404

    if request.method == "GET":
        return jsonify({
            "success": True,
            "ambulance": fr.ambulance_dispatch_view(row, hospital.get("name") or ""),
        })

    if request.method == "PUT":
        payload = request.get_json(silent=True) or {}
        payload.pop("hospital_id", None)
        try:
            row = fr.update_ambulance(aid, payload, read_json, save_json)
        except ValueError as exc:
            return jsonify({"success": False, "message": _safe_client_message(exc)}), 400
        _hospital_sync_ambulance_counts(hid)
        append_audit(
            "hospital_ambulance_updated",
            "ambulance",
            aid,
            {"fields": list(payload.keys()), "hospital_id": hid},
            user.get("id"),
        )
        return jsonify({
            "success": True,
            "ambulance": fr.ambulance_dispatch_view(row, hospital.get("name") or ""),
        })

    try:
        fr.delete_ambulance(aid, read_json, save_json)
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400
    _hospital_sync_ambulance_counts(hid)
    append_audit("hospital_ambulance_deleted", "ambulance", aid, {"hospital_id": hid}, user.get("id"))
    return jsonify({"success": True})


@app.route("/api/hospital/ambulances/<int:aid>/photo", methods=["POST", "DELETE"])
@role_required("hospital")
def api_hospital_ambulance_photo(aid):
    """Upload or clear driver/vehicle photo for a hospital-owned ambulance unit.

    Form/query: kind=driver (default) or kind=vehicle
    """
    import facility_registry as fr
    user = current_user()
    hid, hospital = _get_user_hospital(user)
    if not hid or not hospital:
        return jsonify({"success": False, "message": "Complete hospital registration first."}), 403

    data = fr.load_ambulances(read_json)
    row = fr.get_ambulance(data, aid)
    if not row or row.get("hospital_id") != hid:
        return jsonify({"success": False, "message": "Ambulance not found"}), 404

    kind = (request.args.get("kind") or request.form.get("kind") or "driver").strip().lower()
    if kind not in ("driver", "vehicle"):
        return jsonify({"success": False, "message": "kind must be driver or vehicle"}), 400
    field = "driver_photo_url" if kind == "driver" else "vehicle_photo_url"

    upload_dir = os.path.join(BASE_DIR, "static", "uploads", "ambulances")
    os.makedirs(upload_dir, exist_ok=True)

    def _remove_old_file(url):
        old = (url or "").strip()
        if not old.startswith("/static/uploads/ambulances/"):
            return
        old_path = os.path.join(BASE_DIR, old.lstrip("/").replace("/", os.sep))
        if os.path.isfile(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    if request.method == "DELETE":
        _remove_old_file(row.get(field))
        row = fr.update_ambulance(aid, {field: ""}, read_json, save_json)
        append_audit(
            "hospital_ambulance_photo_cleared",
            "ambulance",
            aid,
            {"hospital_id": hid, "kind": kind},
            user.get("id"),
        )
        view = fr.ambulance_dispatch_view(row, hospital.get("name") or "")
        return jsonify({
            "success": True,
            "ambulance": view,
            "kind": kind,
            field: "",
            "driver_photo_url": view.get("driver_photo_url") or "",
            "vehicle_photo_url": view.get("vehicle_photo_url") or "",
        })

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"success": False, "message": "No file uploaded"}), 400
    settings = load_settings()
    allowed = {
        e.strip().lower()
        for e in str(settings.get("upload_allowed_extensions") or "jpg,jpeg,png,gif,webp").split(",")
        if e.strip()
    }
    ext = (f.filename.rsplit(".", 1)[-1] if "." in f.filename else "").lower()
    if ext not in allowed:
        return jsonify({
            "success": False,
            "message": "File type not allowed. Allowed: " + ", ".join(sorted(allowed)),
        }), 400
    max_mb = min(int(settings.get("upload_max_mb") or 5), 5)
    blob = f.read()
    if len(blob) > max_mb * 1024 * 1024:
        return jsonify({"success": False, "message": f"File exceeds {max_mb} MB limit"}), 400

    _remove_old_file(row.get(field))
    # Unique name so browsers never keep showing a cached previous upload
    filename = f"amb_{hid}_{aid}_{kind}_{int(time.time())}.{ext}"
    path = os.path.join(upload_dir, filename)
    with open(path, "wb") as out:
        out.write(blob)
    url = url_for("static", filename=f"uploads/ambulances/{filename}")
    row = fr.update_ambulance(aid, {field: url}, read_json, save_json)
    append_audit(
        "hospital_ambulance_photo_uploaded",
        "ambulance",
        aid,
        {"hospital_id": hid, "url": url, "kind": kind},
        user.get("id"),
    )
    view = fr.ambulance_dispatch_view(row, hospital.get("name") or "")
    return jsonify({
        "success": True,
        "ambulance": view,
        "kind": kind,
        field: url,
        "driver_photo_url": view.get("driver_photo_url") or "",
        "vehicle_photo_url": view.get("vehicle_photo_url") or "",
    })


@app.route("/api/hospital/ambulances/<int:aid>/location", methods=["POST"])
@role_required("hospital")
def api_hospital_ambulance_location(aid):
    """Update live GPS for dispatch coordination (and follow assigned emergencies)."""
    import facility_registry as fr
    user = current_user()
    hid, hospital = _get_user_hospital(user)
    if not hid or not hospital:
        return jsonify({"success": False, "message": "Complete hospital registration first."}), 403
    data = fr.load_ambulances(read_json)
    row = fr.get_ambulance(data, aid)
    if not row or row.get("hospital_id") != hid:
        return jsonify({"success": False, "message": "Ambulance not found"}), 404
    body = request.get_json(silent=True) or {}
    try:
        row = fr.update_ambulance(
            aid,
            {"latitude": body.get("latitude"), "longitude": body.get("longitude")},
            read_json,
            save_json,
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400

    lat, lng = row.get("latitude"), row.get("longitude")
    _apply_ambulance_gps_to_emergencies(aid, lat, lng)

    return jsonify({
        "success": True,
        "ambulance": fr.ambulance_dispatch_view(row, hospital.get("name") or ""),
    })


def _apply_ambulance_gps_to_emergencies(aid, lat, lng):
    """Keep assigned active emergencies following the moving unit."""
    if lat is None or lng is None:
        return False
    edata = load_emergencies()
    changed = False
    for em in edata.get("emergencies") or []:
        if em.get("assigned_ambulance_id") != aid:
            continue
        st = (em.get("status") or "").lower()
        if st in COMPLETED_STATUSES:
            continue
        em["assigned_ambulance_latitude"] = lat
        em["assigned_ambulance_longitude"] = lng
        em["responder_latitude"] = lat
        em["responder_longitude"] = lng
        em["last_location_update"] = now_str()
        changed = True
    if changed:
        save_emergencies(edata)
    return changed


@app.route("/api/hospital/ambulances/<int:aid>/gps-link", methods=["POST", "DELETE"])
@role_required("hospital")
def api_hospital_ambulance_gps_link(aid):
    """Create/rotate or revoke a mobile Driver GPS share link."""
    import facility_registry as fr

    user = current_user()
    hid, hospital = _get_user_hospital(user)
    if not hid or not hospital:
        return jsonify({"success": False, "message": "Complete hospital registration first."}), 403
    data = fr.load_ambulances(read_json)
    row = fr.get_ambulance(data, aid)
    if not row or row.get("hospital_id") != hid:
        return jsonify({"success": False, "message": "Ambulance not found"}), 404

    if request.method == "DELETE":
        try:
            row = fr.revoke_ambulance_gps_token(aid, read_json, save_json)
        except ValueError as exc:
            return jsonify({"success": False, "message": _safe_client_message(exc)}), 400
        append_audit(
            "hospital_ambulance_gps_link_revoked",
            "ambulance",
            aid,
            {"hospital_id": hid},
            user.get("id"),
        )
        return jsonify({
            "success": True,
            "ambulance": fr.ambulance_dispatch_view(row, hospital.get("name") or ""),
        })

    body = request.get_json(silent=True) or {}
    rotate = bool(body.get("rotate"))
    try:
        row = fr.issue_ambulance_gps_token(aid, read_json, save_json, rotate=rotate)
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400
    view = fr.ambulance_dispatch_view(row, hospital.get("name") or "")
    path = view.get("gps_share_path") or ""
    url = request.url_root.rstrip("/") + path if path else ""
    append_audit(
        "hospital_ambulance_gps_link_issued",
        "ambulance",
        aid,
        {"hospital_id": hid, "rotated": rotate},
        user.get("id"),
    )
    return jsonify({
        "success": True,
        "url": url,
        "path": path,
        "token": view.get("gps_share_token") or "",
        "ambulance": view,
    })


@app.route("/driver/gps/<token>")
def driver_gps_share_page(token):
    """Public mobile page — driver shares live GPS without hospital login."""
    import facility_registry as fr

    row = fr.get_ambulance_by_gps_token(token, read_json)
    if not row:
        return render_template("driver_gps.html", valid=False, unit=None, token=""), 404
    hdata = hl.load_hospitals(read_json, save_json)
    hospital = hl.get_hospital_by_id(hdata, row.get("hospital_id")) or {}
    return render_template(
        "driver_gps.html",
        valid=True,
        token=token,
        unit={
            "id": row.get("id"),
            "call_sign": row.get("call_sign") or "Ambulance",
            "driver_name": row.get("driver_name") or "",
            "hospital_name": hospital.get("name") or "",
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "updated_at": row.get("updated_at") or "",
        },
    )


@app.route("/api/driver/gps/<token>", methods=["GET"])
def api_driver_gps_info(token):
    import facility_registry as fr

    row = fr.get_ambulance_by_gps_token(token, read_json)
    if not row:
        return jsonify({"success": False, "message": "Invalid or revoked GPS link"}), 404
    hdata = hl.load_hospitals(read_json, save_json)
    hospital = hl.get_hospital_by_id(hdata, row.get("hospital_id")) or {}
    return jsonify({
        "success": True,
        "call_sign": row.get("call_sign") or "",
        "driver_name": row.get("driver_name") or "",
        "hospital_name": hospital.get("name") or "",
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "updated_at": row.get("updated_at") or "",
        "status": row.get("status") or "",
    })


@app.route("/api/driver/gps/<token>/location", methods=["POST"])
@csrf.exempt
def api_driver_gps_location(token):
    """Public GPS push from driver phone (tokenized link)."""
    import facility_registry as fr

    row = fr.get_ambulance_by_gps_token(token, read_json)
    if not row:
        return jsonify({"success": False, "message": "Invalid or revoked GPS link"}), 404
    body = request.get_json(silent=True) or {}
    try:
        updated = fr.update_ambulance(
            row["id"],
            {"latitude": body.get("latitude"), "longitude": body.get("longitude")},
            read_json,
            save_json,
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400

    lat, lng = updated.get("latitude"), updated.get("longitude")
    _apply_ambulance_gps_to_emergencies(updated["id"], lat, lng)
    hdata = hl.load_hospitals(read_json, save_json)
    hospital = hl.get_hospital_by_id(hdata, updated.get("hospital_id")) or {}
    return jsonify({
        "success": True,
        "latitude": lat,
        "longitude": lng,
        "updated_at": updated.get("updated_at") or "",
        "ambulance": fr.ambulance_dispatch_view(updated, hospital.get("name") or ""),
    })


@app.route("/police")
@role_required("police")
def police_dashboard():
    user = current_user()
    sid, station = _get_user_station(user, "police")
    import police_logic as pl

    return render_template(
        "police_dashboard.html",
        user=user,
        station=pl.station_view(station) if station else {
            "id": None,
            "name": "Police Station",
            "operating_status": "open",
            "city": "",
            "district": "",
            "phone": "",
            "latitude": None,
            "longitude": None,
        },
        station_linked=bool(sid),
        settings=load_settings(),
    )


def _station_desk_mutate(role, eid, action):
    """Shared accept / dispatch / complete / release for police or fire desk."""
    import police_logic as pl

    if role not in ("police", "fire"):
        return jsonify({"success": False, "message": "Invalid role"}), 400
    label = "Police" if role == "police" else "Fire & Rescue"
    user = current_user()
    sid, station = _get_user_station(user, role)
    if not sid or not station:
        return jsonify({"success": False, "message": f"No {role} station linked"}), 403
    em, edata = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False, "message": "Not found"}), 404
    if not matches_filter(em.get("type"), role):
        return jsonify({"success": False, "message": f"Not a {role} case"}), 403

    try:
        if action == "accept":
            if not pl.emergency_visible_to_station(em, sid, role):
                return jsonify({"success": False, "message": "Case not in your queue"}), 403
            pl.claim_station(em, station, role)
            _append_status(em, "accepted", f"{station.get('name') or label} accepted")
            em["accepted_at"] = now_str()
            _notify(
                "patient",
                em.get("user_id"),
                f"{station.get('name') or label} accepted your emergency.",
                eid,
                "request_accepted",
            )
            append_audit(f"{role}_accept", "emergency", eid, {"station_id": sid}, user.get("id"))
        elif action == "dispatch":
            if em.get("assigned_station_id") != sid:
                return jsonify({"success": False, "message": "Accept the case first"}), 403
            if (em.get("status") or "").lower() in COMPLETED_STATUSES:
                return jsonify({"success": False, "message": "Case is already closed"}), 400
            pl.claim_station(em, station, role)
            unit_word = "Police units" if role == "police" else "Fire crews"
            _append_status(em, "dispatched", f"{unit_word} dispatched")
            em["status"] = "dispatched"
            _notify(
                "patient",
                em.get("user_id"),
                f"{unit_word} are on the way.",
                eid,
                "team_dispatched",
            )
            append_audit(f"{role}_dispatch", "emergency", eid, {"station_id": sid}, user.get("id"))
        elif action == "complete":
            if em.get("assigned_station_id") != sid:
                return jsonify({"success": False, "message": "Not assigned to your station"}), 403
            _append_status(em, "completed", f"{label} closed the case")
            em["status"] = "completed"
            _stop_sos_tracking(em)
            _notify(
                "patient",
                em.get("user_id"),
                "Your emergency has been completed.",
                eid,
                "emergency_completed",
            )
            _ai_record_outcome(em)
            append_audit(f"{role}_complete", "emergency", eid, {"station_id": sid}, user.get("id"))
        elif action == "release":
            if em.get("assigned_station_id") != sid:
                return jsonify({"success": False, "message": "Not assigned to your station"}), 403
            if (em.get("status") or "").lower() in COMPLETED_STATUSES:
                return jsonify({"success": False, "message": "Case is already closed"}), 400
            pl.release_station(em, sid)
            em["assigned_to"] = role
            _append_status(em, "pending", f"Released back to {role} queue")
            em["status"] = "pending"
            _notify_role_operators(
                role, f"Emergency #{eid} released to open {role} queue", eid, "system_alert"
            )
            append_audit(f"{role}_release", "emergency", eid, {"station_id": sid}, user.get("id"))
        else:
            return jsonify({"success": False, "message": "Unknown action"}), 400
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400

    save_emergencies(edata)
    return jsonify({"success": True, "emergency": em, "status": em.get("status")})


def _police_mutate_case(eid, action):
    return _station_desk_mutate("police", eid, action)


def _api_station_profile(kind):
    """GET/PUT station profile for police or fire operator."""
    import police_logic as pl
    import facility_registry as fr

    user = current_user()
    sid, station = _get_user_station(user, kind)
    if not sid or not station:
        return jsonify({
            "success": False,
            "message": f"No {kind} station linked to this account. Ask Admin to set station_id.",
            "station": None,
        }), 403
    if request.method == "GET":
        return jsonify({"success": True, "station": pl.station_view(station)})

    data = request.get_json(silent=True) or {}
    allowed = {}
    for key in ("name", "city", "region", "district", "address", "phone", "operating_status"):
        if key in data:
            allowed[key] = data.get(key)
    if "latitude" in data or "longitude" in data:
        allowed["latitude"] = data.get("latitude", station.get("latitude"))
        allowed["longitude"] = data.get("longitude", station.get("longitude"))
    try:
        row = fr.update_station(sid, allowed, read_json, save_json)
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400
    append_audit(f"{kind}_station_updated", "station", sid, {"fields": list(allowed.keys())}, user.get("id"))
    return jsonify({"success": True, "station": pl.station_view(row)})


@app.route("/api/police/station", methods=["GET", "PUT"])
@role_required("police")
def api_police_station():
    return _api_station_profile("police")


@app.route("/api/police/request/<int:eid>/accept", methods=["POST"])
@role_required("police")
def api_police_accept(eid):
    return _station_desk_mutate("police", eid, "accept")


@app.route("/api/police/request/<int:eid>/dispatch", methods=["POST"])
@role_required("police")
def api_police_dispatch(eid):
    return _station_desk_mutate("police", eid, "dispatch")


@app.route("/api/police/request/<int:eid>/complete", methods=["POST"])
@role_required("police")
def api_police_complete(eid):
    return _station_desk_mutate("police", eid, "complete")


@app.route("/api/police/request/<int:eid>/release", methods=["POST"])
@role_required("police")
def api_police_release(eid):
    return _station_desk_mutate("police", eid, "release")


@app.route("/fire")
@role_required("fire")
def fire_dashboard():
    user = current_user()
    sid, station = _get_user_station(user, "fire")
    import police_logic as pl

    return render_template(
        "fire_dashboard.html",
        user=user,
        station=pl.station_view(station) if station else {
            "id": None,
            "name": "Fire Station",
            "operating_status": "open",
            "city": "",
            "district": "",
            "phone": "",
            "latitude": None,
            "longitude": None,
        },
        station_linked=bool(sid),
        settings=load_settings(),
    )


@app.route("/api/fire/station", methods=["GET", "PUT"])
@role_required("fire")
def api_fire_station():
    return _api_station_profile("fire")


@app.route("/api/fire/request/<int:eid>/accept", methods=["POST"])
@role_required("fire")
def api_fire_accept(eid):
    return _station_desk_mutate("fire", eid, "accept")


@app.route("/api/fire/request/<int:eid>/dispatch", methods=["POST"])
@role_required("fire")
def api_fire_dispatch(eid):
    return _station_desk_mutate("fire", eid, "dispatch")


@app.route("/api/fire/request/<int:eid>/complete", methods=["POST"])
@role_required("fire")
def api_fire_complete(eid):
    return _station_desk_mutate("fire", eid, "complete")


@app.route("/api/fire/request/<int:eid>/release", methods=["POST"])
@role_required("fire")
def api_fire_release(eid):
    return _station_desk_mutate("fire", eid, "release")


@app.route("/admin")
@admin_required
def admin_dashboard():
    user = current_user()
    role = (user or {}).get("role") or _session_role()
    hdata = hl.load_hospitals(read_json, save_json)
    hospitals_registry = sorted(
        hdata.get("hospitals") or [],
        key=lambda h: int(h.get("id") or 0),
    )
    return render_template(
        "admin_dashboard.html",
        user=user,
        admin_role=role,
        is_super_admin=role == "super_admin",
        admin_permissions=sorted(_admin_permissions(role)),
        hospitals_registry=hospitals_registry,
    )


@app.route("/api/location/ip")
@login_required
def location_ip():
    """Approximate location when GPS is denied — live IP geolocation only (no fake coords)."""
    try:
        req = urllib.request.Request(
            "http://ip-api.com/json/?fields=status,lat,lon,city,country,regionName",
            headers={"User-Agent": "GurmadNetAI/1.0"},
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            payload = json.loads(resp.read().decode())
        if payload.get("status") != "success":
            return jsonify({
                "success": False,
                "message": "Could not determine location from network. Enable GPS.",
            }), 404
        lat = float(payload["lat"])
        lng = float(payload["lon"])
        city = payload.get("city") or ""
        country = payload.get("country") or ""
        district = ", ".join([p for p in (city, country) if p]) or f"{lat:.5f}, {lng:.5f}"
        # Prefer Google reverse geocode for a live place name when key is set
        api_key = _google_maps_api_key()
        if api_key and _use_google_maps() and hl.is_in_somalia(lat, lng):
            try:
                g = _google_geocode_reverse(lat, lng, api_key)
                if g:
                    district = g.get("address") or g.get("display_name") or district
            except Exception:
                pass
        return jsonify({
            "success": True,
            "lat": lat,
            "lng": lng,
            "district": district,
            "source": "ip",
            "in_somalia": hl.is_in_somalia(lat, lng),
        })
    except (OSError, ValueError, json.JSONDecodeError, KeyError):
        return jsonify({
            "success": False,
            "message": "Location unavailable. Enable device GPS for accurate positioning.",
        }), 503


@app.route("/api/hospitals", methods=["GET"])
@login_required
def api_hospitals():
    hdata = hl.seed_hospitals_if_empty(read_json, save_json)
    city = request.args.get("city", "")
    region = request.args.get("region", "")
    specialty = request.args.get("specialty", "")
    q = request.args.get("q", "")
    lat = request.args.get("lat")
    lng = request.args.get("lng")
    hospitals = hl.filter_hospitals(hdata["hospitals"], city, region, specialty, q)
    if lat and lng:
        try:
            lat, lng = float(lat), float(lng)
            ranked = hl.hospitals_by_distance(lat, lng, hospitals, emergency_only=False)
            hospitals = [{**h, "distance_km": round(d, 2)} for d, h in ranked]
        except (TypeError, ValueError):
            pass
    return jsonify({"hospitals": hospitals, "count": len(hospitals)})


@app.route("/api/stations", methods=["GET"])
@login_required
def api_stations():
    """Citizen/nearby list of police or fire stations (read-only)."""
    import facility_registry as fr
    kind = (request.args.get("kind") or "").strip().lower()
    if kind and kind not in ("police", "fire"):
        return jsonify({"success": False, "message": "kind must be police or fire"}), 400
    stations = fr.open_stations_with_coords(read_json, kind=kind or None)
    lat = request.args.get("lat")
    lng = request.args.get("lng")
    if lat and lng:
        try:
            lat_f, lng_f = float(lat), float(lng)
            ranked = []
            for s in stations:
                dist = hl.haversine_km(lat_f, lng_f, s["latitude"], s["longitude"])
                ranked.append((dist, s))
            ranked.sort(key=lambda x: x[0])
            stations = [{**s, "distance_km": round(d, 2)} for d, s in ranked]
        except (TypeError, ValueError):
            pass
    public = [
        {
            "id": s.get("id"),
            "name": s.get("name"),
            "kind": s.get("kind"),
            "phone": s.get("phone") or "",
            "city": s.get("city") or "",
            "district": s.get("district") or "",
            "region": s.get("region") or "",
            "latitude": s.get("latitude"),
            "longitude": s.get("longitude"),
            "distance_km": s.get("distance_km"),
            "operating_status": s.get("operating_status") or "open",
        }
        for s in stations
    ]
    return jsonify({"stations": public, "count": len(public), "kind": kind or "all"})


@app.route("/api/nearest_hospital", methods=["GET"])
@role_required("citizen")
def api_nearest_hospital():
    """Return the closest open hospital for patient GPS coordinates."""
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)
    if lat is None or lng is None:
        return jsonify({"success": False, "message": "lat and lng required"}), 400
    hdata = hl.seed_hospitals_if_empty(read_json, save_json)
    ranked = hl.hospitals_by_distance(lat, lng, hdata["hospitals"])
    if not ranked:
        return jsonify({"success": False, "message": "No hospital available nearby"}), 404
    dist_km, hospital = ranked[0]
    eta_min = max(3, int((dist_km / 40) * 60))
    return jsonify({
        "success": True,
        "hospital": {
            "id": hospital["id"],
            "name": hospital["name"],
            "phone": hospital.get("phone", ""),
            "city": hospital.get("city", ""),
            "latitude": hospital["latitude"],
            "longitude": hospital["longitude"],
            "distance_km": round(dist_km, 2),
            "eta_minutes": eta_min,
            "ambulance_available": hospital.get("ambulance_available", False),
        },
    })


@app.route("/api/healthcare/emergency", methods=["POST"])
@role_required("citizen")
def api_healthcare_emergency():
    settings = load_settings()
    if not settings.get("sos_enabled", True):
        return jsonify({"success": False, "message": "SOS disabled."}), 403
    data = request.get_json(silent=True) or {}
    emergency, err = _create_healthcare_emergency(data, request_mode="emergency")
    if err:
        return jsonify({"success": False, "message": err}), 400
    return jsonify({
        "success": True,
        "id": emergency["id"],
        "status": emergency["status"],
        "hospital": {
            "id": emergency.get("assigned_hospital_id"),
            "name": emergency.get("assigned_hospital_name"),
            "distance_km": emergency.get("hospital_distance_km"),
        },
        "message": "Emergency sent to nearest hospital.",
    })


@app.route("/api/healthcare/preferred", methods=["POST"])
@role_required("citizen")
def api_healthcare_preferred():
    data = request.get_json(silent=True) or {}
    if not data.get("hospital_id"):
        return jsonify({"success": False, "message": "Select a hospital."}), 400
    emergency, err = _create_healthcare_emergency(data, request_mode="preferred")
    if err:
        return jsonify({"success": False, "message": err}), 400
    return jsonify({
        "success": True,
        "id": emergency["id"],
        "status": emergency["status"],
        "hospital": {
            "id": emergency.get("assigned_hospital_id"),
            "name": emergency.get("assigned_hospital_name"),
        },
    })


@app.route("/api/my_emergencies")
@role_required("citizen")
def api_my_emergencies():
    uid = session.get("user_id")
    edata = load_emergencies()
    mine = [e for e in edata["emergencies"] if e.get("user_id") == uid]
    mine.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    out = []
    now = datetime.now()
    for e in mine[:5]:
        ts = parse_dt(e.get("timestamp", ""))
        delta = now - ts
        if delta.days > 0:
            ago = f"{delta.days} day{'s' if delta.days > 1 else ''} ago"
        elif delta.seconds >= 3600:
            h = delta.seconds // 3600
            ago = f"{h} hour{'s' if h > 1 else ''} ago"
        elif delta.seconds >= 60:
            m = delta.seconds // 60
            ago = f"{m} min ago"
        else:
            ago = "Just now"
        out.append({
            "id": e["id"],
            "type": (e.get("type") or "emergency").replace("_", " ").title(),
            "status": e.get("status"),
            "time_ago": ago,
        })
    return jsonify({"success": True, "emergencies": out})


@app.route("/api/patient/request/status")
@role_required("citizen")
def api_patient_request_status():
    _run_escalations()
    rid = request.args.get("id", type=int)
    edata = load_emergencies()
    uid = session.get("user_id")
    em = None
    if rid:
        em, _ = get_emergency_by_id(rid)
        if em and em.get("user_id") != uid:
            em = None
    else:
        # Prefer latest active SOS; ignore completed / cancelled / no_hospital
        for e in reversed(edata["emergencies"]):
            if e.get("user_id") != uid:
                continue
            if _is_active_sos(e):
                em = e
                break
        if em is None:
            for e in reversed(edata["emergencies"]):
                if e.get("user_id") == uid:
                    em = e
                    break
    if not em or em.get("user_id") != uid:
        return jsonify({"success": True, "active": False})
    active = _is_active_sos(em)
    if not active:
        _stop_sos_tracking(em)
    hdata = hl.load_hospitals(read_json, save_json)
    hospital = hl.get_hospital_by_id(hdata, em.get("assigned_hospital_id"))
    dist = em.get("hospital_distance_km")
    if dist is None and hospital and em.get("latitude") is not None:
        dist = round(
            hl.haversine_km(
                em["latitude"], em["longitude"],
                hospital["latitude"], hospital["longitude"],
            ),
            2,
        )
    team_label = em.get("assigned_team_label") or TEAM_LABELS.get(em.get("assigned_to"), "Emergency Response Team")
    return jsonify({
        "success": True,
        "active": active,
        "request": {
            "id": em["id"],
            "type": em.get("type"),
            "status": em.get("status"),
            "status_history": em.get("status_history", []),
            "team_label": team_label,
            "assigned_to": em.get("assigned_to"),
            "location": em.get("location"),
            "latitude": em.get("latitude"),
            "longitude": em.get("longitude"),
            "accuracy_m": em.get("accuracy_m"),
            "tracking_active": bool(em.get("tracking_active")) and active,
            "last_location_update": em.get("last_location_update"),
            "location_trail": em.get("location_history", [])[-15:],
        },
    })


@app.route("/api/patient/request/<int:eid>/cancel", methods=["POST"])
@role_required("citizen")
def api_patient_cancel_request(eid):
    """Citizen cancels their own active emergency."""
    uid = session.get("user_id")
    em, edata = get_emergency_by_id(eid)
    if not em or em.get("user_id") != uid:
        return jsonify({"success": False, "message": "Emergency not found"}), 404
    if not _is_active_sos(em):
        return jsonify({"success": False, "message": "This emergency is already closed"}), 400

    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "").strip()[:240]
    note = "Cancelled by citizen"
    if reason:
        note = f"{note}: {reason}"

    _append_status(em, "cancelled", note)
    em["cancelled_at"] = now_str()
    em["cancelled_by"] = "citizen"
    _stop_sos_tracking(em)
    _release_emergency_ambulance(em)
    save_emergencies(edata)

    msg = f"Citizen cancelled emergency #{eid}."
    hid = em.get("assigned_hospital_id")
    if hid:
        _notify("hospital", hid, msg, eid, "emergency_cancelled")
    sid = em.get("assigned_station_id")
    assigned = (em.get("assigned_to") or "").lower()
    if sid and assigned in ("police", "fire"):
        _notify_role_operators(assigned, msg, eid, "emergency_cancelled", station_id=sid)
    elif assigned in ("police", "fire"):
        _notify_role_operators(assigned, msg, eid, "emergency_cancelled")

    append_audit("citizen_cancel", "emergency", eid, {"reason": reason or None}, uid)
    return jsonify({"success": True, "status": "cancelled", "id": eid})


@app.route("/api/hospital/request/<int:eid>/accept", methods=["POST"])
@role_required("hospital")
def hospital_accept_request(eid):
    user = current_user()
    hid, _ = _get_user_hospital(user)
    if not hid:
        return jsonify({"success": False, "message": "Hospital not registered"}), 403
    em, edata = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False}), 404
    if em.get("assigned_hospital_id") != hid:
        return jsonify({"success": False, "message": "Not assigned to your hospital"}), 403
    data = request.get_json(silent=True) or {}
    amb_id = data.get("ambulance_unit_id")
    unit = None
    if amb_id not in (None, ""):
        try:
            unit = _assign_ambulance_to_emergency(em, amb_id, hid)
        except ValueError as exc:
            return jsonify({"success": False, "message": _safe_client_message(exc)}), 400
    _append_status(em, "accepted", "Hospital accepted request")
    em["accepted_at"] = now_str()
    em["assigned_to"] = "hospital"
    if not unit:
        hdata = hl.load_hospitals(read_json, save_json)
        hospital = hl.get_hospital_by_id(hdata, hid)
        if hospital:
            em["responder_latitude"] = hospital["latitude"]
            em["responder_longitude"] = hospital["longitude"]
    save_emergencies(edata)
    _notify("patient", em.get("user_id"), "Your emergency request has been accepted.", eid, "request_accepted")
    append_audit(
        "hospital_accept",
        "emergency",
        eid,
        {"ambulance_unit_id": em.get("assigned_ambulance_id")},
    )
    return jsonify({
        "success": True,
        "status": em["status"],
        "assigned_ambulance_id": em.get("assigned_ambulance_id"),
        "assigned_ambulance_call_sign": em.get("assigned_ambulance_call_sign"),
    })


@app.route("/api/hospital/request/<int:eid>/assign-ambulance", methods=["POST"])
@role_required("hospital")
def hospital_assign_ambulance(eid):
    """Assign or re-assign a hospital unit to an active accepted case."""
    user = current_user()
    hid, _ = _get_user_hospital(user)
    if not hid:
        return jsonify({"success": False, "message": "Hospital not registered"}), 403
    em, edata = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False}), 404
    if em.get("assigned_hospital_id") != hid:
        return jsonify({"success": False, "message": "Not assigned to your hospital"}), 403
    if (em.get("status") or "").lower() in COMPLETED_STATUSES:
        return jsonify({"success": False, "message": "Case is already closed"}), 400
    data = request.get_json(silent=True) or {}
    amb_id = data.get("ambulance_unit_id")
    if amb_id in (None, ""):
        return jsonify({"success": False, "message": "ambulance_unit_id is required"}), 400
    # Release previous unit if switching
    prev = em.get("assigned_ambulance_id")
    try:
        if prev and int(prev) != int(amb_id):
            _release_emergency_ambulance(em)
            em["assigned_ambulance_id"] = None
        unit = _assign_ambulance_to_emergency(em, amb_id, hid)
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400
    if (em.get("status") or "").lower() in ("pending", "pending_hospital"):
        _append_status(em, "accepted", "Hospital accepted with ambulance")
        em["accepted_at"] = now_str()
    elif (em.get("status") or "").lower() == "accepted":
        _append_status(em, "dispatched", "Ambulance " + (unit.get("call_sign") or "") + " dispatched")
        em["status"] = "dispatched"
    save_emergencies(edata)
    append_audit(
        "hospital_ambulance_assigned",
        "emergency",
        eid,
        {"ambulance_unit_id": unit.get("id"), "call_sign": unit.get("call_sign")},
        user.get("id"),
    )
    return jsonify({
        "success": True,
        "emergency": em,
        "ambulance": {
            "id": unit.get("id"),
            "call_sign": unit.get("call_sign"),
            "status": "busy",
            "driver_name": unit.get("driver_name"),
            "driver_phone": unit.get("driver_phone"),
        },
    })


@app.route("/api/hospital/request/<int:eid>/reject", methods=["POST"])
@role_required("hospital")
def hospital_reject_request(eid):
    user = current_user()
    hid, _ = _get_user_hospital(user)
    if not hid:
        return jsonify({"success": False, "message": "Hospital not registered"}), 403
    em, edata = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False}), 404
    if em.get("assigned_hospital_id") != hid:
        return jsonify({"success": False, "message": "Forbidden"}), 403
    settings = load_settings()
    timeout = int(settings.get("hospital_response_timeout_sec", 120))
    hdata = hl.load_hospitals(read_json, save_json)
    _append_status(em, "rejected_by_hospital", "Hospital rejected — escalating")
    em["escalation_index"] = em.get("escalation_index", 0) + 1
    hospital = hl.assign_next_hospital(em, hdata, timeout)
    if hospital:
        _notify("hospital", hospital["id"], f"Escalated emergency #{eid}", eid)
        _notify("patient", em.get("user_id"), f"Request forwarded to {hospital['name']}", eid)
    else:
        _append_status(em, "no_hospital_available", "No more hospitals")
        _stop_sos_tracking(em)
    save_emergencies(edata)
    return jsonify({"success": True, "status": em["status"], "hospital": em.get("assigned_hospital_name")})


@app.route("/api/notifications")
@login_required
def api_notifications():
    role = session.get("role")
    unread_only = request.args.get("unread") == "1"
    if role == "citizen":
        notes = hl.get_notifications_for(read_json, "patient", session.get("user_id"), unread_only=unread_only)
    elif role == "hospital":
        user = current_user()
        hid, _ = _get_user_hospital(user)
        notes = hl.get_notifications_for(read_json, "hospital", hid, unread_only=unread_only) if hid else []
    elif role in STAFF_ADMIN_ROLES:
        # Prefer role-scoped inbox; also include legacy "admin" target for super_admin
        notes = hl.get_notifications_for(read_json, role, session.get("user_id"), unread_only=unread_only)
        if role == "super_admin":
            legacy = hl.get_notifications_for(
                read_json, "admin", session.get("user_id"), unread_only=unread_only
            )
            seen = {n.get("id") for n in notes}
            notes = notes + [n for n in legacy if n.get("id") not in seen]
    elif role in ("police", "fire", "call_center"):
        notes = hl.get_notifications_for(read_json, role, session.get("user_id"), unread_only=unread_only)
    else:
        notes = []
    return jsonify({"notifications": notes, "unread_count": sum(1 for n in notes if not n.get("read"))})


@app.route("/api/notifications/read", methods=["POST"])
@login_required
def api_notifications_read():
    data = request.get_json(silent=True) or {}
    role = session.get("role")
    target_type = "patient" if role == "citizen" else role
    target_id = session.get("user_id")
    if role == "hospital":
        target_id = _user_hospital_id(current_user()) or target_id
    hl.mark_notifications_read(read_json, save_json, target_type, target_id, data.get("ids"))
    return jsonify({"success": True})


@app.route("/api/messages/<int:request_id>", methods=["GET", "POST"])
@login_required
def api_messages(request_id):
    em, _ = get_emergency_by_id(request_id)
    if not em:
        return jsonify({"success": False}), 404
    if not _can_access_emergency(em, session.get("role")):
        return jsonify({"success": False, "message": "Forbidden"}), 403
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        msg_type = data.get("msg_type", "text")
        # Accept text / message / content for client compatibility
        text = (
            data.get("text")
            or data.get("message")
            or data.get("content")
            or ""
        )
        text = str(text).strip()
        audio = (data.get("audio") or "").strip()
        if msg_type == "voice":
            if not audio:
                return jsonify({"success": False, "message": "No audio data"}), 400
            text = "[Voice message]"
        elif not text:
            return jsonify({"success": False, "message": "Empty message"}), 400
        msg = hl.add_message(
            read_json, save_json, request_id, session.get("role"), session.get("user_id"),
            text, msg_type=msg_type, audio_data=audio,
        )
        em_chat, edata = get_emergency_by_id(request_id)
        if em_chat:
            em_chat.pop("chat_typing", None)
            save_emergencies(edata)
        if session.get("role") == "citizen":
            if em.get("assigned_hospital_id"):
                _notify("hospital", em.get("assigned_hospital_id"), f"New message on request #{request_id}", request_id)
            elif em.get("assigned_station_id") and (em.get("assigned_to") or "") in ("police", "fire"):
                _notify_role_operators(
                    em.get("assigned_to"),
                    f"New message on request #{request_id}",
                    request_id,
                    "system_alert",
                    station_id=em.get("assigned_station_id"),
                )
            elif (em.get("assigned_to") or "") in ("police", "fire"):
                _notify_role_operators(
                    em.get("assigned_to"),
                    f"New message on request #{request_id}",
                    request_id,
                    "system_alert",
                )
            else:
                _notify_admins(f"New citizen message on emergency #{request_id}", request_id, "system_alert")
        else:
            _notify("patient", em.get("user_id"), "New message from response team", request_id, "system_alert")
        return jsonify({"success": True, "message": msg})
    msgs = hl.get_messages_for_request(read_json, save_json, request_id, session.get("role"))
    typing = em.get("chat_typing")
    show_typing = False
    if typing and typing.get("role") != session.get("role"):
        show_typing = (datetime.now() - parse_dt(typing.get("at"))).total_seconds() < 8
    return jsonify({"success": True, "messages": msgs, "typing": show_typing})


@app.route("/api/emergencies/<int:eid>/typing", methods=["POST"])
@login_required
def emergency_typing(eid):
    em, edata = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False}), 404
    if not _can_access_emergency(em, session.get("role")):
        return jsonify({"success": False, "message": "Forbidden"}), 403
    data = request.get_json(silent=True) or {}
    if data.get("typing"):
        em["chat_typing"] = {"role": session.get("role"), "at": now_str()}
    else:
        em.pop("chat_typing", None)
    save_emergencies(edata)
    return jsonify({"success": True})


@app.route("/api/user/profile", methods=["GET", "PUT"])
@role_required("citizen")
def api_user_profile():
    user, udata = get_user_by_id(session.get("user_id"))
    if not user:
        return jsonify({"success": False}), 404
    if request.method == "GET":
        return jsonify({"success": True, "profile": public_user_profile(user)})
    data = request.get_json(silent=True) or {}
    allowed = (
        "name", "phone", "emergency_contact_name", "emergency_contact_phone",
        "emergency_contact_relation", "emergency_contact_email", "address", "city",
        "date_of_birth", "blood_type", "medical_notes",
        "saved_locations", "first_name", "middle_name", "last_name", "gender",
    )
    for key in allowed:
        if key in data:
            user[key] = data[key]
    if any(k in data for k in ("first_name", "middle_name", "last_name")):
        user["name"] = _compose_full_name(
            user.get("first_name", ""),
            user.get("middle_name", ""),
            user.get("last_name", ""),
        ) or user.get("name")
    if "national_id" in data:
        try:
            last4, nid_hash, nid_enc = _national_id_fields(data.get("national_id"))
        except ValueError as exc:
            return jsonify({"success": False, "message": str(exc)}), 400
        if nid_hash and any(
            u.get("national_id_hash") == nid_hash and u.get("id") != user.get("id")
            for u in udata["users"]
        ):
            return jsonify({"success": False, "message": "This National ID is already registered."}), 400
        user["national_id_last4"] = last4
        user["national_id_hash"] = nid_hash
        user["national_id_encrypted"] = nid_enc
    if "profile_photo" in data:
        photo = data.get("profile_photo") or ""
        if photo and not str(photo).startswith("data:image/"):
            return jsonify({
                "success": False,
                "message": "Profile photo must be an uploaded image.",
            }), 400
        if photo and len(str(photo)) > 120000:
            return jsonify({"success": False, "message": "Photo too large"}), 400
        user["profile_photo"] = photo
    save_users(udata)
    return jsonify({"success": True, "profile": public_user_profile(user)})


@app.route("/api/user/password", methods=["POST"])
@role_required("citizen")
def api_user_change_password():
    """Citizen changes their own password (current password required)."""
    user, udata = get_user_by_id(session.get("user_id"))
    if not user:
        return jsonify({"success": False, "message": "Account not found"}), 404
    data = request.get_json(silent=True) or {}
    current_password = (data.get("current_password") or "").strip()
    new_password = (data.get("new_password") or data.get("password") or "").strip()
    confirm = (data.get("confirm_password") or "").strip()
    if not current_password:
        return jsonify({"success": False, "message": "Current password is required."}), 400
    if not new_password:
        return jsonify({"success": False, "message": "New password is required."}), 400
    if not check_password_hash(user.get("password_hash") or "", current_password):
        return jsonify({"success": False, "message": "Current password is incorrect."}), 400
    if confirm and confirm != new_password:
        return jsonify({"success": False, "message": "New passwords do not match."}), 400
    if new_password == current_password:
        return jsonify({"success": False, "message": "New password must be different from the current one."}), 400
    pw_err = _password_policy_error(new_password)
    if pw_err:
        return jsonify({"success": False, "message": pw_err}), 400
    user["password_hash"] = generate_password_hash(new_password)
    save_users(udata)
    append_audit("citizen_password_changed", "user", user.get("id"), {}, user.get("id"))
    return jsonify({"success": True, "message": "Password updated successfully."})


@app.route("/api/user/dashboard")
@role_required("citizen")
def api_user_dashboard():
    _run_escalations()
    uid = session.get("user_id")
    edata = load_emergencies()
    mine = [e for e in edata["emergencies"] if e.get("user_id") == uid]
    mine.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    active = [e for e in mine if e.get("status") not in COMPLETED_STATUSES]
    completed = [e for e in mine if e.get("status") in COMPLETED_STATUSES]
    notes = hl.get_notifications_for(read_json, "patient", uid, limit=30)
    unread = sum(1 for n in notes if not n.get("read"))
    announcements = load_announcements().get("announcements", [])[:10]
    user, _ = get_user_by_id(uid)
    active_chat = None
    if active:
        em = active[0]
        msgs = hl.get_messages_for_request(read_json, save_json, em["id"], "citizen")
        active_chat = {"request_id": em["id"], "message_count": len(msgs)}
    return jsonify({
        "success": True,
        "profile_summary": {
            "name": user.get("name") if user else "",
            "email": user.get("email") if user else "",
            "phone": user.get("phone") if user else "",
            "profile_photo": user.get("profile_photo", "") if user else "",
            "emergency_contact_name": user.get("emergency_contact_name", "") if user else "",
            "emergency_contact_phone": user.get("emergency_contact_phone", "") if user else "",
            "account_status": user.get("status", "active") if user else "active",
            "saved_locations": user.get("saved_locations", []) if user else [],
        },
        "active_emergency": _emergency_summary(active[0]) if active else None,
        "active_count": len(active),
        "completed_count": len(completed),
        "recent_emergencies": [_emergency_summary(e) for e in mine[:8]],
        "notifications": notes[:12],
        "unread_notifications": unread,
        "announcements": announcements,
        "active_chat": active_chat,
    })


TIMELINE_STEPS = (
    ("request_submitted", "Request Submitted"),
    ("request_accepted", "Request Accepted"),
    ("team_assigned", "Team Assigned"),
    ("en_route", "Team En Route"),
    ("arrived", "Team Arrived"),
    ("completed", "Case Completed"),
)

DISPLAY_STAGE_LABELS = {
    "request_received": "Request Received",
    "team_assigned": "Team Assigned",
    "on_the_way": "On The Way",
    "arrived": "Arrived",
    "assistance_complete": "Assistance Complete",
    "standby": "Ready — No Active Emergency",
}


def _distance_remaining_km(em, responder=None):
    victim_lat, victim_lng = hl.best_emergency_coords(em)
    if victim_lat is None or victim_lng is None or not hl.is_in_somalia(victim_lat, victim_lng):
        return None
    dist = None
    if responder and responder.get("latitude") is not None:
        if hl.is_in_somalia(responder["latitude"], responder["longitude"]):
            dist = hl.haversine_km(
                responder["latitude"], responder["longitude"], victim_lat, victim_lng
            )
    if dist is None:
        base = _responder_base_location(em)
        if base and hl.is_in_somalia(base["latitude"], base["longitude"]):
            dist = hl.haversine_km(base["latitude"], base["longitude"], victim_lat, victim_lng)
    return hl.cap_local_distance_km(dist)


def _dispatch_unit_info(em):
    """Assigned response unit details shown to the citizen (DB fields only)."""
    team_label = em.get("assigned_team_label") or TEAM_LABELS.get(
        em.get("assigned_to"), "Emergency Response Team"
    )
    base = _responder_base_location(em)
    contact = (em.get("contact_number") or "").strip() or None
    unit_name = team_label
    if base:
        unit_name = base.get("name") or team_label
    if em.get("assigned_hospital_id"):
        hdata = hl.load_hospitals(read_json, save_json)
        hospital = hl.get_hospital_by_id(hdata, em.get("assigned_hospital_id"))
        if hospital:
            unit_name = hospital.get("name") or unit_name
            if hospital.get("phone"):
                contact = hospital["phone"]
    return {
        "team_name": unit_name,
        "team_id": em.get("assigned_team_id") or None,
        "vehicle_number": em.get("vehicle_number") or None,
        "driver_name": em.get("driver_name") or None,
        "contact_number": contact,
    }


def _emergency_display_stage(em):
    if em.get("status") in COMPLETED_STATUSES:
        return "assistance_complete", DISPLAY_STAGE_LABELS["assistance_complete"]
    rs = em.get("responder_status", {})
    if rs.get("arrived_at_scene") or em.get("status") == "in_progress":
        return "arrived", DISPLAY_STAGE_LABELS["arrived"]
    if rs.get("en_route") or em.get("status") == "dispatched":
        return "on_the_way", DISPLAY_STAGE_LABELS["on_the_way"]
    if em.get("assigned_hospital_id") or em.get("assigned_to") in get_response_stations():
        return "team_assigned", DISPLAY_STAGE_LABELS["team_assigned"]
    if em.get("status") in ("pending", "pending_hospital", "accepted"):
        return "request_received", DISPLAY_STAGE_LABELS["request_received"]
    return "request_received", DISPLAY_STAGE_LABELS["request_received"]


def _build_emergency_timeline(em):
    rs = em.get("responder_status", {})
    history = {h.get("status"): h.get("timestamp") for h in em.get("status_history", [])}
    steps = []
    for key, label in TIMELINE_STEPS:
        ts = None
        done = False
        if key == "request_submitted":
            ts = em.get("timestamp")
            done = bool(ts)
        elif key == "request_accepted":
            ts = em.get("accepted_at") or history.get("accepted")
            done = bool(ts) or em.get("status") not in ("pending",)
        elif key == "team_assigned":
            ts = history.get("pending_hospital") or history.get("pending")
            done = bool(em.get("assigned_hospital_id")) or em.get("assigned_to") in get_response_stations()
            if done and not ts:
                ts = em.get("timestamp")
        elif key == "en_route":
            ts = rs.get("en_route")
            done = bool(ts) or em.get("status") in ("dispatched", "in_progress", "completed", "resolved")
        elif key == "arrived":
            ts = rs.get("arrived_at_scene")
            done = bool(ts) or em.get("status") in ("in_progress", "completed", "resolved")
        elif key == "completed":
            ts = rs.get("reached_victim") or history.get("completed") or history.get("resolved")
            done = em.get("status") in COMPLETED_STATUSES
        steps.append({"key": key, "label": label, "timestamp": ts, "completed": done})
    return steps


def _emergency_summary(em):
    team = em.get("assigned_team_label") or TEAM_LABELS.get(em.get("assigned_to"), "Response Team")
    victim_lat, victim_lng, _ = _emergency_coords_view(em)
    em_view = dict(em)
    em_view["latitude"] = victim_lat
    em_view["longitude"] = victim_lng
    responder = _compute_responder_location(em_view)
    stage_key, stage_label = _emergency_display_stage(em)
    unit = _dispatch_unit_info(em)
    hospital_info = None
    if em.get("assigned_hospital_id"):
        hdata = hl.load_hospitals(read_json, save_json)
        hospital = hl.get_hospital_by_id(hdata, em.get("assigned_hospital_id"))
        coords = hl.resolve_hospital_coords(hospital) if hospital else None
        if hospital and coords:
            hospital_info = {
                "id": hospital["id"],
                "name": hospital["name"],
                "latitude": coords[0],
                "longitude": coords[1],
            }
    return {
        "id": em["id"],
        "type": em.get("type", "emergency"),
        "status": em.get("status"),
        "display_stage": stage_key,
        "display_stage_label": stage_label,
        "team": team,
        "timestamp": em.get("timestamp"),
        "last_update": em.get("last_location_update") or em.get("accepted_at") or em.get("timestamp"),
        "location": em.get("location"),
        "latitude": victim_lat,
        "longitude": victim_lng,
        "tracking_active": em.get("tracking_active", False),
        "eta_minutes": _compute_eta_minutes(em_view, responder),
        "distance_km": _distance_remaining_km(em_view, responder),
        "responder_assigned": responder is not None or bool(em.get("assigned_hospital_id")),
        "hospital": hospital_info,
        "assigned_to": em.get("assigned_to"),
        "dispatch_unit": unit,
        "timeline": _build_emergency_timeline(em),
        "responder_status": em.get("responder_status", {}),
    }


def _responder_base_location(em):
    assigned = em.get("assigned_to", "hospital")
    if assigned == "hospital" and em.get("assigned_hospital_id"):
        hdata = hl.load_hospitals(read_json, save_json)
        hospital = hl.get_hospital_by_id(hdata, em.get("assigned_hospital_id"))
        if hospital:
            coords = hl.resolve_hospital_coords(hospital)
            if coords:
                return {
                    "latitude": coords[0],
                    "longitude": coords[1],
                    "name": hospital["name"],
                }
    station = get_response_stations().get(assigned)
    if station and hl.is_in_somalia(station["latitude"], station["longitude"]):
        return dict(station)
    return None


def _compute_responder_location(em):
    """Simulate live responder GPS advancing toward the emergency scene."""
    if em.get("status") in COMPLETED_STATUSES:
        return None
    victim_lat, victim_lng = hl.best_emergency_coords(em)
    if victim_lat is None or victim_lng is None or not hl.is_in_somalia(victim_lat, victim_lng):
        return None
    base = _responder_base_location(em)
    if not base and not em.get("assigned_hospital_id") and em.get("assigned_to") not in get_response_stations():
        return None
    if not base:
        return None

    start_lat, start_lng = base["latitude"], base["longitude"]
    rs = em.get("responder_status", {})
    if rs.get("reached_victim") or em.get("status") in ("completed", "resolved"):
        progress = 1.0
    elif rs.get("arrived_at_scene") or em.get("status") == "in_progress":
        progress = 0.92
    elif rs.get("en_route") or em.get("status") == "dispatched":
        anchor = rs.get("en_route") or em.get("accepted_at") or em.get("timestamp")
        elapsed = (datetime.now() - parse_dt(anchor)).total_seconds()
        progress = min(0.88, max(0.08, elapsed / 420))
    elif em.get("status") in ("accepted", "pending_hospital"):
        progress = 0.0
    elif em.get("assigned_hospital_id") or em.get("assigned_to") in get_response_stations():
        progress = 0.04
    else:
        return None

    lat = start_lat + (victim_lat - start_lat) * progress
    lng = start_lng + (victim_lng - start_lng) * progress
    return {
        "latitude": round(lat, 6),
        "longitude": round(lng, 6),
        "name": base.get("name", em.get("assigned_team_label", "Response Team")),
        "progress_pct": round(progress * 100),
    }


def _compute_eta_minutes(em, responder=None):
    dist = _distance_remaining_km(em, responder)
    if dist is None:
        stored = em.get("hospital_distance_km")
        dist = hl.cap_local_distance_km(stored)
    if dist is None:
        return None
    eta = max(2, int((float(dist) / 35) * 60))
    return min(eta, 120)


def _emergency_coords_view(em):
    """Sanitized Somalia coordinates for map and distance calculations."""
    lat, lng = hl.best_emergency_coords(em)
    stored_valid = (
        em.get("latitude") is not None
        and em.get("longitude") is not None
        and hl.is_in_somalia(em["latitude"], em["longitude"])
    )
    return lat, lng, stored_valid


def _emergency_tracking_payload(em):
    victim_lat, victim_lng, coords_valid = _emergency_coords_view(em)
    em_view = dict(em)
    em_view["latitude"] = victim_lat
    em_view["longitude"] = victim_lng
    responder = _compute_responder_location(em_view)
    trail = hl.filter_somalia_trail(em.get("location_history") or [])
    team_label = em.get("assigned_team_label") or TEAM_LABELS.get(em.get("assigned_to"), "Response Team")

    hospital_payload = None
    station_payload = None
    assigned = em.get("assigned_to", "hospital")
    if assigned == "hospital" and em.get("assigned_hospital_id"):
        hdata = hl.load_hospitals(read_json, save_json)
        hospital = hl.get_hospital_by_id(hdata, em.get("assigned_hospital_id"))
        coords = hl.resolve_hospital_coords(hospital) if hospital else None
        if hospital and coords:
            hospital_payload = {
                "id": hospital["id"],
                "name": hospital["name"],
                "latitude": coords[0],
                "longitude": coords[1],
                "phone": hospital.get("phone"),
            }
    elif assigned in get_response_stations():
        station = get_response_stations()[assigned]
        station_payload = {
            "type": assigned,
            "name": station["name"],
            "latitude": station["latitude"],
            "longitude": station["longitude"],
        }

    district = em.get("district") or ""
    if coords_valid and victim_lat is not None and victim_lng is not None:
        location_label = (district + " (" + str(victim_lat) + ", " + str(victim_lng) + ")").strip()
    else:
        location_label = district or "Location unavailable"

    stored_lat, stored_lng = em.get("latitude"), em.get("longitude")
    coords_corrected = False
    if victim_lat is not None and victim_lng is not None:
        if stored_lat is None or stored_lng is None or not hl.is_in_somalia(stored_lat, stored_lng):
            coords_corrected = True
        else:
            try:
                if abs(float(stored_lat) - float(victim_lat)) > 1e-5 or abs(
                    float(stored_lng) - float(victim_lng)
                ) > 1e-5:
                    coords_corrected = True
            except (TypeError, ValueError):
                coords_corrected = True

    return {
        "emergency_id": em["id"],
        "latitude": victim_lat,
        "longitude": victim_lng,
        "coords_valid": coords_valid,
        "coords_corrected": coords_corrected,
        "accuracy_m": em.get("accuracy_m"),
        "district": district,
        "location": location_label,
        "status": em.get("status"),
        "type": em.get("type"),
        "assigned_to": assigned,
        "tracking_active": em.get("tracking_active", False),
        "last_location_update": em.get("last_location_update"),
        "caller_name": em.get("caller_name"),
        "phone": em.get("phone"),
        "trail": trail[-30:],
        "trail_count": len(trail),
        "team_label": team_label,
        "team_assigned": responder is not None or bool(em.get("assigned_hospital_id")),
        "hospital": hospital_payload,
        "station": station_payload,
        "responder": responder,
        "eta_minutes": _compute_eta_minutes(em_view, responder),
        "distance_km": _distance_remaining_km(em_view, responder),
        "responder_status": em.get("responder_status", {}),
        "display_stage": _emergency_display_stage(em)[0],
        "display_stage_label": _emergency_display_stage(em)[1],
        "dispatch_unit": _dispatch_unit_info(em),
        "timeline": _build_emergency_timeline(em),
        "last_update": em.get("last_location_update") or em.get("accepted_at") or em.get("timestamp"),
        "typing": em.get("chat_typing"),
    }


@app.route("/api/announcements")
@login_required
def api_announcements():
    return jsonify({"announcements": load_announcements().get("announcements", [])})


@app.route("/api/send_alert", methods=["POST"])
@role_required("citizen")
def send_alert():
    settings = load_settings()
    if not settings.get("sos_enabled", True):
        return jsonify({"success": False, "message": "SOS system is currently disabled."}), 403
    if settings.get("maintenance_mode"):
        return jsonify({"success": False, "message": "System under maintenance."}), 503
    if emergencies_today_count() >= settings.get("max_emergencies_per_day", 100):
        return jsonify({"success": False, "message": "Daily emergency limit reached."}), 429

    data = request.get_json(silent=True) or {}
    edata = load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    etype = normalize_type(data.get("type", "medical"))
    assign_map = {
        "medical": "hospital",
        "family_help": "hospital",
        "fire": "fire",
        "security": "police",
        "accident": "police",
    }
    lat = data.get("latitude")
    lng = data.get("longitude")
    district = data.get("district") or ""
    location_text = data.get("location") or "Location unavailable"
    if lat is not None and lng is not None:
        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            lat, lng = None, None

    fix = build_location_fix(data)
    fix["latitude"] = fix["latitude"] if fix["latitude"] is not None else lat
    fix["longitude"] = fix["longitude"] if fix["longitude"] is not None else lng
    if fix["latitude"] is None or fix["longitude"] is None:
        return jsonify({
            "success": False,
            "message": "GPS location is required to send SOS. Enable location and try again.",
        }), 400
    try:
        fix["latitude"], fix["longitude"] = hl.validate_coordinates(
            fix["latitude"], fix["longitude"]
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400

    emergency = {
        "id": eid,
        "user_id": session.get("user_id"),
        "type": etype,
        "location": location_text,
        "district": district or fix.get("district", ""),
        "latitude": fix["latitude"],
        "longitude": fix["longitude"],
        "building": fix.get("building", ""),
        "floor": fix.get("floor", ""),
        "room": fix.get("room", ""),
        "accuracy_m": fix.get("accuracy_m"),
        "method": fix.get("method", "gps"),
        "confidence": fix.get("confidence"),
        "timestamp": now_iso(),
        "status": "pending",
        "caller_name": data.get("name") or session.get("name", "Anonymous"),
        "phone": data.get("phone") or "Not provided",
        "notes": (data.get("notes") or "").strip()[:2000],
        "assigned_to": assign_map.get(etype, "hospital"),
        "location_history": [fix],
        "responder_status": {},
    }
    _apply_tracking_fields(emergency, fix)
    # Persist first so notification FKs (request_id → emergencies.id) succeed on MySQL
    edata["emergencies"].append(emergency)
    save_emergencies(edata)
    _auto_dispatch_emergency(emergency)
    if (emergency.get("status") or "").lower() in COMPLETED_STATUSES:
        _stop_sos_tracking(emergency)
    save_emergencies(edata)
    citizen, _ = get_user_by_id(session.get("user_id"))
    _notify_emergency_contact(citizen, emergency)
    _run_escalations()
    # AI analyzes in parallel — never delays or replaces SOS auto-dispatch
    _schedule_ai_analysis(emergency, source="sos")
    append_audit("emergency_created", "emergency", eid, {"type": etype}, session.get("user_id"))
    return jsonify({
        "success": True,
        "id": eid,
        "status": emergency.get("status"),
        "team": emergency.get("assigned_team_label"),
        "assigned_to": emergency.get("assigned_to"),
        "assigned_hospital": emergency.get("assigned_hospital_name"),
        "assigned_station_id": emergency.get("assigned_station_id"),
        "hospital_distance_km": emergency.get("hospital_distance_km"),
        "message": "Emergency dispatched to response team.",
    })


@app.route("/api/route/osrm")
@app.route("/api/route")
@login_required
def api_osrm_route():
    """Driving route — Google Directions primary, OSRM only if Google unavailable."""
    from_param = request.args.get("from", "")
    to_param = request.args.get("to", "")
    try:
        lat1, lng1 = [float(x) for x in from_param.split(",")]
        lat2, lng2 = [float(x) for x in to_param.split(",")]
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid from/to coordinates."}), 400
    for lat, lng in ((lat1, lng1), (lat2, lng2)):
        if not hl.is_in_somalia(lat, lng):
            return jsonify({"success": False, "message": "Route points must be within Somalia."}), 400

    api_key = _google_maps_api_key()
    if api_key and _use_google_maps():
        try:
            g_route = _google_directions_route(lat1, lng1, lat2, lng2, api_key)
            return jsonify({"success": True, **g_route, "provider": "google"})
        except Exception:
            logging.getLogger(__name__).exception("Google Directions failed; trying OSRM fallback")

    path = f"{lng1},{lat1};{lng2},{lat2}"
    url = (
        "https://router.project-osrm.org/route/v1/driving/"
        + urllib.parse.quote(path, safe=";,")
        + "?overview=full&geometries=geojson&steps=false"
    )
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return jsonify({
            "success": False,
            "message": "Routing unavailable. Configure a Google Maps API key with Directions enabled.",
        }), 502
    routes = payload.get("routes") or []
    if not routes:
        return jsonify({"success": False, "message": "No route found."}), 404
    route = routes[0]
    geom = route.get("geometry") or {}
    coords = geom.get("coordinates") or []
    return jsonify({
        "success": True,
        "coordinates": coords,
        "distance_km": round((route.get("distance") or 0) / 1000, 2),
        "duration_minutes": max(1, int((route.get("duration") or 0) / 60)),
        "provider": "osrm",
        "source": "osrm",
    })


@app.route("/api/emergencies/<int:eid>/tracking", methods=["GET"])
@login_required
def emergency_tracking(eid):
    """Real-time location snapshot + trail for citizen and assigned hospital."""
    em, _ = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False, "message": "Not found"}), 404
    role = session.get("role")
    if not _can_access_emergency(em, role):
        return jsonify({"success": False, "message": "Forbidden"}), 403
    payload = _emergency_tracking_payload(em)
    return jsonify({"success": True, **payload})


@app.route("/api/emergencies/<int:eid>/location", methods=["POST"])
@login_required
def append_emergency_location(eid):
    data = request.get_json(silent=True) or {}
    em, edata = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False, "message": "Not found"}), 404
    role = session.get("role")
    if not _can_access_emergency(em, role):
        return jsonify({"success": False, "message": "Forbidden"}), 403
    if not _is_active_sos(em):
        _stop_sos_tracking(em)
        save_emergencies(edata)
        return jsonify({
            "success": False,
            "message": "SOS is no longer active — location tracking stopped.",
            "tracking_active": False,
        }), 409

    fix = build_location_fix(data)
    if fix["latitude"] is None:
        return jsonify({
            "success": False,
            "message": "Coordinates must be within Somalia.",
        }), 400
    try:
        fix["latitude"], fix["longitude"] = hl.validate_coordinates(
            fix["latitude"], fix["longitude"]
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400
    em.setdefault("location_history", [])
    em["location_history"].append(fix)
    if fix["latitude"] is not None:
        em["latitude"] = fix["latitude"]
        em["longitude"] = fix["longitude"]
    for key in ("building", "floor", "room", "district", "accuracy_m", "method", "confidence"):
        if fix.get(key):
            em[key] = fix[key]
    em["tracking_active"] = True
    em["last_location_update"] = now_str()
    if em.get("location") and fix.get("district"):
        em["location"] = fix["district"] + " (" + str(fix["latitude"]) + ", " + str(fix["longitude"]) + ")"
    save_emergencies(edata)
    append_audit("location_update", "emergency", eid, fix)
    return jsonify({
        "success": True,
        "fix": fix,
        "count": len(em["location_history"]),
        "latitude": em.get("latitude"),
        "longitude": em.get("longitude"),
    })


@app.route("/api/emergencies/<int:eid>/location/stop", methods=["POST"])
@login_required
def stop_emergency_tracking(eid):
    em, edata = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False}), 404
    if session.get("role") == "citizen" and em.get("user_id") != session.get("user_id"):
        return jsonify({"success": False, "message": "Forbidden"}), 403
    em["tracking_active"] = False
    save_emergencies(edata)
    return jsonify({"success": True, "tracking_active": False})


@app.route("/api/emergencies/<int:eid>/locations", methods=["GET"])
@login_required
def get_emergency_locations(eid):
    em, _ = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False}), 404
    if not _can_access_emergency(em, session.get("role")):
        return jsonify({"success": False, "message": "Forbidden"}), 403
    return jsonify({
        "success": True,
        "emergency_id": eid,
        "locations": em.get("location_history", []),
        "latitude": em.get("latitude"),
        "longitude": em.get("longitude"),
        "tracking_active": em.get("tracking_active", False),
    })


@app.route("/api/emergencies/<int:eid>/responder", methods=["POST"])
@login_required
def responder_status_update(eid):
    if session.get("role") not in ROLE_API_TYPE:
        return jsonify({"success": False, "message": "Forbidden"}), 403
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    em, edata = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False}), 404
    if not matches_filter(em["type"], ROLE_API_TYPE[session.get("role")]):
        return jsonify({"success": False, "message": "Forbidden"}), 403

    valid_actions = ("arrived_at_scene", "reached_victim", "en_route")
    if action not in valid_actions:
        return jsonify({"success": False, "message": "Invalid action"}), 400

    em.setdefault("responder_status", {})
    em["responder_status"][action] = now_str()
    uid = em.get("user_id")
    if action == "en_route":
        em["status"] = "dispatched"
        _notify("patient", uid, "Your emergency response team is on the way.", eid, "team_dispatched")
    elif action == "arrived_at_scene":
        em["status"] = "in_progress"
        _notify("patient", uid, "The response team has arrived at your location.", eid, "team_arrived")
    elif action == "reached_victim":
        em["status"] = "completed"
        _stop_sos_tracking(em)
        _release_emergency_ambulance(em)
        _notify("patient", uid, "Your emergency has been resolved.", eid, "emergency_completed")
    save_emergencies(edata)
    append_audit(action, "emergency", eid)
    return jsonify({"success": True, "responder_status": em["responder_status"], "status": em["status"]})


@app.route("/api/emergencies/<int:eid>/route", methods=["GET"])
@login_required
def emergency_route(eid):
    em, _ = get_emergency_by_id(eid)
    if not em:
        return jsonify({"success": False}), 404
    coords = EmergencyLocation_parse(em)
    if coords.get("lat") is None or coords.get("lng") is None:
        return jsonify({
            "success": False,
            "message": "Victim GPS location is not available yet.",
        }), 404
    return jsonify(
        {
            "success": True,
            "victim": {
                "lat": coords["lat"],
                "lng": coords["lng"],
                "label": _location_label(em),
                "building": em.get("building"),
                "floor": em.get("floor"),
                "room": em.get("room"),
            },
            "instructions": _simple_navigation_instructions(em),
            "eta_note": "Use OSRM/Mapbox for production turn-by-turn; outdoor routing only in MVP.",
        }
    )


def EmergencyLocation_parse(em):
    """Parse emergency coords — never invent Mogadishu when GPS is missing."""
    lat, lng = hl.best_emergency_coords(em)
    if lat is not None and lng is not None:
        return {"lat": float(lat), "lng": float(lng)}
    import re

    m = re.search(r"\((-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\)", em.get("location", "") or "")
    if m:
        try:
            plat, plng = float(m.group(1)), float(m.group(2))
            if hl.is_in_somalia(plat, plng):
                return {"lat": plat, "lng": plng}
        except (TypeError, ValueError):
            pass
    return {"lat": None, "lng": None}


def _location_label(em):
    parts = [p for p in [em.get("building"), em.get("floor") and f"Floor {em['floor']}", em.get("room") and f"Room {em['room']}"] if p]
    if parts:
        return ", ".join(parts) + " — " + (em.get("district") or em.get("location", ""))
    return em.get("district") or em.get("location", "Unknown")


def _simple_navigation_instructions(em):
    label = _location_label(em)
    return [
        {"step": 1, "text": "Navigate to " + (em.get("district") or "victim coordinates") + " using the map."},
        {"step": 2, "text": "Use GPS coordinates: " + label + "."},
        {"step": 3, "text": "Call the victim on arrival to confirm exact spot."},
    ]


@app.route("/api/get_emergencies", methods=["GET"])
@login_required
def get_emergencies():
    _run_escalations()
    role = session.get("role")
    filter_type = request.args.get("type", "").lower()
    status_filter = request.args.get("status", "").lower()

    if role in ROLE_API_TYPE:
        allowed = ROLE_API_TYPE[role]
        if filter_type and filter_type != allowed:
            return jsonify({"success": False, "message": "Forbidden"}), 403
        filter_type = allowed
    elif role not in STAFF_ADMIN_ROLES:
        return jsonify({"success": False, "message": "Forbidden"}), 403

    edata = load_emergencies()
    result = []
    user = current_user()
    hospital_id = _user_hospital_id(user) if role == "hospital" else None
    station_id = _user_station_id(user) if role in ("police", "fire") else None
    if role == "hospital" and not hospital_id:
        return jsonify({
            "emergencies": [],
            "count": 0,
            "refresh_interval": load_settings().get("refresh_interval", 5),
            "avg_response_time": None,
            "message": "Complete hospital registration to receive dispatch requests.",
        })
    if role in ("police", "fire") and not station_id:
        return jsonify({
            "emergencies": [],
            "count": 0,
            "refresh_interval": load_settings().get("refresh_interval", 5),
            "avg_response_time": None,
            "message": "Link a police/fire station to your account (Admin → Stations / Users).",
        })
    import police_logic as pl

    for em in edata["emergencies"]:
        if role not in STAFF_ADMIN_ROLES and not matches_filter(em["type"], filter_type):
            continue
        if role == "hospital":
            if em.get("assigned_hospital_id") != hospital_id:
                continue
        if role in ("police", "fire"):
            if not pl.emergency_visible_to_station(em, station_id, role):
                continue
        if status_filter:
            allowed_statuses = {s.strip() for s in status_filter.split(",") if s.strip()}
            # Treat completed/resolved as interchangeable history statuses
            if "resolved" in allowed_statuses:
                allowed_statuses.add("completed")
            if "completed" in allowed_statuses:
                allowed_statuses.add("resolved")
            if em.get("status") not in allowed_statuses:
                continue
        row = dict(em)
        row["tracking_active"] = em.get("tracking_active", False)
        row["last_location_update"] = em.get("last_location_update")
        result.append(row)
    result.sort(key=lambda x: x["timestamp"], reverse=True)
    settings = load_settings()
    return jsonify(
        {
            "emergencies": result,
            "count": len(result),
            "refresh_interval": settings.get("refresh_interval", 5),
            "avg_response_time": _avg_response_minutes(result),
            "station_id": station_id,
        }
    )


@app.route("/api/update_status", methods=["POST"])
@login_required
def update_status():
    role = session.get("role")
    if role not in ROLE_API_TYPE and role not in STAFF_ADMIN_ROLES:
        return jsonify({"success": False, "message": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    eid = int(data.get("id", 0))
    new_status = data.get("status", "dispatched")
    if new_status not in STATUS_VALUES:
        return jsonify({"success": False, "message": "Invalid status"}), 400

    edata = load_emergencies()
    for em in edata["emergencies"]:
        if em["id"] == eid:
            if role in ROLE_API_TYPE and not matches_filter(em["type"], ROLE_API_TYPE[role]):
                return jsonify({"success": False, "message": "Forbidden"}), 403
            em["status"] = new_status
            uid = em.get("user_id")
            if new_status == "dispatched":
                _notify("patient", uid, "Your emergency response team has been dispatched.", eid, "team_dispatched")
            elif new_status in ("completed", "resolved"):
                _stop_sos_tracking(em)
                _release_emergency_ambulance(em)
                _notify("patient", uid, "Your emergency has been completed.", eid, "emergency_completed")
                _ai_record_outcome(em)
            elif new_status in COMPLETED_STATUSES:
                _stop_sos_tracking(em)
                _release_emergency_ambulance(em)
            save_emergencies(edata)
            append_audit("status_update", "emergency", eid, {"status": new_status})
            return jsonify({"success": True, "emergency": em})
    return jsonify({"success": False, "message": "Not found"}), 404


# ---------- ADMIN API ----------


@app.route("/api/admin/backup", methods=["POST"])
@admin_required
def admin_backup():
    denied = _require_admin_perm("backup")
    if denied:
        return denied
    backup_dir = os.path.join(DATABASE_DIR, "backups", datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(backup_dir, exist_ok=True)
    ms = _mysql_backend()
    if ms:
        for name, payload in ms.export_all().items():
            with open(os.path.join(backup_dir, f"{name}.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
    else:
        import shutil

        for name in os.listdir(DATABASE_DIR):
            if name.endswith(".json"):
                shutil.copy2(os.path.join(DATABASE_DIR, name), os.path.join(backup_dir, name))
    append_audit("database_backup", "system", 0, {"path": backup_dir})
    return jsonify({"success": True, "backup_path": backup_dir})

def _admin_command_payload():
    """Shared stats payload for overview + Super Admin command center."""
    udata = load_users()
    edata = load_emergencies()
    users = udata["users"]
    emergencies = edata["emergencies"]
    now = datetime.now()
    today = now.date()
    week_start = today - timedelta(days=7)
    month_start = today - timedelta(days=30)

    by_role = {r: 0 for r in VALID_ROLES}
    for u in users:
        by_role[u["role"]] = by_role.get(u["role"], 0) + 1

    def _eday(e):
        try:
            return parse_dt(e.get("timestamp")).date()
        except Exception:
            return None

    today_count = sum(1 for e in emergencies if _eday(e) == today)
    week_count = sum(1 for e in emergencies if _eday(e) and _eday(e) >= week_start)
    month_count = sum(1 for e in emergencies if _eday(e) and _eday(e) >= month_start)

    by_type = {}
    by_location = {}
    by_day = {}
    by_day_dates = []
    prev_week_start = today - timedelta(days=14)
    prev_day_values = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        key = d.strftime("%a")
        by_day[key] = 0
        by_day_dates.append({"key": key, "date": d.strftime("%b %d"), "iso": d.isoformat()})
        prev_d = today - timedelta(days=i + 7)
        prev_day_values.append({
            "key": prev_d.strftime("%a"),
            "date": prev_d.strftime("%b %d"),
            "count": 0,
            "iso": prev_d.isoformat(),
        })

    for e in emergencies:
        by_type[e.get("type") or "other"] = by_type.get(e.get("type") or "other", 0) + 1
        loc = (e.get("location") or e.get("district") or "Unknown").split(",")[0].strip() or "Unknown"
        by_location[loc] = by_location.get(loc, 0) + 1
        ed = _eday(e)
        if not ed:
            continue
        if ed >= week_start:
            key = ed.strftime("%a")
            if key in by_day:
                by_day[key] += 1
        if prev_week_start <= ed < week_start:
            for slot in prev_day_values:
                if slot["iso"] == ed.isoformat():
                    slot["count"] += 1
                    break

    active_statuses = set(ACTIVE_SOS_STATUSES)
    active_emergencies = [e for e in emergencies if (e.get("status") or "").lower() in active_statuses]
    # "Resolved" in Reports = completed/resolved only (not cancelled)
    done_statuses = ("completed", "resolved")
    resolved_week = sum(
        1
        for e in emergencies
        if _eday(e)
        and _eday(e) >= week_start
        and (e.get("status") or "").lower() in done_statuses
    )
    resolved_total = sum(
        1 for e in emergencies if (e.get("status") or "").lower() in done_statuses
    )
    # Keep week_summary.resolved compatible with prior cancelled-inclusive count
    resolved_week_summary = sum(
        1
        for e in emergencies
        if _eday(e)
        and _eday(e) >= week_start
        and (e.get("status") or "").lower() in ("completed", "resolved", "cancelled")
    )
    prev_week_count = sum(
        1
        for e in emergencies
        if _eday(e) and prev_week_start <= _eday(e) < week_start
    )
    pending_week = sum(
        1
        for e in emergencies
        if _eday(e) and _eday(e) >= week_start and (e.get("status") or "").lower() in active_statuses
    )
    cancelled_week = sum(
        1
        for e in emergencies
        if _eday(e) and _eday(e) >= week_start and (e.get("status") or "").lower() == "cancelled"
    )
    busiest_day = None
    if by_day:
        bk = max(by_day.items(), key=lambda x: x[1])
        if bk[1] > 0:
            match = next((x for x in by_day_dates if x["key"] == bk[0]), None)
            busiest_day = {
                "label": match["date"] if match else bk[0],
                "weekday": bk[0],
                "count": bk[1],
            }

    # Average response from status history when available
    daily_response = {d.strftime("%a"): [] for d in (today - timedelta(days=i) for i in range(6, -1, -1))}
    for e in emergencies:
        hist = e.get("status_history") or []
        start = parse_dt(e.get("timestamp")) if e.get("timestamp") else None
        if not start or start == datetime.min:
            continue
        dispatched_at = None
        for h in hist:
            st = (h.get("status") or "").lower()
            if st in ("dispatched", "accepted", "in_progress") and h.get("timestamp"):
                dispatched_at = parse_dt(h.get("timestamp"))
                break
        if dispatched_at and dispatched_at != datetime.min and dispatched_at >= start:
            mins = (dispatched_at - start).total_seconds() / 60.0
            if 0 <= mins <= 180:
                ed = _eday(e)
                if ed and ed >= week_start:
                    daily_response[ed.strftime("%a")].append(mins)

    settings = load_settings()
    avg = _avg_response_minutes(emergencies)

    avg_by_day = {}
    for day, samples in daily_response.items():
        avg_by_day[day] = (
            round(sum(samples) / len(samples), 1) if samples else None
        )

    hdata = hl.load_hospitals(read_json, save_json)
    hospitals = hdata.get("hospitals") or []
    hospitals_online = sum(
        1
        for h in hospitals
        if str(h.get("status") or "active").lower()
        not in ("offline", "inactive", "blocked", "closed")
    )
    ambulances_free = 0
    ambulances_total = 0
    for h in hospitals:
        try:
            count = int(h.get("ambulance_count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count <= 0 and h.get("ambulance_available"):
            count = 1
        ambulances_total += count
        if h.get("ambulance_available"):
            ambulances_free += count or 1

    # Police / fire "units online" = open stations (same idea as hospitals online).
    # Operator accounts are staff linked to those stations — shown in trends / map meta.
    import facility_registry as fr

    stations_data = fr.load_stations(read_json)
    stations = stations_data.get("stations") or []
    stations_by_id = {s.get("id"): s for s in stations if s.get("id") is not None}
    police_stations = [s for s in stations if (s.get("kind") or "").lower() == "police"]
    fire_stations = [s for s in stations if (s.get("kind") or "").lower() == "fire"]
    police_stations_online = sum(
        1 for s in police_stations if (s.get("operating_status") or "open").lower() != "closed"
    )
    fire_stations_online = sum(
        1 for s in fire_stations if (s.get("operating_status") or "open").lower() != "closed"
    )
    police_operators = sum(
        1
        for u in users
        if (u.get("role") or "").lower() == "police"
        and (u.get("status") or "active").lower() != "blocked"
    )
    fire_operators = sum(
        1
        for u in users
        if (u.get("role") or "").lower() == "fire"
        and (u.get("status") or "active").lower() != "blocked"
    )
    # Prefer stations on the map/KPI (aligned with Hospitals Online).
    # Operator accounts are staff — counted in trends, not as map unit pins when a station exists.
    police_online = police_stations_online
    fire_online = fire_stations_online
    citizens = by_role.get("citizen", 0)

    # Map markers — hospitals + police/fire stations (+ operators with live/station GPS)
    map_markers = []
    for h in hospitals:
        lat, lng = h.get("latitude"), h.get("longitude")
        if lat is None or lng is None:
            continue
        try:
            map_markers.append({
                "kind": "hospital",
                "id": h.get("id"),
                "name": h.get("name") or "Hospital",
                "lat": float(lat),
                "lng": float(lng),
                "meta": {"ambulances": h.get("ambulance_count"), "phone": h.get("phone")},
            })
        except (TypeError, ValueError):
            pass

    for s in stations:
        kind = (s.get("kind") or "").lower()
        if kind not in ("police", "fire"):
            continue
        if (s.get("operating_status") or "open").lower() == "closed":
            continue
        lat, lng = s.get("latitude"), s.get("longitude")
        if lat is None or lng is None:
            continue
        try:
            map_markers.append({
                "kind": kind,
                "id": s.get("id"),
                "name": s.get("name") or (kind.title() + " Station"),
                "lat": float(lat),
                "lng": float(lng),
                "meta": {
                    "source": "station",
                    "phone": s.get("phone"),
                    "status": s.get("operating_status") or "open",
                    "city": s.get("city") or "",
                },
            })
        except (TypeError, ValueError):
            pass

    # Operators: use own GPS, else fall back to linked station coords (avoid duplicate station pin)
    station_coords = {
        sid: (st.get("latitude"), st.get("longitude"))
        for sid, st in stations_by_id.items()
        if st.get("latitude") is not None and st.get("longitude") is not None
    }
    for u in users:
        role = (u.get("role") or "").lower()
        if role not in ("police", "fire"):
            continue
        if (u.get("status") or "active").lower() == "blocked":
            continue
        lat, lng = u.get("latitude"), u.get("longitude")
        source = "operator"
        sid = u.get("station_id")
        if (lat is None or lng is None) and sid:
            coords = station_coords.get(sid) or station_coords.get(int(sid) if str(sid).isdigit() else None)
            if coords:
                # Already have a station pin — skip duplicate at same place
                continue
        if lat is None or lng is None:
            continue
        try:
            map_markers.append({
                "kind": role,
                "id": "op-" + str(u.get("id")),
                "name": user_name(u) or role.title(),
                "lat": float(lat),
                "lng": float(lng),
                "meta": {
                    "source": source,
                    "phone": u.get("phone"),
                    "status": u.get("status"),
                    "station_id": sid,
                },
            })
        except (TypeError, ValueError):
            pass
    for e in sorted(emergencies, key=lambda x: x.get("timestamp") or "", reverse=True)[:80]:
        status = (e.get("status") or "pending").lower()
        # Map shows active SOS only — hide completed / cancelled / no_hospital
        if status not in ACTIVE_SOS_STATUSES:
            continue
        lat, lng = e.get("latitude"), e.get("longitude")
        accuracy_m = e.get("accuracy_m")
        updated_at = (
            e.get("last_location_update")
            or e.get("location_updated_at")
            or e.get("timestamp")
        )
        hist = e.get("location_history") or []
        if hist:
            last = hist[-1] if isinstance(hist[-1], dict) else None
            if last and last.get("latitude") is not None and last.get("longitude") is not None:
                lat, lng = last.get("latitude"), last.get("longitude")
                accuracy_m = last.get("accuracy_m") if last.get("accuracy_m") is not None else accuracy_m
                updated_at = last.get("timestamp") or updated_at
        if lat is None or lng is None:
            continue
        try:
            lat_f, lng_f = float(lat), float(lng)
            if not hl.is_in_somalia(lat_f, lng_f):
                continue
            live = bool(e.get("tracking_active")) and status in ACTIVE_SOS_STATUSES
            map_markers.append({
                "kind": "emergency",
                "id": e.get("id"),
                "name": TYPE_LABELS.get(e.get("type"), e.get("type") or "Emergency"),
                "lat": lat_f,
                "lng": lng_f,
                "accuracy_m": float(accuracy_m) if accuracy_m is not None else None,
                "live": live,
                "meta": {
                    "status": e.get("status"),
                    "priority": e.get("priority") or e.get("type"),
                    "caller": e.get("caller_name") or e.get("name"),
                    "phone": e.get("phone"),
                    "location": e.get("location") or e.get("district"),
                    "type": e.get("type"),
                    "updated_at": updated_at,
                    "method": (hist[-1].get("method") if hist and isinstance(hist[-1], dict) else None)
                    or e.get("method")
                    or "gps",
                },
            })
        except (TypeError, ValueError):
            pass

    feed = []
    for e in sorted(emergencies, key=lambda x: x.get("timestamp") or "", reverse=True)[:25]:
        feed.append({
            "id": e.get("id"),
            "type": e.get("type"),
            "type_label": TYPE_LABELS.get(e.get("type"), (e.get("type") or "Emergency").title()),
            "caller_name": e.get("caller_name") or e.get("name") or "Citizen",
            "location": e.get("location") or e.get("district") or "Unknown",
            "timestamp": e.get("timestamp"),
            "priority": e.get("priority") or ("high" if e.get("type") in ("fire", "medical") else "normal"),
            "status": e.get("status") or "pending",
            "assigned_to": e.get("assigned_to") or e.get("assigned_hospital_id") or "",
            "dispatch_progress": e.get("status") or "pending",
        })

    audit = read_json(AUDIT_FILE, {"entries": [], "next_id": 1})
    user_index = {u.get("id"): u for u in users}
    activities = []
    for entry in (audit.get("entries") or [])[:20]:
        actor = user_index.get(entry.get("user_id")) or {}
        activities.append({
            "id": entry.get("id"),
            "administrator": actor.get("name") or ("User #" + str(entry.get("user_id")) if entry.get("user_id") else "System"),
            "action": entry.get("action"),
            "entity_type": entry.get("entity_type"),
            "entity_id": entry.get("entity_id"),
            "timestamp": entry.get("timestamp"),
            "ip": (entry.get("details") or {}).get("ip") or "—",
            "details": entry.get("details") or {},
        })

    # AI summary — only real engine stats / active incidents from DB
    ai_alerts = 0
    ai_stats = {}
    try:
        ai_stats = _ai_engine().stats() or {}
        ai_alerts = int(ai_stats.get("decisions_today") or 0)
    except Exception:
        ai_stats = {}

    conf = ai_stats.get("average_confidence")
    approved = int(ai_stats.get("approved_recommendations") or 0)
    rejected = int(ai_stats.get("rejected_recommendations") or 0)
    decided = approved + rejected
    ai_center = {
        "has_data": bool(
            ai_alerts
            or decided
            or conf is not None
            or active_emergencies
        ),
        "alert": None,
        "priority": None,
        "recommendation": None,
        "prediction_accuracy": round(float(conf) * 100, 1) if conf is not None else None,
        "incidents_predicted": ai_alerts if ai_alerts else None,
        "approved": approved,
        "rejected": rejected,
        "decisions_today": ai_alerts,
    }
    if active_emergencies:
        top = active_emergencies[0]
        loc = top.get("location") or top.get("district") or "Unknown location"
        etype = TYPE_LABELS.get(top.get("type"), top.get("type") or "emergency")
        ai_center["alert"] = (
            f"{len(active_emergencies)} active emergency(ies). "
            f"Latest: {etype} at {loc}."
        )
        ai_center["priority"] = "High" if len(active_emergencies) >= 3 else "Active"
        ai_center["recommendation"] = (
            f"Review emergency #{top.get('id')} ({etype}) — status: {top.get('status') or 'pending'}."
        )
    elif ai_alerts or decided:
        ai_center["alert"] = f"{ai_alerts} AI decision(s) recorded today."
        ai_center["priority"] = "Info"
        ai_center["recommendation"] = (
            f"Approved: {approved} · Rejected: {rejected}."
        )

    # System health — real probes only (no fabricated CPU/memory/storage)
    email_ok = False
    email_provider = os.environ.get("EMAIL_PROVIDER") or "smtp"
    try:
        from email_service.factory import get_email_provider

        prov = get_email_provider()
        email_provider = getattr(prov, "name", email_provider)
        email_ok = bool(getattr(prov, "configured", lambda: True)())
    except Exception:
        email_ok = False

    storage = _storage_status()
    db_ok = bool(storage.get("live")) if USE_MYSQL else False
    if not USE_MYSQL and _json_store_allowed():
        try:
            load_users()
            db_ok = True
        except Exception:
            db_ok = False

    health = {
        "database": {
            "status": "healthy" if db_ok else "degraded",
            "detail": (
                "MySQL {db}@{host} ({user})".format(
                    db=storage.get("database") or "?",
                    host=storage.get("host") or "?",
                    user=storage.get("user") or "?",
                )
                if USE_MYSQL and storage.get("live")
                else (storage.get("error") or ("JSON store" if not USE_MYSQL else "MySQL offline"))
            ),
            "backend": storage.get("backend"),
            "live": storage.get("live"),
            "table_counts": storage.get("table_counts") or {},
        },
        "api": {"status": "healthy", "detail": "Online"},
        "sms_gateway": {
            "status": "healthy" if settings.get("sms_notifications") else "idle",
            "detail": "Enabled" if settings.get("sms_notifications") else "Disabled in settings",
        },
        "email_service": {
            "status": "healthy" if email_ok else "degraded",
            "detail": str(email_provider),
        },
        "ai_engine": {
            "status": "healthy" if settings.get("ai_enabled", True) else "idle",
            "detail": settings.get("ai_provider") or "rule_based",
        },
        "google_maps": {
            "status": "healthy"
            if (os.environ.get("GOOGLE_MAPS_API_KEY") or settings.get("google_maps_api_key"))
            else "idle",
            "detail": "API key configured"
            if (os.environ.get("GOOGLE_MAPS_API_KEY") or settings.get("google_maps_api_key"))
            else "Using Leaflet tiles",
        },
    }
    try:
        import psutil  # type: ignore[reportMissingModuleSource]

        health["cpu"] = {
            "status": "healthy",
            "detail": "Live",
            "usage": round(psutil.cpu_percent(interval=0.05)),
        }
        mem = psutil.virtual_memory()
        health["memory"] = {
            "status": "healthy" if mem.percent < 90 else "degraded",
            "detail": "Live",
            "usage": round(mem.percent),
        }
        disk = psutil.disk_usage(os.path.abspath(DATABASE_DIR) if DATABASE_DIR else os.getcwd())
        health["storage"] = {
            "status": "healthy" if disk.percent < 90 else "degraded",
            "detail": "Live",
            "usage": round(disk.percent),
        }
    except Exception:
        pass

    hotline = (
        settings.get("emergency_hotline")
        or settings.get("call_center_phone")
        or settings.get("contact_phone")
        or ""
    ).strip()

    return {
        "total_users": len(users),
        "users_by_role": by_role,
        "citizens": citizens,
        "hospitals_online": hospitals_online,
        "hospitals_total": len(hospitals),
        "police_online": police_online,
        "fire_online": fire_online,
        "police_stations_total": len(police_stations),
        "fire_stations_total": len(fire_stations),
        "police_operators": police_operators,
        "fire_operators": fire_operators,
        "ambulances_available": ambulances_free,
        "ambulances_total": ambulances_total,
        "active_emergencies": len(active_emergencies),
        "ai_alerts": ai_alerts,
        "emergencies_today": today_count,
        "emergencies_week": week_count,
        "emergencies_month": month_count,
        "emergencies_total": len(emergencies),
        "avg_response_time": avg,
        "avg_response_by_day": avg_by_day,
        "emergencies_by_day": by_day,
        "week_summary": {
            "total": week_count,
            "resolved": resolved_week_summary,
            "pending": pending_week,
            "cancelled": cancelled_week,
        },
        "reports": {
            "range_start": (today - timedelta(days=6)).strftime("%b %d, %Y"),
            "range_end": today.strftime("%b %d, %Y"),
            "updated_at": now.strftime("%b %d, %Y %I:%M %p"),
            "total_emergencies": len(emergencies),
            "active_emergencies": len(active_emergencies),
            "resolved_emergencies": resolved_total,
            "resolved_week": resolved_week,
            "week_total": week_count,
            "prev_week_total": prev_week_count,
            "week_change_pct": (
                round(((week_count - prev_week_count) / prev_week_count) * 100, 1)
                if prev_week_count
                else (100.0 if week_count else 0.0)
            ),
            "resolution_rate": (
                round((resolved_week / week_count) * 100, 1) if week_count else 0.0
            ),
            "avg_response_time": avg,
            "avg_response_display": (
                f"{int(avg)}m {int(round((avg % 1) * 60)):02d}s" if avg is not None else None
            ),
            "hospitals_online": hospitals_online,
            "hospitals_total": len(hospitals),
            "ambulances_available": ambulances_free,
            "ambulances_total": ambulances_total,
            "by_type": by_type,
            "by_location": dict(sorted(by_location.items(), key=lambda x: -x[1])[:8]),
            "trend_labels": [x["date"] for x in by_day_dates],
            "trend_this_week": [by_day.get(x["key"], 0) for x in by_day_dates],
            "trend_last_week": [x["count"] for x in prev_day_values],
            "busiest_day": busiest_day,
        },
        "active_sessions": 1
        if has_request_context() and session.get("user_id")
        else 0,
        "by_type": by_type,
        "by_location": dict(sorted(by_location.items(), key=lambda x: -x[1])[:8]),
        "blocked_users": sum(1 for u in users if u.get("status") == "blocked"),
        "map_markers": map_markers,
        "emergency_feed": feed,
        "ai_center": ai_center,
        "recent_activities": activities,
        "system_health": health,
        "system_status": "operational" if db_ok else "degraded",
        "hotline": hotline or None,
        "has_emergencies": bool(emergencies),
        "has_users": bool(users),
    }


@app.route("/api/admin/stats")
@admin_required
def admin_stats():
    return jsonify(_admin_command_payload())


def _analytics_region(em):
    loc = (em.get("district") or em.get("location") or "Unknown").strip()
    if not loc:
        return "Unknown"
    return loc.split(",")[0].strip() or "Unknown"


def _build_admin_analytics(days=7, region="", etype="", status=""):
    """Filterable BI analytics for the Executive Reports dashboard."""
    edata = load_emergencies()
    emergencies = list(edata.get("emergencies") or [])
    hdata = hl.load_hospitals(read_json, save_json)
    hospitals = hdata.get("hospitals") or []
    now = datetime.now()
    today = now.date()
    try:
        days = max(1, min(90, int(days or 7)))
    except (TypeError, ValueError):
        days = 7
    start = today - timedelta(days=days - 1)
    region = (region or "").strip().lower()
    etype = (etype or "").strip().lower()
    status = (status or "").strip().lower()

    def _eday(e):
        try:
            return parse_dt(e.get("timestamp")).date()
        except Exception:
            return None

    # Filter options from full dataset (so dropdowns stay populated)
    all_regions = sorted({_analytics_region(e) for e in emergencies if _analytics_region(e)})
    all_types = sorted({(e.get("type") or "other").lower() for e in emergencies})
    all_statuses = sorted({(e.get("status") or "pending").lower() for e in emergencies})

    filtered = []
    for e in emergencies:
        ed = _eday(e)
        if ed is None or ed < start or ed > today:
            continue
        if region and _analytics_region(e).lower() != region:
            continue
        if etype and (e.get("type") or "other").lower() != etype:
            continue
        st = (e.get("status") or "pending").lower()
        if status:
            if status == "active" and st not in ACTIVE_SOS_STATUSES:
                continue
            if status == "resolved" and st not in ("completed", "resolved"):
                continue
            if status not in ("active", "resolved") and st != status:
                continue
        filtered.append(e)

    by_type = {}
    by_region = {}
    by_status = {}
    day_keys = []
    by_day = {}
    stacked = {}  # day -> {active, resolved, other}
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        label = d.strftime("%b %d")
        day_keys.append(label)
        by_day[label] = 0
        stacked[label] = {"active": 0, "resolved": 0, "other": 0}

    for e in filtered:
        t = (e.get("type") or "other").lower()
        by_type[t] = by_type.get(t, 0) + 1
        r = _analytics_region(e)
        by_region[r] = by_region.get(r, 0) + 1
        st = (e.get("status") or "pending").lower()
        by_status[st] = by_status.get(st, 0) + 1
        ed = _eday(e)
        if ed:
            label = ed.strftime("%b %d")
            if label in by_day:
                by_day[label] += 1
                if st in ACTIVE_SOS_STATUSES:
                    stacked[label]["active"] += 1
                elif st in ("completed", "resolved"):
                    stacked[label]["resolved"] += 1
                else:
                    stacked[label]["other"] += 1

    active_n = sum(1 for e in filtered if (e.get("status") or "").lower() in ACTIVE_SOS_STATUSES)
    resolved_n = sum(
        1 for e in filtered if (e.get("status") or "").lower() in ("completed", "resolved")
    )
    total_n = len(filtered)
    avg = _avg_response_minutes(filtered)
    hospitals_online = sum(
        1
        for h in hospitals
        if str(h.get("status") or "active").lower()
        not in ("offline", "inactive", "blocked", "closed")
    )
    ambulances_free = 0
    ambulances_total = 0
    for h in hospitals:
        try:
            count = int(h.get("ambulance_count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count <= 0 and h.get("ambulance_available"):
            count = 1
        ambulances_total += count
        if h.get("ambulance_available"):
            ambulances_free += count or 1

    recent = []
    for e in sorted(filtered, key=lambda x: x.get("timestamp") or "", reverse=True)[:12]:
        recent.append({
            "id": e.get("id"),
            "type": e.get("type"),
            "status": e.get("status"),
            "region": _analytics_region(e),
            "caller": e.get("caller_name") or e.get("name") or "—",
            "timestamp": e.get("timestamp"),
        })

    return {
        "success": True,
        "filters": {
            "days": days,
            "region": region,
            "type": etype,
            "status": status,
            "range_start": start.strftime("%b %d, %Y"),
            "range_end": today.strftime("%b %d, %Y"),
        },
        "filter_options": {
            "regions": all_regions,
            "types": all_types,
            "statuses": ["active", "resolved"] + [s for s in all_statuses if s not in ("completed", "resolved")],
        },
        "updated_at": now.strftime("%b %d, %Y %I:%M %p"),
        "kpis": {
            "total": total_n,
            "active": active_n,
            "resolved": resolved_n,
            "resolution_rate": round((resolved_n / total_n) * 100, 1) if total_n else 0.0,
            "avg_response_time": avg,
            "avg_response_display": (
                f"{int(avg)}m {int(round((avg % 1) * 60)):02d}s" if avg is not None else "—"
            ),
            "hospitals_online": hospitals_online,
            "hospitals_total": len(hospitals),
            "ambulances_available": ambulances_free,
            "ambulances_total": ambulances_total,
        },
        "by_type": by_type,
        "by_region": dict(sorted(by_region.items(), key=lambda x: -x[1])[:10]),
        "by_status": by_status,
        "trend": {
            "labels": day_keys,
            "values": [by_day[k] for k in day_keys],
        },
        "stacked_status": {
            "labels": day_keys,
            "active": [stacked[k]["active"] for k in day_keys],
            "resolved": [stacked[k]["resolved"] for k in day_keys],
            "other": [stacked[k]["other"] for k in day_keys],
        },
        "recent": recent,
    }


@app.route("/api/admin/analytics")
@admin_required
def admin_analytics():
    denied = _require_admin_perm("reports")
    if denied:
        return denied
    return jsonify(
        _build_admin_analytics(
            days=request.args.get("days", 7),
            region=request.args.get("region", ""),
            etype=request.args.get("type", ""),
            status=request.args.get("status", ""),
        )
    )


@app.route("/api/admin/command-center")
@admin_required
def admin_command_center():
    """Rich Super Admin command-center payload (map, feed, health, AI)."""
    return jsonify({"success": True, **_admin_command_payload()})


@app.route("/api/admin/audit")
@admin_required
def admin_audit_logs():
    denied = _require_admin_perm("audit")
    if denied:
        return denied
    log = read_json(AUDIT_FILE, {"entries": [], "next_id": 1})
    udata = load_users()
    user_index = {u.get("id"): u for u in udata["users"]}
    rows = []
    for entry in (log.get("entries") or [])[:200]:
        actor = user_index.get(entry.get("user_id")) or {}
        rows.append({
            "id": entry.get("id"),
            "administrator": actor.get("name") or "System",
            "email": actor.get("email") or "",
            "action": entry.get("action"),
            "entity_type": entry.get("entity_type"),
            "entity_id": entry.get("entity_id"),
            "timestamp": entry.get("timestamp"),
            "ip": (entry.get("details") or {}).get("ip") or "—",
            "details": entry.get("details") or {},
        })
    return jsonify({"success": True, "entries": rows})


def _is_privileged_role(role):
    return role in PRIVILEGED_ROLES


def _is_active_privileged(user):
    return (
        user
        and _is_privileged_role(user.get("role"))
        and (user.get("status") or "active") != "blocked"
    )


def _is_active_super_admin(user):
    return (
        user
        and user.get("role") == "super_admin"
        and (user.get("status") or "active") != "blocked"
    )


def _count_active_super_admins(udata, exclude_id=None):
    count = 0
    for u in udata.get("users") or []:
        if exclude_id is not None and u.get("id") == exclude_id:
            continue
        if _is_active_super_admin(u):
            count += 1
    return count


def _count_active_privileged(udata, exclude_id=None):
    count = 0
    for u in udata.get("users") or []:
        if exclude_id is not None and u.get("id") == exclude_id:
            continue
        if _is_active_privileged(u):
            count += 1
    return count


def _admin_account_guard(target, action, udata):
    """Safety + RBAC rules when managing accounts. Returns error message or None."""
    actor_id = session.get("user_id")
    actor_role = _session_role()
    if not target:
        return "User not found"
    if target.get("id") == actor_id and action in ("block", "delete", "demote"):
        return "You cannot " + action + " your own account"

    target_privileged = _is_privileged_role(target.get("role"))
    if target_privileged and not _has_admin_perm("users_admins"):
        # Regular Admin may edit their own profile (not role/status/security actions)
        if not (target.get("id") == actor_id and action == "edit"):
            return "Only Super Admin can manage Admin accounts"

    if not target_privileged and not _has_admin_perm("users_ops"):
        return "You do not have permission to manage users"

    # Only Super Admin may touch other Super Admins
    if target.get("role") == "super_admin" and actor_role != "super_admin":
        return "Only Super Admin can manage Super Admin accounts"

    if action in ("block", "delete", "demote") and _is_active_super_admin(target):
        if _count_active_super_admins(udata, exclude_id=target.get("id")) < 1:
            return "Cannot " + action + " the last active Super Admin account"
    return None


def _migrate_legacy_admins_to_super():
    """One-time: if no super_admin exists, promote legacy admin accounts."""
    try:
        udata = load_users()
        if any(u.get("role") == "super_admin" for u in udata.get("users") or []):
            return
        changed = False
        for u in udata.get("users") or []:
            if u.get("role") == "admin":
                u["role"] = "super_admin"
                log_activity(u, "Migrated to Super Admin role")
                changed = True
        if changed:
            save_users(udata)
            append_audit(
                "role_migration",
                "system",
                0,
                {"from": "admin", "to": "super_admin", "note": "legacy full-access admins"},
            )
    except Exception:
        logging.getLogger(__name__).exception("Admin role migration failed")


@app.route("/api/admin/me")
@admin_required
def admin_me():
    role = _session_role()
    return jsonify({
        "success": True,
        "role": role,
        "is_super_admin": role == "super_admin",
        "permissions": sorted(_admin_permissions(role)),
        "user_id": session.get("user_id"),
        "name": session.get("name"),
    })


def _admin_self_profile_payload(user):
    """Safe admin self-profile for My Profile UI."""
    role = (user.get("role") or "").lower()
    profile = public_user_profile(user) or {}
    return {
        "id": profile.get("id"),
        "name": profile.get("name") or "",
        "email": profile.get("email") or "",
        "phone": profile.get("phone") or "",
        "role": role,
        "role_label": "Super Administrator" if role == "super_admin" else "Administrator",
        "status": profile.get("status") or "active",
        "profile_photo": profile.get("profile_photo") or "",
        "created_at": profile.get("created_at") or user.get("created_at") or "",
        "last_login": profile.get("last_login") or user.get("last_login") or "",
        "email_verified": bool(profile.get("email_verified") or user.get("email_verified")),
        "is_super_admin": role == "super_admin",
        "permissions": sorted(_admin_permissions(role)),
        # Account preferences (self-only; never used to edit other admins)
        "settings": {
            "notify_email_on_sos": bool(user.get("notify_email_on_sos", True)),
            "notify_email_on_dispatch": bool(user.get("notify_email_on_dispatch", True)),
        },
    }


@app.route("/api/admin/profile", methods=["GET", "PUT"])
@admin_required
def api_admin_profile():
    """Admin My Profile — always edits the logged-in admin only.

    Other admin accounts stay on /api/admin/users/edit (requires users_admins).
    """
    uid = session.get("user_id")
    user, udata = get_user_by_id(uid)
    if not user or not _is_privileged_role(user.get("role")):
        return jsonify({"success": False, "message": "Admin account not found"}), 404

    if request.method == "GET":
        return jsonify({"success": True, "profile": _admin_self_profile_payload(user)})

    data = request.get_json(silent=True) or {}
    # Never accept another user id — self-only endpoint
    if data.get("id") not in (None, "", uid, str(uid)):
        return jsonify({
            "success": False,
            "message": "You can only edit your own profile here. Use Users Management to edit other accounts.",
        }), 403

    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name or len(name) < 2:
            return jsonify({"success": False, "message": "Name is required"}), 400
        user["name"] = name[:120]

    if "phone" in data:
        user["phone"] = (data.get("phone") or "").strip()[:40]

    if "email" in data:
        email = normalize_email(data.get("email") or "")
        if not email:
            return jsonify({"success": False, "message": "A valid email address is required"}), 400
        if any(
            other.get("email", "").lower() == email and other.get("id") != uid
            for other in udata.get("users", [])
        ):
            return jsonify({"success": False, "message": "Email already registered"}), 400
        reject = signup_email_rejection_reason(email)
        if reject and not allow_test_email_domains():
            return jsonify({"success": False, "message": reject}), 400
        if email.lower() != (user.get("email") or "").lower():
            user["email"] = email
            user["email_verified"] = False

    if "profile_photo" in data:
        photo = data.get("profile_photo") or ""
        if photo and not str(photo).startswith("data:image/"):
            return jsonify({
                "success": False,
                "message": "Profile photo must be an uploaded image.",
            }), 400
        if photo and len(str(photo)) > 120000:
            return jsonify({"success": False, "message": "Photo too large (max ~90KB)."}), 400
        user["profile_photo"] = photo

    settings_in = data.get("settings") if isinstance(data.get("settings"), dict) else None
    if settings_in is not None:
        if "notify_email_on_sos" in settings_in:
            user["notify_email_on_sos"] = bool(settings_in.get("notify_email_on_sos"))
        if "notify_email_on_dispatch" in settings_in:
            user["notify_email_on_dispatch"] = bool(settings_in.get("notify_email_on_dispatch"))

    new_password = (data.get("new_password") or data.get("password") or "").strip()
    if new_password:
        current_password = (data.get("current_password") or "").strip()
        if not current_password:
            return jsonify({
                "success": False,
                "message": "Current password is required to set a new password.",
            }), 400
        if not check_password_hash(user.get("password_hash") or "", current_password):
            return jsonify({"success": False, "message": "Current password is incorrect."}), 400
        confirm = (data.get("confirm_password") or "").strip()
        if confirm and confirm != new_password:
            return jsonify({"success": False, "message": "New passwords do not match."}), 400
        pw_err = _password_policy_error(new_password)
        if pw_err:
            return jsonify({"success": False, "message": pw_err}), 400
        user["password_hash"] = generate_password_hash(new_password)
        log_activity(user, "Password changed from My Profile")

    # Role / status are never self-editable here
    log_activity(user, "Profile updated from My Profile")
    save_users(udata)
    session["name"] = user.get("name")
    session["email"] = user.get("email")
    append_audit(
        "admin_profile_updated",
        "user",
        uid,
        {"fields": [k for k in ("name", "email", "phone", "profile_photo", "settings", "password")
                    if k in data or (k == "password" and new_password) or (k == "settings" and settings_in)]},
        uid,
    )
    return jsonify({"success": True, "profile": _admin_self_profile_payload(user)})


@app.route("/api/admin/users")
@admin_required
def admin_users():
    denied = _require_admin_perm("users_ops")
    if denied:
        return denied
    udata = load_users()
    q = request.args.get("q", "").lower()
    role = request.args.get("role", "")
    users = list(udata["users"])
    can_manage_admins = _has_admin_perm("users_admins")
    if not can_manage_admins:
        users = [u for u in users if not _is_privileged_role(u.get("role"))]
    if q:
        users = [
            u
            for u in users
            if q in user_name(u).lower() or q in u.get("email", "").lower()
        ]
    if role == "admins":
        users = [u for u in users if _is_privileged_role(u.get("role"))]
    elif role:
        users = [u for u in users if u.get("role") == role]
    me = session.get("user_id")
    hnames = _hospital_name_map()
    hdata = hl.load_hospitals(read_json, save_json)
    hospital_options = [
        {"id": h["id"], "name": h.get("name") or f"Hospital #{h['id']}"}
        for h in (hdata.get("hospitals") or [])
    ]
    import facility_registry as fr
    sdata = fr.load_stations(read_json)
    station_options = [
        {
            "id": s["id"],
            "name": s.get("name") or f"Station #{s['id']}",
            "kind": s.get("kind"),
        }
        for s in (sdata.get("stations") or [])
    ]
    snames = {s["id"]: s.get("name") for s in station_options}
    ccdata = fr.load_call_centers(read_json)
    call_center_options = [
        {"id": c["id"], "name": c.get("name") or f"Call Center #{c['id']}"}
        for c in (ccdata.get("call_centers") or [])
    ]
    ccnames = {c["id"]: c.get("name") for c in call_center_options}
    safe = []
    for u in users:
        hid = u.get("hospital_id")
        sid = u.get("station_id")
        cid = u.get("call_center_id")
        safe.append(
            {
                "id": u["id"],
                "name": user_name(u),
                "email": u["email"],
                "phone": u.get("phone", ""),
                "role": u["role"],
                "status": u.get("status", "active"),
                "hospital_id": hid,
                "hospital_name": hnames.get(hid) if hid else None,
                "station_id": sid,
                "station_name": snames.get(sid) if sid else None,
                "call_center_id": cid,
                "call_center_name": ccnames.get(cid) if cid else None,
                "created_at": u.get("created_at"),
                "last_login": u.get("last_login"),
                "activity": u.get("activity", []),
                "is_self": u["id"] == me,
                "email_verified": bool(u.get("email_verified", True)),
                "is_privileged": _is_privileged_role(u.get("role")),
            }
        )
    return jsonify({
        "users": safe,
        "hospitals": hospital_options,
        "stations": station_options,
        "call_centers": call_center_options,
        "current_user_id": me,
        "active_admins": _count_active_privileged(udata),
        "active_super_admins": _count_active_super_admins(udata),
        "can_manage_admins": can_manage_admins,
    })


@app.route("/api/admin/users/block", methods=["POST"])
@admin_required
def admin_block_user():
    data = request.get_json(silent=True) or {}
    uid = int(data.get("id", 0))
    udata = load_users()
    for u in udata["users"]:
        if u["id"] == uid:
            next_status = "blocked" if u.get("status") != "blocked" else "active"
            action = "block" if next_status == "blocked" else "unblock"
            err = _admin_account_guard(u, "block" if next_status == "blocked" else "unblock", udata)
            if err:
                return jsonify({"success": False, "message": err}), 400
            u["status"] = next_status
            log_activity(u, "Status changed to " + u["status"] + " by admin")
            save_users(udata)
            append_audit(
                "admin_user_" + action,
                "user",
                uid,
                {"role": u.get("role"), "status": u.get("status")},
                session.get("user_id"),
            )
            return jsonify({"success": True, "status": u["status"]})
    return jsonify({"success": False, "message": "User not found"}), 404


@app.route("/api/admin/users/delete", methods=["POST"])
@admin_required
def admin_delete_user():
    data = request.get_json(silent=True) or {}
    uid = int(data.get("id", 0))
    udata = load_users()
    for i, u in enumerate(udata["users"]):
        if u["id"] == uid:
            err = _admin_account_guard(u, "delete", udata)
            if err:
                return jsonify({"success": False, "message": err}), 400
            role = u.get("role")
            # Drop facility ownership before removing the account
            hdata = hl.load_hospitals(read_json, save_json)
            h_changed = False
            for h in hdata.get("hospitals") or []:
                if h.get("owner_user_id") == uid:
                    h["owner_user_id"] = None
                    h_changed = True
            if h_changed:
                hl.save_hospitals(hdata, save_json)
            udata["users"].pop(i)
            save_users(udata)
            append_audit(
                "admin_user_deleted",
                "user",
                uid,
                {"role": role},
                session.get("user_id"),
            )
            return jsonify({"success": True})
    return jsonify({"success": False, "message": "User not found"}), 404


@app.route("/api/admin/users/edit", methods=["PUT", "POST"])
@admin_required
def admin_edit_user():
    data = request.get_json(silent=True) or {}
    uid = int(data.get("id", 0))
    udata = load_users()
    for u in udata["users"]:
        if u["id"] == uid:
            err = _admin_account_guard(u, "edit", udata)
            if err:
                return jsonify({"success": False, "message": err}), 400
            new_role = data.get("role")
            if new_role and new_role in VALID_ROLES and new_role != u.get("role"):
                # Role changes involving privileged roles require Super Admin
                if _is_privileged_role(new_role) or _is_privileged_role(u.get("role")):
                    if not _has_admin_perm("users_admins"):
                        return jsonify({
                            "success": False,
                            "message": "Only Super Admin can change Admin roles",
                        }), 403
                if _is_privileged_role(u.get("role")) and not _is_privileged_role(new_role):
                    err = _admin_account_guard(u, "demote", udata)
                    if err:
                        return jsonify({"success": False, "message": err}), 400
                # Regular Admin cannot assign privileged roles
                if _is_privileged_role(new_role) and not _has_admin_perm("users_admins"):
                    return jsonify({
                        "success": False,
                        "message": "Only Super Admin can assign Admin roles",
                    }), 403
                u["role"] = new_role
            if data.get("name"):
                u["name"] = data["name"].strip()
            elif data.get("full_name"):
                u["name"] = data["full_name"].strip()
            if data.get("email"):
                email = normalize_email(data.get("email") or "")
                if not email:
                    return jsonify({"success": False, "message": "A real email address is required"}), 400
                if any(
                    other["email"].lower() == email and other["id"] != uid
                    for other in udata["users"]
                ):
                    return jsonify({"success": False, "message": "Email already registered"}), 400
                reject = signup_email_rejection_reason(email)
                if reject and not allow_test_email_domains():
                    return jsonify({"success": False, "message": reject}), 400
                u["email"] = email
            if "phone" in data:
                u["phone"] = (data.get("phone") or "").strip()
            if data.get("password"):
                password = str(data.get("password") or "").strip()
                pw_err = _password_policy_error(password)
                if pw_err:
                    return jsonify({"success": False, "message": pw_err}), 400
                u["password_hash"] = generate_password_hash(password)
                log_activity(u, "Password reset by admin")
            if data.get("status") in ("active", "blocked"):
                next_status = data["status"]
                if next_status == "blocked" and u.get("status") != "blocked":
                    err = _admin_account_guard(u, "block", udata)
                    if err:
                        return jsonify({"success": False, "message": err}), 400
                u["status"] = next_status

            # Keep hospital account ↔ facility row in sync
            if u.get("role") == "hospital":
                raw_hid = data.get("hospital_id", u.get("hospital_id"))
                try:
                    hid = int(raw_hid or 0)
                except (TypeError, ValueError):
                    hid = 0
                if not hid:
                    return jsonify({
                        "success": False,
                        "message": "Select a hospital facility for this hospital account",
                    }), 400
                if not hl.get_hospital_by_id(hl.load_hospitals(read_json, save_json), hid):
                    return jsonify({"success": False, "message": "Hospital not found"}), 400
                u["hospital_id"] = hid
                u["station_id"] = None
                u["call_center_id"] = None
                save_users(udata)
                _link_user_to_hospital(uid, hid, set_owner=True)
                udata = load_users()
                u = next((x for x in udata["users"] if x["id"] == uid), u)
            elif u.get("role") in ("police", "fire"):
                import facility_registry as fr
                raw_sid = data.get("station_id", u.get("station_id"))
                try:
                    sid = int(raw_sid or 0)
                except (TypeError, ValueError):
                    sid = 0
                if not sid:
                    return jsonify({
                        "success": False,
                        "message": "Select a station facility for this account",
                    }), 400
                stn = fr.get_station(fr.load_stations(read_json), sid)
                if not stn or stn.get("kind") != u.get("role"):
                    return jsonify({"success": False, "message": "Matching station not found"}), 400
                u["station_id"] = sid
                u["hospital_id"] = None
                u["call_center_id"] = None
            elif u.get("role") == "call_center":
                import facility_registry as fr
                raw_cid = data.get("call_center_id", u.get("call_center_id"))
                try:
                    cid = int(raw_cid or 0)
                except (TypeError, ValueError):
                    cid = 0
                if not cid:
                    return jsonify({
                        "success": False,
                        "message": "Select a call center facility for this account",
                    }), 400
                if not fr.get_call_center(fr.load_call_centers(read_json), cid):
                    return jsonify({"success": False, "message": "Call center not found"}), 400
                u["call_center_id"] = cid
                u["hospital_id"] = None
                u["station_id"] = None
            else:
                if u.get("hospital_id"):
                    u["hospital_id"] = None
                    hdata = hl.load_hospitals(read_json, save_json)
                    for h in hdata.get("hospitals") or []:
                        if h.get("owner_user_id") == uid:
                            h["owner_user_id"] = None
                    hl.save_hospitals(hdata, save_json)
                u["station_id"] = None
                u["call_center_id"] = None

            log_activity(u, "Profile updated by admin")
            save_users(udata)
            append_audit(
                "admin_user_updated",
                "user",
                uid,
                {
                    "role": u.get("role"),
                    "status": u.get("status"),
                    "hospital_id": u.get("hospital_id"),
                    "station_id": u.get("station_id"),
                    "call_center_id": u.get("call_center_id"),
                },
                session.get("user_id"),
            )
            # Keep session role in sync if editing self
            if u["id"] == session.get("user_id"):
                session["role"] = u["role"]
                session["name"] = u.get("name")
                session["email"] = u.get("email")
            return jsonify({"success": True, "user": {k: u[k] for k in u if k != "password_hash"}})
    return jsonify({"success": False, "message": "User not found"}), 404


@app.route("/api/admin/users/create", methods=["POST"])
@admin_required
def admin_create_user():
    data = request.get_json(silent=True) or {}
    udata = load_users()
    uid = udata["next_id"]
    role = data.get("role", "citizen")
    if role not in VALID_ROLES:
        return jsonify({"success": False, "message": "Invalid role"}), 400

    # + Add user → citizens (users_ops)
    # + Create staff → hospital/police/fire/call_center (users_ops); admin (users_admins)
    if role == "citizen":
        denied = _require_admin_perm("users_ops")
        if denied:
            return denied
    elif role in OPS_CREATE_ROLES:
        denied = _require_admin_perm("users_ops")
        if denied:
            return denied
    elif role == "admin":
        if not _has_admin_perm("users_admins"):
            return jsonify({
                "success": False,
                "message": "Only Super Admin can create Admin accounts",
            }), 403
    elif role in SUPER_CREATE_ROLES:
        if not _has_admin_perm("users_admins"):
            return jsonify({
                "success": False,
                "message": "Only Super Admin can create this account type",
            }), 403
    else:
        return jsonify({
            "success": False,
            "message": "This role cannot be created here. Use Add user for citizens, "
            "or Create staff for Hospital / Police / Fire / Call Center.",
        }), 400

    name = (data.get("name") or data.get("full_name") or "").strip()
    if not name:
        return jsonify({"success": False, "message": "Full name is required"}), 400
    email = normalize_email(data.get("email") or "")
    if not email:
        return jsonify({"success": False, "message": "A real email address is required"}), 400
    reject = signup_email_rejection_reason(email)
    # Test domains (example.com) allowed only when EMAIL_PROVIDER=memory / ALLOW_TEST_EMAILS.
    if reject and not allow_test_email_domains():
        return jsonify({"success": False, "message": reject}), 400
    password = (data.get("password") or "").strip()
    pw_err = _password_policy_error(password)
    if pw_err:
        return jsonify({"success": False, "message": pw_err}), 400

    existing = next(
        (u for u in udata["users"] if (u.get("email") or "").lower() == email),
        None,
    )
    # Creating a new citizen must not collide; staff create may upgrade a citizen.
    if existing and role == "citizen":
        return jsonify({"success": False, "message": "Email already registered"}), 400
    if existing and role != "citizen":
        existing_role = (existing.get("role") or "").lower()
        if existing_role not in ("citizen", role):
            return jsonify({
                "success": False,
                "message": (
                    f"Email already registered as {existing_role}. "
                    "Open Users → Edit that account, or use a different email."
                ),
            }), 400
        # Fall through — upgrade / refresh staff fields on existing user below.
    elif existing:
        return jsonify({"success": False, "message": "Email already registered"}), 400

    actor = current_user() or {}
    if role == "admin":
        created_label = "Admin account created by " + (actor.get("name") or "Super Admin")
    elif role in SUPER_CREATE_ROLES:
        created_label = role.title() + " account created by " + (actor.get("name") or "Super Admin")
    else:
        created_label = "Citizen created by admin"
    hospital_id = None
    station_id = None
    call_center_id = None
    if role == "hospital":
        try:
            hospital_id = int(data.get("hospital_id") or 0)
        except (TypeError, ValueError):
            hospital_id = 0
        if not hospital_id:
            return jsonify({
                "success": False,
                "message": "Select a hospital facility to link this account",
            }), 400
        if not hl.get_hospital_by_id(hl.load_hospitals(read_json, save_json), hospital_id):
            return jsonify({"success": False, "message": "Hospital not found"}), 400
    if role in ("police", "fire"):
        import facility_registry as fr
        try:
            station_id = int(data.get("station_id") or 0)
        except (TypeError, ValueError):
            station_id = 0
        if not station_id:
            return jsonify({
                "success": False,
                "message": "Select a police/fire station facility to link this account",
            }), 400
        stn = fr.get_station(fr.load_stations(read_json), station_id)
        if not stn or stn.get("kind") != role:
            return jsonify({"success": False, "message": "Matching station not found"}), 400
    if role == "call_center":
        import facility_registry as fr
        try:
            call_center_id = int(data.get("call_center_id") or 0)
        except (TypeError, ValueError):
            call_center_id = 0
        if not call_center_id:
            return jsonify({
                "success": False,
                "message": "Select a call center facility to link this account",
            }), 400
        if not fr.get_call_center(fr.load_call_centers(read_json), call_center_id):
            return jsonify({"success": False, "message": "Call center not found"}), 400

    if existing:
        # Promote citizen (or refresh same-role staff) instead of failing duplicate email.
        existing["name"] = name
        existing["phone"] = (data.get("phone") or "").strip()
        existing["password_hash"] = generate_password_hash(password)
        existing["role"] = role
        existing["status"] = "active"
        existing["email_verified"] = True
        existing["hospital_id"] = hospital_id
        existing["station_id"] = station_id
        existing["call_center_id"] = call_center_id
        existing.setdefault("activity", [])
        existing["activity"] = (
            [{"action": created_label + " (upgraded)", "timestamp": now_str()}]
            + existing["activity"]
        )[:50]
        save_users(udata)
        if role == "hospital" and hospital_id:
            _link_user_to_hospital(existing["id"], hospital_id, set_owner=True)
        if role == "call_center" and call_center_id:
            try:
                import facility_registry as fr

                ccdata = fr.load_call_centers(read_json)
                row = fr.get_call_center(ccdata, call_center_id)
                if row is not None:
                    row["owner_user_id"] = existing["id"]
                    fr.save_call_centers(ccdata, save_json)
            except Exception:
                logging.getLogger(__name__).exception("call center owner link failed")
        append_audit(
            "admin_user_upgraded",
            "user",
            existing["id"],
            {
                "role": role, "email": email, "name": name,
                "hospital_id": hospital_id, "station_id": station_id,
                "call_center_id": call_center_id,
            },
            session.get("user_id"),
        )
        return jsonify({
            "success": True,
            "id": existing["id"],
            "role": role,
            "upgraded": True,
            "hospital_id": hospital_id,
            "station_id": station_id,
            "call_center_id": call_center_id,
            "message": "Existing account upgraded to " + role,
        })

    udata["next_id"] += 1
    user = {
        "id": uid,
        "name": name,
        "email": email,
        "phone": (data.get("phone") or "").strip(),
        "password_hash": generate_password_hash(password),
        "role": role,
        "status": "active",
        # Admin-created accounts are trusted and active immediately
        "email_verified": True,
        "email_verify_token": None,
        "email_verify_expires": None,
        "hospital_id": hospital_id,
        "station_id": station_id,
        "call_center_id": call_center_id,
        "created_at": now_str(),
        "last_login": None,
        "created_by": session.get("user_id"),
        "activity": [{"action": created_label, "timestamp": now_str()}],
    }
    udata["users"].append(user)
    save_users(udata)
    if role == "hospital" and hospital_id:
        _link_user_to_hospital(uid, hospital_id, set_owner=True)
    if role == "call_center" and call_center_id:
        try:
            import facility_registry as fr

            ccdata = fr.load_call_centers(read_json)
            row = fr.get_call_center(ccdata, call_center_id)
            if row is not None:
                row["owner_user_id"] = uid
                fr.save_call_centers(ccdata, save_json)
        except Exception:
            logging.getLogger(__name__).exception("call center owner link failed")
    append_audit(
        "admin_user_created",
        "user",
        uid,
        {
            "role": role, "email": email, "name": name,
            "hospital_id": hospital_id, "station_id": station_id,
            "call_center_id": call_center_id,
        },
        session.get("user_id"),
    )
    return jsonify({
        "success": True, "id": uid, "role": role,
        "hospital_id": hospital_id, "station_id": station_id,
        "call_center_id": call_center_id,
    })


@app.route("/api/admin/content")
@admin_required
def admin_content():
    denied = _require_admin_perm("content_edit")
    if denied:
        return denied
    return jsonify(load_content())


@app.route("/api/admin/content/update", methods=["POST"])
@admin_required
def admin_content_update():
    denied = _require_admin_perm("content_edit")
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    key = data.get("key")
    value = data.get("value")
    if not key:
        return jsonify({"success": False}), 400
    content = load_content()
    if key in DEFAULT_CONTENT or key in content:
        content[key] = value
        save_content(content)
        return jsonify({"success": True, "content": content})
    return jsonify({"success": False, "message": "Unknown key"}), 400


@app.route("/api/admin/content/reset", methods=["POST"])
@admin_required
def admin_content_reset():
    denied = _require_admin_perm("content_reset")
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    key = data.get("key")
    content = load_content()
    if key and key in DEFAULT_CONTENT:
        content[key] = DEFAULT_CONTENT[key]
        save_content(content)
    else:
        content = DEFAULT_CONTENT.copy()
        save_content(content)
    return jsonify({"success": True, "content": content})


@app.route("/api/admin/settings")
@admin_required
def admin_settings():
    return jsonify(_public_settings_view(load_settings()))


@app.route("/api/admin/settings/update", methods=["POST"])
@admin_required
def admin_settings_update():
    data = request.get_json(silent=True) or {}
    settings = load_settings()
    can_system = _has_admin_perm("settings_system")
    can_ops = _has_admin_perm("settings_ops")
    can_appearance = _has_admin_perm("appearance")
    can_cc = _has_admin_perm("call_center")
    if not (can_system or can_ops or can_appearance or can_cc):
        return _forbid_admin("You do not have permission to update settings.")
    appearance_keys = {
        "color_hospital",
        "color_police",
        "color_fire",
        "dark_mode",
        "theme_mode",
        "brand_primary_color",
        "brand_accent_color",
    }
    cc_keys = {k for k in DEFAULT_SETTINGS if k.startswith("call_center_")}
    rejected = []
    for key in DEFAULT_SETTINGS:
        if key not in data:
            continue
        if key in SUPER_ONLY_SETTINGS:
            if not can_system:
                rejected.append(key)
                continue
        elif key in appearance_keys:
            if not can_appearance and not can_system:
                rejected.append(key)
                continue
        elif key in cc_keys:
            if not can_cc and not can_system:
                rejected.append(key)
                continue
        elif not can_ops and not can_system:
            rejected.append(key)
            continue
        if key in SECRET_SETTING_KEYS:
            raw = data.get(key)
            if raw is None or str(raw).strip() == "" or str(raw).strip() in ("••••••••", "********"):
                continue
        settings[key] = _coerce_setting_value(key, data[key])
    if rejected and not any(k in data and k not in rejected for k in DEFAULT_SETTINGS):
        return jsonify({
            "success": False,
            "message": "Super Admin access is required to change: " + ", ".join(rejected),
        }), 403
    save_settings(settings)
    if can_system:
        _sync_branding_to_content(settings)
    return jsonify({
        "success": True,
        "settings": _public_settings_view(settings),
        "rejected_keys": rejected,
    })


@app.route("/api/admin/system-settings", methods=["GET"])
@admin_required
def admin_system_settings_get():
    """Full Super Admin system configuration payload (schema + values)."""
    denied = _require_admin_perm("settings_system")
    if denied:
        return denied
    settings = load_settings()
    storage = _storage_status()
    db_info = {
        "backend": "MySQL" if USE_MYSQL else "JSON file store (tests only)",
        "live": storage.get("live"),
        "database": storage.get("database"),
        "user": storage.get("user"),
        "host": storage.get("host"),
        "port": storage.get("port"),
        "table_counts": storage.get("table_counts") or {},
        "error": storage.get("error"),
        "database_dir": DATABASE_DIR,
    }
    return jsonify({
        "success": True,
        "settings": _public_settings_view(settings),
        "groups": SYSTEM_SETTINGS_GROUPS,
        "secret_keys": sorted(SECRET_SETTING_KEYS),
        "database": db_info,
    })


@app.route("/api/admin/system-settings", methods=["POST"])
@admin_required
def admin_system_settings_save():
    """Save Super Admin system configuration from the unified page."""
    denied = _require_admin_perm("settings_system")
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    incoming = data.get("settings") if isinstance(data.get("settings"), dict) else data
    settings = load_settings()
    updated = []
    for key in DEFAULT_SETTINGS:
        if key not in incoming:
            continue
        if key in SECRET_SETTING_KEYS:
            raw = incoming.get(key)
            if raw is None or str(raw).strip() == "" or str(raw).strip() in ("••••••••", "********"):
                continue
        settings[key] = _coerce_setting_value(key, incoming[key])
        updated.append(key)
    save_settings(settings)
    _sync_branding_to_content(settings)
    append_audit(
        "system_settings_updated",
        "settings",
        0,
        {"keys": updated[:80], "count": len(updated)},
        session.get("user_id"),
    )
    return jsonify({
        "success": True,
        "updated": updated,
        "settings": _public_settings_view(settings),
    })


@app.route("/api/admin/system-settings/upload", methods=["POST"])
@admin_required
def admin_system_settings_upload():
    """Upload logo or favicon into static/uploads/branding."""
    denied = _require_admin_perm("settings_system")
    if denied:
        return denied
    kind = (request.form.get("kind") or "logo").strip().lower()
    if kind not in ("logo", "favicon"):
        return jsonify({"success": False, "message": "kind must be logo or favicon"}), 400
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"success": False, "message": "No file uploaded"}), 400
    settings = load_settings()
    allowed = {
        e.strip().lower()
        for e in str(settings.get("upload_allowed_extensions") or "jpg,jpeg,png,gif,webp").split(",")
        if e.strip()
    }
    ext = (f.filename.rsplit(".", 1)[-1] if "." in f.filename else "").lower()
    if ext not in allowed:
        return jsonify({
            "success": False,
            "message": "File type not allowed. Allowed: " + ", ".join(sorted(allowed)),
        }), 400
    max_mb = int(settings.get("upload_max_mb") or 5)
    data = f.read()
    if len(data) > max_mb * 1024 * 1024:
        return jsonify({"success": False, "message": f"File exceeds {max_mb} MB limit"}), 400
    upload_dir = os.path.join(BASE_DIR, "static", "uploads", "branding")
    os.makedirs(upload_dir, exist_ok=True)
    filename = f"{kind}.{ext}"
    path = os.path.join(upload_dir, filename)
    with open(path, "wb") as out:
        out.write(data)
    url = url_for("static", filename=f"uploads/branding/{filename}")
    key = "app_logo_url" if kind == "logo" else "app_favicon_url"
    settings[key] = url
    save_settings(settings)
    append_audit("branding_upload", "settings", 0, {"kind": kind, "url": url}, session.get("user_id"))
    return jsonify({"success": True, "url": url, "key": key, "settings": _public_settings_view(settings)})


@app.route("/api/admin/emergencies")
@admin_required
def admin_emergencies():
    denied = _require_admin_perm("emergencies_view")
    if denied:
        return denied
    edata = load_emergencies()
    return jsonify({"emergencies": edata["emergencies"]})


@app.route("/api/admin/emergencies/update", methods=["POST"])
@admin_required
def admin_emergencies_update():
    denied = _require_admin_perm("emergencies_update")
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    eid = int(data.get("id", 0))
    edata = load_emergencies()
    for em in edata["emergencies"]:
        if em["id"] != eid:
            continue
        normalize_emergency_record(em)
        if "status" in data:
            st = (data.get("status") or "").strip()
            allowed = set(STATUS_VALUES) | set(COMPLETED_STATUSES) | {"no_hospital_available", "rejected_by_hospital"}
            if st not in allowed:
                return jsonify({"success": False, "message": "Invalid status"}), 400
            _append_status(em, st, data.get("note") or "Admin status update")
            if st in COMPLETED_STATUSES:
                _stop_sos_tracking(em)
        if "assigned_to" in data:
            em["assigned_to"] = data["assigned_to"]
            em["assigned_team_label"] = TEAM_LABELS.get(data["assigned_to"], em.get("assigned_team_label") or "")
        if "assigned_hospital_id" in data:
            hid = data.get("assigned_hospital_id")
            if hid in (None, "", 0, "0"):
                em["assigned_hospital_id"] = None
                em["assigned_hospital_name"] = ""
            else:
                try:
                    hid = int(hid)
                except (TypeError, ValueError):
                    return jsonify({"success": False, "message": "Invalid hospital id"}), 400
                h = hl.get_hospital_by_id(hl.load_hospitals(read_json, save_json), hid)
                if not h:
                    return jsonify({"success": False, "message": "Hospital not found"}), 400
                em["assigned_hospital_id"] = hid
                em["assigned_hospital_name"] = h.get("name") or ""
        if "assigned_station_id" in data:
            sid = data.get("assigned_station_id")
            if sid in (None, "", 0, "0"):
                em["assigned_station_id"] = None
            else:
                import facility_registry as fr
                try:
                    sid = int(sid)
                except (TypeError, ValueError):
                    return jsonify({"success": False, "message": "Invalid station id"}), 400
                stn = fr.get_station(fr.load_stations(read_json), sid)
                if not stn:
                    return jsonify({"success": False, "message": "Station not found"}), 400
                em["assigned_station_id"] = sid
                em["assigned_team_label"] = stn.get("name") or em.get("assigned_team_label")
        if "notes" in data and data["notes"] is not None:
            em["notes"] = str(data["notes"])
        if "assigned_team_label" in data and data["assigned_team_label"] is not None:
            em["assigned_team_label"] = str(data["assigned_team_label"])
        save_emergencies(edata)
        append_audit("emergency_updated", "emergency", eid, {
            "status": em.get("status"),
            "assigned_to": em.get("assigned_to"),
            "assigned_hospital_id": em.get("assigned_hospital_id"),
            "assigned_station_id": em.get("assigned_station_id"),
        }, session.get("user_id"))
        return jsonify({"success": True, "emergency": em})
    return jsonify({"success": False}), 404


@app.route("/api/admin/emergencies/delete", methods=["POST"])
@admin_required
def admin_emergencies_delete():
    denied = _require_admin_perm("emergencies_delete")
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    eid = int(data.get("id", 0))
    edata = load_emergencies()
    edata["emergencies"] = [e for e in edata["emergencies"] if e["id"] != eid]
    save_emergencies(edata)
    return jsonify({"success": True})


@app.route("/api/admin/emergencies/export")
@admin_required
def admin_emergencies_export():
    denied = _require_admin_perm("emergencies_export")
    if denied:
        return denied
    edata = load_emergencies()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["ID", "Type", "Location", "Caller", "Phone", "Timestamp", "Status", "Assigned To"]
    )
    for e in edata["emergencies"]:
        writer.writerow(
            [
                e["id"],
                e["type"],
                e["location"],
                e.get("caller_name", ""),
                e.get("phone", ""),
                e["timestamp"],
                e["status"],
                e.get("assigned_to", ""),
            ]
        )
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=emergencies.csv"},
    )


def _sync_emergency_location_from_call(emergency, call):
    """Copy current call GPS/address onto an emergency (after operator location fix)."""
    lat = call.get("latitude")
    lng = call.get("longitude")
    if lat is None or lng is None:
        return emergency
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return emergency
    address = (call.get("address") or call.get("district") or "").strip()
    emergency["latitude"] = lat
    emergency["longitude"] = lng
    emergency["location"] = address or emergency.get("location") or f"GPS {lat:.5f}, {lng:.5f}"
    emergency["district"] = (call.get("district") or address or emergency.get("district") or "")
    if call.get("accuracy_m") is not None:
        emergency["accuracy_m"] = call.get("accuracy_m")
    fix = build_location_fix({
        "latitude": lat,
        "longitude": lng,
        "district": emergency["district"],
        "method": "gps",
        "accuracy_m": emergency.get("accuracy_m"),
        "confidence": 90,
    })
    emergency.setdefault("location_history", [])
    if fix.get("latitude") is not None:
        emergency["location_history"].append(fix)
    _apply_tracking_fields(emergency, fix if fix.get("latitude") is not None else None)
    return emergency


def _force_assign_call_center_facility(emergency, team, preferred_id):
    """Pin the emergency to the nearest facility shown on the Call Center card."""
    if preferred_id is None or preferred_id == "":
        return emergency
    try:
        preferred_id = int(preferred_id)
    except (TypeError, ValueError):
        return emergency

    if team == "hospital":
        hdata = hl.seed_hospitals_if_empty(read_json, save_json)
        hospital = hl.get_hospital_by_id(hdata, preferred_id)
        if not hospital:
            return emergency
        queue = [preferred_id] + [
            hid for hid in (emergency.get("escalation_queue") or []) if hid != preferred_id
        ]
        emergency["escalation_queue"] = queue
        emergency["escalation_index"] = 0
        settings = load_settings()
        timeout = int(settings.get("hospital_response_timeout_sec", 120))
        assigned = hl.assign_next_hospital(emergency, hdata, timeout)
        if assigned:
            dist = emergency.get("hospital_distance_km")
            dist_txt = f" ({dist} km)" if dist is not None else ""
            _append_status(
                emergency,
                "pending_hospital",
                f"Call Center nearest hospital: {assigned['name']}{dist_txt}",
            )
        return emergency

    if team in ("police", "fire"):
        import facility_registry as fr
        import police_logic as pl

        data = fr.load_stations(read_json)
        station = fr.get_station(data, preferred_id)
        if not station or (station.get("kind") or "") != team:
            # Fall back to nearest open by current GPS
            station = pl.nearest_open_station(
                team, emergency.get("latitude"), emergency.get("longitude"), read_json
            )
        if not station:
            return emergency
        emergency["assigned_station_id"] = station.get("id")
        emergency["assigned_team_label"] = station.get("name") or emergency.get("assigned_team_label")
        if station.get("phone"):
            emergency["contact_number"] = station.get("phone")
        try:
            dist = hl.haversine_km(
                float(emergency.get("latitude") or 0),
                float(emergency.get("longitude") or 0),
                float(station["latitude"]),
                float(station["longitude"]),
            )
            dist = round(dist, 2)
        except (TypeError, ValueError, KeyError):
            dist = None
        dist_txt = f" ({dist} km)" if dist is not None else ""
        _append_status(
            emergency,
            emergency.get("status") or "pending",
            f"Call Center nearest {team}: {station.get('name')}{dist_txt}",
        )
    return emergency


def _create_emergency_from_call(call, etype, team, operator, notes="", preferred_facility_id=None):
    """Create a standard emergency record from a call-center session (reuses auto-dispatch)."""
    edata = load_emergencies()
    eid = edata["next_id"]
    edata["next_id"] += 1
    fix = build_location_fix({
        "latitude": call.get("latitude"),
        "longitude": call.get("longitude"),
        "district": call.get("district") or call.get("address"),
        "method": "gps",
        "accuracy_m": call.get("accuracy_m"),
        "confidence": 90,
    })
    emergency = {
        "id": eid,
        "user_id": call.get("user_id"),
        "type": normalize_type(etype),
        "location": call.get("address") or call.get("district") or "Call Center GPS",
        "district": call.get("district") or "",
        "latitude": call.get("latitude"),
        "longitude": call.get("longitude"),
        "accuracy_m": call.get("accuracy_m"),
        "method": "gps",
        "confidence": 90,
        "timestamp": now_iso(),
        "status": "pending",
        "caller_name": call.get("caller_name") or "Unknown",
        "phone": call.get("phone") or "Not provided",
        "notes": (notes or call.get("notes") or "").strip()[:2000],
        "assigned_to": team,
        "location_history": [fix] if fix.get("latitude") is not None else [],
        "responder_status": {},
        "source": "call_center",
        "call_id": call.get("id"),
        "operator_id": operator.get("id") if operator else None,
        "operator_name": (operator or {}).get("name", ""),
        "request_mode": "call_center",
    }
    _apply_tracking_fields(emergency, fix if fix.get("latitude") is not None else None)
    # Pin preferred hospital before auto-dispatch so the correct desk is notified once
    if preferred_facility_id is not None and team == "hospital":
        try:
            pid = int(preferred_facility_id)
            emergency["escalation_queue"] = [pid]
            emergency["escalation_index"] = 0
        except (TypeError, ValueError):
            pass
    edata["emergencies"].append(emergency)
    save_emergencies(edata)
    _auto_dispatch_emergency(emergency)
    # Pin preferred police/fire station after soft-assign
    if preferred_facility_id is not None and team in ("police", "fire"):
        _force_assign_call_center_facility(emergency, team, preferred_facility_id)
    # Ensure hospital preferred stuck (re-pin if auto-dispatch drifted)
    if preferred_facility_id is not None and team == "hospital":
        if emergency.get("assigned_hospital_id") != int(preferred_facility_id):
            _force_assign_call_center_facility(emergency, team, preferred_facility_id)
    save_emergencies(edata)
    append_audit(
        "call_center_dispatch",
        "emergency",
        eid,
        {
            "call_id": call.get("id"),
            "type": etype,
            "team": team,
            "preferred_facility_id": preferred_facility_id,
        },
        (operator or {}).get("id"),
    )
    # Parallel AI memory for learning; operator already approved via Call Center UI
    _schedule_ai_analysis(emergency, source="call_center")
    return emergency


def _notify_call_dispatch(call, emergency, teams):
    """Notify citizen, responders, and admins after call-center dispatch."""
    eid = emergency["id"]
    etype = emergency.get("type")
    gps_line = (
        f"{call.get('caller_name')} — GPS {call.get('latitude')}, {call.get('longitude')} "
        f"— {call.get('address')}"
    )
    _notify(
        "patient",
        call.get("user_id"),
        f"Call Center dispatched {TYPE_LABELS.get(etype, etype)} help for your call #{call.get('id')}.",
        eid,
        "team_assigned",
    )
    if "hospital" in teams and emergency.get("assigned_hospital_id"):
        _notify(
            "hospital",
            emergency["assigned_hospital_id"],
            f"CALL CENTER: {gps_line}",
            eid,
            "team_assigned",
        )
    # Notify all active police / fire user accounts for those teams
    if "police" in teams or "fire" in teams:
        udata = load_users()
        for u in udata["users"]:
            if u.get("status") == "blocked":
                continue
            if "police" in teams and u.get("role") == "police":
                _notify("police", u["id"], f"CALL CENTER: {gps_line}", eid, "team_assigned")
            if "fire" in teams and u.get("role") == "fire":
                _notify("fire", u["id"], f"CALL CENTER: {gps_line}", eid, "team_assigned")
    _notify_admins(
        f"Call Center dispatched emergency #{eid} ({etype}) from call #{call.get('id')}.",
        eid,
        "system_alert",
    )


def _notify_role_users(role, message, request_id=None, ntype="system_alert"):
    udata = load_users()
    for u in udata["users"]:
        if u.get("role") == role and u.get("status") != "blocked":
            _notify(role, u["id"], message, request_id, ntype)


# ---------- CALL CENTER (Method 2) ----------


@app.route("/call-center/login", methods=["GET", "POST"])
def call_center_login():
    if session.get("user_id") and session.get("role") == "call_center":
        return redirect(url_for("call_center_dashboard"))
    if session.get("user_id") and session.get("role") != "call_center":
        flash("Please use the Call Center login for operator accounts.", "warning")

    if request.method == "POST":
        login_id = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user, udata = get_user_by_login(login_id)
        if user and user.get("status") == "blocked":
            flash("Your account has been blocked. Contact admin.", "error")
        elif user and user.get("role") != "call_center":
            flash("This login is for Call Center Operators only.", "error")
        elif user and check_password_hash(user["password_hash"], password):
            user["last_login"] = now_str()
            user["last_seen_call_center"] = now_str()
            log_activity(user, "Call Center login")
            save_users(udata)
            login_user(user)
            flash("Welcome to the Emergency Call Center, " + user_name(user) + "!", "success")
            return redirect(url_for("call_center_dashboard"))
        else:
            flash("Invalid operator email or password.", "error")
    return render_template("call_center_login.html")


@app.route("/call-center")
@call_center_required
def call_center_dashboard():
    user = current_user()
    settings = load_settings()
    return render_template(
        "call_center_dashboard.html",
        user=user,
        call_center_phone=settings.get("call_center_phone") or "",
        type_options=cc.EMERGENCY_TYPE_OPTIONS,
    )


@app.route("/call-center/history")
@call_center_required
def call_center_history_page():
    return render_template("call_center_history.html", user=current_user())


@app.route("/api/call-center/initiate", methods=["POST"])
@role_required("citizen")
def api_call_center_initiate():
    """Citizen starts an in-app WebRTC voice session with Call Center (no tel: dialer)."""
    settings = load_settings()
    if not settings.get("call_center_enabled", True):
        return jsonify({"success": False, "message": "Call Center is currently unavailable."}), 403
    if settings.get("maintenance_mode"):
        return jsonify({"success": False, "message": "System under maintenance."}), 503

    data = request.get_json(silent=True) or {}
    user = current_user()
    # Always bind call to the authenticated session user — never trust client citizen_id.
    payload = {
        "user_id": session.get("user_id"),
        "name": data.get("name") or (user.get("name") if user else "") or session.get("name"),
        "phone": data.get("phone") or (user.get("phone") if user else "") or "",
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "address": data.get("address") or data.get("district") or "",
        "district": data.get("district") or "",
        "accuracy_m": data.get("accuracy_m"),
        "voice_mode": True,
        "device_info": data.get("device_info") or {
            "user_agent": request.headers.get("User-Agent", "")[:300],
        },
    }
    try:
        call = cc.create_incoming_call(
            payload, read_json, save_json, stations=get_response_station_list()
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400

    from voice_signaling import emit_incoming_call, ice_servers

    try:
        emit_incoming_call(call)
    except Exception:
        logging.getLogger(__name__).exception("emit_incoming_call failed")

    _notify_admins(
        f"Incoming Call Center call #{call['id']} from {call['caller_name']} ({call['phone']})",
        None,
        "system_alert",
    )
    append_audit("call_center_incoming", "call", call["id"], {"user_id": payload["user_id"]})
    return jsonify({
        "success": True,
        "call_id": call["id"],
        "voice_mode": True,
        "ice_servers": ice_servers(),
        "message": "Connecting to Emergency Call Center. Your location was shared with the operator.",
        "call": {
            "id": call["id"],
            "status": call["status"],
            "latitude": call["latitude"],
            "longitude": call["longitude"],
            "address": call["address"],
            "voice_mode": True,
            "caller_name": call.get("caller_name"),
            "phone": call.get("phone"),
        },
    })


@app.route("/api/call-center/live")
@call_center_required
def api_call_center_live():
    """Live queue for operators + heartbeat."""
    user = current_user()
    udata = load_users()
    for u in udata["users"]:
        if u.get("id") == user["id"]:
            u["last_seen_call_center"] = now_str()
            save_users(udata)
            break
    settings = load_settings()
    heartbeat = int(settings.get("call_center_heartbeat_sec", 45))
    online = cc.online_operators(load_users, heartbeat)
    active = cc.active_calls(read_json, save_json)
    stats = cc.call_stats(read_json, save_json, [o["id"] for o in online])
    return jsonify({
        "success": True,
        "calls": active,
        "stats": stats,
        "operators_online": online,
        "refresh_interval": settings.get("refresh_interval", 5),
        "call_center_phone": settings.get("call_center_phone"),
    })


@app.route("/api/call-center/history")
@login_required
def api_call_center_history():
    role = session.get("role")
    if role not in ("call_center", "admin"):
        return jsonify({"success": False, "message": "Forbidden"}), 403
    limit = request.args.get("limit", 100, type=int)
    return jsonify({"success": True, "calls": cc.call_history(read_json, save_json, limit=limit)})


@app.route("/api/call-center/calls/<int:call_id>")
@call_center_required
def api_call_center_get(call_id):
    data = cc.load_calls(read_json, save_json)
    call = cc.get_call_by_id(data, call_id)
    if not call:
        return jsonify({"success": False, "message": "Not found"}), 404
    if call.get("latitude") and call.get("longitude"):
        call["nearest"] = cc.find_nearest_responders(
            call["latitude"], call["longitude"], read_json, save_json, get_response_station_list()
        )
    history = _citizen_emergency_history(call.get("user_id"))
    call["emergency_history"] = history
    packed = _ai_panel_for_call(call_id)
    return jsonify({
        "success": True,
        "call": call,
        "emergency_history": history,
        "ai": {
            "analysis": packed.get("analysis"),
            "recommendation": packed.get("recommendation"),
            "panel": packed.get("panel"),
        },
    })


@app.route("/api/call-center/calls/<int:call_id>/answer", methods=["POST"])
@call_center_required
def api_call_center_answer(call_id):
    try:
        call = cc.answer_call(
            call_id, current_user(), read_json, save_json, stations=get_response_station_list()
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400
    history = _citizen_emergency_history(call.get("user_id"))
    call["emergency_history"] = history
    if call.get("latitude") is not None and call.get("longitude") is not None:
        call["nearest"] = cc.find_nearest_responders(
            call["latitude"], call["longitude"], read_json, save_json, get_response_station_list()
        )
    ai_payload = None
    try:
        ai_payload = _run_call_center_ai(call, notes=call.get("notes"))
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Call Center AI on answer failed for call %s", call_id)
    return jsonify({
        "success": True,
        "call": call,
        "emergency_history": history,
        "ai": ai_payload,
    })


@app.route("/api/call-center/calls/<int:call_id>/location", methods=["POST"])
@call_center_required
def api_call_center_location(call_id):
    """Operator corrects caller GPS/address; nearest hospital/police/fire recomputed."""
    payload = request.get_json(silent=True) or {}
    try:
        call = cc.update_call_location(
            call_id,
            payload,
            read_json,
            save_json,
            stations=get_response_station_list(),
            operator=current_user(),
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400
    append_audit(
        "call_center_location_update",
        "call",
        call_id,
        {
            "latitude": call.get("latitude"),
            "longitude": call.get("longitude"),
            "address": call.get("address"),
        },
        current_user().get("id"),
    )
    return jsonify({
        "success": True,
        "message": "Location updated — nearest responders refreshed.",
        "call": call,
        "nearest": call.get("nearest") or {},
    })


@app.route("/api/call-center/calls/<int:call_id>/status", methods=["POST"])
@call_center_required
def api_call_center_status(call_id):
    data = request.get_json(silent=True) or {}
    try:
        call = cc.set_call_status(
            call_id,
            data.get("status", "in_progress"),
            read_json,
            save_json,
            notes=data.get("notes"),
            operator=current_user(),
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400
    return jsonify({"success": True, "call": call})


@app.route("/api/call-center/calls/<int:call_id>/ai", methods=["GET"])
@call_center_required
def api_call_center_ai_get(call_id):
    """Return latest AI recommendation for an open call (never dispatches)."""
    data = cc.load_calls(read_json, save_json)
    call = cc.get_call_by_id(data, call_id)
    if not call:
        return jsonify({"success": False, "message": "Not found"}), 404
    packed = _ai_panel_for_call(call_id)
    history = _citizen_emergency_history(call.get("user_id"))
    return jsonify({
        "success": True,
        "call_id": call_id,
        "emergency_history": history,
        "analysis": packed.get("analysis"),
        "recommendation": packed.get("recommendation"),
        "panel": packed.get("panel"),
    })


@app.route("/api/call-center/calls/<int:call_id>/ai/analyze", methods=["POST"])
@call_center_required
def api_call_center_ai_analyze(call_id):
    """
    Re-run Call Center AI from operator notes / description.
    Does NOT dispatch — operator must Approve or use Manual Dispatch.
    """
    payload = request.get_json(silent=True) or {}
    data = cc.load_calls(read_json, save_json)
    call = cc.get_call_by_id(data, call_id)
    if not call:
        return jsonify({"success": False, "message": "Not found"}), 404
    notes = payload.get("notes")
    if notes is None:
        notes = call.get("notes") or ""
    if notes != call.get("notes"):
        call["notes"] = notes
        cc.save_calls(data, save_json)
    try:
        result = _run_call_center_ai(call, notes=notes)
    except Exception as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc, "Internal server error.")}), 500
    return jsonify(result)


@app.route("/api/call-center/calls/<int:call_id>/ai/decision", methods=["POST"])
@call_center_required
def api_call_center_ai_decision(call_id):
    """
    Operator decision on AI recommendation: approve | reject | manual.
    Approve uses existing Call Center dispatch (AI never dispatches itself).
    """
    payload = request.get_json(silent=True) or {}
    decision = (payload.get("decision") or "").strip().lower()
    if decision not in ("approve", "reject", "manual", "approved", "rejected"):
        return jsonify({
            "success": False,
            "message": "decision must be approve, reject, or manual",
        }), 400

    data = cc.load_calls(read_json, save_json)
    call = cc.get_call_by_id(data, call_id)
    if not call:
        return jsonify({"success": False, "message": "Not found"}), 404

    operator = current_user()
    engine = _ai_engine()
    packed = engine.get_latest_for_call(call_id)
    rec = packed.get("recommendation") or {}
    rec_id = payload.get("recommendation_id") or rec.get("id")
    if not rec_id:
        return jsonify({"success": False, "message": "No AI recommendation to decide on. Analyze first."}), 400

    notes = (payload.get("notes") or "").strip()
    if notes:
        call["notes"] = notes
        cc.save_calls(data, save_json)

    engine.record_human_decision({
        "call_id": call_id,
        "emergency_id": rec.get("emergency_id"),
        "recommendation_id": rec_id,
        "decision": decision,
        "operator_id": operator.get("id"),
        "operator_name": operator.get("name"),
        "notes": payload.get("decision_notes") or "",
    })

    if decision in ("reject", "rejected", "manual"):
        return jsonify({
            "success": True,
            "decision": "manual" if decision == "manual" else "reject",
            "message": (
                "AI recommendation rejected. Use Manual Dispatch."
                if decision in ("reject", "rejected")
                else "Manual selection mode — choose types and dispatch."
            ),
            "panel": _ai_panel_for_call(call_id).get("panel"),
        })

    # Approve → existing multi-dispatch path (human-approved)
    types = payload.get("types") or rec.get("suggested_dispatch_types") or []
    if isinstance(types, str):
        types = [types]
    if not types and rec.get("recommended_hospital"):
        types = ["medical"]
    if not types:
        return jsonify({
            "success": False,
            "message": "No suggested dispatch types. Use Manual Dispatch.",
        }), 400

    if not call.get("operator_id"):
        try:
            call = cc.answer_call(
                call_id, operator, read_json, save_json, stations=get_response_station_list()
            )
        except ValueError:
            pass
        data = cc.load_calls(read_json, save_json)
        call = cc.get_call_by_id(data, call_id)

    pairs = cc.resolve_dispatch_types(types)
    if not pairs:
        return jsonify({"success": False, "message": "Invalid emergency types."}), 400

    created = []
    teams = []
    for etype, team in pairs:
        em = _create_emergency_from_call(call, etype, team, operator, notes or call.get("notes") or "")
        created.append(em)
        teams.append(team)
        _notify_call_dispatch(call, em, [team])

    call = cc.record_dispatch(
        call_id,
        [e.get("type") for e in created],
        [e["id"] for e in created],
        teams,
        read_json,
        save_json,
    )

    try:
        engine.record_dispatch_result({
            "call_id": call_id,
            "recommendation_id": rec_id,
            "human_decision": "approve",
            "dispatched_to": teams,
            "emergency_ids": [e["id"] for e in created],
            "notes": "Operator approved AI recommendation",
        })
    except Exception:
        pass

    return jsonify({
        "success": True,
        "decision": "approve",
        "message": f"AI recommendation approved. Dispatched to {', '.join(teams)}.",
        "call": call,
        "emergencies": [
            {
                "id": e["id"],
                "type": e.get("type"),
                "status": e.get("status"),
                "assigned_to": e.get("assigned_to"),
                "assigned_hospital_name": e.get("assigned_hospital_name"),
            }
            for e in created
        ],
        "panel": _ai_panel_for_call(call_id).get("panel"),
    })


@app.route("/api/call-center/calls/<int:call_id>/nearest", methods=["GET"])
@call_center_required
def api_call_center_nearest(call_id):
    data = cc.load_calls(read_json, save_json)
    call = cc.get_call_by_id(data, call_id)
    if not call:
        return jsonify({"success": False, "message": "Not found"}), 404
    nearest = cc.find_nearest_responders(
        call["latitude"], call["longitude"], read_json, save_json, get_response_station_list()
    )
    call["nearest"] = nearest
    cc.save_calls(data, save_json)
    return jsonify({"success": True, "nearest": nearest})


@app.route("/api/call-center/calls/<int:call_id>/dispatch", methods=["POST"])
@call_center_required
def api_call_center_dispatch(call_id):
    """Dispatch to one or more responder teams. Body: { types: ["medical","fire"], notes }"""
    payload = request.get_json(silent=True) or {}
    types = payload.get("types") or payload.get("emergency_types") or []
    if isinstance(types, str):
        types = [types]
    if not types:
        return jsonify({"success": False, "message": "Select at least one emergency type."}), 400

    data = cc.load_calls(read_json, save_json)
    call = cc.get_call_by_id(data, call_id)
    if not call:
        return jsonify({"success": False, "message": "Call not found"}), 404

    operator = current_user()
    if not call.get("operator_id"):
        try:
            call = cc.answer_call(
                call_id, operator, read_json, save_json, stations=get_response_station_list()
            )
        except ValueError:
            pass
        data = cc.load_calls(read_json, save_json)
        call = cc.get_call_by_id(data, call_id)

    notes = (payload.get("notes") or "").strip()
    if notes:
        call["notes"] = notes
        cc.save_calls(data, save_json)

    pairs = cc.resolve_dispatch_types(types)
    if not pairs:
        return jsonify({"success": False, "message": "Invalid emergency types."}), 400

    created = []
    teams = []
    for etype, team in pairs:
        em = _create_emergency_from_call(call, etype, team, operator, notes)
        created.append(em)
        teams.append(team)
        _notify_call_dispatch(call, em, [team])

    call = cc.record_dispatch(
        call_id,
        [e.get("type") for e in created],
        [e["id"] for e in created],
        teams,
        read_json,
        save_json,
    )

    if payload.get("complete_call"):
        call = cc.set_call_status(call_id, "completed", read_json, save_json, operator=operator)

    return jsonify({
        "success": True,
        "call": call,
        "emergencies": [
            {
                "id": e["id"],
                "type": e.get("type"),
                "status": e.get("status"),
                "assigned_to": e.get("assigned_to"),
                "assigned_hospital_id": e.get("assigned_hospital_id"),
                "assigned_hospital_name": e.get("assigned_hospital_name"),
            }
            for e in created
        ],
        "message": f"Dispatched to {', '.join(teams)}.",
    })


@app.route("/api/call-center/calls/<int:call_id>/alert", methods=["POST"])
@call_center_required
def api_call_center_alert(call_id):
    """
    Friin / alert nearest hospital, police, or fire.
    Uses current call GPS + the exact nearest facility on the Call Center card.
    """
    payload = request.get_json(silent=True) or {}
    target = (payload.get("target") or payload.get("team") or "").strip().lower()
    type_map = {
        "hospital": "medical",
        "medical": "medical",
        "police": "security",
        "security": "security",
        "fire": "fire",
    }
    etype = type_map.get(target)
    if not etype:
        return jsonify({
            "success": False,
            "message": "target must be hospital, police, or fire",
        }), 400
    team = "hospital" if etype == "medical" else ("police" if etype == "security" else "fire")

    call_data = cc.load_calls(read_json, save_json)
    call = cc.get_call_by_id(call_data, call_id)
    if not call:
        return jsonify({"success": False, "message": "Call not found"}), 404
    if call.get("status") in ("completed", "cancelled", "missed"):
        return jsonify({"success": False, "message": "Call is closed."}), 400
    if call.get("latitude") is None or call.get("longitude") is None:
        return jsonify({"success": False, "message": "Call has no GPS. Update location first."}), 400

    operator = current_user()
    if not call.get("operator_id"):
        try:
            call = cc.answer_call(
                call_id, operator, read_json, save_json, stations=get_response_station_list()
            )
        except ValueError:
            pass
        call_data = cc.load_calls(read_json, save_json)
        call = cc.get_call_by_id(call_data, call_id)

    # Always recompute nearest from the *current* call GPS (after operator corrections)
    nearest = cc.find_nearest_responders(
        call["latitude"],
        call["longitude"],
        read_json,
        save_json,
        get_response_station_list(),
    )
    call["nearest"] = nearest
    cc.save_calls(call_data, save_json)

    preferred_id = payload.get("preferred_id") or payload.get("facility_id")
    if preferred_id is None and nearest.get(team):
        preferred_id = nearest[team].get("id")
    # Ignore non-numeric legacy ids like "police"
    try:
        preferred_id = int(preferred_id) if preferred_id is not None else None
    except (TypeError, ValueError):
        preferred_id = nearest.get(team, {}).get("id") if nearest.get(team) else None
        try:
            preferred_id = int(preferred_id) if preferred_id is not None else None
        except (TypeError, ValueError):
            preferred_id = None

    notes = (payload.get("notes") or call.get("notes") or "").strip()
    existing_teams = set(call.get("dispatched_to") or [])
    created = None
    if team not in existing_teams:
        created = _create_emergency_from_call(
            call,
            etype,
            team,
            operator,
            notes or f"Call Center alert ({team})",
            preferred_facility_id=preferred_id,
        )
        _notify_call_dispatch(call, created, [team])
        existing_ids = list(dict.fromkeys((call.get("emergency_ids") or []) + [created["id"]]))
        existing_types = list(dict.fromkeys((call.get("emergency_types") or []) + [created.get("type")]))
        call = cc.record_dispatch(
            call_id,
            existing_types,
            existing_ids,
            list(existing_teams | {team}),
            read_json,
            save_json,
        )
        emergency = created
        fac_name = (
            (nearest.get(team) or {}).get("name")
            or created.get("assigned_hospital_name")
            or created.get("assigned_team_label")
            or team
        )
        message = f"Alert sent to {fac_name} with caller GPS {call.get('latitude')}, {call.get('longitude')}."
    else:
        # Re-alert: sync corrected GPS onto existing emergency, then notify again
        edata = load_emergencies()
        emergency = None
        for em in edata.get("emergencies") or []:
            if em.get("call_id") == call_id and em.get("assigned_to") == team:
                emergency = em
                break
        if not emergency:
            for em in edata.get("emergencies") or []:
                if em.get("id") in (call.get("emergency_ids") or []) and em.get("assigned_to") == team:
                    emergency = em
                    break
        if not emergency:
            return jsonify({"success": False, "message": "No existing case to re-alert."}), 404

        _sync_emergency_location_from_call(emergency, call)
        if preferred_id is not None:
            _force_assign_call_center_facility(emergency, team, preferred_id)
        save_emergencies(edata)

        gps_line = (
            f"{call.get('caller_name')} — GPS {call.get('latitude')}, {call.get('longitude')} "
            f"— {call.get('address')}"
        )
        friin = f"FRIIN / ALERT from Call Center (updated location): {gps_line}"
        if team == "hospital" and emergency.get("assigned_hospital_id"):
            _notify("hospital", emergency["assigned_hospital_id"], friin, emergency["id"], "team_assigned")
        elif team in ("police", "fire"):
            udata = load_users()
            for u in udata["users"]:
                if u.get("status") == "blocked":
                    continue
                if u.get("role") != team:
                    continue
                sid = emergency.get("assigned_station_id")
                if sid and u.get("station_id") and int(u.get("station_id") or 0) != int(sid):
                    continue
                _notify(team, u["id"], friin, emergency["id"], "team_assigned")
        message = (
            f"Re-alert sent with corrected GPS {call.get('latitude')}, {call.get('longitude')}."
        )

    append_audit(
        "call_center_alert",
        "call",
        call_id,
        {
            "target": team,
            "emergency_id": (emergency or {}).get("id"),
            "created": bool(created),
            "preferred_facility_id": preferred_id,
            "latitude": call.get("latitude"),
            "longitude": call.get("longitude"),
        },
        operator.get("id"),
    )
    return jsonify({
        "success": True,
        "message": message,
        "target": team,
        "call": call,
        "nearest": nearest,
        "emergency": {
            "id": emergency.get("id"),
            "assigned_to": emergency.get("assigned_to"),
            "assigned_hospital_id": emergency.get("assigned_hospital_id"),
            "assigned_hospital_name": emergency.get("assigned_hospital_name"),
            "assigned_station_id": emergency.get("assigned_station_id"),
            "latitude": emergency.get("latitude"),
            "longitude": emergency.get("longitude"),
            "location": emergency.get("location"),
        } if emergency else None,
    })


@app.route("/api/call-center/calls/<int:call_id>/send-gps", methods=["POST"])
@call_center_required
def api_call_center_send_gps(call_id):
    """Push GPS packet to selected responder dashboards."""
    payload = request.get_json(silent=True) or {}
    targets = payload.get("targets") or payload.get("dispatched_to") or []
    if isinstance(targets, str):
        targets = [targets]
    type_map = {
        "hospital": "medical",
        "medical": "medical",
        "police": "security",
        "fire": "fire",
    }
    types = []
    for t in targets:
        mapped = type_map.get(str(t).lower())
        if mapped:
            types.append(mapped)
    if not types and payload.get("types"):
        types = payload.get("types")
    if not types:
        return jsonify({"success": False, "message": "Select Hospital, Police, and/or Fire."}), 400

    call_data = cc.load_calls(read_json, save_json)
    call = cc.get_call_by_id(call_data, call_id)
    if not call:
        return jsonify({"success": False, "message": "Call not found"}), 404
    operator = current_user()
    pairs = cc.resolve_dispatch_types(types)
    created = []
    existing_ids = set(call.get("emergency_ids") or [])
    existing_teams = set(call.get("dispatched_to") or [])
    for etype, team in pairs:
        if team in existing_teams:
            continue
        em = _create_emergency_from_call(
            call, etype, team, operator, payload.get("notes") or "GPS shared by Call Center"
        )
        created.append(em)
        existing_ids.add(em["id"])
        existing_teams.add(team)
        _notify_call_dispatch(call, em, [team])

    all_types = list(dict.fromkeys((call.get("emergency_types") or []) + [e.get("type") for e in created]))
    call = cc.record_dispatch(
        call_id,
        all_types,
        list(existing_ids),
        list(existing_teams),
        read_json,
        save_json,
    )
    return jsonify({
        "success": True,
        "call": call,
        "emergencies": [{"id": e["id"], "assigned_to": e.get("assigned_to")} for e in created],
        "message": "GPS sent to selected responders.",
    })


@app.route("/api/call-center/calls/<int:call_id>/transfer", methods=["POST"])
@call_center_required
def api_call_center_transfer(call_id):
    """Transfer an active call to another Call Center operator."""
    payload = request.get_json(silent=True) or {}
    target_id = payload.get("operator_id")
    if not target_id:
        return jsonify({"success": False, "message": "Select a target operator_id."}), 400
    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid operator_id."}), 400

    me = current_user()
    if target_id == me.get("id"):
        return jsonify({"success": False, "message": "Cannot transfer a call to yourself."}), 400

    target, udata = get_user_by_id(target_id)
    if not target or target.get("role") != "call_center" or target.get("status") == "blocked":
        return jsonify({"success": False, "message": "Target must be an active Call Center operator."}), 400

    data = cc.load_calls(read_json, save_json)
    call = cc.get_call_by_id(data, call_id)
    if not call:
        return jsonify({"success": False, "message": "Call not found"}), 404
    if call.get("status") in ("completed", "cancelled", "missed"):
        return jsonify({"success": False, "message": "Cannot transfer a closed call."}), 400

    prev_op = call.get("operator_name") or me.get("name")
    call["operator_id"] = target["id"]
    call["operator_name"] = target.get("name") or "Operator"
    call["transferred_from"] = me.get("id")
    call["transferred_at"] = now_str()
    call["status"] = "answered" if call.get("status") == "ringing" else call.get("status")
    note = (payload.get("notes") or "").strip()
    if note:
        call["notes"] = ((call.get("notes") or "") + "\n[Transfer] " + note).strip()
    cc.save_calls(data, save_json)

    _notify(
        "call_center",
        target["id"],
        f"Call #{call_id} transferred to you from {prev_op}: {call.get('caller_name')} ({call.get('phone')})",
        None,
        "system_alert",
    )
    append_audit(
        "call_center_transfer",
        "call",
        call_id,
        {"from": me.get("id"), "to": target_id},
        me.get("id"),
    )
    return jsonify({
        "success": True,
        "call": call,
        "message": f"Call transferred to {call['operator_name']}.",
    })


@app.route("/api/call-center/calls/<int:call_id>/cancel", methods=["POST"])
@call_center_required
def api_call_center_cancel(call_id):
    try:
        call = cc.set_call_status(
            call_id, "cancelled", read_json, save_json, operator=current_user()
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400
    _notify(
        "patient",
        call.get("user_id"),
        f"Your Call Center session #{call_id} was cancelled by the operator.",
        None,
        "system_alert",
    )
    return jsonify({"success": True, "call": call})


@app.route("/api/call-center/calls/<int:call_id>/complete", methods=["POST"])
@call_center_required
def api_call_center_complete(call_id):
    try:
        call = cc.set_call_status(
            call_id, "completed", read_json, save_json, operator=current_user()
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc)}), 400
    return jsonify({"success": True, "call": call})


@app.route("/api/call-center/settings", methods=["GET"])
@login_required
def api_call_center_settings():
    if session.get("role") not in STAFF_ADMIN_ROLES and session.get("role") != "call_center":
        return jsonify({"success": False, "message": "Forbidden"}), 403
    settings = load_settings()
    return jsonify({
        "success": True,
        "settings": {
            "enabled": settings.get("call_center_enabled", True),
            "phone_primary": settings.get("call_center_phone") or "",
            "phone_secondary": settings.get("call_center_phone_secondary", ""),
            "auto_nearest": settings.get("call_center_auto_nearest", True),
            "heartbeat_sec": settings.get("call_center_heartbeat_sec", 45),
            "priority_medical": settings.get("call_center_priority_medical", 1),
            "priority_fire": settings.get("call_center_priority_fire", 1),
            "priority_police": settings.get("call_center_priority_police", 1),
        },
    })


@app.route("/api/admin/call-center/stats")
@admin_required
def api_admin_call_center_stats():
    denied = _require_admin_perm("call_center")
    if denied:
        return denied
    settings = load_settings()
    online = cc.online_operators(load_users, int(settings.get("call_center_heartbeat_sec", 45)))
    stats = cc.call_stats(read_json, save_json, [o["id"] for o in online])
    udata = load_users()
    operators = [
        {
            "id": u["id"],
            "name": u.get("name"),
            "email": u.get("email"),
            "phone": u.get("phone"),
            "status": u.get("status"),
            "last_seen": u.get("last_seen_call_center") or u.get("last_login"),
        }
        for u in udata["users"]
        if u.get("role") == "call_center"
    ]
    return jsonify({
        "success": True,
        "stats": stats,
        "operators": operators,
        "operators_online": online,
        "settings": {
            "enabled": settings.get("call_center_enabled", True),
            "phone_primary": settings.get("call_center_phone"),
            "phone_secondary": settings.get("call_center_phone_secondary"),
        },
        "recent_calls": cc.call_history(read_json, save_json, limit=20),
    })


@app.route("/api/admin/call-center/settings", methods=["POST"])
@admin_required
def api_admin_call_center_settings():
    denied = _require_admin_perm("call_center")
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    settings = load_settings()
    mapping = {
        "enabled": "call_center_enabled",
        "phone_primary": "call_center_phone",
        "phone_secondary": "call_center_phone_secondary",
        "auto_nearest": "call_center_auto_nearest",
        "heartbeat_sec": "call_center_heartbeat_sec",
        "priority_medical": "call_center_priority_medical",
        "priority_fire": "call_center_priority_fire",
        "priority_police": "call_center_priority_police",
    }
    for src, dest in mapping.items():
        if src in data:
            settings[dest] = data[src]
        if dest in data:
            settings[dest] = data[dest]
    save_settings(settings)
    append_audit("call_center_settings_updated", "settings", 0, data)
    return jsonify({"success": True, "settings": settings})


@app.route("/api/admin/ai/stats", methods=["GET"])
@admin_required
def api_admin_ai_stats():
    """AI Intelligence dashboard counters (recommendations only — AI never dispatches)."""
    denied = _require_admin_perm("ai")
    if denied:
        return denied
    settings = load_settings()
    try:
        stats = _ai_engine().stats()
    except Exception as exc:
        return jsonify({"success": False, "message": _safe_client_message(exc, "Internal server error.")}), 500
    approved = stats.get("approved_recommendations") or 0
    rejected = stats.get("rejected_recommendations") or 0
    decided = approved + rejected
    approval_rate = round((approved / decided) * 100, 1) if decided else None
    return jsonify({
        "success": True,
        "ai_enabled": settings.get("ai_enabled", True),
        "ai_provider": settings.get("ai_provider") or "rule_based",
        "stats": {
            **stats,
            "approval_rate_pct": approval_rate,
            # No fabricated improvement metric — omit until real timing analytics exist
            "average_response_improvement": None,
        },
    })


# Facility registries + command workflow admin APIs
from admin_registry_api import register_admin_registry_routes

register_admin_registry_routes(app, {
    "admin_required": admin_required,
    "_require_admin_perm": _require_admin_perm,
    "read_json": read_json,
    "save_json": save_json,
    "load_users": load_users,
    "load_emergencies": load_emergencies,
    "save_emergencies": save_emergencies,
    "save_users": save_users,
    "append_audit": append_audit,
    "user_name": user_name,
    "_append_status": _append_status,
    "TEAM_LABELS": TEAM_LABELS,
    "ACTIVE_SOS_STATUSES": ACTIVE_SOS_STATUSES,
    "STATUS_VALUES": STATUS_VALUES,
    "COMPLETED_STATUSES": COMPLETED_STATUSES,
    "normalize_emergency_record": normalize_emergency_record,
    "now_str": now_str,
    "load_settings": load_settings,
    "normalize_email": normalize_email,
    "signup_email_rejection_reason": signup_email_rejection_reason,
    "allow_test_email_domains": allow_test_email_domains,
    "_password_policy_error": _password_policy_error,
    "_link_user_to_hospital": _link_user_to_hospital,
    "notify": lambda target_type, target_id, message, request_id, ntype="dispatch": (
        hl.add_notification(read_json, save_json, target_type, target_id, message, request_id, ntype)
        if target_id else None
    ),
})

# After all helpers exist: boot MySQL seed for gunicorn import path
if USE_MYSQL and "pytest" not in sys.modules:
    try:
        ensure_mysql_boot()
    except Exception:
        logging.getLogger(__name__).exception("Deferred MySQL boot failed")

# WebRTC voice signaling (Flask-SocketIO) — reuse existing auth/session
try:
    from voice_signaling import init_socketio

    socketio = init_socketio(
        app,
        read_json=read_json,
        save_json=save_json,
        get_user_by_id=get_user_by_id,
        now_str=now_str,
    )
except Exception:
    socketio = None
    logging.getLogger(__name__).exception("SocketIO voice signaling init failed")


if __name__ == "__main__":
    # Ensure SMTP from .env wins over leftover shell EMAIL_PROVIDER=memory
    _apply_email_env_from_dotenv(force=True)
    logging.basicConfig(level=logging.INFO, force=True)
    log = logging.getLogger(__name__)

    print("=" * 50, flush=True)
    print("GurmadNet Starting", flush=True)
    print("=" * 50, flush=True)

    if not USE_MYSQL:
        raise SystemExit(
            "GurmadNet requires MySQL. Set database/db_config.env and ensure the "
            "server is running. (GURMADNET_DB=json is for automated tests only.)"
        )
    status = _storage_status()
    if not status.get("live"):
        raise SystemExit(f"MySQL not live: {status.get('error')}")
    print(
        f"[OK] MySQL connected: {status.get('user')}@{status.get('host')}:"
        f"{status.get('port')}/{status.get('database')}",
        flush=True,
    )
    log.info("Live MySQL counts=%s", status.get("table_counts"))

    try:
        from email_service.factory import clear_email_provider_cache, get_email_provider

        clear_email_provider_cache()
        provider = get_email_provider(force_new=True)
        configured = getattr(provider, "configured", lambda: True)()
        print(
            f"[OK] SMTP configured: {getattr(provider, 'name', type(provider).__name__)} "
            f"(configured={configured})",
            flush=True,
        )
    except Exception:
        log.exception("Email provider init failed")
        print("[WARN] SMTP init failed — continuing without blocking startup", flush=True)

    ensure_database_dir()
    ensure_mysql_boot()
    print("[OK] Flask application initialized", flush=True)

    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes", "on")
    # Mobile / LAN testing: bind all interfaces by default (override with HOST=127.0.0.1).
    host = (os.environ.get("HOST") or os.environ.get("GURMADNET_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.environ.get("PORT", "5000"))

    if socketio is None:
        print("[WARN] Socket.IO not available — falling back to plain Flask", flush=True)
    else:
        mode = getattr(socketio, "async_mode", "?")
        print(f"[OK] Socket.IO initialized (async_mode={mode})", flush=True)
        print("[OK] WebRTC signaling ready", flush=True)

    # Fail fast with a clear message if something else already owns the port.
    import socket as _socket

    def _lan_ipv4s():
        addrs = []
        try:
            import psutil

            for _name, snics in psutil.net_if_addrs().items():
                for snic in snics:
                    if getattr(snic, "family", None) != _socket.AF_INET:
                        continue
                    ip = snic.address or ""
                    if ip.startswith("127.") or ip.startswith("169.254."):
                        continue
                    addrs.append(ip)
        except Exception:
            pass
        if not addrs:
            try:
                hostname = _socket.gethostname()
                for info in _socket.getaddrinfo(hostname, None, _socket.AF_INET):
                    ip = info[4][0]
                    if not ip.startswith("127.") and not ip.startswith("169.254."):
                        addrs.append(ip)
            except Exception:
                pass
        # Stable unique order
        seen = set()
        out = []
        for ip in addrs:
            if ip not in seen:
                seen.add(ip)
                out.append(ip)
        return out

    _probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        _probe.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        _probe.bind((host, port))
    except OSError as exc:
        raise SystemExit(
            f"\nPort {port} is already in use.\n"
            f"GurmadNet is probably already running at http://127.0.0.1:{port}\n"
            f"Open that URL — do NOT start a second python app.py.\n"
            f"To restart: stop the other python process, then run once.\n"
            f"Detail: {exc}\n"
        ) from exc
    finally:
        try:
            _probe.close()
        except Exception:
            pass

    lan_ips = _lan_ipv4s()
    print(flush=True)
    print("Server URLs:", flush=True)
    print(f"  Local:  http://127.0.0.1:{port}", flush=True)
    for ip in lan_ips:
        print(f"  Phone:  http://{ip}:{port}  (same Wi-Fi)", flush=True)
    if not lan_ips:
        print("  Phone:  (no LAN IPv4 detected — check Wi-Fi)", flush=True)
    print("  Bind:   {}:{}".format(host, port), flush=True)
    print("=" * 50, flush=True)
    print("Press CTRL+C to stop", flush=True)
    print(flush=True)
    # Force debug off for LAN/mobile exposure unless explicitly enabled.
    if host in ("0.0.0.0", "::") and not (os.environ.get("FLASK_DEBUG") or "").strip():
        debug = False

    try:
        if socketio is not None:
            # use_reloader=False: Windows + SocketIO must not spawn a second process
            socketio.run(
                app,
                debug=debug,
                host=host,
                port=port,
                allow_unsafe_werkzeug=True,
                use_reloader=False,
            )
        else:
            app.run(debug=debug, host=host, port=port, use_reloader=False)
    except OSError as exc:
        err = str(exc).lower()
        if getattr(exc, "winerror", None) == 10048 or "address already in use" in err or "10048" in err:
            raise SystemExit(
                f"\nPort {port} is already in use.\n"
                f"GurmadNet is probably already running at http://127.0.0.1:{port}\n"
                f"Open that URL in the browser — do NOT run python app.py again.\n"
                f"To restart: close the other terminal / stop the old python process, then run once.\n"
            ) from exc
        raise
    except KeyboardInterrupt:
        print("\nGurmadNet stopped.", flush=True)
