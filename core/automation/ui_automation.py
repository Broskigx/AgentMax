"""Windows UI Automation helpers -- window management via pywinauto/Win32."""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)


class WindowAutomation:
    """High-level window control: find, focus, resize, move."""

    async def get_window(self, title_contains: str = None, visual_target: str = None) -> Any | None:
        # Deprecated name-based matching. Computer control must use vision + mouse.
        # Prefer passing visual_target and let the vision system provide coords to click.
        # This function now only used for low-level legacy window listing if needed.
        if visual_target:
            log.info("window.get_vision", note="use vision agent + click instead of name matching")
        # Returning None forces callers to use pure mouse/vision path.
        return None

    async def focus_window(self, title_contains: str = None, visual_target: str = None) -> bool:
        # Removed word-based focus. AI must click the title bar or use Alt+Tab / vision to focus.
        log.warning("window.focus_deprecated", reason="use vision-based mouse click on window area")
        return False

    async def get_all_windows(self) -> list[dict]:
        try:
            import win32gui

            results = []

            def callback(hwnd: int, _: None) -> bool:
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if title:
                        rect = win32gui.GetWindowRect(hwnd)
                        results.append(
                            {
                                "hwnd": hwnd,
                                "title": title,
                                "rect": list(rect),
                            }
                        )
                return True

            win32gui.EnumWindows(callback, None)
            return results
        except Exception as exc:
            log.warning("windows.enum_error", error=str(exc))
            return []
