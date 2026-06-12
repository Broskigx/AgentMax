"""
Vision bridge — async interface to the PixelAnalyzer engine.

Provides the same capture/OCR/accessibility surface that agents expect,
backed by the smart PIL-based PixelAnalyzer. Includes:
  - Frame deduplication with perceptual hashing
  - Adaptive quality/region encoding
  - Preprocessed OCR for accuracy
  - Frame cache to avoid re-encoding identical frames
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from core.pixel_engine.pixel_analyzer import EncodedFrame, PixelAnalyzer

log = structlog.get_logger(__name__)

_MAX_CACHED_B64 = 3  # Keep last 3 base64 frames in memory


class RustVisionBridge:
    """
    Async vision bridge wrapping PixelAnalyzer.

    Provides:
      - capture_base64()  → frame-aware dedup + adaptive encoding
      - capture()         → raw PIL Image
      - recognize()       → OCR with preprocessing
      - get_focused_window_tree() → accessibility via win32
      - get_encoded_frame() → full EncodedFrame with diff metadata

    All heavy I/O runs in asyncio.to_thread to keep the event loop responsive.
    """

    def __init__(self, base_url: str | None = None) -> None:
        # base_url accepted for backward compatibility
        self._pixel = PixelAnalyzer()
        self._started = False
        self._frame_cache: list[EncodedFrame] = []
        self._last_b64: str = ""
        # Frame throttle: max 5 fps capture rate
        self._min_frame_interval: float = 0.2  # 200 ms
        self._last_capture_time: float = 0.0

    async def start(self) -> None:
        self._started = True
        log.info(
            "rust_vision_bridge.ready",
            pil_available=self._pixel._pil_available,
            ocr_available=self._pixel._ocr_available,
            screen_dims=self._pixel.get_screen_dimensions(),
        )

    async def stop(self) -> None:
        self._started = False

    # ── Frame capture (smart encoding + throttle) ────────────────────────────

    async def capture_base64(self) -> str:
        """
        Capture screen and return base64-encoded image.

        Uses the smart encoding pipeline:
          - Perceptual hash dedup (returns cached frame for identical screens)
          - Adaptive quality based on change magnitude
          - Region-only encoding for small changes
          - Frame throttle: max 5 fps to avoid flooding AI with near-identical frames

        Returns:
            Base64 string, or empty string on failure.
            If the frame is a duplicate of the last one, returns the cached b64.
        """
        now = time.time()

        # Throttle: if we just captured, return last good frame
        if now - self._last_capture_time < self._min_frame_interval and self._last_b64:
            return self._last_b64

        # Fast path: check cache for a recent non-duplicate frame (< 0.5s old)
        for cached in reversed(self._frame_cache):
            if not cached.is_duplicate and cached.base64_data:
                age = now - cached.timestamp
                if age < 0.5:
                    self._last_capture_time = now
                    self._last_b64 = cached.base64_data
                    return cached.base64_data

        frame = await asyncio.to_thread(self._pixel.encode_current_frame)

        # Cache the frame
        self._frame_cache.append(frame)
        if len(self._frame_cache) > _MAX_CACHED_B64:
            self._frame_cache.pop(0)

        self._last_capture_time = now

        if frame.is_duplicate:
            # Return last successful b64 (avoid sending all-zeros to AI)
            if self._last_b64:
                return self._last_b64
            return frame.base64_data

        self._last_b64 = frame.base64_data
        return frame.base64_data

    async def get_encoded_frame(self) -> EncodedFrame:
        """
        Capture and encode the current screen, returning full EncodedFrame metadata.

        This is the richest API — returns everything the AI needs:
          - Image data (base64)
          - Dimensions
          - Change ratio and region
          - OCR text
          - Perceptual hash
          - Dedup flag
          - Animation state hints

        Frame throttle ensures we don't capture more than 5 fps.
        """
        now = time.time()

        # Throttle: if we just captured, return last encoded frame from cache
        if now - self._last_capture_time < self._min_frame_interval and self._frame_cache:
            return self._frame_cache[-1]

        frame = await asyncio.to_thread(self._pixel.encode_current_frame)

        self._frame_cache.append(frame)
        if len(self._frame_cache) > _MAX_CACHED_B64:
            self._frame_cache.pop(0)

        self._last_capture_time = now
        if not frame.is_duplicate:
            self._last_b64 = frame.base64_data

        return frame

    async def wait_for_stable_screen(
        self,
        timeout_sec: float = 5.0,
        poll_interval: float = 0.3,
        stable_threshold: float = 0.02,
        min_stable_frames: int = 3,
    ) -> bool:
        """
        Wait until the screen stops changing.

        This is an async wrapper around PixelAnalyzer.wait_for_stable_screen().
        Call this before taking a screenshot for the AI when you know an
        animation or transition just started (e.g., after a click).

        Args:
            timeout_sec: Max time to wait.
            poll_interval: Seconds between screen checks.
            stable_threshold: Max change ratio to consider stable.
            min_stable_frames: Consecutive stable frames required.

        Returns:
            True if screen stabilized, False on timeout.
        """
        # Reset throttle so the next capture is fresh
        self._last_capture_time = 0.0
        return await asyncio.to_thread(
            self._pixel.wait_for_stable_screen,
            timeout_sec,
            poll_interval,
            stable_threshold,
            min_stable_frames,
        )

    async def detect_animation(self) -> str:
        """
        Quick check if the screen is currently animating or stable.

        Returns:
            "stable", "spinner", "transition", "typing", "scrolling", or "unknown".
        """
        try:
            img1 = await asyncio.to_thread(self._pixel.capture_screenshot)
            if img1 is None:
                return "unknown"
            await asyncio.sleep(0.15)
            img2 = await asyncio.to_thread(self._pixel.capture_screenshot)
            if img2 is None:
                return "unknown"

            diff = self._pixel._compute_diff(img2, img1)

            if not diff.motion_detected:
                return "stable"

            ratio = diff.changed_ratio
            region = diff.changed_region
            region_area = (region[2] * region[3]) / (img1.size[0] * img1.size[1]) if region else 0

            if ratio < 0.005:
                return "stable"
            elif ratio < 0.03 and region_area < 0.05:
                return "spinner"
            elif ratio < 0.10 and region_area < 0.15:
                return "typing"
            elif ratio < 0.30:
                return "scrolling"
            else:
                return "transition"
        except Exception:
            return "unknown"

    async def capture(self) -> Any:
        """Capture screen as a raw PIL Image. Returns None on failure."""
        return await asyncio.to_thread(self._pixel.capture_screenshot)

    # ── OCR with preprocessing ────────────────────────────────────────────────

    async def recognize(self, image: Any = None) -> list[dict[str, Any]]:
        """
        Run preprocessed OCR. If no image is provided, captures the screen.

        Returns a list of word-level dicts:
            ``[{"text", "x", "y", "w", "h", "bbox", "conf"}]``
        when bbox-aware OCR is available, which is required for click
        targeting. Falls back to ``[{"text"}]`` line-level on errors.
        """
        # ── No image provided: capture + bbox OCR ─────────────────────────
        if image is None:
            try:
                items = await asyncio.to_thread(self._pixel.ocr_with_bounds)
                if items:
                    return items
                # Fallback: legacy line-only path
                ocr_text = await asyncio.to_thread(self._pixel.ocr_text)
                if not ocr_text:
                    return []
                lines = [line.strip() for line in ocr_text.split("\n") if line.strip()]
                return [{"text": line} for line in lines]
            except Exception as exc:
                log.warning("rust_vision_bridge.ocr_error", error=str(exc))
                return []

        # ── Image provided as base64 string: decode first ─────────────────
        if isinstance(image, str):
            try:
                import base64
                import io

                from PIL import Image

                img_bytes = base64.b64decode(image)
                image = Image.open(io.BytesIO(img_bytes))
            except Exception as exc:
                log.warning("rust_vision_bridge.recognize_decode_error", error=str(exc))
                return []

        # ── Bbox-aware path on provided PIL image ─────────────────────────
        try:
            items = await asyncio.to_thread(self._pixel._ocr_with_bounds, image)
            if items:
                return items
            # Fallback: legacy line-only
            ocr_text = await asyncio.to_thread(self._pixel._ocr_with_preprocessing, image)
            if not ocr_text:
                return []
            lines = [line.strip() for line in ocr_text.split("\n") if line.strip()]
            return [{"text": line} for line in lines]
        except Exception as exc:
            log.warning("rust_vision_bridge.recognize_error", error=str(exc))
            return []

    async def ocr_regions(self, regions: list[tuple[int, int, int, int]]) -> list[str]:
        """Run preprocessed OCR on specific screen regions."""
        return await asyncio.to_thread(self._pixel.ocr_regions, regions)

    # ── Accessibility ─────────────────────────────────────────────────────────

    async def get_focused_window_tree(self) -> dict[str, Any]:
        """
        Return the active window title and HWND via win32 (Windows only).

        Returns:
            Dict with 'name' (title), 'hwnd' (int), 'children' (empty list).
            Empty dict on non-Windows or error.
        """
        import sys

        if sys.platform != "win32":
            return {"name": "", "children": []}

        def _win32_window_tree() -> dict[str, Any]:
            try:
                import win32gui

                hwnd = win32gui.GetForegroundWindow()
                title = win32gui.GetWindowText(hwnd)
                return {"name": title, "hwnd": hwnd, "children": []}
            except ImportError:
                return {"name": "", "children": []}
            except Exception as exc:
                log.warning("rust_vision_bridge.win32_error", error=str(exc))
                return {"name": "", "children": []}

        return await asyncio.to_thread(_win32_window_tree)

    async def get_active_window_title(self) -> str:
        """Return the title of the currently focused window."""
        tree = await self.get_focused_window_tree()
        return tree.get("name", "") if isinstance(tree, dict) else ""

    async def get_screen_dimensions(self) -> tuple[int, int]:
        """
        Return (width, height) of the primary screen.

        Delegates to PixelAnalyzer.get_screen_dimensions().
        """
        return await asyncio.to_thread(self._pixel.get_screen_dimensions)

    @property
    def dedup_stats(self) -> dict:
        """Frame deduplication statistics."""
        return self._pixel.dedup_stats


import time  # noqa: E402 — import after class for clean top-level imports
