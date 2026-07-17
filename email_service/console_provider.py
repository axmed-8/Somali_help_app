"""Console email provider for local debugging without SMTP credentials."""
import logging

from email_service.base import EmailProvider

logger = logging.getLogger(__name__)


class ConsoleEmailProvider(EmailProvider):
    name = "console"

    def send(self, to_email, subject, text_body, html_body=None, from_email=None):
        logger.info(
            "CONSOLE EMAIL to=%s subject=%s\n%s",
            to_email,
            subject,
            text_body or "",
        )
        print("----- GurmadNet email (console provider) -----")
        print("To:", to_email)
        print("Subject:", subject)
        print(text_body or "")
        print("----------------------------------------------")
        return {
            "success": True,
            "provider": self.name,
            "message_id": "console",
            "error": None,
        }
