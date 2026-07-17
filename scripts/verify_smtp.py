"""
Verify Gmail/SMTP settings from .env without starting the full Flask app.

Usage (from project root):
  python scripts/verify_smtp.py
  python scripts/verify_smtp.py --send you@gmail.com
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from email_service.env_loader import load_email_env, reset_email_env_loader
from email_service.factory import clear_email_provider_cache, get_email_provider


def main():
    parser = argparse.ArgumentParser(description="Verify GurmadNet SMTP configuration")
    parser.add_argument(
        "--send",
        metavar="EMAIL",
        help="Also send a test verification-style email to this address",
    )
    args = parser.parse_args()

    reset_email_env_loader()
    clear_email_provider_cache()
    # Force SMTP check even if shell has EMAIL_PROVIDER=memory from pytest
    os.environ["EMAIL_PROVIDER"] = "smtp"
    load_email_env(ROOT)

    print("EMAIL_PROVIDER =", os.environ.get("EMAIL_PROVIDER", "smtp"))
    print("SMTP_HOST     =", os.environ.get("SMTP_HOST", ""))
    print("SMTP_PORT     =", os.environ.get("SMTP_PORT", ""))
    print("SMTP_USER     =", os.environ.get("SMTP_USER", ""))
    print("SMTP_FROM     =", os.environ.get("SMTP_FROM", ""))
    pw = os.environ.get("SMTP_PASSWORD", "")
    print("SMTP_PASSWORD =", "(set)" if pw and "your_gmail" not in pw else "(missing/placeholder)")

    provider = get_email_provider("smtp", force_new=True)
    result = provider.verify_connection()
    if result.get("success"):
        print("OK: SMTP connection verified.")
    else:
        print("FAIL:", result.get("error"))
        return 1

    if args.send:
        from email_service import send_verification_email

        send = send_verification_email(
            to_email=args.send,
            verify_url="http://127.0.0.1:5000/verify-email/test-token-demo",
            user_name="SMTP Test",
        )
        if send.get("success"):
            print("OK: Test email sent to", args.send)
        else:
            print("FAIL send:", send.get("error"))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
