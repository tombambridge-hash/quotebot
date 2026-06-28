"""Easy-PV (easy-pv.co.uk) integration — REST API + Claude Computer Use.

v2 flow for one lead:
  1. create_project()    -> POST /api/v1/projects/create (X-API-KEY header,
                            owner=bambridgeelectrical@icloud.com, magicMode=true)
                            returns the projectId.
  2. generate_proposal() -> launch a dedicated Chrome window (QuoteBot profile)
                            and hand it to the Computer Use agent, which logs in,
                            opens the project, completes the design, and clicks
                            generate proposal. Chrome is always closed afterwards.
  3. wait_for_proposal() -> poll GET /api/v1/files/list every 30s for up to 3
                            minutes for a file whose id is "customerProposal".

Nothing here ever silently fails: create_project / generate_proposal raise
EasyPVError, and the caller (main.run_pipeline) emails the owner the project URL
to finish manually on any failure or timeout.
"""

import logging
import time
from typing import Any, Dict, List, Optional

import requests

from .browser import ChromeSession
from .providers import ProviderError

log = logging.getLogger("quotebot.easypv")

# Standard components saved in the Bambridge Easy-PV product library (Computer
# Use searches each by name). Override via easypv.components in config.yaml.
DEFAULT_COMPONENTS = {
    "panels": "Jinko JKM460N 460W",
    "inverter": "Solis S5-EH1P5K hybrid",
    "batteries": "Dyness H5B G2",
}


class EasyPVError(ProviderError):
    pass


class EasyPV:
    def __init__(self, cfg: dict):
        self._full_cfg = cfg
        self.cfg = cfg.get("easypv", {}) or {}
        self.enabled = bool(self.cfg.get("enabled", True))
        self.base_url = self.cfg.get("base_url", "https://easy-pv.co.uk").rstrip("/")
        self.api_key = self.cfg.get("api_key", "")
        self.owner_email = self.cfg.get("owner_email", "bambridgeelectrical@icloud.com")
        self.timeout = int(self.cfg.get("api_timeout", 30))
        self.poll_interval = int(self.cfg.get("poll_interval_seconds", 30))
        self.poll_timeout = int(self.cfg.get("poll_timeout_seconds", 180))
        self.log_dir = cfg.get("bot", {}).get("log_dir", "logs")

    # ------------------------------------------------------------------ API
    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise EasyPVError("easypv.api_key is not set in config.yaml")
        return {"X-API-KEY": self.api_key, "Accept": "application/json"}

    def project_url(self, project_id: str) -> str:
        return f"{self.base_url}/project/{project_id}"

    def create_project(self, lead: Dict[str, Any]) -> str:
        """POST /api/v1/projects/create. Returns the new projectId."""
        payload: Dict[str, Any] = {
            "owner": self.owner_email,
            "magicMode": True,
            "name": _project_name(lead),
            "address": lead.get("address") or "",
            "postcode": lead.get("postcode") or "",
            "customerName": lead.get("name") or "",
            "customerEmail": lead.get("email") or "",
            "customerPhone": lead.get("phone") or "",
        }
        payload.update(self.cfg.get("extra_project_fields") or {})
        url = f"{self.base_url}/api/v1/projects/create"
        log.info("Creating Easy-PV project for %s (%s)",
                 lead.get("name"), lead.get("postcode"))
        try:
            resp = requests.post(url, headers=self._headers(), json=payload,
                                 timeout=self.timeout)
        except requests.RequestException as exc:
            raise EasyPVError(f"Easy-PV create_project request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise EasyPVError(
                f"Easy-PV create_project returned {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise EasyPVError(f"Easy-PV create_project returned non-JSON: "
                              f"{resp.text[:300]}") from exc
        project_id = _extract_project_id(data)
        if not project_id:
            raise EasyPVError(f"No projectId in create_project response: {data}")
        log.info("Easy-PV project created: %s", project_id)
        return str(project_id)

    def list_files(self, project_id: str) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/api/v1/files/list"
        resp = requests.get(url, headers=self._headers(),
                            params={"projectId": project_id}, timeout=self.timeout)
        if resp.status_code >= 400:
            raise EasyPVError(
                f"files/list returned {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if isinstance(data, list):
            return data
        for key in ("files", "data", "results"):
            if isinstance(data.get(key), list):
                return data[key]
        return []

    def proposal_exists(self, project_id: str) -> bool:
        files = self.list_files(project_id)
        return any(str(f.get("id")) == "customerProposal" for f in files)

    def wait_for_proposal(self, project_id: str) -> bool:
        """Poll files/list every poll_interval seconds for up to poll_timeout
        seconds, looking for the customerProposal file."""
        deadline = time.monotonic() + self.poll_timeout
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            try:
                if self.proposal_exists(project_id):
                    log.info("customerProposal confirmed for project %s "
                             "(poll #%s)", project_id, attempt)
                    return True
            except Exception as exc:
                log.warning("files/list poll #%s failed: %s", attempt, exc)
            log.info("Proposal not ready yet (poll #%s) — waiting %ss",
                     attempt, self.poll_interval)
            time.sleep(self.poll_interval)
        try:
            return self.proposal_exists(project_id)
        except Exception:
            return False

    # -------------------------------------------------------- Computer Use
    def generate_proposal(self, project_id: str, lead: Dict[str, Any]) -> str:
        """Launch the dedicated Chrome window and run the Computer Use agent to
        complete the design and generate the proposal. Raises EasyPVError on
        any failure so the caller can fall back to manual completion."""
        from .computer_use import ComputerUseAgent, ComputerUseError

        agent = ComputerUseAgent(self._full_cfg)
        if not agent.enabled:
            raise EasyPVError(
                "Computer Use not configured — set anthropic.api_key in config.yaml")
        task = _easypv_task(self.base_url, self.project_url(project_id),
                            self.cfg.get("components") or DEFAULT_COMPONENTS)
        with ChromeSession(self._full_cfg, start_url=self.base_url):
            try:
                return agent.run(task, login_email=self.cfg.get("email", ""),
                                 login_password=self.cfg.get("password", ""),
                                 lead=lead)
            except ComputerUseError as exc:
                raise EasyPVError(str(exc)) from exc


def _project_name(lead: Dict[str, Any]) -> str:
    bits = [lead.get("name") or "Web lead", lead.get("postcode") or ""]
    return " - ".join(b for b in bits if b)


def _easypv_task(base_url: str, project_url: str, components: Dict[str, str]) -> str:
    return f"""Create the solar PV customer proposal in Easy-PV.

1. If Chrome is not already on {base_url}, navigate there via the address bar.
2. Click "Login" in the top navigation. The login form opens as a MODAL POPUP \
(not a separate page) — wait for it to fully appear before typing.
3. Enter the email and password from <robot_credentials> and click the button \
labelled exactly "Login" (not "Log in").
4. Open the project: type {project_url} into the address bar and press Enter.
5. Complete the roof design. magicMode auto-detects the roof — wait up to about \
2 minutes for it to finish before intervening. If it does not detect the roof, \
outline the main (largest, least-shaded) roof face on the satellite map \
yourself, avoiding chimneys and roof windows.
6. Select these standard components (already saved in the Bambridge product \
library — search each by name):
   - Panels: {components.get('panels')}
   - Inverter: {components.get('inverter')}
   - Batteries: {components.get('batteries')}
7. Generate the customer proposal (the button that produces the customer \
proposal / PDF).
8. When the proposal has been generated, write "DONE" and end your turn."""


def _extract_project_id(data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    for key in ("projectId", "project_id", "id"):
        if data.get(key):
            return str(data[key])
    for nest in ("project", "data", "result"):
        inner = data.get(nest)
        if isinstance(inner, dict):
            pid = _extract_project_id(inner)
            if pid:
                return pid
    return None


def _calibrate() -> None:
    """Quick manual check: create a test project via the API and print its URL.
    Run on the Mac with:  ./venv/bin/python -m quotebot.easypv"""
    import sys
    from . import config as cfg_mod

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = cfg_mod.load()
    bot = EasyPV(cfg)
    demo = {"name": "Calibration Test", "address": "1 Test Street, Newport",
            "postcode": "NP19 8AZ", "email": "", "phone": ""}
    try:
        pid = bot.create_project(demo)
        print(f"OK — created test project: {bot.project_url(pid)}")
        if "--design" in sys.argv:
            print("Running Computer Use on the test project (watch Chrome)...")
            bot.generate_proposal(pid, demo)
            print("Confirmed proposal:" , bot.wait_for_proposal(pid))
    except EasyPVError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _calibrate()
