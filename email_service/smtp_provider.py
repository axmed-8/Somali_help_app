"""SMTP email provider (Gmail App Password and any standard SMTP server)."""
import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from email_service.base import EmailProvider
from email_service.env_loader import load_email_env

logger = logging.getLogger(__name__)

# Treat .env.example placeholders as "not configured"
_PLACEHOLDER_VALUES = {
    "",
    "your.address@gmail.com",
    "your_gmail_app_password",
    "change-me",
    "changeme",
    "password",
    "xxxxx",
}


def _clean(value):
    return (value or "").strip()


def _is_real_secret(value):
    v = _clean(value)
    if not v:
        return False
    if v.lower() in _PLACEHOLDER_VALUES:
        return False
    if "your_" in v.lower() or "example" in v.lower():
        return False
    return True


class SMTPEmailProvider(EmailProvider):
    """Reads SMTP_* from environment only — never hardcodes credentials."""

    name = "smtp"

    def __init__(self):
        load_email_env()
        self._reload_from_env()

    def _reload_from_env(self):
        load_email_env()
        self.host = _clean(os.environ.get("SMTP_HOST") or "smtp.gmail.com")
        try:
            self.port = int(_clean(os.environ.get("SMTP_PORT") or "587") or "587")
        except ValueError:
            self.port = 587
        self.user = _clean(os.environ.get("SMTP_USER"))
        self.password = _clean(os.environ.get("SMTP_PASSWORD"))
        self.from_email = _clean(os.environ.get("SMTP_FROM") or self.user)
        self.use_tls = (_clean(os.environ.get("SMTP_USE_TLS") or "true")).lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        self.app_name = _clean(os.environ.get("APP_NAME") or "Somali Help App")

    def configured(self):
        self._reload_from_env()
        return bool(
            self.host
            and _is_real_secret(self.user)
            and _is_real_secret(self.password)
            and _is_real_secret(self.from_email)
        )

    def health_check(self):
        """True when required SMTP env vars are present (does not open a socket)."""
        return self.configured()

    def _open_server(self):
        """Open an authenticated SMTP session (caller must close/context-manage)."""
        if self.use_tls:
            context = ssl.create_default_context()
            server = smtplib.SMTP(self.host, self.port, timeout=30)
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(self.user, self.password)
            return server
        server = smtplib.SMTP_SSL(self.host, self.port, timeout=30)
        server.login(self.user, self.password)
        return server

    def verify_connection(self):
        """
        Verify SMTP host login before sending mail.
        Returns {"success": bool, "error": str|None, "host": str, "user": str}
        """
        self._reload_from_env()
        if not self.configured():
            missing = []
            if not self.host:
                missing.append("SMTP_HOST")
            if not _is_real_secret(self.user):
                missing.append("SMTP_USER")
            if not _is_real_secret(self.password):
                missing.append("SMTP_PASSWORD")
            if not _is_real_secret(self.from_email):
                missing.append("SMTP_FROM")
            err = (
                "SMTP not configured. Set real values in .env for: "
                + ", ".join(missing)
                + ". Use a Gmail App Password (Google Account -> Security -> App passwords)."
            )
            logger.error(err)
            return {
                "success": False,
                "error": err,
                "host": self.host,
                "user": self.user or "(empty)",
            }
        try:
            with self._open_server() as server:
                # noop ensures session is usable
                server.noop()
            logger.info(
                "SMTP connection verified host=%s user=%s",
                self.host,
                self.user,
            )
            return {
                "success": True,
                "error": None,
                "host": self.host,
                "user": self.user,
            }
        except Exception as exc:
            logger.exception(
                "SMTP connection verify failed host=%s user=%s error=%s",
                self.host,
                self.user,
                exc,
            )
            return {
                "success": False,
                "error": str(exc),
                "host": self.host,
                "user": self.user,
            }

    def send(self, to_email, subject, text_body, html_body=None, from_email=None):
        self._reload_from_env()
        verify = self.verify_connection()
        if not verify.get("success"):
            return {
                "success": False,
                "provider": self.name,
                "message_id": None,
                "error": verify.get("error") or "SMTP connection failed",
            }

        sender = (from_email or self.from_email).strip()
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = formataddr((self.app_name, sender))
        msg["To"] = to_email
        msg.attach(MIMEText(text_body or "", "plain", "utf-8"))
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            with self._open_server() as server:
                refused = server.sendmail(sender, [to_email], msg.as_string())
            if refused:
                err = f"SMTP refused recipients: {refused}"
                logger.error(err)
                return {
                    "success": False,
                    "provider": self.name,
                    "message_id": None,
                    "error": err,
                }
            logger.info("SMTP email sent to=%s subject=%s", to_email, subject)
            return {
                "success": True,
                "provider": self.name,
                "message_id": None,
                "error": None,
            }
        except Exception as exc:
            logger.exception("SMTP send failed to=%s error=%s", to_email, exc)
            return {
                "success": False,
                "provider": self.name,
                "message_id": None,
                "error": str(exc),
            }
