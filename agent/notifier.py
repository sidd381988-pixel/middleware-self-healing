"""
Email notifications via smtplib (TLS or plain).
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, cfg: dict):
        email_cfg = cfg.get("email", {})
        self._host = email_cfg.get("smtp_host", "localhost")
        self._port = int(email_cfg.get("smtp_port", 587))
        self._user = email_cfg.get("smtp_user", "")
        self._password = email_cfg.get("smtp_password", "")
        self._from = email_cfg.get("from_addr", self._user)
        self._admins = email_cfg.get("admin_addrs", [])
        self._use_tls = email_cfg.get("use_tls", True)

    def notify_admin(self, subject: str, body: str) -> dict:
        """Send an email to all configured admin addresses."""
        if not self._admins:
            logger.warning("No admin email addresses configured — skipping notification")
            return {"success": False, "output": "No admin addresses configured"}

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[Middleware Agent] {subject}"
        msg["From"] = self._from
        msg["To"] = ", ".join(self._admins)
        msg["Date"] = _rfc2822_now()

        full_body = (
            f"{body}\n\n"
            f"--\n"
            f"Sent by middleware-self-healing agent at {_iso_now()}"
        )
        msg.attach(MIMEText(full_body, "plain"))

        try:
            if self._use_tls:
                server = smtplib.SMTP(self._host, self._port, timeout=15)
                server.ehlo()
                server.starttls()
                server.ehlo()
            else:
                server = smtplib.SMTP(self._host, self._port, timeout=15)

            if self._user and self._password:
                server.login(self._user, self._password)

            server.sendmail(self._from, self._admins, msg.as_string())
            server.quit()

            logger.info("Notification sent to: %s | Subject: %s", self._admins, subject)
            return {"success": True, "output": f"Email sent to {self._admins}"}
        except Exception as e:
            logger.error("Failed to send notification: %s", e)
            return {"success": False, "output": str(e)}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rfc2822_now() -> str:
    from email.utils import formatdate
    return formatdate(localtime=True)
