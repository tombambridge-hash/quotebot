"""Claude Computer Use agent — drives the Mac mini's real screen to complete an
Easy-PV proposal end to end (log in, open the project, finish the roof design,
pick the standard Bambridge components, generate the customer proposal).

This is the official Anthropic Computer Use tool (the `computer` tool with a
beta header), not browser automation: Claude sees screenshots of macOS display
:1 and issues mouse/keyboard actions, which we execute with pyautogui. The bot
launches a dedicated Chrome window first (see browser.ChromeSession); this agent
just operates whatever is on screen.

The correct beta header and tool-version string depend on the model, and they
change over time — they are derived from the model here from the published
matrix (verified against the Computer use docs, June 2026):

  computer-use-2025-11-24 / computer_20251124 -> Opus 4.8/4.7/4.6, Sonnet 4.6, Opus 4.5
  computer-use-2025-01-24 / computer_20250124 -> Sonnet 4.5, Haiku 4.5, (older 4.x)

Requirements on the Mac: Screen Recording + Accessibility granted to Terminal
and Python, and `pip install pyautogui pillow` (done by install.sh).
"""

import base64
import io
import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("quotebot.computeruse")


class ComputerUseError(Exception):
    pass


# Models that use the newer computer-use beta + tool version (and support zoom).
_NEW_TOOL_MODELS = ("opus-4-8", "opus-4-7", "opus-4-6", "opus-4-5", "sonnet-4-6")
# Opus 4.7/4.8 accept higher-resolution screenshots with 1:1 coordinates.
_HIRES_MODELS = ("opus-4-8", "opus-4-7")


def tool_spec(model: str) -> Dict[str, Any]:
    """Resolve the computer-use beta header, tool `type`, zoom support and image
    limits for a model. Keeping this in one place means the header and tool
    version can never drift out of sync with the chosen model."""
    m = (model or "").lower()
    if any(tag in m for tag in _NEW_TOOL_MODELS):
        beta, tool_type, zoom = "computer-use-2025-11-24", "computer_20251124", True
    else:
        beta, tool_type, zoom = "computer-use-2025-01-24", "computer_20250124", False
    if any(tag in m for tag in _HIRES_MODELS):
        max_long_edge, max_pixels = 2576, 3_750_000
    else:
        max_long_edge, max_pixels = 1568, 1_150_000
    return {"beta": beta, "tool_type": tool_type, "zoom": zoom,
            "max_long_edge": max_long_edge, "max_pixels": max_pixels}


def scale_factor(width: int, height: int,
                 max_long_edge: int = 1568, max_pixels: int = 1_150_000) -> float:
    """Factor to shrink a screenshot so it satisfies the API's image limits
    (longest edge and total megapixels). 1.0 means no scaling needed."""
    if width <= 0 or height <= 0:
        return 1.0
    long_edge_scale = max_long_edge / max(width, height)
    total_pixel_scale = math.sqrt(max_pixels / (width * height))
    return min(1.0, long_edge_scale, total_pixel_scale)


_KEY_MAP = {
    "return": "enter", "enter": "enter", "escape": "esc", "esc": "esc",
    "page_down": "pagedown", "page_up": "pageup", "pagedown": "pagedown",
    "pageup": "pageup", "back_space": "backspace", "backspace": "backspace",
    "delete": "delete", "super": "command", "cmd": "command", "win": "win",
    "control": "ctrl", "ctrl": "ctrl", "option": "option", "alt": "alt",
    "shift": "shift", "tab": "tab", "space": "space", "up": "up", "down": "down",
    "left": "left", "right": "right", "home": "home", "end": "end",
}


def _norm_key(key: str) -> str:
    k = (key or "").strip().lower()
    return _KEY_MAP.get(k, k)


def _png_b64(image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode()


class MacComputer:
    """Executes computer-tool actions on the real macOS display and produces
    screenshots scaled to the resolution Claude sees. Coordinates Claude
    returns (in that scaled space) are mapped back to the physical screen,
    accounting for Retina pixel-doubling."""

    def __init__(self, max_long_edge: int = 1568, max_pixels: int = 1_150_000):
        import pyautogui  # lazy: only needed on the Mac at run time
        self._pg = pyautogui
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0.4

        self.logical_w, self.logical_h = pyautogui.size()
        shot = pyautogui.screenshot()
        self.cap_w, self.cap_h = shot.size
        self.retina = (self.cap_w / self.logical_w) if self.logical_w else 1.0
        self.scale = scale_factor(self.cap_w, self.cap_h, max_long_edge, max_pixels)
        self.target_w = max(1, round(self.cap_w * self.scale))
        self.target_h = max(1, round(self.cap_h * self.scale))
        log.info("Display: logical %sx%s, capture %sx%s (retina %.2f), "
                 "sending %sx%s to Claude", self.logical_w, self.logical_h,
                 self.cap_w, self.cap_h, self.retina, self.target_w, self.target_h)

    # ---- coordinate mapping ------------------------------------------------
    def _to_logical(self, x: float, y: float) -> Tuple[float, float]:
        px, py = x / self.scale, y / self.scale          # scaled -> capture px
        lx, ly = px / self.retina, py / self.retina       # capture px -> points
        lx = min(max(lx, 0), max(self.logical_w - 1, 0))
        ly = min(max(ly, 0), max(self.logical_h - 1, 0))
        return lx, ly

    # ---- screenshots -------------------------------------------------------
    def screenshot_block(self) -> Dict[str, Any]:
        shot = self._pg.screenshot()
        if shot.size != (self.target_w, self.target_h):
            shot = shot.resize((self.target_w, self.target_h))
        return {"type": "image", "source": {"type": "base64",
                "media_type": "image/png", "data": _png_b64(shot)}}

    def _zoom_block(self, region: List[int]) -> Dict[str, Any]:
        x1, y1, x2, y2 = region
        box = (int(x1 / self.scale), int(y1 / self.scale),
               int(x2 / self.scale), int(y2 / self.scale))
        shot = self._pg.screenshot()
        try:
            crop = shot.crop(box)
            s = scale_factor(crop.width, crop.height)
            if s < 1.0:
                crop = crop.resize((max(1, round(crop.width * s)),
                                    max(1, round(crop.height * s))))
            return {"type": "image", "source": {"type": "base64",
                    "media_type": "image/png", "data": _png_b64(crop)}}
        except Exception:
            return self.screenshot_block()

    # ---- action execution --------------------------------------------------
    def execute(self, action: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Run one computer action. Returns an image block for screenshot/zoom,
        else None."""
        pg = self._pg
        coord = params.get("coordinate")
        modifiers = [_norm_key(k) for k in str(params.get("text", "")).split("+")
                     if k] if action in ("left_click", "right_click",
                     "middle_click", "double_click", "triple_click",
                     "scroll") and params.get("text") else []

        def _with_mods(fn):
            for mod in modifiers:
                pg.keyDown(mod)
            try:
                fn()
            finally:
                for mod in reversed(modifiers):
                    pg.keyUp(mod)

        if action == "screenshot":
            return self.screenshot_block()
        if action == "zoom":
            return self._zoom_block(params.get("region") or [0, 0, self.target_w, self.target_h])
        if action == "mouse_move" and coord:
            x, y = self._to_logical(*coord)
            pg.moveTo(x, y)
        elif action in ("left_click", "right_click", "middle_click",
                        "double_click", "triple_click") and coord:
            x, y = self._to_logical(*coord)
            button = {"left_click": "left", "right_click": "right",
                      "middle_click": "middle", "double_click": "left",
                      "triple_click": "left"}[action]
            clicks = {"double_click": 2, "triple_click": 3}.get(action, 1)
            _with_mods(lambda: pg.click(x, y, clicks=clicks, button=button,
                                        interval=0.05))
        elif action == "left_click_drag":
            start = params.get("start_coordinate") or coord
            end = params.get("coordinate") or coord
            if start and end:
                sx, sy = self._to_logical(*start)
                ex, ey = self._to_logical(*end)
                pg.moveTo(sx, sy)
                pg.dragTo(ex, ey, duration=0.6, button="left")
        elif action == "left_mouse_down" and coord:
            x, y = self._to_logical(*coord)
            pg.moveTo(x, y)
            pg.mouseDown()
        elif action == "left_mouse_up" and coord:
            x, y = self._to_logical(*coord)
            pg.moveTo(x, y)
            pg.mouseUp()
        elif action == "scroll":
            if coord:
                x, y = self._to_logical(*coord)
                pg.moveTo(x, y)
            amount = int(params.get("scroll_amount", 3))
            direction = (params.get("scroll_direction") or "down").lower()
            clicks = amount * 100
            if direction in ("up", "down"):
                _with_mods(lambda: pg.scroll(clicks if direction == "up" else -clicks))
            else:
                _with_mods(lambda: pg.hscroll(clicks if direction == "right" else -clicks))
        elif action == "type":
            pg.write(str(params.get("text", "")), interval=0.02)
        elif action == "key":
            keys = [_norm_key(k) for k in str(params.get("text", "")).split("+") if k]
            if len(keys) == 1:
                pg.press(keys[0])
            elif keys:
                pg.hotkey(*keys)
        elif action == "hold_key":
            keys = [_norm_key(k) for k in str(params.get("text", "")).split("+") if k]
            duration = min(float(params.get("duration", 1)), 10.0)
            for k in keys:
                pg.keyDown(k)
            time.sleep(duration)
            for k in reversed(keys):
                pg.keyUp(k)
        elif action == "wait":
            time.sleep(min(float(params.get("duration", 1)), 10.0))
        elif action == "cursor_position":
            pass
        else:
            log.warning("Unhandled computer action: %s %s", action, params)
        return None


GENERIC_PREAMBLE = """You are operating a macOS computer through screenshots and \
mouse/keyboard actions for Bambridge Renewables. A Google Chrome window using the \
dedicated "QuoteBot" profile is already open and focused. Work ONLY in this Chrome \
window — do not open or interact with other applications, windows, or the user's \
other Chrome profiles.

Working rules:
- After each action, take a screenshot and check it worked before continuing. \
Briefly state what you see and what you'll do next.
- Navigate by typing full URLs into the address bar (click it, type, press Enter).
- If a dropdown or scrollbar is awkward to click, prefer keyboard navigation.
- When the whole task is finished, write "DONE" and end your turn.
- If you are stuck on the same screen after several attempts, or something is \
clearly broken, write "FAILED: <short reason>" and end your turn so a human can \
finish it. Do not loop indefinitely."""


def build_system(task_text: str, login_email: str = "", login_password: str = "",
                 lead: Optional[Dict[str, Any]] = None) -> str:
    parts = [GENERIC_PREAMBLE, "Your task:\n" + (task_text or "").strip()]
    if login_email or login_password:
        parts.append("<robot_credentials>\nemail: %s\npassword: %s\n"
                     "</robot_credentials>" % (login_email, login_password))
    if lead:
        parts.append("Customer / project details:\n" + _lead_summary(lead))
    return "\n\n".join(parts)

_KEEP_IMAGES = 3  # screenshots retained in context; older ones become placeholders


class ComputerUseAgent:
    def __init__(self, cfg: dict):
        a = cfg.get("anthropic", {}) or {}
        import os
        self.api_key = a.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
        self.enabled = bool(a.get("enabled", True)) and bool(self.api_key)
        # Most cost-effective model the docs recommend for reliable computer use.
        self.model = a.get("computer_use_model") or a.get("model") or "claude-sonnet-4-6"
        self.max_steps = int(a.get("max_steps", 50))
        self.max_tokens = int(a.get("max_tokens", 4096))
        self.effort = a.get("effort", "medium")
        self.timeout_seconds = int(a.get("timeout_seconds", 600))  # 10 minutes
        self.display_number = int(a.get("display_number", 1))

    def run(self, task_text: str, login_email: str = "", login_password: str = "",
            lead: Optional[Dict[str, Any]] = None) -> str:
        """Drive a provider's web app to completion. `task_text` is the
        provider-specific instruction body (the numbered steps for Easy-PV,
        Pylon, …); login credentials and customer details are appended here.
        Returns Claude's final text. Raises ComputerUseError on step-cap,
        timeout, or API error so the caller can fall back to manual completion."""
        if not self.enabled:
            raise ComputerUseError(
                "Computer Use not configured — set anthropic.api_key in config.yaml")

        import anthropic

        spec = tool_spec(self.model)
        computer = MacComputer(max_long_edge=spec["max_long_edge"],
                               max_pixels=spec["max_pixels"])
        tool: Dict[str, Any] = {
            "type": spec["tool_type"], "name": "computer",
            "display_width_px": computer.target_w,
            "display_height_px": computer.target_h,
            "display_number": self.display_number,
        }
        if spec["zoom"]:
            tool["enable_zoom"] = True

        client = anthropic.Anthropic(api_key=self.api_key)
        system = build_system(task_text, login_email, login_password, lead)
        messages: List[dict] = [{"role": "user", "content": [
            {"type": "text", "text": "Begin now. Here is the current screen:"},
            computer.screenshot_block()]}]

        deadline = time.monotonic() + self.timeout_seconds
        last_text = ""
        for step in range(self.max_steps):
            if time.monotonic() > deadline:
                raise ComputerUseError(
                    f"Computer Use timed out after {self.timeout_seconds}s "
                    f"({step} steps)")
            _trim_screenshots(messages)
            try:
                response = client.beta.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system,
                    tools=[tool],
                    thinking={"type": "adaptive"},
                    output_config={"effort": self.effort},
                    messages=messages,
                    betas=[spec["beta"]],
                )
            except anthropic.APIError as exc:
                raise ComputerUseError(f"Claude API error during Computer Use: {exc}") from exc

            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            last_text = " ".join(b.text for b in response.content
                                 if b.type == "text").strip() or last_text

            if not tool_uses:
                # Claude ended its turn. Treat an explicit failure as fatal so we
                # fall back; otherwise assume it finished and let API polling
                # confirm the proposal actually exists.
                if "FAILED" in last_text.upper():
                    raise ComputerUseError(
                        f"Computer Use reported failure: {last_text[:300]}")
                log.info("Computer Use finished after %s steps: %s",
                         step + 1, last_text[:200])
                return last_text

            results = []
            for idx, tu in enumerate(tool_uses):
                action = tu.input.get("action", "")
                log.info("step %s action: %s %s", step + 1, action,
                         {k: v for k, v in tu.input.items() if k != "action"})
                try:
                    image = computer.execute(action, tu.input)
                except Exception as exc:  # never crash the loop on one bad action
                    results.append({"type": "tool_result", "tool_use_id": tu.id,
                                    "content": f"Error performing {action}: {exc}",
                                    "is_error": True})
                    continue
                # Only attach a fresh screenshot to the final result of the batch
                # (state is cumulative) to keep token use down.
                if image is not None:
                    content: Any = [image]
                elif idx == len(tool_uses) - 1:
                    content = [{"type": "text", "text": "Action performed. Current screen:"},
                               computer.screenshot_block()]
                else:
                    content = "Action performed."
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": content})
            messages.append({"role": "user", "content": results})

        raise ComputerUseError(
            f"Computer Use hit the {self.max_steps}-step cap without finishing")


def _lead_summary(lead: Dict[str, Any]) -> str:
    bits = []
    for label, key in (("Customer", "name"), ("Address", "address"),
                       ("Postcode", "postcode")):
        if lead.get(key):
            bits.append(f"- {label}: {lead[key]}")
    if lead.get("panels"):
        bits.append(f"- Suggested panel count: {lead['panels']} "
                    "(use as a guide; let magicMode size the array)")
    return "\n".join(bits) or "- (no extra details)"


def _trim_screenshots(messages: List[dict]) -> None:
    """Keep only the most recent _KEEP_IMAGES screenshots in context; replace
    older image blocks with a short placeholder so long sessions don't blow up
    token use (and cost)."""
    seen = 0
    for msg in reversed(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result" and isinstance(block.get("content"), list):
                if any(isinstance(c, dict) and c.get("type") == "image"
                       for c in block["content"]):
                    seen += 1
                    if seen > _KEEP_IMAGES:
                        block["content"] = "(earlier screenshot removed to save context)"
            elif block.get("type") == "image":
                seen += 1
                if seen > _KEEP_IMAGES:
                    block["type"] = "text"
                    block.pop("source", None)
                    block["text"] = "(earlier screenshot removed to save context)"
