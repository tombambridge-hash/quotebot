"""iCloud IMAP monitor.

Connects to imap.mail.me.com with an app-specific password, tracks the last
processed UID so nothing is missed even if mail is read on another device,
and classifies new messages as Formspree leads or owner commands.
"""

import email
import email.header
import email.message
import email.utils
import imaplib
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger("quotebot.mail")


@dataclass
class InboundMail:
    uid: int
    sender: str
    subject: str
    body: str
    message_id: str
    is_lead: bool
    is_command: bool


def _decode_header(value: str) -> str:
    parts = email.header.decode_header(value or "")
    out = []
    for text, charset in parts:
        if isinstance(text, bytes):
            out.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    html = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|table)>", "\n", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    # Keep "label: value" structure from table cells
    html = re.sub(r"(?i)</t[dh]>\s*<t[dh][^>]*>", ": ", html)
    text = re.sub(r"<[^>]+>", "", html)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&#39;", "'").replace("&quot;", '"'))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def extract_body(msg: email.message.Message) -> str:
    plain, html = None, None
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8",
                                  errors="replace")
        except Exception:
            continue
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and html is None:
            html = text
    if plain and plain.strip():
        return plain
    if html:
        return _html_to_text(html)
    return ""


class Mailbox:
    def __init__(self, cfg: dict):
        ic = cfg["icloud"]
        self.host = ic.get("imap_host", "imap.mail.me.com")
        self.port = int(ic.get("imap_port", 993))
        self.user = ic["email"]
        self.password = ic["app_password"]
        leads = cfg.get("leads", {})
        self.lead_senders = [s.lower() for s in
                             leads.get("formspree_senders", ["formspree.io"])]
        owner = cfg.get("owner", {})
        self.command_senders = [s.lower() for s in
                                owner.get("command_senders", [self.user])]

    def fetch_new(self, last_uid: int) -> List[InboundMail]:
        conn = imaplib.IMAP4_SSL(self.host, self.port)
        try:
            conn.login(self.user, self.password)
            conn.select("INBOX")
            typ, data = conn.uid("search", None, f"UID {last_uid + 1}:*")
            if typ != "OK" or not data or not data[0]:
                return []
            results = []
            for uid_b in data[0].split():
                uid = int(uid_b)
                if uid <= last_uid:  # servers return >=1 result even if none newer
                    continue
                typ, msg_data = conn.uid("fetch", uid_b, "(BODY.PEEK[])")
                if typ != "OK" or not msg_data or msg_data[0] is None:
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                sender = email.utils.parseaddr(msg.get("From", ""))[1].lower()
                subject = _decode_header(msg.get("Subject", ""))
                body = extract_body(msg)
                is_lead = any(s in sender for s in self.lead_senders)
                is_command = (
                    not is_lead
                    and sender in self.command_senders
                    and subject.strip().lower().startswith(
                        ("bot", "re: [quotebot]", "quotebot", "fwd: "))
                )
                results.append(InboundMail(
                    uid=uid, sender=sender, subject=subject, body=body,
                    message_id=msg.get("Message-ID", ""),
                    is_lead=is_lead, is_command=is_command))
            return results
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def current_max_uid(self) -> int:
        """Used on first run so the bot only handles mail from now on."""
        conn = imaplib.IMAP4_SSL(self.host, self.port)
        try:
            conn.login(self.user, self.password)
            conn.select("INBOX")
            typ, data = conn.uid("search", None, "ALL")
            if typ == "OK" and data and data[0]:
                return int(data[0].split()[-1])
            return 0
        finally:
            try:
                conn.logout()
            except Exception:
                pass
