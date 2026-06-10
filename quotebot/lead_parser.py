"""Parse Formspree lead-notification emails into structured lead fields.

Formspree notifications list submitted form fields as "label: value" lines
(or an HTML table, which email_monitor flattens to the same shape). Field
names vary by form, so config.yaml maps your form's labels to canonical
fields via leads.field_aliases.
"""

import re
from typing import Any, Dict, Optional

from . import pricing

DEFAULT_ALIASES = {
    "name": ["name", "full name", "fullname", "your name", "first name"],
    "email": ["email", "email address", "_replyto", "e-mail"],
    "phone": ["phone", "phone number", "telephone", "mobile", "contact number"],
    "address": ["address", "property address", "site address", "street address"],
    "postcode": ["postcode", "post code", "zip", "zip code"],
    "message": ["message", "details", "comments", "enquiry", "notes",
                "additional information"],
    "panels": ["panels", "number of panels", "panel count", "no of panels"],
    "batteries": ["batteries", "number of batteries", "battery",
                  "battery count", "no of batteries"],
    "property": ["property type", "house type", "storeys", "stories",
                 "property", "building type"],
}

LINE_RE = re.compile(r"^\s*\*?\s*([A-Za-z][A-Za-z0-9 _'/-]{0,40}?)\s*[:：]\s*(.+?)\s*$")
POSTCODE_RE = re.compile(
    r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", re.IGNORECASE)
PHONE_RE = re.compile(r"\b(\+?44\s?7\d{3}|07\d{3}|0\d{3,4})[\s-]?\d{3}[\s-]?\d{3,4}\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
INT_RE = re.compile(r"\d+")


def parse_lead(body: str, aliases: Optional[Dict[str, list]] = None) -> Dict[str, Any]:
    alias_map = {}
    merged = dict(DEFAULT_ALIASES)
    for key, names in (aliases or {}).items():
        merged[key] = list(names) + merged.get(key, [])
    for canonical, names in merged.items():
        for n in names:
            alias_map[n.lower()] = canonical

    fields: Dict[str, Any] = {}
    for line in body.splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        label, value = m.group(1).strip().lower(), m.group(2).strip()
        canonical = alias_map.get(label)
        if canonical and canonical not in fields and value:
            fields[canonical] = value

    # Fallbacks scanning the whole body
    if "postcode" not in fields:
        m = POSTCODE_RE.search(body)
        if m:
            fields["postcode"] = m.group(1).upper()
    if "phone" not in fields:
        m = PHONE_RE.search(body)
        if m:
            fields["phone"] = m.group(0)
    if "email" not in fields:
        m = EMAIL_RE.search(body)
        if m and "formspree" not in m.group(0):
            fields["email"] = m.group(0)

    # Numeric coercion
    for key in ("panels", "batteries"):
        if key in fields:
            m = INT_RE.search(str(fields[key]))
            fields[key] = int(m.group(0)) if m else None

    # Try to infer specs from the free-text message too
    text = " ".join(str(v) for v in (fields.get("message"), body) if v)
    if fields.get("panels") is None or "panels" not in fields:
        m = re.search(r"(\d{1,2})\s*(?:solar\s*)?panels?", text, re.IGNORECASE)
        if m:
            fields["panels"] = int(m.group(1))
    if fields.get("batteries") is None or "batteries" not in fields:
        m = re.search(r"(\d{1,2})\s*batter(?:y|ies)", text, re.IGNORECASE)
        if m:
            fields["batteries"] = int(m.group(1))
        elif re.search(r"\bno battery|without battery\b", text, re.IGNORECASE):
            fields["batteries"] = 0

    prop = fields.get("property") or text
    fields["scaffold"] = pricing.scaffold_from_text(prop)
    return fields


def has_specs(fields: Dict[str, Any]) -> bool:
    """Enough info to price the job without asking Tom?"""
    return isinstance(fields.get("panels"), int) and fields["panels"] > 0
