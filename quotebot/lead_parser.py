"""Parse Formspree lead-notification emails into structured lead fields.

Formspree's website form sends fields in a **two-line** layout — the field
label on one line, the value on the next:

    address
    12 Walden Grange Close, Newport, NP19 8AZ
    property_type
    terraced

This parser handles that two-line format **and** the older "label: value"
single-line format (which is also what email_monitor produces when it flattens
an HTML table), so forwarded/legacy emails keep working. Field labels vary by
form, so config.yaml can map your form's labels to canonical fields via
leads.field_aliases.

Postcode is not a separate Formspree field on the current form — it is embedded
in the address line — so it is extracted from the address (or the whole body)
with a UK-postcode regex.
"""

import re
from typing import Any, Dict, List, Optional

from . import pricing

# Canonical field -> accepted labels (written in either "two words" or
# "two_words" form; both normalise to the same key, so you only need one).
DEFAULT_ALIASES: Dict[str, List[str]] = {
    "name": ["name", "full name", "fullname", "your name", "first name",
             "customer name", "contact name"],
    "email": ["email", "email address", "e-mail", "_replyto", "your email"],
    "phone": ["phone", "phone number", "telephone", "mobile", "contact number",
              "tel"],
    "address": ["address", "property address", "site address", "street address",
                "full address"],
    "postcode": ["postcode", "post code", "zip", "zip code", "postal code"],
    "property_type": ["property type", "house type", "building type", "property",
                      "dwelling type"],
    "ownership": ["ownership", "ownership status", "own or rent", "tenure"],
    "year_built": ["year built", "build year", "year of construction",
                   "property age", "age of property"],
    "epc_rating": ["epc rating", "epc", "energy rating", "epc band"],
    "bedrooms": ["bedrooms", "number of bedrooms", "no of bedrooms", "beds"],
    "occupants": ["occupants", "number of occupants", "people in home",
                  "household size", "residents"],
    "roof_type": ["roof type", "roof material", "roof covering"],
    "roof_orientation": ["roof orientation", "orientation", "roof direction",
                         "roof aspect", "aspect"],
    "roof_pitch": ["roof pitch", "pitch", "roof angle", "roof slope"],
    "roof_size": ["roof size", "roof area", "roof dimensions",
                  "available roof area"],
    "shading": ["shading", "shade", "shading issues", "obstructions"],
    "roof_age": ["roof age", "age of roof"],
    "roof_notes": ["roof notes", "roof comments", "additional roof info",
                   "roof details"],
    "annual_kwh": ["annual kwh", "annual usage", "annual consumption",
                   "yearly kwh", "annual electricity usage", "kwh per year"],
    "monthly_bill": ["monthly bill", "monthly cost", "electricity bill",
                     "monthly electricity bill", "average monthly bill", "bill"],
    "interested_in": ["interested in", "interest", "products", "looking for",
                      "system type"],
    "preferred_size": ["preferred size", "system size", "desired size", "size",
                       "preferred system size"],
    "timeline": ["timeline", "timescale", "when", "time frame", "timeframe",
                 "urgency"],
    "message": ["message", "details", "comments", "enquiry", "notes",
                "additional information", "more info"],
    "panels": ["panels", "number of panels", "panel count", "no of panels",
               "how many panels"],
    "batteries": ["batteries", "number of batteries", "battery",
                  "battery count", "no of batteries", "how many batteries"],
}

# Fields that are part of the rich Formspree form but have no dedicated DB
# column — they are preserved in the lead's JSON `details` blob.
EXTRA_FIELDS = [
    "property_type", "ownership", "year_built", "epc_rating", "bedrooms",
    "occupants", "roof_type", "roof_orientation", "roof_pitch", "roof_size",
    "shading", "roof_age", "roof_notes", "annual_kwh", "monthly_bill",
    "interested_in", "preferred_size", "timeline",
]

COLON_RE = re.compile(
    r"^\s*\*?\s*([A-Za-z][A-Za-z0-9 _'/-]{0,40}?)\s*[:：]\s*(.+?)\s*$")
POSTCODE_RE = re.compile(
    r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", re.IGNORECASE)
PHONE_RE = re.compile(
    r"\b(\+?44\s?7\d{3}|07\d{3}|0\d{3,4})[\s-]?\d{3}[\s-]?\d{3,4}\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
INT_RE = re.compile(r"\d+")


def _normalise(label: str) -> str:
    """Canonicalise a label for matching: lowercase, drop bullets/quote marks,
    turn underscores/hyphens into spaces, collapse whitespace, drop a trailing
    colon. So 'Property_Type', 'property type' and '*Property Type:' all match."""
    s = (label or "").strip().lstrip(">").strip().lstrip("*").strip()
    s = s.rstrip(":：").strip()
    s = s.replace("_", " ").replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def _build_alias_map(aliases: Optional[Dict[str, list]]) -> Dict[str, str]:
    merged = {k: list(v) for k, v in DEFAULT_ALIASES.items()}
    for key, names in (aliases or {}).items():
        merged[key] = list(names) + merged.get(key, [])
    alias_map: Dict[str, str] = {}
    for canonical, names in merged.items():
        # The canonical name itself is always a valid label.
        for name in [canonical, *names]:
            norm = _normalise(name)
            if norm and norm not in alias_map:
                alias_map[norm] = canonical
    return alias_map


def parse_lead(body: str, aliases: Optional[Dict[str, list]] = None) -> Dict[str, Any]:
    alias_map = _build_alias_map(aliases)
    fields: Dict[str, Any] = {}

    def set_field(canonical: str, value: str) -> None:
        value = (value or "").strip().lstrip(">").strip()
        if canonical and value and canonical not in fields:
            fields[canonical] = value

    def is_label(line: str) -> bool:
        norm = _normalise(line)
        if norm in alias_map:
            return True
        m = COLON_RE.match(line.strip())
        return bool(m and _normalise(m.group(1)) in alias_map)

    lines = body.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i].strip()
        if not raw:
            i += 1
            continue

        # Case 1: "label: value" on a single line (legacy / flattened HTML).
        m = COLON_RE.match(raw.lstrip(">").strip())
        if m and _normalise(m.group(1)) in alias_map:
            set_field(alias_map[_normalise(m.group(1))], m.group(2))
            i += 1
            continue

        # Case 2: a bare label line — the value is on the next non-blank line
        # (the current Formspree two-line format).
        norm = _normalise(raw)
        if norm in alias_map:
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j < n and not is_label(lines[j]):
                set_field(alias_map[norm], lines[j])
                i = j + 1
                continue

        i += 1

    _fill_fallbacks(fields, body)
    return fields


def _fill_fallbacks(fields: Dict[str, Any], body: str) -> None:
    # Postcode: prefer the address line, then the whole body.
    if not fields.get("postcode"):
        for source in (fields.get("address"), body):
            if not source:
                continue
            m = POSTCODE_RE.search(source)
            if m:
                fields["postcode"] = re.sub(r"\s+", " ", m.group(1)).upper()
                break
    if not fields.get("phone"):
        m = PHONE_RE.search(body)
        if m:
            fields["phone"] = m.group(0)
    if not fields.get("email"):
        m = EMAIL_RE.search(body)
        if m and "formspree" not in m.group(0).lower():
            fields["email"] = m.group(0)

    # Numeric coercion for system-size hints.
    for key in ("panels", "batteries"):
        if key in fields and not isinstance(fields[key], int):
            m = INT_RE.search(str(fields[key]))
            fields[key] = int(m.group(0)) if m else None

    # Infer panel/battery counts from free text (message, preferred size, body).
    text = " ".join(str(v) for v in (fields.get("message"),
                                     fields.get("preferred_size"),
                                     fields.get("interested_in"), body) if v)
    if not isinstance(fields.get("panels"), int):
        m = re.search(r"(\d{1,2})\s*(?:solar\s*)?panels?", text, re.IGNORECASE)
        if m:
            fields["panels"] = int(m.group(1))
    if not isinstance(fields.get("batteries"), int):
        m = re.search(r"(\d{1,2})\s*batter(?:y|ies)", text, re.IGNORECASE)
        if m:
            fields["batteries"] = int(m.group(1))
        elif re.search(r"\b(no battery|without battery|no batteries)\b",
                       text, re.IGNORECASE):
            fields["batteries"] = 0

    # Scaffolding rate from the property type (or any storey hint in the text).
    prop = fields.get("property_type") or fields.get("message") or body
    fields["scaffold"] = pricing.scaffold_from_text(prop)


def details_blob(fields: Dict[str, Any]) -> Dict[str, Any]:
    """The rich form fields that don't have dedicated DB columns, for storage
    in the lead's JSON `details` column."""
    return {k: fields[k] for k in EXTRA_FIELDS if fields.get(k) is not None}


def has_address(fields: Dict[str, Any]) -> bool:
    """Enough location detail to create an Easy-PV project and run the design?
    The v2 pipeline proceeds on address + postcode alone (no panel count gate)."""
    return bool(fields.get("address")) and bool(fields.get("postcode"))


def has_specs(fields: Dict[str, Any]) -> bool:
    """Whether a panel count is known (used by the manual command channel to
    decide if it can price the job without asking)."""
    return isinstance(fields.get("panels"), int) and fields["panels"] > 0
