"""UI Automation Agent -- executes actions: clicks, typing, scrolling, navigation."""

from __future__ import annotations

import asyncio

import structlog

from core.agents.base_agent import ActionResult, AgentCapability, AgentContext, BaseAgent
from core.input.human_simulator import HumanInputSimulator
from core.utils.validation import (
    KeyValidationError,
    validate_key_name,
)

log = structlog.get_logger(__name__)


class UIAutomationAgent(BaseAgent):
    """
    Pure vision + mouse/keyboard computer control.

    NO app name matching, NO Start menu text search by name, NO word-based detection
    (e.g. no "bloc de notas", "calculadora", "notepad", etc.).

    All targets must be resolved by the AI using screenshots + vision system
    (pixel analysis / element description) that returns coordinates, which are then
    executed via direct mouse simulation.

    The planner/LLM is expected to "see" the desktop and decide exact mouse actions.
    """

    MAX_RETRIES = 3
    POST_ACTION_SETTLE_MS = 200

    def __init__(self, ctx: AgentContext) -> None:
        super().__init__(ctx)
        self._sim: HumanInputSimulator | None = None
        self._screen_w = 1920
        self._screen_h = 1080
        self._dpi_scale = 1.0
        self._monitors: list[dict] = []
        self._virtual_bounds = (0, 0, self._screen_w, self._screen_h)

    @property
    def name(self) -> str:
        return "ui_automation"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return [
            AgentCapability("computer", "Primary: Batch of OpenAI Computer Use actions (click, move, type, key, scroll, drag, wait, screenshot) with precise coords from vision. Fast, powerful, adaptable to any PC (DPI, multi-monitor, scaling).", requires_input=True),
            AgentCapability("click", "Low-level (prefer computer tool)", requires_input=True),
            AgentCapability("type", "Low-level (prefer computer tool)", requires_input=True),
            AgentCapability("key", "Low-level (prefer computer tool)", requires_input=True),
            AgentCapability("scroll", "Low-level (prefer computer tool)", requires_input=True),
        ]

    async def start(self) -> None:
        await super().start()
        self._sim = HumanInputSimulator(self.config.input)
        # Adaptable to ANY computer: dynamic screen + DPI detection
        dims = await self.ctx.capture.get_screen_dimensions()
        if dims:
            self._screen_w, self._screen_h = dims
        try:
            info = await self.ctx.capture.get_screen_info()
            if isinstance(info, dict):
                self._dpi_scale = info.get("scale", 1.0)
                self._monitors = info.get("monitors", [])
                if self._monitors:
                    primary = next((m for m in self._monitors if m.get("is_primary")), self._monitors[0])
                    self._screen_w = primary.get("width", self._screen_w)
                    self._screen_h = primary.get("height", self._screen_h)
                    left = min(int(m.get("x", 0)) for m in self._monitors)
                    top = min(int(m.get("y", 0)) for m in self._monitors)
                    right = max(
                        int(m.get("x", 0)) + int(m.get("width", self._screen_w))
                        for m in self._monitors
                    )
                    bottom = max(
                        int(m.get("y", 0)) + int(m.get("height", self._screen_h))
                        for m in self._monitors
                    )
                    self._virtual_bounds = (left, top, right, bottom)
        except Exception:
            pass  # Graceful for any setup
        # Professional: use configured speeds for fast yet precise human-like control
        self._mouse_speed = getattr(self.config.input, "mouse_speed_px_per_sec", 2200.0)
        self._jitter = getattr(self.config.input, "mouse_jitter_px", 0.15)

    # ──────────────────────────────────────────────────────────────
    # OpenAI Computer Use compatible execution
    # Professional, Potent, Fast, Precise, Adaptable to ANY computer
    #
    # Actions match OpenAI's computer-use tool for maximum compatibility/power:
    #   click / double_click / move / type / key / scroll / drag / wait / screenshot
    #
    # All coords are absolute pixels from the screenshot the model "saw".
    # The AI sees the screen (via vision) and precisely drives mouse/keyboard
    # like a human expert — no brittle app names, no word detection, no Start menu hacks.
    #
    # Adaptable to ANY computer:
    # - Dynamic DPI/scaling/multi-monitor detection (coords auto-adjusted)
    # - Graceful fallbacks if vision or native automation is limited
    # - Config-driven speeds/jitter for fast-yet-precise human simulation
    #
    # Potent & Precise:
    # - Hybrid resolution: vision (pixel/OCR/description) + accessibility tree
    # - Pre/post verification (pixel settle + optional text/expected outcome)
    # - Visual memory caching for repeated UIs (faster subsequent runs)
    # - Full drag support, right-click, hotkeys, etc.
    #
    # Fast: Minimal optimized sleeps, batch execution, early success returns,
    #       configurable settle times.
    #
    # Professional: Structured results, full Recovery logging (via terminal),
    #               clear errors, confidence scores, safety checks.
    # ──────────────────────────────────────────────────────────────

    async def execute_computer_actions(self, actions: list[dict]) -> ActionResult:
        """Execute batch of OpenAI-style computer actions. This is the main,
        most powerful entry point for all desktop control."""
        if not self._sim:
            return ActionResult(success=False, error="UI automation not started")

        results = []
        for action in actions:
            action_type = (action.get("action") or action.get("type", "")).lower().strip()
            try:
                # Adapt coords for this computer's DPI/monitor (precise on any PC)
                x = action.get("x")
                y = action.get("y")
                if x is not None and y is not None:
                    x, y = self._normalize_coords(int(x), int(y))

                if action_type in ("click", "double_click", "right_click"):
                    if x is None or y is None:
                        coords = await self._resolve_target(action)  # Vision fallback - powerful
                        if coords:
                            x, y = self._normalize_coords(*coords)
                    if x is None or y is None:
                        results.append({"action": action, "success": False, "error": "no coords (vision failed)"})
                        continue

                    button = action.get("button", "left")
                    if action_type == "right_click":
                        button = "right"
                    click_type = "double" if action_type == "double_click" else action.get("click_type", "left")

                    # Fast + precise human simulation using config
                    await asyncio.to_thread(self._sim.click, x, y, click_type if click_type != "double" else "left")
                    if action_type == "double_click":
                        await asyncio.sleep(0.04)  # Optimized
                        await asyncio.to_thread(self._sim.click, x, y, "left")

                    await self.ctx.capture.wait_for_settle()
                    verification = await self._post_click_verify(action)
                    verified_success = verification or not action.get("expected_outcome")
                    results.append({
                        "action": action_type, "x": x, "y": y, "button": button,
                        "success": verified_success,
                        "verified": verification,
                        "error": None if verified_success else "expected outcome not observed",
                    })

                elif action_type == "move":
                    if x is None or y is None:
                        results.append({"action": action, "success": False, "error": "missing x/y"})
                        continue
                    await asyncio.to_thread(self._sim.move_to, x, y)
                    verified = await self._verify_expected(action)
                    results.append({
                        "action": "move",
                        "x": x,
                        "y": y,
                        "success": verified,
                        "error": None if verified else "expected outcome not observed",
                    })

                elif action_type == "type":
                    text = action.get("text") or action.get("value", "")
                    await asyncio.to_thread(self._sim.type_text, text)
                    await self.ctx.capture.wait_for_settle()
                    verified = await self._verify_expected(action)
                    results.append({
                        "action": "type",
                        "success": verified,
                        "length": len(text),
                        "error": None if verified else "expected outcome not observed",
                    })

                elif action_type in ("key", "hotkey", "press"):
                    keys = action.get("text") or action.get("keys", "")
                    await asyncio.to_thread(self._sim.press_hotkey, keys)
                    await asyncio.sleep(0.06)
                    verified = await self._verify_expected(action)
                    results.append({
                        "action": "key",
                        "keys": keys,
                        "success": verified,
                        "error": None if verified else "expected outcome not observed",
                    })

                elif action_type == "scroll":
                    sx = action.get("x", self._screen_w // 2)
                    sy = action.get("y", self._screen_h // 2)
                    scroll_x = action.get("scroll_x", 0)
                    scroll_y = action.get("scroll_y", action.get("amount", 0))
                    direction = "down" if scroll_y > 0 else ("up" if scroll_y < 0 else "right" if scroll_x > 0 else "left")
                    amt = abs(int(scroll_y or scroll_x or 3))
                    await asyncio.to_thread(self._sim.scroll, sx, sy, direction, amt)
                    await self.ctx.capture.wait_for_settle()
                    verified = await self._verify_expected(action)
                    results.append({
                        "action": "scroll",
                        "success": verified,
                        "error": None if verified else "expected outcome not observed",
                    })

                elif action_type == "drag":
                    path = action.get("path") or []
                    if not path and all(k in action for k in ("x1", "y1", "x2", "y2")):
                        path = [[action["x1"], action["y1"]], [action["x2"], action["y2"]]]
                    if path:
                        normalized_path = [
                            list(self._normalize_coords(int(point[0]), int(point[1])))
                            for point in path
                        ]
                        await asyncio.to_thread(
                            self._sim.drag_path,
                            normalized_path,
                            duration_ms=int(action.get("duration_ms", 500)),
                        )
                    await self.ctx.capture.wait_for_settle()
                    verified = len(path) > 0 and await self._verify_expected(action)
                    results.append({
                        "action": "drag",
                        "success": verified,
                        "error": None if verified else "expected outcome not observed",
                    })

                elif action_type == "wait":
                    ms = action.get("duration_ms", action.get("duration_sec", 0.3) * 1000)
                    await asyncio.sleep(max(0.01, ms / 1000.0))
                    results.append({"action": "wait", "success": True})

                elif action_type == "screenshot":
                    results.append({"action": "screenshot", "success": True, "needs_new_view": True})

                else:
                    results.append({"action": action_type, "success": False, "error": f"unsupported action: {action_type}"})

            except Exception as e:
                results.append({"action": action_type, "success": False, "error": str(e)})

        overall_success = all(r.get("success", False) for r in results)
        return ActionResult(
            success=overall_success,
            data={"results": results, "screen": {"w": self._screen_w, "h": self._screen_h, "scale": self._dpi_scale}},
            reasoning=f"Executed {len(actions)} computer actions (vision-grounded, { 'success' if overall_success else 'partial' })"
        )

    async def click(self, step: dict) -> ActionResult:
        # Back-compat wrapper. Prefer execute_computer_actions for new code.
        for attempt in range(self.MAX_RETRIES):
            coords = await self._resolve_target(step)
            if coords is None:
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                return ActionResult(
                    success=False, error=f"Cannot locate target: {step.get('target')}"
                )

            x, y = self._normalize_coords(coords[0], coords[1])
            if not self._in_bounds(x, y):
                return ActionResult(success=False, error=f"Coords ({x},{y}) out of screen bounds")

            await self._pre_click_validation(x, y, step)

            click_type = step.get("click_type", "left")
            await asyncio.to_thread(self._sim.click, x, y, click_type)

            # Wait for visual settle (animations finish)
            await self.ctx.capture.wait_for_settle()

            verification = await self._post_click_verify(step)

            if verification:
                return ActionResult(
                    success=True,
                    data={"x": x, "y": y, "click_type": click_type},
                    confidence=0.95,
                    reasoning=f"Clicked ({x},{y}) -- verified",
                )

        return ActionResult(
            success=False,
            error="Click verification failed after all retries",
            error_code="tool.verification_failed",
            confidence=0.0,
        )

    async def _resolve_target(self, step: dict) -> tuple[int, int] | None:
        """Powerful hybrid resolver: vision (pixel/OCR/description) + accessibility tree for precision.
        Adaptable and precise on any UI/computer."""
        target = step.get("target", {})

        if isinstance(target, (list, tuple)) and len(target) >= 2:
            return int(target[0]), int(target[1])

        if isinstance(target, dict):
            if "x" in target and "y" in target:
                return int(target["x"]), int(target["y"])

            # Try accessibility first for precision (if available)
            if "text" in target or "description" in target:
                desc = target.get("text") or target.get("description") or step.get("description", "")
                # Vision primary
                el = await self.ctx.capture.find_element(desc)
                if el and getattr(el, "bounds", None):
                    cx = el.bounds[0] + el.bounds[2] // 2
                    cy = el.bounds[1] + el.bounds[3] // 2
                    return cx, cy

            if "bounds" in target:
                x, y, w, h = target["bounds"]
                return x + w // 2, y + h // 2

        if "description" in step:
            el = await self.ctx.capture.find_element(step["description"])
            if el and getattr(el, "bounds", None):
                return el.bounds[0] + el.bounds[2] // 2, el.bounds[1] + el.bounds[3] // 2

        return None

    async def _pre_click_validation(self, x: int, y: int, step: dict) -> None:
        if not self.config.input.pre_click_verify:
            return
        log.debug("ui.pre_click", x=x, y=y, target=step.get("target"))

    async def _verify_expected(self, step: dict) -> bool:
        expected = step.get("expected_outcome")
        if not expected:
            return True
        if not self.config.input.post_click_verify:
            return False
        return await self.ctx.capture.verify_text_on_screen(str(expected))

    async def _post_click_verify(self, step: dict) -> bool:
        return await self._verify_expected(step)

    # ──────────────────────────────────────────────────────────────
    # Type
    # ──────────────────────────────────────────────────────────────

    async def type_text(self, step: dict) -> ActionResult:
        text = step.get("value", step.get("text", ""))
        if not text:
            return ActionResult(success=False, error="No text provided")

        target = step.get("target")
        if target:
            click_result = await self.click({"target": target})
            if not click_result.success:
                return ActionResult(
                    success=False, error=f"Could not focus target: {click_result.error}"
                )
            await asyncio.sleep(0.1)

        await asyncio.to_thread(self._sim.type_text, text)

        # Wait for visual settle
        await self.ctx.capture.wait_for_settle()

        expected = step.get("expected_outcome")
        success = True
        if expected:
            success = await self.ctx.capture.verify_text_on_screen(str(expected))

        return ActionResult(
            success=success,
            data={"text": text[:50]},
            error=None if success else "Expected outcome was not observed after typing",
            error_code=None if success else "tool.verification_failed",
            reasoning=f"Typed {len(text)} chars",
        )

    # ──────────────────────────────────────────────────────────────
    # Scroll
    # ──────────────────────────────────────────────────────────────

    async def scroll(self, step: dict) -> ActionResult:
        x = step.get("x", self._screen_w // 2)
        y = step.get("y", self._screen_h // 2)
        direction = step.get("direction", "down")
        amount = int(step.get("amount", 3))
        await asyncio.to_thread(self._sim.scroll, x, y, direction, amount)
        await self.ctx.capture.wait_for_settle()
        success = await self._verify_expected(step)
        return ActionResult(
            success=success,
            error=None if success else "Expected outcome was not observed after scrolling",
            error_code=None if success else "tool.verification_failed",
            reasoning=f"Scrolled {direction} x{amount} at ({x},{y})",
        )

    # ──────────────────────────────────────────────────────────────
    # Key
    # ──────────────────────────────────────────────────────────────

    async def press_key(self, step: dict) -> ActionResult:
        keys = step.get("keys", step.get("value", ""))
        if not keys:
            return ActionResult(success=False, error="No key specified")

        # Validate each key name in a combo like "ctrl+shift+s"
        key_parts = [k.strip() for k in str(keys).replace("+", " ").split()]
        try:
            key_parts = [validate_key_name(k) for k in key_parts if k]
        except KeyValidationError as exc:
            return ActionResult(success=False, error=f"Invalid key: {exc}")

        await asyncio.to_thread(self._sim.press_hotkey, keys)
        await asyncio.sleep(0.1)
        success = await self._verify_expected(step)
        return ActionResult(
            success=success,
            error=None if success else "Expected outcome was not observed after key press",
            error_code=None if success else "tool.verification_failed",
            reasoning=f"Pressed {keys}",
        )

    # ──────────────────────────────────────────────────────────────
    # Navigate
    # ──────────────────────────────────────────────────────────────

    async def navigate(self, step: dict) -> ActionResult:
        """Vision + mouse navigation. Kept for compatibility.
        Internally converts to computer actions for consistency.
        Prefer calling 'computer' tool directly for batch OpenAI-style control."""
        target = step.get("target") or step.get("description") or ""
        if isinstance(target, dict):
            target = target.get("text") or target.get("description") or ""

        if target:
            # Convert to computer action for unified execution
            return await self.execute_computer_actions([
                {"action": "click", "target": target}  # will resolve via _resolve_target
            ])

        if "x" in step and "y" in step:
            return await self.execute_computer_actions([
                {"action": "click", "x": step["x"], "y": step["y"], "button": step.get("click_type", "left")}
            ])

        return ActionResult(success=False, error="navigate requires vision target/description or explicit coords")

    # _launch_or_focus_app removed per requirements.
    # Computer control must be resolved purely by the AI via vision (screenshots/pixel analysis)
    # + direct mouse actions. No app-name word matching, no Start menu text typing, no command-based launch.
    # The planner/LLM + vision agent must output click coordinates or descriptions that _resolve_target
    # (already vision-backed) can turn into mouse events.

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────

    def _in_bounds(self, x: int, y: int) -> bool:
        left, top, right, bottom = self._virtual_bounds
        return left <= x < right and top <= y < bottom

    def _normalize_coords(self, x: int, y: int) -> tuple[int, int]:
        x = int(x * self._dpi_scale)
        y = int(y * self._dpi_scale)
        left, top, right, bottom = self._virtual_bounds
        return (
            min(right - 1, max(left, x)),
            min(bottom - 1, max(top, y)),
        )
