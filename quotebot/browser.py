"""Shared, provider-agnostic dedicated-Chrome launcher.

Launches an isolated Chrome window for the bot (its own --user-data-dir plus the
"QuoteBot" profile) so the Computer Use agent never touches the user's everyday
Chrome, and so we own a process group we can cleanly kill on teardown. Used by
every proposal provider (Easy-PV, Pylon, …).
"""

import logging
import os
import signal
import subprocess
import time
from typing import Optional

log = logging.getLogger("quotebot.browser")

DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class BrowserError(Exception):
    pass


class ChromeSession:
    def __init__(self, cfg: dict, start_url: str):
        chrome = (cfg.get("chrome") or {})
        self.binary = chrome.get("binary", DEFAULT_CHROME)
        self.user_data_dir = os.path.expanduser(
            chrome.get("user_data_dir", "~/quotebot2/chrome-quotebot"))
        self.profile = chrome.get("profile", "QuoteBot")
        self.settle_seconds = int(chrome.get("settle_seconds", 6))
        self.start_url = start_url
        self.proc: Optional[subprocess.Popen] = None

    def __enter__(self) -> "ChromeSession":
        os.makedirs(self.user_data_dir, exist_ok=True)
        cmd = [
            self.binary,
            f"--user-data-dir={self.user_data_dir}",
            f"--profile-directory={self.profile}",
            "--no-first-run", "--no-default-browser-check",
            "--start-maximized", "--new-window", self.start_url,
        ]
        log.info("Launching dedicated Chrome (%s profile) at %s",
                 self.profile, self.start_url)
        try:
            self.proc = subprocess.Popen(
                cmd, start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError as exc:
            raise BrowserError(
                f"Could not launch Chrome at {self.binary}. Set chrome.binary "
                "in config.yaml.") from exc
        time.sleep(self.settle_seconds)  # let the window open before CU looks
        return self

    def __exit__(self, *exc) -> None:
        if not self.proc:
            return
        log.info("Closing dedicated Chrome window")
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        except Exception:
            log.warning("Failed to cleanly close the bot's Chrome", exc_info=True)
