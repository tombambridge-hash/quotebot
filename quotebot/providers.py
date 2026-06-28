"""Proposal-provider selection.

A provider turns a parsed lead into a finished solar proposal. Each one exposes
the same small interface so the pipeline (main.run_pipeline) is backend-agnostic:

    enabled: bool
    create_project(lead: dict) -> project_id: str
    project_url(project_id: str) -> str
    generate_proposal(project_id: str, lead: dict) -> None   # raises on failure
    wait_for_proposal(project_id: str) -> bool

Pick the backend with `provider: easypv | pylon` in config.yaml.
"""

import logging

log = logging.getLogger("quotebot.providers")


class ProviderError(Exception):
    """Raised by any provider when a step fails (project creation, proposal
    generation, …). main.run_pipeline catches this and emails the owner the
    project URL to finish manually."""


def get_provider(cfg: dict):
    name = (cfg.get("provider") or "easypv").strip().lower()
    # Imported lazily to avoid an import cycle (providers <-> easypv/pylon).
    if name == "pylon":
        from .pylon import Pylon
        log.info("Using proposal provider: pylon")
        return Pylon(cfg)
    if name == "easypv":
        from .easypv import EasyPV
        log.info("Using proposal provider: easypv")
        return EasyPV(cfg)
    raise ProviderError(
        f"Unknown provider {name!r} in config.yaml (use 'easypv' or 'pylon')")
