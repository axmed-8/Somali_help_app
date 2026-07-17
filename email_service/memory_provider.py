"""In-memory email provider for automated tests (same send() contract as SMTP)."""
from email_service.base import EmailProvider

# Process-wide outbox for assertions in pytest
OUTBOX = []


class MemoryEmailProvider(EmailProvider):
    name = "memory"

    def send(self, to_email, subject, text_body, html_body=None, from_email=None):
        record = {
            "to": to_email,
            "subject": subject,
            "text": text_body or "",
            "html": html_body or "",
            "from": from_email or "test@gurmadnet.local",
        }
        OUTBOX.append(record)
        return {
            "success": True,
            "provider": self.name,
            "message_id": f"mem-{len(OUTBOX)}",
            "error": None,
        }

    def health_check(self):
        return True


def clear_outbox():
    OUTBOX.clear()
