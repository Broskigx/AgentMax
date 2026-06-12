"""
Event Recorder -- global mouse/keyboard hook that captures user actions
for later synthesis into an intelligent, replayable agent workflow.

Each click is asynchronously enriched with:
  - OCR text at the click location (what label was under the cursor)
  - Accessibility element name (semantic role from the OS)
  - Window title at the moment of the event
  - Screenshot index (taken at window transitions and significant pauses)

The resulting event log is semantic, not pixel-positional, so the
WorkflowSynthesizer can generate plans that survive UI layout changes.
"""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import asdict, dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_TYPING_MERGE_GAP_SEC = 0.8  # keys within this gap → one "type" event
_SCREENSHOT_ON_WINDOW_CHANGE = True
_MIN_SCROLL_CLICKS = 3  # ignore tiny accidental scrolls


@dataclass
class RecordedEvent:
    type: str  # click | type | key | scroll | window_change
    timestamp: float
    window_title: str
    position: list[int] | None = None  # [x, y] screen coords
    target_text: str | None = None  # OCR text at cursor
    target_element: str | None = None  # accessibility name/role
    value: str | None = None  # typed text, key name, scroll amount
    screenshot_idx: int | None = None  # index into self.screenshots list


class EventRecorder:
    """
    Records user activity using pynput global listeners.

    Thread-safety note: pynput callbacks run in daemon threads.
    All async work (screenshots, OCR, accessibility) is dispatched
    with asyncio.run_coroutine_threadsafe into the main event loop.
    """

    def __init__(self, capture: Any, accessibility: Any, ocr: Any) -> None:
        self._capture = capture
        self._accessibility = accessibility
        self._ocr = ocr

        self.events: list[RecordedEvent] = []
        self.screenshots: list[str] = []  # base64-encoded PNG

        self._is_recording = False
        self._mouse_listener: Any = None
        self._keyboard_listener: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None

        # Typing accumulation
        self._type_chars: list[str] = []
        self._type_window: str = ""
        self._last_key_time: float = 0.0

        self._current_window: str = ""

    # ──────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._is_recording = True
        self._loop = asyncio.get_running_loop()
        self.events.clear()
        self.screenshots.clear()
        self._type_chars.clear()

        # Initial screenshot for synthesizer context
        await self._capture_screenshot()

        try:
            from pynput import keyboard, mouse

            self._mouse_listener = mouse.Listener(
                on_click=self._on_click,
                on_scroll=self._on_scroll,
            )
            self._keyboard_listener = keyboard.Listener(
                on_press=self._on_key_press,
            )
            self._mouse_listener.start()
            self._keyboard_listener.start()
            log.info("recorder.started")
        except ImportError:
            log.warning("recorder.pynput_not_available -- recording mouse/keyboard hooks skipped")

    async def stop(self) -> list[RecordedEvent]:
        self._is_recording = False
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if self._keyboard_listener:
            self._keyboard_listener.stop()
            self._keyboard_listener = None

        self._flush_type_buffer()
        await self._capture_screenshot()
        log.info("recorder.stopped", events=len(self.events), screenshots=len(self.screenshots))
        return list(self.events)

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    # ──────────────────────────────────────────────────────────────
    # pynput callbacks (run in daemon threads)
    # ──────────────────────────────────────────────────────────────

    def _on_click(self, x: int, y: int, button: Any, pressed: bool) -> None:
        if not self._is_recording or not pressed:
            return

        self._flush_type_buffer()

        win_title = self._get_foreground_title()
        event = RecordedEvent(
            type="click",
            timestamp=time.time(),
            window_title=win_title,
            position=[x, y],
        )
        self.events.append(event)

        # Async enrichment: OCR + accessibility + maybe screenshot
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._enrich_click(event, x, y, win_title),
                self._loop,
            )

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        if not self._is_recording:
            return
        self._flush_type_buffer()
        direction = "up" if dy > 0 else "down"
        last = self.events[-1] if self.events else None
        # Merge consecutive same-direction scrolls
        if (
            last
            and last.type == "scroll"
            and last.value
            and last.value.startswith(direction)
            and time.time() - last.timestamp < 1.0
        ):
            parts = last.value.split(":")
            amt = int(parts[1]) + abs(dy or dx) if len(parts) > 1 else abs(dy or dx)
            last.value = f"{direction}:{amt}"
            last.timestamp = time.time()
        else:
            self.events.append(
                RecordedEvent(
                    type="scroll",
                    timestamp=time.time(),
                    window_title=self._get_foreground_title(),
                    value=f"{direction}:{abs(dy or dx)}",
                )
            )

    def _on_key_press(self, key: Any) -> None:
        if not self._is_recording:
            return
        try:
            char = key.char
            if char:
                now = time.time()
                if now - self._last_key_time > _TYPING_MERGE_GAP_SEC:
                    self._flush_type_buffer()
                    self._type_window = self._get_foreground_title()
                self._type_chars.append(char)
                self._last_key_time = now
                return
        except AttributeError:
            pass

        # Special key (Enter, Tab, F-key, etc.)
        self._flush_type_buffer()
        key_name = str(key).replace("Key.", "")
        self.events.append(
            RecordedEvent(
                type="key",
                timestamp=time.time(),
                window_title=self._get_foreground_title(),
                value=key_name,
            )
        )

    # ──────────────────────────────────────────────────────────────
    # Async enrichment
    # ──────────────────────────────────────────────────────────────

    async def _enrich_click(self, event: RecordedEvent, x: int, y: int, win_title: str) -> None:
        try:
            # Screenshot if window changed
            if win_title != self._current_window:
                self._current_window = win_title
                event.screenshot_idx = await self._capture_screenshot()
                self.events.append(
                    RecordedEvent(
                        type="window_change",
                        timestamp=event.timestamp - 0.001,
                        window_title=win_title,
                        screenshot_idx=event.screenshot_idx,
                    )
                )

            # OCR text under cursor (crop a 120×40 region around click)
            crop = await self._capture.capture(region=(max(0, x - 60), max(0, y - 20), 120, 40))
            if crop and self._ocr:
                results = await asyncio.to_thread(self._ocr.recognize, crop["image"])
                if results:
                    event.target_text = results[0].get("text", "")

            # Accessibility element
            elements = await self._accessibility.get_all_elements()
            for el in elements:
                bounds = el.get("bounds", (0, 0, 0, 0))
                bx, by, bw, bh = bounds
                if bx <= x <= bx + bw and by <= y <= by + bh:
                    event.target_element = el.get("name", el.get("role", ""))
                    break

        except Exception as exc:
            log.debug("recorder.enrich_error", error=str(exc))

    async def _capture_screenshot(self) -> int:
        try:
            frame = await self._capture.capture()
            if frame:
                idx = len(self.screenshots)
                b64 = base64.b64encode(frame["png_bytes"]).decode()
                self.screenshots.append(b64)
                return idx
        except Exception:
            pass
        return -1

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────

    def _flush_type_buffer(self) -> None:
        if not self._type_chars:
            return
        text = "".join(self._type_chars)
        self._type_chars.clear()
        self.events.append(
            RecordedEvent(
                type="type",
                timestamp=self._last_key_time,
                window_title=self._type_window,
                value=text,
            )
        )

    @staticmethod
    def _get_foreground_title() -> str:
        try:
            import win32gui

            return win32gui.GetWindowText(win32gui.GetForegroundWindow())
        except Exception:
            return ""

    def export_events(self) -> list[dict]:
        return [asdict(e) for e in self.events]
