"""AI roof designer — gives the bot eyes for the Easy-PV design step.

Scripted automation can't judge a satellite photo, so this module hands the
browser to Claude: screenshot the page, ask Claude where to click/drag, execute
the action with Playwright, repeat until the panel array is drawn. The same
approach Claude uses interactively, wired into the pipeline.

Needs an Anthropic API key (https://console.anthropic.com -> API Keys) in
config.yaml under anthropic.api_key, or the ANTHROPIC_API_KEY env var.
Typical cost is a few pence per design (each step sends one screenshot).
"""

import base64
import logging
import os
import time
from typing import Any, Dict, List

log = logging.getLogger("quotebot.roof")

TOOLS = [
    {"name": "click",
     "description": "Left-click at pixel coordinates on the current screenshot. "
                    "Use for buttons, menus, map markers, and selecting tools.",
     "input_schema": {"type": "object",
                      "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                      "required": ["x", "y"], "additionalProperties": False}},
    {"name": "double_click",
     "description": "Double-click at pixel coordinates (e.g. to finish drawing a shape).",
     "input_schema": {"type": "object",
                      "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                      "required": ["x", "y"], "additionalProperties": False}},
    {"name": "drag",
     "description": "Press the mouse at the start point, drag to the end point, release. "
                    "Use for drawing roof outlines/panel areas and moving panels.",
     "input_schema": {"type": "object",
                      "properties": {"x1": {"type": "integer"}, "y1": {"type": "integer"},
                                     "x2": {"type": "integer"}, "y2": {"type": "integer"}},
                      "required": ["x1", "y1", "x2", "y2"], "additionalProperties": False}},
    {"name": "scroll",
     "description": "Scroll the page or map. Positive dy scrolls down/zooms out depending on context.",
     "input_schema": {"type": "object",
                      "properties": {"dx": {"type": "integer"}, "dy": {"type": "integer"}},
                      "required": ["dx", "dy"], "additionalProperties": False}},
    {"name": "type_text",
     "description": "Type text into the currently focused field (click it first).",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}},
                      "required": ["text"], "additionalProperties": False}},
    {"name": "press_key",
     "description": "Press a single key, e.g. Enter, Escape, Backspace, Delete.",
     "input_schema": {"type": "object", "properties": {"key": {"type": "string"}},
                      "required": ["key"], "additionalProperties": False}},
    {"name": "wait",
     "description": "Wait for the page to load/settle before the next screenshot.",
     "input_schema": {"type": "object",
                      "properties": {"seconds": {"type": "number", "enum": [1, 2, 3, 5]}},
                      "required": ["seconds"], "additionalProperties": False}},
    {"name": "done",
     "description": "Call when the panel array is drawn and saved. Summarize what was designed "
                    "(panels placed, roof face used, orientation).",
     "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}},
                      "required": ["summary"], "additionalProperties": False}},
    {"name": "fail",
     "description": "Call if the design genuinely cannot be completed (property not found on the "
                    "map, page broken, etc.). Explain why so a human can finish it.",
     "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}},
                      "required": ["reason"], "additionalProperties": False}},
]

SYSTEM = """You are operating the Easy-PV (easy-pv.co.uk) solar design tool through a \
browser for Bambridge Renewables. The project has already been created and you are on or \
near the roof design step. Your job:

1. If an address/postcode search is shown, search the property's postcode and pick the \
correct building on the satellite map. Zoom in close enough to see the roof clearly.
2. Identify the most suitable roof face — prefer south-facing (bottom of a UK satellite \
image is usually south, check the map's orientation), unshaded, large enough for the array.
3. Use Easy-PV's drawing tools to outline the roof face and place the requested number of \
panels. Avoid obvious obstructions (chimneys, velux windows, vents). If the requested \
count doesn't fit on one face, use the second-best face for the remainder.
4. Set roof pitch/orientation if prompted (UK pitched roofs are typically 30-40 degrees).
5. Save/confirm the design, then call done with a short summary.

Work step by step: after each action you receive a fresh screenshot. Coordinates you give \
map 1:1 onto the screenshot pixels. If a click does nothing, look again rather than \
repeating it. If you are stuck after several attempts at the same thing, call fail with \
an explanation — a human will finish the design."""

KEEP_IMAGES = 3  # screenshots kept in context; older ones are dropped to save tokens


class RoofDesignError(Exception):
    pass


class RoofDesigner:
    def __init__(self, cfg: dict):
        a = cfg.get("anthropic", {}) or {}
        self.api_key = a.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
        self.enabled = bool(a.get("enabled", True)) and bool(self.api_key)
        self.model = a.get("model", "claude-opus-4-8")
        self.max_steps = int(a.get("max_design_steps", 40))

    # ------------------------------------------------------------------
    def design(self, page, lead: Dict[str, Any]) -> str:
        """Drive the Easy-PV design step. Returns Claude's summary on success."""
        if not self.enabled:
            raise RoofDesignError(
                "AI roof design not configured — set anthropic.api_key in config.yaml")
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        task = (f"Design the array for this lead:\n"
                f"- Customer: {lead.get('name') or 'unknown'}\n"
                f"- Address: {lead.get('address') or ''} {lead.get('postcode') or ''}\n"
                f"- Panels to place: {lead.get('panels')}\n"
                f"- Roof faces available: {lead.get('roof_faces') or 1}\n"
                f"Here is the current page:")
        messages: List[dict] = [
            {"role": "user", "content": [{"type": "text", "text": task},
                                         self._screenshot_block(page)]}]

        for step in range(self.max_steps):
            self._trim_old_screenshots(messages)
            try:
                response = client.messages.create(
                    model=self.model,
                    max_tokens=8000,
                    thinking={"type": "adaptive"},
                    system=SYSTEM,
                    tools=TOOLS,
                    messages=messages,
                )
            except anthropic.APIError as exc:
                raise RoofDesignError(f"Claude API error during roof design: {exc}") from exc

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            messages.append({"role": "assistant", "content": response.content})

            if not tool_uses:
                messages.append({"role": "user", "content": [
                    {"type": "text", "text": "Continue using the tools, or call done/fail."}]})
                continue

            results = []
            for tu in tool_uses:
                if tu.name == "done":
                    log.info("Roof design complete in %s steps: %s",
                             step + 1, tu.input.get("summary"))
                    return str(tu.input.get("summary", "Design complete"))
                if tu.name == "fail":
                    raise RoofDesignError(
                        f"Claude could not finish the design: {tu.input.get('reason')}")
                self._execute(page, tu.name, tu.input)
                results.append({
                    "type": "tool_result", "tool_use_id": tu.id,
                    "content": [{"type": "text", "text": "Action done. Current page:"},
                                self._screenshot_block(page)]})
            messages.append({"role": "user", "content": results})

        raise RoofDesignError(f"Roof design did not finish within {self.max_steps} steps")

    # ------------------------------------------------------------------
    @staticmethod
    def _execute(page, name: str, args: Dict[str, Any]) -> None:
        log.info("roof action: %s %s", name, args)
        if name == "click":
            page.mouse.click(args["x"], args["y"])
        elif name == "double_click":
            page.mouse.dblclick(args["x"], args["y"])
        elif name == "drag":
            page.mouse.move(args["x1"], args["y1"])
            page.mouse.down()
            page.mouse.move(args["x2"], args["y2"], steps=12)
            page.mouse.up()
        elif name == "scroll":
            page.mouse.wheel(args["dx"], args["dy"])
        elif name == "type_text":
            page.keyboard.type(args["text"], delay=30)
        elif name == "press_key":
            page.keyboard.press(args["key"])
        elif name == "wait":
            time.sleep(min(float(args.get("seconds", 1)), 10))
            return
        time.sleep(0.8)  # let the UI react before the next screenshot

    @staticmethod
    def _screenshot_block(page) -> dict:
        png = page.screenshot()
        return {"type": "image",
                "source": {"type": "base64", "media_type": "image/png",
                           "data": base64.standard_b64encode(png).decode()}}

    @staticmethod
    def _trim_old_screenshots(messages: List[dict]) -> None:
        """Drop screenshots from all but the last KEEP_IMAGES user messages so
        long designs don't blow up the context (and the bill)."""
        seen = 0
        for msg in reversed(messages):
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    has_image = any(isinstance(c, dict) and c.get("type") == "image"
                                    for c in block.get("content", []))
                    if has_image:
                        seen += 1
                        if seen > KEEP_IMAGES:
                            block["content"] = [{"type": "text",
                                                 "text": "(earlier screenshot removed)"}]
                elif isinstance(block, dict) and block.get("type") == "image":
                    seen += 1
                    if seen > KEEP_IMAGES:
                        msg["content"] = [c for c in msg["content"] if c is not block] or \
                                         [{"type": "text", "text": "(screenshot removed)"}]
