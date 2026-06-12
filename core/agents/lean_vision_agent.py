"""
LeanVisionAgent -- lightweight "vision" agent backed by RustVisionBridge.

No ML models. Element detection uses three layers in priority order:
  1. Visual template memory  (saved bounds from prior confirmed clicks)
  2. Windows Accessibility tree (UIA text/role matching via the Rust bridge)
  3. OCR text items           (from the Rust bridge OCR endpoint)

This restores the `vision_agent.find_element(target)` contract that
SupervisorAgent's Learning Mode and _dispatch_step require, without
pulling in heavy computer-vision dependencies.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any

import structlog

from core.agents.base_agent import ActionResult, AgentCapability, BaseAgent

log = structlog.get_logger(__name__)


@dataclass
class FoundElement:
    """Returned by find_element(); supervisor reads .confidence and .bounds."""

    bounds: tuple[int, int, int, int] | None  # (x, y, w, h)  pixel coords
    confidence: float  # 0.0 - 1.0
    source: str  # "template" | "accessibility" | "ocr" | "none"
    text: str = ""


class LeanVisionAgent(BaseAgent):
    """
    Registers as the "vision" agent so SupervisorAgent's learning-mode check
    and reactive-vision calls all route here instead of getting a None agent.
    """

    @property
    def name(self) -> str:
        return "vision"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return [
            AgentCapability("find_element", "Locate UI elements via accessibility + OCR"),
            AgentCapability("get_current_state", "Return current accessibility + capture snapshot"),
            AgentCapability(
                "capture_and_analyze", "Take screenshot and return data for AI planner"
            ),
        ]

    # ── Public API expected by SupervisorAgent ─────────────────────────────────

    async def get_current_state(self) -> dict[str, Any]:
        """Called by supervisor before every step to refresh vision state."""
        try:
            tree = await self.ctx.accessibility.get_focused_window_tree()
            return {"accessibility_tree": tree, "ok": True}
        except Exception as exc:
            log.debug("vision.get_current_state_failed", error=str(exc))
            return {"ok": False}

    async def find_element(self, target: str) -> FoundElement | None:
        """
        Search for a UI element matching *target* text.

        Returns a FoundElement (never raises). If nothing is found,
        returns a stub with confidence=0.0 -- the supervisor's learning-mode
        check will trigger and ask the user to confirm.
        """
        if not target:
            return None

        # ── 1. Visual-memory templates (fastest, highest trust) ────────────────
        el = await self._search_template_memory(target)
        if el and el.confidence >= 0.90:
            log.debug("vision.found_via_template", target=target, conf=el.confidence)
            return el

        # ── 2. Accessibility tree (UIA, no pixels needed) ──────────────────────
        el = await self._search_accessibility(target)
        if el and el.confidence >= 0.75:
            log.debug("vision.found_via_accessibility", target=target, conf=el.confidence)
            return el

        # ── 3. OCR (pixel-based, slightly less trusted) ────────────────────────
        el_ocr = await self._search_ocr(target)
        if el_ocr and (el is None or el_ocr.confidence > el.confidence):
            el = el_ocr

        if el and el.confidence >= 0.55:
            log.debug("vision.found_via_ocr", target=target, conf=el.confidence)
            return el

        # Nothing found -- return stub so learning-mode kicks in
        log.info("vision.element_not_found", target=target)
        return FoundElement(bounds=None, confidence=0.0, source="none", text=target)

    async def wait_for_stable_screen(
        self,
        timeout_sec: float = 5.0,
        poll_interval: float = 0.3,
        stable_threshold: float = 0.02,
        min_stable_frames: int = 3,
    ) -> bool:
        """
        Wait until the screen stops changing (animations/transitions settle).

        The AI gets disoriented when it receives a frame mid-animation:
        - Partial loading states (spinners, progress bars)
        - Window transitions (minimize/maximize animations)
        - Scrolling (content flickering)
        - Typing (cursor blinking)

        Delegates to RustVisionBridge.wait_for_stable_screen() which polls
        the PixelAnalyzer until the screen is stable for `min_stable_frames`
        consecutive polls, or until timeout.

        Args:
            timeout_sec: Max time to wait.
            poll_interval: Seconds between screen checks.
            stable_threshold: Max change ratio (0.0-1.0) to consider stable.
            min_stable_frames: Consecutive stable frames required.

        Returns:
            True if screen stabilized, False on timeout.
        """
        try:
            return await self.ctx.capture.wait_for_stable_screen(
                timeout_sec,
                poll_interval,
                stable_threshold,
                min_stable_frames,
            )
        except Exception as exc:
            log.warning("vision.wait_stable_error", error=str(exc))
            return False

    async def detect_animation(self) -> str:
        """
        Detect if the screen is currently animating or stable.

        Delegates to RustVisionBridge.detect_animation() which captures
        two frames with a short delay and analyzes the change pattern.

        Returns:
            "stable" — no significant motion
            "spinner" — likely a loading spinner / indeterminate progress
            "transition" — scene change / window transition
            "typing" — text input (cursor + small text area changes)
            "scrolling" — content moving vertically
            "unknown" — can't determine
        """
        try:
            return await self.ctx.capture.detect_animation()
        except Exception:
            return "unknown"

    async def capture_and_analyze(self, step: dict) -> ActionResult:
        """
        Capture screen and return rich analysis for AI consumption.

        Uses the smart encoding pipeline:
          - Perceptual hash dedup (skips if frame hasn't changed)
          - Adaptive quality/region encoding
          - Preprocessed OCR text
          - Change ratio and motion detection
          - Accessibility tree

        Returns ActionResult with:
          - image_b64: base64 screenshot (smart-encoded)
          - accessibility_tree: active window info
          - ocr_text: preprocessed OCR result
          - changed_ratio: how much the screen changed
          - is_duplicate: true if this is the same as last frame
          - perceptual_hash: hash for client-side dedup
        """
        try:
            # Use the rich encoding pipeline
            encoded = await self.ctx.capture.get_encoded_frame()
            tree = await self.ctx.accessibility.get_focused_window_tree()

            if encoded.is_duplicate:
                # Frame didn't change — return minimal response
                return ActionResult(
                    success=True,
                    data={
                        "is_duplicate": True,
                        "changed_ratio": 0.0,
                        "perceptual_hash": encoded.perceptual_hash,
                        "accessibility_tree": tree,
                        "note": "Screen hasn't changed since last capture",
                    },
                )

            return ActionResult(
                success=True,
                data={
                    "image_b64": encoded.base64_data,
                    "width": encoded.width,
                    "height": encoded.height,
                    "accessibility_tree": tree,
                    "ocr_text": encoded.ocr_text,
                    "changed_ratio": encoded.changed_ratio,
                    "changed_region": encoded.changed_region,
                    "is_duplicate": False,
                    "perceptual_hash": encoded.perceptual_hash,
                    "quality": encoded.quality,
                },
            )
        except Exception as exc:
            return ActionResult(success=False, error=str(exc))

    # ── Internal search helpers ────────────────────────────────────────────────

    async def _search_template_memory(self, target: str) -> FoundElement | None:
        try:
            tree = await self.ctx.accessibility.get_focused_window_tree()
            active_win = tree.get("name", "") if isinstance(tree, dict) else ""
            tmpl = self.ctx.visual_memory.find_template(active_win, target)
            if tmpl and tmpl.get("bounds"):
                b = tmpl["bounds"]
                return FoundElement(
                    bounds=(int(b[0]), int(b[1]), int(b[2]), int(b[3])),
                    confidence=0.92,
                    source="template",
                    text=target,
                )
        except Exception:
            pass
        return None

    async def _search_accessibility(self, target: str) -> FoundElement | None:
        try:
            tree = await self.ctx.accessibility.get_focused_window_tree()
            return _search_tree(tree, target)
        except Exception as exc:
            log.debug("vision.accessibility_search_error", error=str(exc))
            return None

    async def _search_ocr(self, target: str) -> FoundElement | None:
        try:
            # Always run explicit OCR — frame dedup may skip encoding but
            # the agent still needs to read text from the current screen.
            encoded = await self.ctx.capture.get_encoded_frame()

            # If we have OCR text from the smart encoding pipeline, use it
            if encoded.ocr_text:
                lines = [
                    {"text": line.strip()} for line in encoded.ocr_text.split("\n") if line.strip()
                ]
                result = _search_ocr_items(lines, target)
                if result and result.confidence >= 0.60:
                    return result

            # If the encoded frame has base64 data, run OCR explicitly
            if encoded.base64_data:
                items = await self.ctx.ocr.recognize(encoded.base64_data)
                ocr_result = _search_ocr_items(items, target)
                if ocr_result:
                    return ocr_result

            # If the frame was a duplicate (no base64 data captured),
            # force a fresh capture just for OCR
            if encoded.is_duplicate or not encoded.base64_data:
                # Capture fresh screenshot and run OCR
                fresh_b64 = await self.ctx.capture.capture_base64()
                if fresh_b64:
                    items = await self.ctx.ocr.recognize(fresh_b64)
                    return _search_ocr_items(items, target)

            return None
        except Exception as exc:
            log.debug("vision.ocr_search_error", error=str(exc))
            return None


# ── Pure-function helpers (no I/O) ─────────────────────────────────────────────


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _search_tree(node: Any, target: str) -> FoundElement | None:
    """BFS through the accessibility tree returned by RustVisionBridge."""
    if not isinstance(node, dict):
        return None

    best: FoundElement | None = None
    queue = [node]

    while queue:
        n = queue.pop(0)

        raw = n.get("name") or n.get("label") or n.get("value") or ""
        name = raw if isinstance(raw, str) else ""
        if name:
            score = _similarity(name, target)
            if score >= 0.60:
                raw_bounds = n.get("bounds") or n.get("rect")
                bounds: tuple[int, int, int, int] | None = None
                if raw_bounds and len(raw_bounds) == 4:
                    bounds = (
                        int(raw_bounds[0]),
                        int(raw_bounds[1]),
                        int(raw_bounds[2]),
                        int(raw_bounds[3]),
                    )
                conf = min(score, 0.95)
                if best is None or conf > best.confidence:
                    best = FoundElement(
                        bounds=bounds,
                        confidence=conf,
                        source="accessibility",
                        text=name,
                    )

        for child in n.get("children", []):
            queue.append(child)

    return best


def _search_ocr_items(items: list[dict[str, Any]], target: str) -> FoundElement | None:
    """Search the OCR result list for the best text match."""
    if not items:
        return None

    best: FoundElement | None = None
    for item in items:
        raw_text = item.get("text") or item.get("label") or ""
        text = raw_text if isinstance(raw_text, str) else ""
        if not text:
            continue

        score = _similarity(text, target)
        if score < 0.55:
            continue

        bbox = item.get("bbox") or item.get("bounds")
        if bbox and len(bbox) == 4:
            bounds: tuple[int, int, int, int] | None = tuple(int(v) for v in bbox)  # type: ignore[assignment]
        elif all(k in item for k in ("x", "y", "w", "h")):
            bounds = (int(item["x"]), int(item["y"]), int(item["w"]), int(item["h"]))
        else:
            bounds = None

        conf = score * 0.85  # OCR is slightly less trusted than accessibility tree
        if best is None or conf > best.confidence:
            best = FoundElement(bounds=bounds, confidence=conf, source="ocr", text=text)

    return best
