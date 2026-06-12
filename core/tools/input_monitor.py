"""User input safety monitoring for desktop automation.

The monitor distinguishes agent-owned cursor movement from physical user
movement by recording short-lived expected cursor targets around tool actions.
If the cursor moves outside that expected envelope while a task is active, the
pause controller blocks new mouse/keyboard actions until the user has been idle
for a full quiet window.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

CursorProvider = Callable[[], tuple[int, int]]
KeyboardProvider = Callable[[], bool]


def _default_cursor_provider() -> tuple[int, int]:
    try:
        import pyautogui

        pos = pyautogui.position()
        return int(pos.x), int(pos.y)
    except Exception:
        pass
    try:
        import win32api

        return tuple(map(int, win32api.GetCursorPos()))
    except Exception:
        return 0, 0


def _default_keyboard_provider() -> bool:
    return False


@dataclass(slots=True)
class LastKnownCursorState:
    x: int = 0
    y: int = 0
    timestamp: float = 0.0
    source: str = "unknown"


class MouseIdleDetector:
    def __init__(
        self,
        cursor_provider: CursorProvider = _default_cursor_provider,
        *,
        movement_threshold_px: int = 4,
        idle_after_sec: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cursor_provider = cursor_provider
        self.movement_threshold_px = movement_threshold_px
        self.idle_after_sec = idle_after_sec
        self.clock = clock
        x, y = self.cursor_provider()
        now = self.clock()
        self.last_state = LastKnownCursorState(x=x, y=y, timestamp=now, source="initial")
        self.last_user_activity_at = now
        self._agent_targets: list[tuple[int, int, float]] = []

    def record_agent_move(
        self, x: int, y: int, tolerance_px: int = 8, ttl_sec: float = 1.5
    ) -> None:
        self._agent_targets.append((int(x), int(y), self.clock() + ttl_sec))
        self._agent_targets = self._agent_targets[-32:]

    def sample(self) -> tuple[bool, LastKnownCursorState]:
        now = self.clock()
        x, y = self.cursor_provider()
        old = self.last_state
        moved = (
            abs(x - old.x) > self.movement_threshold_px
            or abs(y - old.y) > self.movement_threshold_px
        )
        source = "stable"
        if moved:
            source = "agent" if self._matches_agent_target(x, y, now) else "user"
            if source == "user":
                self.last_user_activity_at = now
        self.last_state = LastKnownCursorState(x=x, y=y, timestamp=now, source=source)
        self._agent_targets = [target for target in self._agent_targets if target[2] >= now]
        return source == "user", self.last_state

    def is_idle(self) -> bool:
        return (self.clock() - self.last_user_activity_at) >= self.idle_after_sec

    def _matches_agent_target(self, x: int, y: int, now: float) -> bool:
        for target_x, target_y, expires_at in self._agent_targets:
            if expires_at < now:
                continue
            if abs(x - target_x) <= 12 and abs(y - target_y) <= 12:
                return True
        return False


class KeyboardActivityDetector:
    def __init__(
        self,
        keyboard_provider: KeyboardProvider = _default_keyboard_provider,
        *,
        idle_after_sec: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.keyboard_provider = keyboard_provider
        self.idle_after_sec = idle_after_sec
        self.clock = clock
        self.last_activity_at = clock()

    def sample(self) -> bool:
        active = bool(self.keyboard_provider())
        if active:
            self.last_activity_at = self.clock()
        return active

    def is_idle(self) -> bool:
        return (self.clock() - self.last_activity_at) >= self.idle_after_sec


class AgentPauseController:
    def __init__(self) -> None:
        self.paused = False
        self.reason: str | None = None
        self.paused_task_id: str | None = None
        self._event = asyncio.Event()
        self._event.set()

    def pause(self, task_id: str | None, reason: str) -> None:
        self.paused = True
        self.reason = reason
        self.paused_task_id = task_id
        self._event.clear()

    def resume(self) -> None:
        self.paused = False
        self.reason = None
        self.paused_task_id = None
        self._event.set()

    async def wait_if_paused(self) -> None:
        await self._event.wait()


class UserOverrideDetector:
    def __init__(self, mouse: MouseIdleDetector, keyboard: KeyboardActivityDetector) -> None:
        self.mouse = mouse
        self.keyboard = keyboard

    def sample(self) -> tuple[bool, str | None]:
        user_mouse, _state = self.mouse.sample()
        user_keyboard = self.keyboard.sample()
        if user_mouse:
            return True, "mouse_moved_by_user"
        if user_keyboard:
            return True, "keyboard_activity_by_user"
        return False, None


class SafeResumeManager:
    def __init__(
        self,
        mouse: MouseIdleDetector,
        keyboard: KeyboardActivityDetector,
        *,
        window_validator: Callable[[], bool] | None = None,
        context_validator: Callable[[], bool] | None = None,
    ) -> None:
        self.mouse = mouse
        self.keyboard = keyboard
        self.window_validator = window_validator or (lambda: True)
        self.context_validator = context_validator or (lambda: True)

    def can_resume(self) -> tuple[bool, str | None]:
        if not self.mouse.is_idle():
            return False, "mouse_not_idle"
        if not self.keyboard.is_idle():
            return False, "keyboard_not_idle"
        if not self.window_validator():
            return False, "unsafe_window_change"
        if not self.context_validator():
            return False, "context_changed"
        return True, None


class UserInputMonitor:
    """Background monitor used by ToolExecutor during task execution."""

    def __init__(
        self,
        *,
        cursor_provider: CursorProvider = _default_cursor_provider,
        keyboard_provider: KeyboardProvider = _default_keyboard_provider,
        poll_interval_sec: float = 0.075,
        idle_after_sec: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.mouse = MouseIdleDetector(
            cursor_provider,
            idle_after_sec=idle_after_sec,
            clock=clock,
        )
        self.keyboard = KeyboardActivityDetector(
            keyboard_provider,
            idle_after_sec=idle_after_sec,
            clock=clock,
        )
        self.pause_controller = AgentPauseController()
        self.override_detector = UserOverrideDetector(self.mouse, self.keyboard)
        self.resume_manager = SafeResumeManager(self.mouse, self.keyboard)
        self.poll_interval_sec = poll_interval_sec
        self.clock = clock
        self._task: asyncio.Task[None] | None = None
        self._active_task_id: str | None = None
        self._running = False

    @property
    def status(self) -> dict[str, object]:
        return {
            "paused": self.pause_controller.paused,
            "reason": self.pause_controller.reason,
            "task_id": self._active_task_id,
            "mouse_idle": self.mouse.is_idle(),
            "keyboard_idle": self.keyboard.is_idle(),
            "last_cursor": asdict(self.mouse.last_state),
        }

    def start_task(self, task_id: str | None) -> None:
        self._active_task_id = task_id
        self._running = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="tool-user-input-monitor")

    def stop_task(self) -> None:
        self._active_task_id = None
        self._running = False
        self.pause_controller.resume()
        if self._task and not self._task.done():
            self._task.cancel()

    def record_agent_mouse_action(self, x: int, y: int) -> None:
        self.mouse.record_agent_move(x, y)

    async def wait_until_safe(self) -> None:
        while self.pause_controller.paused:
            can_resume, _reason = self.resume_manager.can_resume()
            if can_resume:
                self.pause_controller.resume()
                return
            await asyncio.sleep(self.poll_interval_sec)
        await self.pause_controller.wait_if_paused()

    async def _loop(self) -> None:
        try:
            while self._running:
                override, reason = self.override_detector.sample()
                if override and not self.pause_controller.paused:
                    self.pause_controller.pause(self._active_task_id, reason or "user_input")
                elif self.pause_controller.paused:
                    can_resume, _resume_reason = self.resume_manager.can_resume()
                    if can_resume:
                        self.pause_controller.resume()
                await asyncio.sleep(self.poll_interval_sec)
        except asyncio.CancelledError:
            pass
