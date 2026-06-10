"""QuoteBot daemon.

Loop: poll iCloud inbox -> new Formspree leads get parsed, priced with the
Bambridge formula, pushed into Easy-PV via Chrome, and you get an email (and
optional iMessage). Leads missing system details trigger a "what's the spec?"
email you can answer from any device. Owner emails with subject "Bot ..." are
treated as commands (see commands.py / `help`).

Run:  python -m quotebot.main          (or via launchd, see install.sh)
"""

import logging
import logging.handlers
import os
import time
import traceback

from . import commands, config, lead_parser, notify, pricing
from .easypv import EasyPV, EasyPVError
from .email_monitor import Mailbox
from .state import Store

log = logging.getLogger("quotebot")


def setup_logging(log_dir: str) -> None:
    os.makedirs(log_dir, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "quotebot.log"), maxBytes=2_000_000, backupCount=3)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[handler, console])


class Bot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        bot_cfg = cfg.get("bot", {})
        base = os.path.dirname(cfg.get("_config_path") or ".") or "."
        self.store = Store(os.path.join(base, bot_cfg.get("state_db", "state.db")))
        self.mailbox = Mailbox(cfg)
        self.easypv = EasyPV(cfg)
        self.poll = int(bot_cfg.get("poll_seconds", 60))
        self.dry_run = bool(bot_cfg.get("dry_run", False))

    # ------------------------------------------------------------------
    def run_forever(self) -> None:
        if not self.store.get_meta("last_uid"):
            # First run: skip historical mail, only act on what arrives now.
            uid = self.mailbox.current_max_uid()
            self.store.set_meta("last_uid", str(uid))
            log.info("First run — starting from inbox UID %s", uid)
        log.info("QuoteBot watching %s every %ss", self.cfg["icloud"]["email"], self.poll)
        while True:
            try:
                self.tick()
            except Exception:
                log.error("tick failed:\n%s", traceback.format_exc())
            time.sleep(self.poll)

    def tick(self) -> None:
        last_uid = int(self.store.get_meta("last_uid", "0"))
        messages = self.mailbox.fetch_new(last_uid)
        for msg in messages:
            try:
                if msg.is_lead:
                    self.handle_lead(msg)
                elif msg.is_command:
                    self.handle_command(msg)
            except Exception:
                log.error("Failed handling uid %s:\n%s", msg.uid,
                          traceback.format_exc())
            self.store.set_meta("last_uid", str(msg.uid))

    # ------------------------------------------------------------------
    def handle_lead(self, msg) -> None:
        aliases = self.cfg.get("leads", {}).get("field_aliases", {})
        fields = lead_parser.parse_lead(msg.body, aliases)
        lead_id = self.store.add_lead(fields, raw=msg.body)
        log.info("New lead #%s from %s: %s", lead_id, msg.sender,
                 fields.get("name"))
        if lead_parser.has_specs(fields):
            result = self.run_pipeline(lead_id)
            log.info("Pipeline: %s", result.splitlines()[0] if result else "")
        else:
            self.store.update_lead(lead_id, status="awaiting_spec")
            notify.notify_owner(
                self.cfg,
                f"New lead #{lead_id}: {fields.get('name') or 'unknown'} — specs needed",
                "New website lead received:\n\n"
                + self.store.dump_lead(self.store.get_lead(lead_id))
                + "\n\nI couldn't work out the system size from the enquiry.\n"
                  "Reply from any device with subject 'Bot' and a first line like:\n\n"
                  f"    spec {lead_id} panels=12 batteries=1 storeys=2\n\n"
                  "and I'll price it and create the Easy-PV proposal.")

    def handle_command(self, msg) -> None:
        log.info("Command from %s: %s", msg.sender, msg.subject)
        reply = commands.handle(msg.body, self.store, self.cfg, self.run_pipeline)
        notify.send_email(self.cfg, msg.sender, f"Re: {msg.subject}", reply)

    # ------------------------------------------------------------------
    def run_pipeline(self, lead_id: int) -> str:
        """Price the lead and create the Easy-PV proposal. Returns a summary."""
        row = self.store.get_lead(lead_id)
        lead = {k: row[k] for k in row.keys()}
        spec = pricing.JobSpec(
            panels=int(lead.get("panels") or 0),
            batteries=int(lead.get("batteries") or 0),
            roof_faces=int(lead.get("roof_faces") or 1),
            dc_strings=int(lead.get("dc_strings") or 1),
            scaffold=lead.get("scaffold") or "two_storey",
            panel_wattage_kw=float(self.cfg.get("pricing", {}).get("panel_wattage_kw", 0.44)),
            ev_charger=bool(lead.get("ev_charger")),
        )
        if spec.panels <= 0:
            return (f"Lead #{lead_id} has no panel count yet — send: "
                    f"spec {lead_id} panels=12 batteries=1 storeys=2")
        quote = pricing.calculate(spec)
        self.store.update_lead(lead_id, status="quoted",
                               quote_total=quote.total,
                               quote_text=quote.as_text())

        easypv_note, url = "", ""
        if self.dry_run or not self.easypv.enabled:
            easypv_note = "(dry run — Easy-PV step skipped)"
        else:
            try:
                result = self.easypv.create_proposal(lead, quote)
                url = result["url"]
                self.store.update_lead(lead_id, status="proposal_created",
                                       easypv_url=url)
                easypv_note = (f"Easy-PV project created: {url}\n"
                               "Open it to finish the roof design & send the proposal.")
            except EasyPVError as exc:
                self.store.update_lead(lead_id, status="error", error=str(exc))
                easypv_note = (f"Easy-PV step FAILED: {exc}\n"
                               "Quote is below — create the project manually, or "
                               f"fix and email me 'Bot' / 'run {lead_id}' to retry.")

        summary = (f"Lead #{lead_id}: {lead.get('name') or 'unknown'} "
                   f"({lead.get('postcode') or lead.get('address') or 'no address'})\n\n"
                   f"{quote.as_text()}\n\n{easypv_note}")
        notify.notify_owner(self.cfg,
                            f"Lead #{lead_id} {lead.get('name') or ''}: "
                            f"£{quote.total:,} services" + (" + proposal" if url else ""),
                            summary)
        return summary


def main() -> None:
    cfg = config.load()
    setup_logging(cfg.get("bot", {}).get("log_dir", "logs"))
    Bot(cfg).run_forever()


if __name__ == "__main__":
    main()
