"""Window manager -- enumerate, focus, move, minimize, maximize windows.

Platform support:
  Windows -- win32gui / win32con (pywin32)
  macOS   -- AppKit via pyobjc (best-effort; hwnd treated as NSWindow ptr)
  Linux   -- wmctrl subprocess (X11); requires wmctrl installed
"""

from __future__ import annotations

import asyncio
import subprocess
import sys

import structlog

log = structlog.get_logger(__name__)


class WindowManager:
    async def minimize(self, hwnd: int) -> None:
        try:
            if sys.platform == "win32":
                import win32con
                import win32gui

                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            elif sys.platform == "darwin":
                await self._macos_set_miniaturized(hwnd, True)
            else:
                await self._wmctrl(["wmctrl", "-i", "-r", hex(hwnd), "-b", "add,hidden"])
        except Exception as exc:
            log.warning("window.minimize_error", platform=sys.platform, error=str(exc))

    async def restore(self, hwnd: int) -> None:
        try:
            if sys.platform == "win32":
                import win32con
                import win32gui

                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
            elif sys.platform == "darwin":
                await self._macos_set_miniaturized(hwnd, False)
            else:
                await self._wmctrl(["wmctrl", "-i", "-r", hex(hwnd), "-b", "remove,hidden"])
                await self._wmctrl(["wmctrl", "-i", "-a", hex(hwnd)])
        except Exception as exc:
            log.warning("window.restore_error", platform=sys.platform, error=str(exc))

    async def move_resize(self, hwnd: int, x: int, y: int, w: int, h: int) -> None:
        try:
            if sys.platform == "win32":
                import win32gui

                win32gui.MoveWindow(hwnd, x, y, w, h, True)
            elif sys.platform == "darwin":
                await self._macos_move_resize(hwnd, x, y, w, h)
            else:
                # wmctrl -e gravity,x,y,w,h  (gravity 0 = ignore)
                await self._wmctrl(["wmctrl", "-i", "-r", hex(hwnd), "-e", f"0,{x},{y},{w},{h}"])
        except Exception as exc:
            log.warning("window.move_error", platform=sys.platform, error=str(exc))

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _wmctrl(self, cmd: list[str]) -> None:
        """Run a wmctrl command (Linux X11)."""
        await asyncio.to_thread(
            subprocess.run,
            cmd,
            check=False,
            timeout=3,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    async def _macos_set_miniaturized(self, hwnd: int, miniaturize: bool) -> None:
        try:
            import AppKit  # type: ignore[import]

            win = AppKit.NSApp.windowWithWindowNumber_(hwnd)
            if win:
                if miniaturize:
                    win.miniaturize_(None)
                else:
                    win.deminiaturize_(None)
        except Exception as exc:
            log.warning("window.macos_miniaturize_error", error=str(exc))

    async def _macos_move_resize(self, hwnd: int, x: int, y: int, w: int, h: int) -> None:
        try:
            import AppKit  # type: ignore[import]
            import Foundation  # type: ignore[import]

            win = AppKit.NSApp.windowWithWindowNumber_(hwnd)
            if win:
                frame = Foundation.NSMakeRect(x, y, w, h)
                win.setFrame_display_(frame, True)
        except Exception as exc:
            log.warning("window.macos_move_error", error=str(exc))
