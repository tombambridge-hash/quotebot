"""Load and validate quotebot configuration (config.yaml)."""

import os
from typing import Any, Dict

import yaml

DEFAULT_PATHS = [
    os.environ.get("QUOTEBOT_CONFIG", ""),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml"),
    os.path.expanduser("~/.quotebot/config.yaml"),
]


def load(path: str = "") -> Dict[str, Any]:
    candidates = [path] if path else DEFAULT_PATHS
    for p in candidates:
        if p and os.path.exists(p):
            with open(p, "r") as fh:
                cfg = yaml.safe_load(fh) or {}
            cfg["_config_path"] = p
            _validate(cfg)
            return cfg
    raise FileNotFoundError(
        "No config.yaml found. Copy config.example.yaml to config.yaml and "
        "fill in your iCloud app-specific password and EasyPV login."
    )


def _validate(cfg: Dict[str, Any]) -> None:
    icloud = cfg.get("icloud") or {}
    missing = [k for k in ("email", "app_password") if not icloud.get(k)]
    if missing:
        raise ValueError(f"config.yaml: icloud.{missing[0]} is required")
    if "xxxx" in str(icloud.get("app_password", "")):
        raise ValueError(
            "config.yaml: icloud.app_password is still the placeholder. "
            "Create one at https://appleid.apple.com -> Sign-In and Security "
            "-> App-Specific Passwords."
        )
