"""Abstract email provider contract."""


class EmailProvider:
    """Replaceable email backend. Auth logic never imports a concrete vendor."""

    name = "base"

    def send(self, to_email, subject, text_body, html_body=None, from_email=None):
        """
        Send one email.
        Returns dict: {"success": bool, "provider": str, "message_id": str|None, "error": str|None}
        """
        raise NotImplementedError

    def health_check(self):
        return True
