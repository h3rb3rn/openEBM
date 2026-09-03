"""
Minimal outbound email via stdlib smtplib — no new dependency, and no
mandatory external service: if SMTP isn't configured, sending is a
logged no-op rather than a crash, so the app still runs fully
air-gapped/offline with password reset simply unavailable until an
admin configures SMTP_HOST.
"""
import logging
import smtplib
from email.mime.text import MIMEText

from ..config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def send_email(to: str, subject: str, body: str) -> bool:
    if not settings.smtp_host:
        logger.warning("SMTP not configured (SMTP_HOST empty) — email to %s not sent: %s", to, subject)
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_from, [to], msg.as_string())
        return True
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to, e)
        return False


def send_password_reset_email(to: str, reset_url: str) -> bool:
    subject = "EBM Analyzer – Passwort zurücksetzen"
    body = (
        f"Es wurde eine Passwort-Zurücksetzung für Ihr EBM-Analyzer-Konto angefordert.\n\n"
        f"Falls Sie das nicht waren, ignorieren Sie diese E-Mail — es passiert nichts weiter.\n\n"
        f"Zum Zurücksetzen des Passworts folgen Sie diesem Link (gültig 1 Stunde):\n"
        f"{reset_url}\n"
    )
    return send_email(to, subject, body)
