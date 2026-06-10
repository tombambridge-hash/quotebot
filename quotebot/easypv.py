"""Easy-PV (easy-pv.co.uk) browser automation via Playwright + Google Chrome.

Creates a new Easy-PV project pre-filled with the lead's details and records
the project URL. The roof design itself (drawing the array on the satellite
photo) genuinely needs a human eye, so the bot gets the project to that point,
adds the Bambridge services total where it can, and emails you the link to
finish the design.

Run `python -m quotebot.easypv --calibrate` once on the Mac: it opens a
visible Chrome window, logs in, and walks the flow slowly so you can confirm
it matches your Easy-PV account (selectors are best-effort; Midsummer update
their UI from time to time).
"""

import logging
import os
import re
import time
from typing import Any, Dict, Optional

log = logging.getLogger("quotebot.easypv")


class EasyPVError(Exception):
    pass


class EasyPV:
    def __init__(self, cfg: dict):
        self._full_cfg = cfg
        self.cfg = cfg.get("easypv", {})
        self.enabled = bool(self.cfg.get("enabled", True))
        self.base_url = self.cfg.get("base_url", "https://easy-pv.co.uk").rstrip("/")
        self.log_dir = cfg.get("bot", {}).get("log_dir", "logs")
        os.makedirs(self.log_dir, exist_ok=True)

    # ------------------------------------------------------------------
    def create_proposal(self, lead: Dict[str, Any], quote) -> Dict[str, str]:
        """Open the bot's Chrome profile, create a project for this lead,
        attach the services cost.

        Login uses the persistent Chrome profile (log in once during
        calibration and it stays logged in) — easypv.email/password in
        config.yaml are an optional fallback for automatic form login.

        Returns {"url": ..., "screenshot": ...}. Raises EasyPVError with a
        screenshot saved to logs/ on failure.
        """
        if not self.enabled:
            raise EasyPVError("Easy-PV automation disabled in config")

        from playwright.sync_api import sync_playwright

        headless = bool(self.cfg.get("headless", False))
        channel = self.cfg.get("chrome_channel", "chrome")
        profile_dir = os.path.abspath(self.cfg.get("profile_dir", "chrome-profile"))
        os.makedirs(profile_dir, exist_ok=True)
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                profile_dir, channel=channel, headless=headless)
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(int(self.cfg.get("timeout_ms", 20000)))
            try:
                self._login(page)
                url = self._new_project(page, lead)
                design_note = self._design_roof(page, lead)
                self._add_services_cost(page, quote)
                shot = self._screenshot(page, f"lead{lead.get('id', 0)}_proposal")
                return {"url": url or page.url, "screenshot": shot,
                        "design": design_note}
            except EasyPVError:
                raise
            except Exception as exc:
                shot = self._screenshot(page, f"lead{lead.get('id', 0)}_error")
                raise EasyPVError(
                    f"Easy-PV automation failed: {exc} (screenshot: {shot})"
                ) from exc
            finally:
                context.close()

    # ------------------------------------------------------------------
    @staticmethod
    def _settle(page) -> None:
        """Wait for the page to go quiet, but never fail the run over it."""
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

    def _login(self, page) -> None:
        page.goto(self.base_url)
        self._settle(page)
        # Already logged in (persistent Chrome profile keeps the session)?
        if self._logged_in(page):
            return
        if not (self.cfg.get("email") and self.cfg.get("password")):
            if not self.cfg.get("headless", False):
                # Visible window: let Tom log in by hand, wait up to 5 minutes.
                log.info("Not logged into Easy-PV — log in now in the Chrome "
                         "window (waiting up to 5 minutes)...")
                for _ in range(60):
                    time.sleep(5)
                    if self._logged_in(page):
                        log.info("Easy-PV login detected — profile saved, no "
                                 "password needed from now on")
                        return
            raise EasyPVError(
                "Not logged into Easy-PV. Run 'python -m quotebot.easypv' once "
                "and log in by hand in the Chrome window — the bot's Chrome "
                "profile remembers it. (Or set easypv.email/password in "
                "config.yaml for automatic login.)")
        for sel in ('a:has-text("Log in")', 'a:has-text("Login")',
                    'a:has-text("Sign in")'):
            if self._visible(page, sel):
                page.locator(sel).first.click()
                break
        email_box = page.locator(
            'input[type="email"], input[name*="email" i], input[name*="user" i]'
        ).first
        email_box.fill(self.cfg["email"])
        page.locator('input[type="password"]').first.fill(self.cfg["password"])
        page.locator(
            'button[type="submit"], input[type="submit"], '
            'button:has-text("Log in"), button:has-text("Sign in")'
        ).first.click()
        self._settle(page)
        if self._visible(page, 'input[type="password"]'):
            raise EasyPVError("Easy-PV login failed — check easypv credentials in config.yaml")
        log.info("Logged into Easy-PV as %s", self.cfg["email"])

    def _logged_in(self, page) -> bool:
        return self._visible(
            page, 'a:has-text("My Projects"), a:has-text("Log out"), '
                  'a:has-text("Logout"), a:has-text("New Project")')

    def _new_project(self, page, lead: Dict[str, Any]) -> Optional[str]:
        for sel in ('a:has-text("New Project")', 'button:has-text("New Project")',
                    'a:has-text("Start a new project")', 'a:has-text("New project")'):
            if self._visible(page, sel):
                page.locator(sel).first.click()
                break
        else:
            page.goto(f"{self.base_url}/projects/new")
        self._settle(page)

        self._fill_first(page, ["project name", "name", "title", "reference"],
                         self._project_name(lead))
        if lead.get("postcode"):
            self._fill_first(page, ["postcode", "post code", "location", "address"],
                             lead["postcode"])
        elif lead.get("address"):
            self._fill_first(page, ["address", "location"], lead["address"])
        if lead.get("name"):
            self._fill_first(page, ["client", "customer"], lead["name"])

        for sel in ('button:has-text("Create")', 'button:has-text("Save")',
                    'button:has-text("Next")', 'button[type="submit"]'):
            if self._visible(page, sel):
                page.locator(sel).first.click()
                break
        self._settle(page)
        return page.url

    def _design_roof(self, page, lead: Dict[str, Any]) -> str:
        """Hand the design step to the AI roof designer (Claude vision loop).
        Non-fatal: if it can't finish, the project is still created and the
        email tells Tom to complete the design by hand."""
        from .roof_designer import RoofDesigner, RoofDesignError

        designer = RoofDesigner(self._full_cfg)
        if not designer.enabled:
            return ("AI roof design not configured — finish the roof design "
                    "manually (set anthropic.api_key in config.yaml to automate it).")
        if not lead.get("panels"):
            return "No panel count on this lead — roof design left for manual entry."
        try:
            summary = designer.design(page, lead)
            return f"AI roof design complete: {summary}"
        except RoofDesignError as exc:
            log.warning("AI roof design failed: %s", exc)
            self._screenshot(page, f"lead{lead.get('id', 0)}_design_failed")
            return f"AI roof design FAILED ({exc}) — finish the design manually."

    def _add_services_cost(self, page, quote) -> None:
        """Best effort: add total services as a custom cost / quote line."""
        opened = False
        for sel in ('a:has-text("Quote")', 'a:has-text("Costs")',
                    'a:has-text("Pricing")', 'button:has-text("Quote")'):
            if self._visible(page, sel):
                page.locator(sel).first.click()
                self._settle(page)
                opened = True
                break
        if not opened:
            log.warning("Could not find quote/costs tab — services total left "
                        "for manual entry (£%s)", quote.total)
            return
        for sel in ('button:has-text("Add item")', 'button:has-text("Add line")',
                    'a:has-text("Add custom")', 'button:has-text("Custom item")'):
            if self._visible(page, sel):
                page.locator(sel).first.click()
                self._fill_first(page, ["description", "item", "name"],
                                 "Bambridge installation services (labour, "
                                 "scaffolding, commissioning)")
                self._fill_first(page, ["price", "cost", "amount", "value"],
                                 str(quote.total))
                for save in ('button:has-text("Save")', 'button:has-text("Add")',
                             'button:has-text("OK")'):
                    if self._visible(page, save):
                        page.locator(save).first.click()
                        break
                log.info("Added services line: £%s", quote.total)
                return
        log.warning("No 'add item' control found — add £%s manually", quote.total)

    # ------------------------------------------------------------------
    @staticmethod
    def _project_name(lead: Dict[str, Any]) -> str:
        bits = [lead.get("name") or "Lead", lead.get("postcode") or ""]
        return " - ".join(b for b in bits if b)

    @staticmethod
    def _visible(page, selector: str) -> bool:
        try:
            loc = page.locator(selector).first
            return loc.count() > 0 and loc.is_visible()
        except Exception:
            return False

    def _fill_first(self, page, label_words, value: str) -> bool:
        for word in label_words:
            for loc in (page.get_by_label(re.compile(word, re.IGNORECASE)),
                        page.locator(f'input[placeholder*="{word}" i]'),
                        page.locator(f'input[name*="{word.replace(" ", "")}" i]')):
                try:
                    if loc.first.count() > 0 and loc.first.is_visible():
                        loc.first.fill(value)
                        return True
                except Exception:
                    continue
        log.debug("No field matched %s", label_words)
        return False

    def _screenshot(self, page, tag: str) -> str:
        path = os.path.join(self.log_dir, f"easypv_{tag}_{int(time.time())}.png")
        try:
            page.screenshot(path=path, full_page=True)
        except Exception:
            return ""
        return path


def _calibrate() -> None:
    """Visible dry-run of the Easy-PV flow so Tom can watch and verify."""
    import sys
    from . import config as cfg_mod
    from .pricing import JobSpec, calculate

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = cfg_mod.load()
    cfg.setdefault("easypv", {})["headless"] = False
    bot = EasyPV(cfg)
    demo_lead = {"id": 0, "name": "Calibration Test", "postcode": "SA31 1AA"}
    quote = calculate(JobSpec(panels=10, batteries=1))
    print("Opening Chrome — watch the window. Ctrl-C to abort.")
    try:
        result = bot.create_proposal(demo_lead, quote)
        print(f"OK — project at {result['url']}\nScreenshot: {result['screenshot']}")
    except EasyPVError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _calibrate()
