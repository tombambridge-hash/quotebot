"""Outbound notifications: email via iCloud SMTP, optional iMessage ping
(macOS only, via osascript)."""

import logging
import platform
import smtplib
import subprocess
from email.mime.text import MIMEText

log = logging.getLogger("quotebot.notify")


def send_email(cfg: dict, to: str, subject: str, body: str) -> None:
    ic = cfg["icloud"]
    msg = MIMEText(body)
    msg["Subject"] = f"[QuoteBot] {subject}"
    msg["From"] = ic["email"]
    msg["To"] = to
    host = ic.get("smtp_host", "smtp.mail.me.com")
    port = int(ic.get("smtp_port", 587))
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(ic["email"], ic["app_password"])
        smtp.send_message(msg)
    log.info("Sent email to %s: %s", to, subject)


def notify_owner(cfg: dict, subject: str, body: str) -> None:
    to = cfg.get("owner", {}).get("notify_email") or cfg["icloud"]["email"]
    try:
        send_email(cfg, to, subject, body)
    except Exception:
        log.exception("Failed to email owner")
    imessage(cfg, f"QuoteBot: {subject}")


def imessage(cfg: dict, text: str) -> None:
    """Optional iMessage ping so new proposals pop up on all Apple devices."""
    handle = cfg.get("owner", {}).get("imessage_handle")
    if not handle or platform.system() != "Darwin":
        return
    script = (
        'tell application "Messages" to send "%s" to buddy "%s" of '
        '(service 1 whose service type is iMessage)'
        % (text.replace('"', "'"), handle)
    )
    try:
        subprocess.run(["osascript", "-e", script], check=True,
                       capture_output=True, timeout=30)
    except Exception:
        log.warning("iMessage ping failed (check Messages automation permission)")
