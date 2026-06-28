"""QuoteBot v2 daemon.

Loop: poll the iCloud inbox every 60s. New Formspree leads are parsed; if the
lead has an address + postcode the bot creates an Easy-PV project via the API,
hands a dedicated Chrome window to Claude Computer Use to complete the design
and generate the customer proposal, polls the API to confirm the proposal PDF
exists, and emails the owner the result. If Computer Use fails or times out, the
owner is always emailed the project URL to finish manually — the bot never
silently fails. Owner emails with subject "Bot ..." are treated as commands
(see commands.py / `help`).

Run:  python -m quotebot.main          (or via launchd, see install.sh)
"""

import logging
import logging.handlers
import os
import time
import traceback

from . import commands, config, lead_parser, notify, pricing
from .email_monitor import Mailbox
from .providers import ProviderError, get_provider
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
        self.provider = get_provider(cfg)
        self.provider_name = {"easypv": "Easy-PV", "pylon": "Pylon"}.get(
            (cfg.get("provider") or "easypv").lower(), cfg.get("provider") or "provider")
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
            # Record the UID BEFORE processing so a crash mid-pipeline can't
            # cause the same email to be picked up and re-processed on restart.
            self.store.set_meta("last_uid", str(msg.uid))
            try:
                if msg.is_lead:
                    self.handle_lead(msg)
                elif msg.is_command:
                    self.handle_command(msg)
            except Exception:
                log.error("Failed handling uid %s:\n%s", msg.uid,
                          traceback.format_exc())

    # ------------------------------------------------------------------
    def handle_lead(self, msg) -> None:
        aliases = self.cfg.get("leads", {}).get("field_aliases", {})
        fields = lead_parser.parse_lead(msg.body, aliases)
        lead_id = self.store.add_lead(fields, raw=msg.body)
        log.info("New lead #%s from %s: %s (%s)", lead_id, msg.sender,
                 fields.get("name"), fields.get("postcode"))
        # v2: proceed to Easy-PV as long as we have an address + postcode — no
        # panel-count gate (that gate flagged every lead as awaiting_spec).
        if lead_parser.has_address(fields):
            result = self.run_pipeline(lead_id)
            log.info("Pipeline #%s: %s", lead_id,
                     result.splitlines()[0] if result else "")
        else:
            self.store.update_lead(lead_id, status="awaiting_info")
            notify.notify_owner(
                self.cfg,
                f"New lead #{lead_id}: {fields.get('name') or 'unknown'} — "
                "address needed",
                "New website lead received:\n\n"
                + self.store.dump_lead(self.store.get_lead(lead_id))
                + "\n\nI couldn't read a full address + postcode from the "
                  "enquiry, so I can't create the project automatically.\n"
                  "Reply from any device with subject 'Bot' and the address, or "
                  f"once it's added, send:  run {lead_id}")

    def handle_command(self, msg) -> None:
        log.info("Command from %s: %s", msg.sender, msg.subject)
        reply = commands.handle(msg.body, self.store, self.cfg,
                                self.run_pipeline, subject=msg.subject)
        notify.send_email(self.cfg, msg.sender, f"Re: {msg.subject}", reply)

    # ------------------------------------------------------------------
    def run_pipeline(self, lead_id: int) -> str:
        """Create the Easy-PV project, run Computer Use to generate the proposal,
        confirm it via API polling, and email the owner. Returns a summary."""
        row = self.store.get_lead(lead_id)
        if row is None:
            return f"No lead #{lead_id}"
        lead = {k: row[k] for k in row.keys()}

        if not lead_parser.has_address(lead):
            self.store.update_lead(lead_id, status="awaiting_info")
            return (f"Lead #{lead_id} has no address/postcode yet — reply with "
                    "the address so I can create the project.")

        quote_note = self._pricing_note(lead)

        if self.dry_run or not self.provider.enabled:
            summary = (f"Lead #{lead_id}: {lead.get('name') or 'unknown'} "
                       f"({lead.get('postcode')})\n(dry run — {self.provider_name} skipped)\n\n{quote_note}")
            notify.notify_owner(self.cfg, f"Lead #{lead_id} (dry run)", summary)
            return summary

        # 1. Create the Easy-PV project via the API.
        try:
            project_id = self.provider.create_project(lead)
        except ProviderError as exc:
            self.store.update_lead(lead_id, status="error", error=str(exc))
            summary = (f"Lead #{lead_id}: couldn't create the {self.provider_name} project.\n"
                       f"{exc}\n\nCreate it manually and reply 'run {lead_id}' to retry.")
            notify.notify_owner(self.cfg,
                                f"Lead #{lead_id} — {self.provider_name} project FAILED", summary)
            return summary

        url = self.provider.project_url(project_id)
        self.store.update_lead(lead_id, status="project_created",
                               easypv_project_id=project_id, easypv_url=url)

        # 2. Computer Use completes the design + generates the proposal.
        cu_error = ""
        try:
            self.provider.generate_proposal(project_id, lead)
        except Exception as exc:
            cu_error = str(exc)
            log.error("Computer Use failed for lead #%s: %s", lead_id, exc)

        # 3. Confirm the proposal PDF exists via API polling (only worth it if
        #    Computer Use actually reached the generate step).
        confirmed = False
        if not cu_error:
            try:
                confirmed = self.provider.wait_for_proposal(project_id)
            except Exception as exc:
                log.error("Proposal polling failed for lead #%s: %s", lead_id, exc)

        if confirmed:
            self.store.update_lead(lead_id, status="proposal_generated",
                                   proposal_confirmed=1, error=None)
            summary = (f"New lead #{lead_id} processed — {self.provider_name} proposal "
                       f"generated.\nProject: {url}\n\n{_lead_line(lead)}\n\n{quote_note}")
            notify.notify_owner(
                self.cfg,
                f"New lead #{lead_id} processed — proposal generated",
                summary)
            return summary

        # 4. Fallback — never leave the owner unaware.
        detail = cu_error or ("Computer Use finished but the customerProposal "
                              "PDF did not appear within the polling window.")
        self.store.update_lead(lead_id, status="needs_manual", error=detail)
        summary = (f"Computer Use failed — please complete this proposal "
                   f"manually: {url}\n\nLead #{lead_id}: {_lead_line(lead)}\n"
                   f"Reason: {detail}\n\n{quote_note}")
        notify.notify_owner(
            self.cfg,
            f"Lead #{lead_id} — finish proposal manually",
            summary)
        return summary

    # ------------------------------------------------------------------
    def _pricing_note(self, lead: dict) -> str:
        """Bambridge services quote, included for the owner's reference when a
        panel count is known. Never gates the pipeline."""
        panels = int(lead.get("panels") or 0)
        if panels <= 0:
            return ("(No panel count on this lead — the design tool's own proposal "
                    "covers the pricing. Send 'spec %s panels=.. batteries=..' "
                    "for a Bambridge services quote.)" % lead.get("id", "X"))
        spec = pricing.JobSpec(
            panels=panels,
            batteries=int(lead.get("batteries") or 0),
            roof_faces=int(lead.get("roof_faces") or 1),
            dc_strings=int(lead.get("dc_strings") or 1),
            scaffold=lead.get("scaffold") or "two_storey",
            panel_wattage_kw=float(self.cfg.get("pricing", {}).get("panel_wattage_kw", 0.44)),
            ev_charger=bool(lead.get("ev_charger")),
        )
        quote = pricing.calculate(spec)
        self.store.update_lead(int(lead["id"]), quote_total=quote.total,
                               quote_text=quote.as_text())
        return quote.as_text()


def _lead_line(lead: dict) -> str:
    return (f"{lead.get('name') or 'unknown'} — "
            f"{lead.get('address') or ''} {lead.get('postcode') or ''}".strip())


def main() -> None:
    cfg = config.load()
    setup_logging(cfg.get("bot", {}).get("log_dir", "logs"))
    Bot(cfg).run_forever()


if __name__ == "__main__":
    main()
