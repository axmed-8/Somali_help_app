"""
Configurable email delivery for GurmadNet.

Business/auth code must use send_email() only — never a vendor SDK directly.
Providers: smtp/gmail (real SMTP), memory (tests), console (local debug).
"""
from email_service.env_loader import load_email_env

load_email_env()

from email_service.factory import get_email_provider, list_email_providers
from email_service.service import send_email, send_verification_email, is_valid_email_format, signup_email_rejection_reason

__all__ = [
    "get_email_provider",
    "list_email_providers",
    "send_email",
    "send_verification_email",
    "is_valid_email_format",
    "signup_email_rejection_reason",
    "load_email_env",
]
