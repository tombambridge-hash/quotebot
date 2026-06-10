"""Email command channel.

Email the bot from any of your devices (iPhone, iPad, another Mac — anything
signed into your Apple account can send from your iCloud address). Subject
must start with "Bot" (or reply to a [QuoteBot] email). First non-blank line
of the body is the command:

    help                                  show commands
    status                                recent leads & their state
    show 3                                full details for lead #3
    price panels=12 batteries=1 scaffold=two_storey ev=1
    spec 3 panels=12 batteries=1          set job specs for lead #3
                                          (then bot prices it + creates the
                                          Easy-PV proposal automatically)
    run 3                                 (re)price lead #3 & create proposal
    ignore 3                              mark lead #3 ignored
"""

import logging
import re
import shlex
from typing import Any, Callable, Dict, Optional

from . import pricing
from .state import Store

log = logging.getLogger("quotebot.commands")

HELP = __doc__.split("command:", 1)[1]

KV_RE = re.compile(r"^([a-z_]+)\s*=\s*(.+)$", re.IGNORECASE)

SPEC_KEYS = {"panels", "batteries", "scaffold", "roof_faces", "dc_strings",
             "ev", "ev_charger", "storeys"}


def _parse_kv(tokens) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for tok in tokens:
        m = KV_RE.match(tok)
        if not m:
            continue
        key, val = m.group(1).lower(), m.group(2)
        if key == "ev":
            key = "ev_charger"
        if key == "storeys":
            key, val = "scaffold", pricing.scaffold_from_text(val)
        if key in ("panels", "batteries", "roof_faces", "dc_strings", "ev_charger"):
            try:
                out[key] = int(re.sub(r"\D", "", val) or 0)
            except ValueError:
                continue
        else:
            out[key] = val.strip().lower().replace(" ", "_").replace("-", "_")
    return out


def _spec_from(fields: Dict[str, Any], cfg: dict) -> pricing.JobSpec:
    defaults = cfg.get("pricing", {}).get("defaults", {})
    return pricing.JobSpec(
        panels=int(fields.get("panels") or 0),
        batteries=int(fields.get("batteries") or 0),
        roof_faces=int(fields.get("roof_faces") or defaults.get("roof_faces", 1)),
        dc_strings=int(fields.get("dc_strings") or defaults.get("dc_strings", 1)),
        scaffold=fields.get("scaffold") or defaults.get("scaffold", "two_storey"),
        panel_wattage_kw=float(cfg.get("pricing", {}).get("panel_wattage_kw", 0.44)),
        ev_charger=bool(fields.get("ev_charger")),
    )


def handle(body: str, store: Store, cfg: dict,
           run_pipeline: Callable[[int], str]) -> str:
    """Execute a command email body; return the reply text."""
    line = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    # Strip quoted-reply junk like "> " prefixes
    line = line.lstrip("> ").strip()
    if not line:
        return "Empty command.\n" + HELP
    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = line.split()
    cmd = tokens[0].lower()

    if cmd in ("help", "?"):
        return HELP

    if cmd == "status":
        rows = store.recent_leads(10)
        if not rows:
            return "No leads yet."
        return "Recent leads:\n" + "\n".join(store.lead_summary(r) for r in rows)

    if cmd == "show" and len(tokens) > 1 and tokens[1].isdigit():
        row = store.get_lead(int(tokens[1]))
        return store.dump_lead(row) if row else f"No lead #{tokens[1]}"

    if cmd == "price":
        kv = _parse_kv(tokens[1:])
        if not kv.get("panels"):
            return "Usage: price panels=12 batteries=1 scaffold=two_storey ev=1"
        quote = pricing.calculate(_spec_from(kv, cfg))
        return quote.as_text()

    if cmd == "spec" and len(tokens) > 2 and tokens[1].isdigit():
        lead_id = int(tokens[1])
        if not store.get_lead(lead_id):
            return f"No lead #{lead_id}"
        kv = {k: v for k, v in _parse_kv(tokens[2:]).items() if k in SPEC_KEYS - {"ev", "storeys"}}
        if not kv:
            return "Usage: spec <id> panels=12 batteries=1 storeys=2"
        store.update_lead(lead_id, **kv)
        return run_pipeline(lead_id)

    if cmd == "run" and len(tokens) > 1 and tokens[1].isdigit():
        lead_id = int(tokens[1])
        if not store.get_lead(lead_id):
            return f"No lead #{lead_id}"
        return run_pipeline(lead_id)

    if cmd == "ignore" and len(tokens) > 1 and tokens[1].isdigit():
        store.update_lead(int(tokens[1]), status="ignored")
        return f"Lead #{tokens[1]} ignored."

    return f"Unknown command: {line}\n" + HELP
