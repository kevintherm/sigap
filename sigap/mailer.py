"""Outgoing email for student login codes (SMTP settings from .env).

    SIGAP_SMTP_HOST, SIGAP_SMTP_PORT (587 STARTTLS / 465 SSL / 25 plain), SIGAP_SMTP_USER,
    SIGAP_SMTP_PASSWORD, SIGAP_SMTP_FROM, optional SIGAP_SMTP_SECURITY=starttls|ssl|none.
    SIGAP_EMAIL_DEV=1 (development only): no SMTP, the message is written to the server log instead.
"""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("sigap.mail")


def configured() -> bool:
    return bool(os.environ.get("SIGAP_SMTP_HOST")) or os.environ.get("SIGAP_EMAIL_DEV") == "1"


def send(to: str, subject: str, body: str) -> bool:
    host = os.environ.get("SIGAP_SMTP_HOST")
    if not host:
        if os.environ.get("SIGAP_EMAIL_DEV") == "1":
            log.warning("DEV EMAIL to %s · %s\n%s", to, subject, body)
            return True
        log.error("Email not sent: SIGAP_SMTP_HOST is not set")
        return False
    port = int(os.environ.get("SIGAP_SMTP_PORT", "587"))
    security = os.environ.get("SIGAP_SMTP_SECURITY") or {465: "ssl", 25: "none", 1025: "none"}.get(port, "starttls")
    msg = EmailMessage()
    msg["From"] = os.environ.get("SIGAP_SMTP_FROM") or os.environ.get("SIGAP_SMTP_USER") or "sigap@localhost"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
        with server:
            if os.environ.get("SIGAP_SMTP_USER"):
                server.login(os.environ["SIGAP_SMTP_USER"], os.environ.get("SIGAP_SMTP_PASSWORD", ""))
            server.send_message(msg)
        # Accepted by our SMTP server only; the recipient's provider can still reject it (check SPF/DMARC of From).
        log.info("Email to %s accepted by %s (from %s)", to, host, msg["From"])
        return True
    except (OSError, smtplib.SMTPException) as e:
        log.error("Email to %s failed: %s", to, e)
        return False
