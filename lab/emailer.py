"""Email delivery over SMTP (provider-agnostic; works with a Gmail app password).

All configuration comes from the environment via config.Email — no address or
credential is committed. If SMTP isn't configured, sending is a no-op that
returns False, so the loop never crashes on a missing email setup.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .config import Email


def build_message(email: Email, subject: str, html: str, text: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email.from_ or email.user
    msg["To"] = email.to
    msg.set_content(text or "This is an HTML email; view it in an HTML-capable client.")
    msg.add_alternative(html, subtype="html")
    return msg


def send(email: Email, subject: str, html: str, text: str = "", *, smtp=None) -> bool:
    """Send an email. Returns True on success, False if unconfigured or on error.

    ``smtp`` is injectable (a fake with .starttls/.login/.send_message/.quit) for tests.
    """
    if not email.configured:
        return False
    msg = build_message(email, subject, html, text)
    try:
        server = smtp or smtplib.SMTP(email.host, email.port, timeout=30)
        try:
            server.starttls()
        except Exception:
            pass  # some servers/ports don't use STARTTLS
        if email.user and email.password:
            server.login(email.user, email.password)
        server.send_message(msg)
        try:
            server.quit()
        except Exception:
            pass
        return True
    except Exception:
        return False
