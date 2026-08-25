"""Facade used by GurmadNet auth — never call provider classes from app routes."""
import os
import re

from email_service.factory import get_email_provider

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Domains that are not real inboxes for public signup (RFC examples + common fakes).
_RESERVED_FAKE_DOMAINS = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "example.edu",
        "test.com",
        "test.org",
        "test.net",
        "localhost",
        "invalid",
        "local",
    }
)

# Common disposable / temporary inbox providers (signup blocked).
_DISPOSABLE_DOMAINS = frozenset(
    {
        "mailinator.com",
        "guerrillamail.com",
        "guerrillamail.org",
        "guerrillamail.net",
        "sharklasers.com",
        "grr.la",
        "guerrillamailblock.com",
        "pokemail.net",
        "spam4.me",
        "tempmail.com",
        "temp-mail.org",
        "temp-mail.io",
        "throwaway.email",
        "yopmail.com",
        "yopmail.fr",
        "trashmail.com",
        "trashmail.me",
        "10minutemail.com",
        "10minutemail.net",
        "minuteinbox.com",
        "getnada.com",
        "maildrop.cc",
        "discard.email",
        "dispostable.com",
        "fakeinbox.com",
        "mailnesia.com",
        "mozmail.com",
        "inboxkitten.com",
        "tempail.com",
        "emailondeck.com",
        "gettempmail.com",
        "tmpmail.org",
        "tmpmail.net",
        "mailcatch.com",
        "mytemp.email",
        "tempinbox.com",
    }
)


def is_valid_email_format(email):
    email = (email or "").strip()
    if not email or len(email) > 180:
        return False
    if ".." in email or email.startswith(".") or "@." in email:
        return False
    return bool(_EMAIL_RE.match(email))


def normalize_email(email):
    return (email or "").strip().lower()


def email_domain(email):
    email = normalize_email(email)
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1]


def allow_test_email_domains():
    """Tests / memory provider may use example.com and similar."""
    provider = (os.environ.get("EMAIL_PROVIDER") or "").strip().lower()
    flag = (os.environ.get("ALLOW_TEST_EMAILS") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if provider in ("memory", "console"):
        return True
    try:
        from flask import current_app

        if current_app and current_app.config.get("TESTING"):
            return True
    except RuntimeError:
        pass
    return False


def is_disposable_or_fake_email_domain(domain):
    domain = (domain or "").strip().lower().rstrip(".")
    if not domain:
        return True
    # Disposable / temporary inboxes are never allowed for signup.
    if domain in _DISPOSABLE_DOMAINS:
        return True
    for blocked in _DISPOSABLE_DOMAINS:
        if domain.endswith("." + blocked):
            return True
    # RFC/example/test domains blocked in production; allowed under test/memory.
    if allow_test_email_domains():
        return False
    if domain in _RESERVED_FAKE_DOMAINS:
        return True
    for blocked in _RESERVED_FAKE_DOMAINS:
        if domain.endswith("." + blocked):
            return True
    if domain.endswith(".local") or domain.endswith(".test") or domain.endswith(".invalid"):
        return True
    return False


def signup_email_rejection_reason(email):
    """
    Return a user-safe rejection reason, or None if the email may be used for signup.
    Ownership is still proven later via the verification link.
    """
    email = normalize_email(email)
    if not is_valid_email_format(email):
        return "Please enter a valid email address that you own."
    domain = email_domain(email)
    if is_disposable_or_fake_email_domain(domain):
        return (
            "Please use a real email address you own. "
            "Temporary or disposable email addresses are not allowed."
        )
    local = email.split("@", 1)[0]
    if local in ("test", "fake", "noreply", "no-reply", "donotreply"):
        if not allow_test_email_domains():
            return "Please use a real personal or work email address that you own."
    return None


def is_acceptable_signup_email(email):
    return signup_email_rejection_reason(email) is None


def send_email(to_email, subject, text_body, html_body=None, from_email=None, provider_name=None):
    provider = get_email_provider(provider_name)
    return provider.send(
        to_email=to_email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        from_email=from_email,
    )


def send_verification_email(to_email, verify_url, user_name=None):
    """Legacy link-based verification email (kept for compatibility)."""
    app_name = os.environ.get("APP_NAME", "Somali Help App")
    greeting = f"Hello {user_name}," if user_name else "Hello,"
    subject = f"Verify your {app_name} email address"
    text_body = (
        f"{greeting}\n\n"
        f"Thank you for signing up for {app_name}.\n\n"
        "Please verify your email address by opening this link:\n"
        f"{verify_url}\n\n"
        "This link expires in 24 hours.\n\n"
        "If you did not create this account, you can ignore this email.\n"
    )
    html_body = (
        f"<p>{greeting}</p>"
        f"<p>Thank you for signing up for <strong>{app_name}</strong>.</p>"
        f"<p><a href=\"{verify_url}\">Verify my email address</a></p>"
        f"<p>Or paste this link into your browser:<br>{verify_url}</p>"
        "<p>This link expires in 24 hours.</p>"
        "<p>If you did not create this account, you can ignore this email.</p>"
    )
    return send_email(to_email, subject, text_body, html_body=html_body)


def send_email_verification_otp_email(to_email, otp_code, user_name=None, minutes=30):
    """Send a one-time email verification code after citizen registration."""
    app_name = os.environ.get("APP_NAME", "Somali Help App")
    greeting = f"Hello {user_name}," if user_name else "Hello,"
    subject = f"Your {app_name} email verification code"
    text_body = (
        f"{greeting}\n\n"
        f"Your email verification code is: {otp_code}\n\n"
        f"Enter this code to activate your {app_name} account.\n"
        f"This code expires in {minutes} minutes and can be used only once.\n\n"
        "If you did not create this account, you can ignore this email.\n"
    )
    html_body = (
        f"<p>{greeting}</p>"
        f"<p>Your email verification code is:</p>"
        f"<p style=\"font-size:28px;font-weight:700;letter-spacing:4px;\">{otp_code}</p>"
        f"<p>Enter this code to activate your <strong>{app_name}</strong> account.</p>"
        f"<p>This code expires in {minutes} minutes and can be used only once.</p>"
        "<p>If you did not create this account, you can ignore this email.</p>"
    )
    return send_email(to_email, subject, text_body, html_body=html_body)


def send_emergency_contact_alert_email(
    to_email,
    contact_name=None,
    citizen_name=None,
    citizen_phone=None,
    emergency_type=None,
    location=None,
    notes=None,
    emergency_id=None,
    occurred_at=None,
    latitude=None,
    longitude=None,
):
    """Notify a citizen's registered emergency contact when SOS is submitted."""
    app_name = os.environ.get("APP_NAME", "Somali Help App")
    greeting = f"Hello {contact_name}," if contact_name else "Hello,"
    etype = (emergency_type or "emergency").replace("_", " ").title()
    when = occurred_at or ""
    gps = ""
    if latitude is not None and longitude is not None:
        try:
            gps = f"{float(latitude):.6f}, {float(longitude):.6f}"
        except (TypeError, ValueError):
            gps = f"{latitude}, {longitude}"
    subject = f"{app_name}: Emergency alert for {citizen_name or 'a registered user'}"
    text_body = (
        f"{greeting}\n\n"
        f"This is an automated alert from {app_name}.\n\n"
        f"{citizen_name or 'A registered user'} has submitted an SOS emergency.\n\n"
        f"Citizen name: {citizen_name or '—'}\n"
        f"Phone number: {citizen_phone or 'Not provided'}\n"
        f"Emergency type: {etype}\n"
        f"Date & time: {when or '—'}\n"
        f"Location: {location or 'Not available'}\n"
        f"GPS: {gps or 'Not available'}\n"
        f"Notes: {notes or '—'}\n"
        f"Emergency ID: {emergency_id or '—'}\n\n"
        "Please try to reach them if you can.\n"
    )
    html_body = (
        f"<p>{greeting}</p>"
        f"<p>This is an automated alert from <strong>{app_name}</strong>.</p>"
        f"<p><strong>{citizen_name or 'A registered user'}</strong> has submitted an SOS emergency.</p>"
        f"<ul>"
        f"<li><strong>Citizen name:</strong> {citizen_name or '—'}</li>"
        f"<li><strong>Phone number:</strong> {citizen_phone or 'Not provided'}</li>"
        f"<li><strong>Emergency type:</strong> {etype}</li>"
        f"<li><strong>Date &amp; time:</strong> {when or '—'}</li>"
        f"<li><strong>Location:</strong> {location or 'Not available'}</li>"
        f"<li><strong>GPS:</strong> {gps or 'Not available'}</li>"
        f"<li><strong>Notes:</strong> {notes or '—'}</li>"
        f"<li><strong>Emergency ID:</strong> {emergency_id or '—'}</li>"
        f"</ul>"
        "<p>Please try to reach them if you can.</p>"
    )
    return send_email(to_email, subject, text_body, html_body=html_body)


def send_password_reset_otp_email(to_email, otp_code, user_name=None, minutes=10):
    """Send a one-time password-reset code (OTP) via configured provider."""
    app_name = os.environ.get("APP_NAME", "Somali Help App")
    greeting = f"Hello {user_name}," if user_name else "Hello,"
    subject = f"Your {app_name} password reset code"
    text_body = (
        f"{greeting}\n\n"
        f"Your password reset code is: {otp_code}\n\n"
        f"This code expires in {minutes} minutes and can be used only once.\n\n"
        "If you did not request a password reset, you can ignore this email.\n"
    )
    html_body = (
        f"<p>{greeting}</p>"
        f"<p>Your password reset code is:</p>"
        f"<p style=\"font-size:28px;font-weight:700;letter-spacing:4px;\">{otp_code}</p>"
        f"<p>This code expires in {minutes} minutes and can be used only once.</p>"
        "<p>If you did not request a password reset, you can ignore this email.</p>"
    )
    return send_email(to_email, subject, text_body, html_body=html_body)


def send_password_reset_email(to_email, reset_url, user_name=None):
    """Legacy link-based reset email (kept for compatibility). Prefer OTP."""
    app_name = os.environ.get("APP_NAME", "Somali Help App")
    greeting = f"Hello {user_name}," if user_name else "Hello,"
    subject = f"Reset your {app_name} password"
    text_body = (
        f"{greeting}\n\n"
        f"We received a request to reset your {app_name} password.\n\n"
        "Open this link to choose a new password:\n"
        f"{reset_url}\n\n"
        "This link expires in 2 hours.\n\n"
        "If you did not request a reset, you can ignore this email.\n"
    )
    html_body = (
        f"<p>{greeting}</p>"
        f"<p>We received a request to reset your <strong>{app_name}</strong> password.</p>"
        f"<p><a href=\"{reset_url}\">Reset my password</a></p>"
        f"<p>Or paste this link into your browser:<br>{reset_url}</p>"
        "<p>This link expires in 2 hours.</p>"
        "<p>If you did not request a reset, you can ignore this email.</p>"
    )
    return send_email(to_email, subject, text_body, html_body=html_body)