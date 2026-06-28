"""Pylon (getpylon.com) proposal provider — API project/lead creation + Claude
Computer Use to finish the design and generate the customer proposal.

Same shape as the Easy-PV provider, but Pylon differs in the specifics:
  - Auth is `Authorization: Bearer <token>` (not Easy-PV's X-API-KEY).
  - Leads/projects are created via a `/lead_form` endpoint.
  - "Proposal ready" is delivered by webhooks rather than a pollable files list.

IMPORTANT: Pylon's full API reference is gated (returns 403 to automated
fetches), so the exact base URL, create-payload field names, project-URL
pattern and any proposal-confirmation endpoint are **driven by config** here
(quotebot/pylon section) with best-effort defaults — confirm them against your
Pylon account + docs before relying on it. None of these are hard-coded
guesses baked into logic; set them in config.yaml.
"""

import logging
import time
from typing import Any, Dict, List, Optional

import requests

from .browser import ChromeSession
from .providers import ProviderError

log = logging.getLogger("quotebot.pylon")

# Defaults — same Bambridge kit as Easy-PV. The names must match the items in
# YOUR Pylon product library; override via pylon.components in config.yaml.
DEFAULT_COMPONENTS = {
    "panels": "Jinko JKM460N 460W",
    "inverter": "Solis S5-EH1P5K hybrid",
    "batteries": "Dyness H5B G2",
}

# Default payload mapping: API field name -> lead field name. Override exact
# Pylon field names via pylon.create_field_map without touching code.
DEFAULT_CREATE_MAP = {
    "name": "name", "email": "email", "phone": "phone",
    "address": "address", "postcode": "postcode",
}


class PylonError(ProviderError):
    pass


class Pylon:
    def __init__(self, cfg: dict):
        self._full_cfg = cfg
        self.cfg = cfg.get("pylon", {}) or {}
        self.enabled = bool(self.cfg.get("enabled", True))
        # API base (confirm against Pylon docs) + web-app base for project URLs.
        self.base_url = self.cfg.get("base_url", "https://api.getpylon.com").rstrip("/")
        self.app_url = self.cfg.get("app_url", "https://app.getpylon.com").rstrip("/")
        self.api_token = self.cfg.get("api_token", "")
        self.create_path = self.cfg.get("create_path", "/lead_form")
        self.project_url_template = self.cfg.get(
            "project_url_template", "{app_url}/projects/{id}")
        self.login_url = self.cfg.get("login_url", self.app_url)
        self.timeout = int(self.cfg.get("api_timeout", 30))
        self.confirm_mode = (self.cfg.get("confirm_mode", "trust")).lower()
        self.poll_interval = int(self.cfg.get("poll_interval_seconds", 30))
        self.poll_timeout = int(self.cfg.get("poll_timeout_seconds", 180))

    # ------------------------------------------------------------------ API
    def _headers(self) -> Dict[str, str]:
        if not self.api_token:
            raise PylonError("pylon.api_token is not set in config.yaml")
        return {"Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json", "Accept": "application/json"}

    def project_url(self, project_id: str) -> str:
        return self.project_url_template.format(app_url=self.app_url, id=project_id)

    def create_project(self, lead: Dict[str, Any]) -> str:
        """Create the lead/project via Pylon's /lead_form endpoint. Returns the
        new id used to build the project URL."""
        field_map = {**DEFAULT_CREATE_MAP, **(self.cfg.get("create_field_map") or {})}
        payload = {api_key: lead.get(lead_key)
                   for api_key, lead_key in field_map.items()
                   if lead.get(lead_key) not in (None, "")}
        payload.update(self.cfg.get("extra_create_fields") or {})
        url = f"{self.base_url}{self.create_path}"
        log.info("Creating Pylon lead/project for %s (%s)",
                 lead.get("name"), lead.get("postcode"))
        try:
            resp = requests.post(url, headers=self._headers(), json=payload,
                                 timeout=self.timeout)
        except requests.RequestException as exc:
            raise PylonError(f"Pylon create request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise PylonError(
                f"Pylon {self.create_path} returned {resp.status_code}: "
                f"{resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise PylonError(f"Pylon create returned non-JSON: {resp.text[:300]}") from exc
        project_id = _extract_id(data, self.cfg.get("id_field"))
        if not project_id:
            raise PylonError(f"No id in Pylon create response: {data}")
        log.info("Pylon project created: %s", project_id)
        return str(project_id)

    def proposal_exists(self, project_id: str) -> bool:
        """Best-effort confirmation. With confirm_mode 'poll', GET a configured
        endpoint and look for a marker; with 'trust' (default) we can't confirm
        via API (Pylon signals readiness by webhook), so rely on Computer Use."""
        check_path = self.cfg.get("proposal_check_path")
        if self.confirm_mode != "poll" or not check_path:
            return True
        marker = str(self.cfg.get("proposal_marker", "proposal")).lower()
        param = self.cfg.get("proposal_id_param", "projectId")
        resp = requests.get(f"{self.base_url}{check_path}", headers=self._headers(),
                            params={param: project_id}, timeout=self.timeout)
        if resp.status_code >= 400:
            raise PylonError(
                f"Pylon proposal check returned {resp.status_code}: {resp.text[:200]}")
        return marker in resp.text.lower()

    def wait_for_proposal(self, project_id: str) -> bool:
        if self.confirm_mode != "poll" or not self.cfg.get("proposal_check_path"):
            log.info("Pylon confirm_mode=%s — trusting Computer Use completion "
                     "(no API confirmation configured)", self.confirm_mode)
            return True
        deadline = time.monotonic() + self.poll_timeout
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            try:
                if self.proposal_exists(project_id):
                    log.info("Pylon proposal confirmed for %s (poll #%s)",
                             project_id, attempt)
                    return True
            except Exception as exc:
                log.warning("Pylon proposal poll #%s failed: %s", attempt, exc)
            time.sleep(self.poll_interval)
        try:
            return self.proposal_exists(project_id)
        except Exception:
            return False

    # -------------------------------------------------------- Computer Use
    def generate_proposal(self, project_id: str, lead: Dict[str, Any]) -> str:
        from .computer_use import ComputerUseAgent, ComputerUseError

        agent = ComputerUseAgent(self._full_cfg)
        if not agent.enabled:
            raise PylonError(
                "Computer Use not configured — set anthropic.api_key in config.yaml")
        task = self.cfg.get("task_override") or _pylon_task(
            self.login_url, self.project_url(project_id),
            self.cfg.get("components") or DEFAULT_COMPONENTS)
        with ChromeSession(self._full_cfg, start_url=self.login_url):
            try:
                return agent.run(task, login_email=self.cfg.get("email", ""),
                                 login_password=self.cfg.get("password", ""),
                                 lead=lead)
            except ComputerUseError as exc:
                raise PylonError(str(exc)) from exc


def _pylon_task(login_url: str, project_url: str, components: Dict[str, str]) -> str:
    return f"""Create the solar PV customer proposal in Pylon (getpylon.com).

1. If Chrome is not already on the Pylon web app, navigate to {login_url} via \
the address bar.
2. Log in: enter the email and password from <robot_credentials> and submit the \
login form. Wait for the dashboard to load.
3. Open the project that was just created via the API: type {project_url} into \
the address bar and press Enter. (If it doesn't open directly, find the project \
by the customer's name/address from the projects list.)
4. Complete the roof/array design — let any automatic roof detection finish \
before intervening; otherwise outline the main, least-shaded roof face yourself, \
avoiding chimneys and roof windows.
5. Select these standard components (search each by name in the product library):
   - Panels: {components.get('panels')}
   - Inverter: {components.get('inverter')}
   - Batteries: {components.get('batteries')}
6. Generate the customer proposal / quote document.
7. When the proposal has been generated, write "DONE" and end your turn."""


def _extract_id(data: Any, preferred: Optional[str] = None) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    if preferred and data.get(preferred):
        return str(data[preferred])
    for key in ("projectId", "project_id", "id", "leadId", "lead_id",
                "opportunityId", "opportunity_id", "uuid"):
        if data.get(key):
            return str(data[key])
    for nest in ("project", "lead", "opportunity", "data", "result"):
        inner = data.get(nest)
        if isinstance(inner, dict):
            found = _extract_id(inner, preferred)
            if found:
                return found
    return None
