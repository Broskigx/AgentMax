"""
Human Input Simulator -- cross-platform, DPI-aware, humanised mouse & keyboard.

Backend priority
----------------
Windows  : pydirectinput (DirectInput, game-compatible) → pyautogui → pynput
macOS    : pyautogui → pynput
Linux    : pyautogui → pynput

Key improvements over v1
------------------------
- Pynput Controllers cached (single instance per process -- big perf win)
- DPI-aware on Windows: SetProcessDpiAwareness(2) called at init
- Screen bounds detection + coordinate clamping (no out-of-bounds moves)
- drag_to()        -- smooth mouse drag with Bézier path
- middle_click()   -- middle mouse button
- key_down() / key_up() -- raw key hold/release (game automation)
- press_hotkey()   -- all backends including pynput
- scroll()         -- vertical + horizontal on all backends
- type_text()      -- unicode-safe (pynput.Controller.type for non-ASCII)
- get_screen_size() -- cached, multi-monitor aware
"""

from __future__ import annotations

import math
import random
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class HumanInputSimulator:
    def __init__(self, config: Any) -> None:
        self._config = config
        self._backend = self._init_backend()

        # Cached pynput controllers -- created once, reused every call
        self._mouse_ctrl: Any = None
        self._kbd_ctrl: Any = None
        if self._backend == "pynput":
            self._mouse_ctrl, self._kbd_ctrl = self._init_pynput_controllers()

        # Screen dimensions (cached)
        self._screen_w, self._screen_h = self._detect_screen_size()

        # Last known cursor position (avoids querying OS every move)
        self._last_x, self._last_y = self._get_cursor_pos()

        log.info(
            "input.ready",
            backend=self._backend,
            screen=f"{self._screen_w}x{self._screen_h}",
        )

    # ── Backend initialisation ────────────────────────────────────────────────

    def _init_backend(self) -> str:
        self._enable_dpi_awareness()

        try:
            import pyautogui

            pyautogui.FAILSAFE = False
            pyautogui.PAUSE = 0
            return "pyautogui"
        except ImportError:
            pass

        if sys.platform == "win32":
            try:
                import pydirectinput

                pydirectinput.FAILSAFE = False
                return "direct"
            except ImportError:
                pass

        try:
            from pynput import keyboard, mouse  # noqa: F401

            return "pynput"
        except ImportError:
            pass

        log.error("input.no_backend -- all input libraries missing")
        return "none"

    def _init_pynput_controllers(self) -> tuple[Any, Any]:
        from pynput.keyboard import Controller as KC
        from pynput.mouse import Controller as MC

        return MC(), KC()

    def _enable_dpi_awareness(self) -> None:
        """
        Tell Windows we are per-monitor DPI-aware so that mouse coordinates
        and screen-size queries return physical pixels, not logical pixels.
        Without this, a 200% scaled 4K monitor reports 1920×1080 instead of
        3840×2160 and every click lands in the wrong spot.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes

            # PROCESS_PER_MONITOR_DPI_AWARE = 2
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    # ── Screen size ───────────────────────────────────────────────────────────

    def _detect_screen_size(self) -> tuple[int, int]:
        """Return (width, height) of the primary screen in physical pixels."""
        try:
            import pyautogui

            return pyautogui.size()
        except Exception:
            pass

        if sys.platform == "win32":
            try:
                import ctypes

                user32 = ctypes.windll.user32
                # SM_CXVIRTUALSCREEN / SM_CYVIRTUALSCREEN spans all monitors
                w = user32.GetSystemMetrics(78)
                h = user32.GetSystemMetrics(79)
                if w > 0 and h > 0:
                    return w, h
            except Exception:
                pass

        if sys.platform == "darwin":
            try:
                import Quartz  # type: ignore[import]

                main = Quartz.CGMainDisplayID()
                return (
                    Quartz.CGDisplayPixelsWide(main),
                    Quartz.CGDisplayPixelsHigh(main),
                )
            except Exception:
                pass

        if sys.platform == "linux":
            try:
                import subprocess

                out = subprocess.check_output(
                    ["xdpyinfo"], timeout=3, stderr=subprocess.DEVNULL
                ).decode()
                for line in out.splitlines():
                    if "dimensions:" in line:
                        part = line.split("dimensions:")[1].strip().split()[0]
                        w, h = part.split("x")
                        return int(w), int(h)
            except Exception:
                pass

        return 1920, 1080  # safe fallback

    def get_screen_size(self) -> tuple[int, int]:
        return self._screen_w, self._screen_h

    # ── Coordinate helpers ────────────────────────────────────────────────────

    def _clamp(self, x: int, y: int) -> tuple[int, int]:
        """Ensure coordinates are within screen bounds."""
        x = max(0, min(x, self._screen_w - 1))
        y = max(0, min(y, self._screen_h - 1))
        return x, y

    # ── Cursor position ───────────────────────────────────────────────────────

    def _get_cursor_pos(self) -> tuple[int, int]:
        try:
            import pyautogui

            p = pyautogui.position()
            return int(p.x), int(p.y)
        except Exception:
            pass
        if sys.platform == "win32":
            try:
                import win32api

                return win32api.GetCursorPos()
            except Exception:
                pass
        if self._mouse_ctrl is not None:
            try:
                pos = self._mouse_ctrl.position
                return int(pos[0]), int(pos[1])
            except Exception:
                pass
        return 0, 0

    # ── Low-level move ────────────────────────────────────────────────────────

    def _raw_move(self, x: int, y: int) -> None:
        x, y = self._clamp(x, y)
        if self._backend == "direct":
            import pydirectinput

            pydirectinput.moveTo(x, y)
        elif self._backend == "pyautogui":
            import pyautogui

            pyautogui.moveTo(x, y, duration=0, _pause=False)
        elif self._backend == "pynput" and self._mouse_ctrl:
            self._mouse_ctrl.position = (x, y)

    # ── Public: Mouse movement ────────────────────────────────────────────────

    def move_to(self, x: int, y: int) -> None:
        """Move cursor to (x, y) with a humanised Bézier curve."""
        x, y = self._clamp(x, y)
        cx, cy = self._last_x, self._last_y

        steps = self._calc_steps(cx, cy, x, y)
        points = self._bezier_path(cx, cy, x, y, steps)

        jitter = self._config.mouse_jitter_px
        for px, py in points:
            jx = px + random.gauss(0, jitter)
            jy = py + random.gauss(0, jitter)
            self._raw_move(int(jx), int(jy))
            time.sleep(max(0.001, random.gauss(0.004, 0.001)))

        self._raw_move(x, y)  # land exactly on target
        self._last_x, self._last_y = x, y

    def move_relative(self, dx: int, dy: int) -> None:
        """Move cursor by (dx, dy) relative to current position."""
        cx, cy = self._get_cursor_pos()
        self.move_to(cx + dx, cy + dy)

    # ── Public: Mouse buttons ─────────────────────────────────────────────────

    def click(self, x: int, y: int, button: str = "left") -> None:
        """Move to (x, y) and click. button: 'left' | 'right' | 'middle' | 'double'."""
        self.move_to(x, y)
        time.sleep(random.gauss(0.07, 0.015))
        self._do_click(x, y, button)
        time.sleep(random.gauss(0.05, 0.01))

    def double_click(self, x: int, y: int, button: str = "left") -> None:
        """Double-click at (x, y)."""
        self.move_to(x, y)
        time.sleep(random.gauss(0.06, 0.01))
        self._do_click(x, y, button)
        time.sleep(random.uniform(0.04, 0.08))
        self._do_click(x, y, button)
        time.sleep(random.gauss(0.04, 0.008))

    def middle_click(self, x: int, y: int) -> None:
        """Middle-click at (x, y)."""
        self.move_to(x, y)
        time.sleep(random.gauss(0.06, 0.01))
        self._do_click(x, y, "middle")

    def right_click(self, x: int, y: int) -> None:
        """Right-click at (x, y)."""
        self.click(x, y, button="right")

    def mouse_down(self, button: str = "left") -> None:
        self._mouse_press(button)

    def mouse_up(self, button: str = "left") -> None:
        self._mouse_release(button)

    def drag_path(
        self,
        points: list[tuple[int, int]] | list[list[int]],
        *,
        button: str = "left",
        duration_ms: int = 500,
    ) -> None:
        if len(points) < 2:
            raise ValueError("drag_path requires at least two points")
        normalized = [self._clamp(int(point[0]), int(point[1])) for point in points]
        interpolated: list[tuple[int, int]] = [normalized[0]]
        for start, end in zip(normalized, normalized[1:], strict=False):
            distance = math.hypot(end[0] - start[0], end[1] - start[1])
            steps = max(2, int(distance / 16))
            for index in range(1, steps + 1):
                ratio = index / steps
                interpolated.append(
                    (
                        round(start[0] + (end[0] - start[0]) * ratio),
                        round(start[1] + (end[1] - start[1]) * ratio),
                    )
                )
        self.move_to(*normalized[0])
        self.mouse_down(button)
        try:
            delay = max(0.001, duration_ms / 1000.0 / max(1, len(interpolated) - 1))
            for x, y in interpolated[1:]:
                self._raw_move(x, y)
                time.sleep(delay)
        finally:
            self.mouse_up(button)
        self._last_x, self._last_y = normalized[-1]

    def drag_to(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        button: str = "left",
        duration: float | None = None,
    ) -> None:
        """
        Press button at (x0, y0), move to (x1, y1) with Bézier, release.
        duration overrides mouse_speed_px_per_sec for this move only.
        """
        self.move_to(x0, y0)
        time.sleep(random.gauss(0.05, 0.01))
        self.mouse_down(button)
        time.sleep(random.gauss(0.03, 0.005))

        x1, y1 = self._clamp(x1, y1)
        steps = self._calc_steps(x0, y0, x1, y1)
        if duration is not None and duration > 0:
            delay_per_step = duration / max(steps, 1)
        else:
            delay_per_step = None

        points = self._bezier_path(x0, y0, x1, y1, steps)
        jitter = self._config.mouse_jitter_px * 0.5  # less jitter while dragging
        for px, py in points:
            jx = px + random.gauss(0, jitter)
            jy = py + random.gauss(0, jitter)
            self._raw_move(int(jx), int(jy))
            if delay_per_step is not None:
                time.sleep(delay_per_step)
            else:
                time.sleep(max(0.001, random.gauss(0.004, 0.001)))

        self._raw_move(x1, y1)
        time.sleep(random.gauss(0.03, 0.005))
        self.mouse_up(button)
        self._last_x, self._last_y = x1, y1

    def scroll(
        self,
        x: int,
        y: int,
        direction: str = "down",
        amount: int = 3,
        *,
        horizontal: bool = False,
    ) -> None:
        """
        Scroll at (x, y).
        direction: 'down' | 'up' | 'right' | 'left'
        horizontal: True for horizontal scroll (overrides direction for axis)
        """
        self.move_to(x, y)
        time.sleep(random.gauss(0.03, 0.005))

        # Map direction → signed scroll amounts (dx, dy)
        if horizontal or direction in ("left", "right"):
            dx = amount if direction in ("right",) else -amount
            dy = 0
        else:
            dx = 0
            dy = -amount if direction == "down" else amount

        if self._backend == "direct":
            import pydirectinput

            if dy != 0:
                pydirectinput.scroll(dy)
            # pydirectinput doesn't expose hscroll; fall through to win32
            if dx != 0:
                self._win32_hscroll(x, y, dx)

        elif self._backend == "pyautogui":
            import pyautogui

            if dy != 0:
                pyautogui.scroll(dy, x=x, y=y)
            if dx != 0:
                try:
                    pyautogui.hscroll(dx, x=x, y=y)
                except AttributeError:
                    self._win32_hscroll(x, y, dx)

        elif self._backend == "pynput" and self._mouse_ctrl:
            self._mouse_ctrl.scroll(dx, dy)

    # ── Public: Keyboard ──────────────────────────────────────────────────────

    def type_text(self, text: str) -> None:
        """Type text with human-like per-character timing."""
        base_delay = 60.0 / (self._config.typing_wpm * 5)
        variance = self._config.typing_variance

        for char in text:
            self._type_char(char)
            delay = max(0.015, random.gauss(base_delay, base_delay * variance))
            time.sleep(delay)
            # Occasional micro-pause (simulates thinking)
            if random.random() < 0.002:
                time.sleep(random.uniform(0.3, 0.9))

    def press_hotkey(self, keys: str) -> None:
        """
        Press a key combination: 'ctrl+c', 'ctrl+shift+esc', etc.
        Works on all backends.
        """
        parts = [k.strip() for k in keys.lower().replace("+", " ").split()]

        if self._backend == "direct":
            import pydirectinput

            pydirectinput.hotkey(*parts)

        elif self._backend == "pyautogui":
            import pyautogui

            pyautogui.hotkey(*parts)

        elif self._backend == "pynput" and self._kbd_ctrl:
            mapped = [self._map_key(p) for p in parts]
            for k in mapped:
                self._kbd_ctrl.press(k)
            time.sleep(random.gauss(0.05, 0.01))
            for k in reversed(mapped):
                self._kbd_ctrl.release(k)

    def key_down(self, key: str) -> None:
        """Press and hold a key (for game automation)."""
        if self._backend == "direct":
            import pydirectinput

            pydirectinput.keyDown(key)
        elif self._backend == "pyautogui":
            import pyautogui

            pyautogui.keyDown(key)
        elif self._backend == "pynput" and self._kbd_ctrl:
            self._kbd_ctrl.press(self._map_key(key))

    def key_up(self, key: str) -> None:
        """Release a held key."""
        if self._backend == "direct":
            import pydirectinput

            pydirectinput.keyUp(key)
        elif self._backend == "pyautogui":
            import pyautogui

            pyautogui.keyUp(key)
        elif self._backend == "pynput" and self._kbd_ctrl:
            self._kbd_ctrl.release(self._map_key(key))

    @contextmanager
    def hold_key(self, key: str) -> Generator[None, None, None]:
        """Context manager: hold a key for the duration of the block."""
        self.key_down(key)
        try:
            yield
        finally:
            self.key_up(key)

    # ── Internal: click helpers ───────────────────────────────────────────────

    def _do_click(self, x: int, y: int, button: str) -> None:
        if self._backend == "direct":
            import pydirectinput

            _map = {
                "left": pydirectinput.click,
                "right": pydirectinput.rightClick,
                "middle": pydirectinput.middleClick,
                "double": pydirectinput.doubleClick,
            }
            _map.get(button, pydirectinput.click)(x, y)

        elif self._backend == "pyautogui":
            import pyautogui

            if button == "double":
                pyautogui.doubleClick(x, y)
            elif button == "middle":
                pyautogui.middleClick(x, y)
            elif button == "right":
                pyautogui.rightClick(x, y)
            else:
                pyautogui.click(x, y)

        elif self._backend == "pynput" and self._mouse_ctrl:
            from pynput.mouse import Button

            _btn_map = {
                "left": Button.left,
                "right": Button.right,
                "middle": Button.middle,
                "double": Button.left,
            }
            btn = _btn_map.get(button, Button.left)
            count = 2 if button == "double" else 1
            self._mouse_ctrl.click(btn, count)

    def _mouse_press(self, button: str) -> None:
        if self._backend == "direct":
            import pydirectinput

            pydirectinput.mouseDown(button=button if button != "double" else "left")
        elif self._backend == "pyautogui":
            import pyautogui

            pyautogui.mouseDown(button=button if button != "double" else "left")
        elif self._backend == "pynput" and self._mouse_ctrl:
            from pynput.mouse import Button

            self._mouse_ctrl.press(Button.left if button != "right" else Button.right)

    def _mouse_release(self, button: str) -> None:
        if self._backend == "direct":
            import pydirectinput

            pydirectinput.mouseUp(button=button if button != "double" else "left")
        elif self._backend == "pyautogui":
            import pyautogui

            pyautogui.mouseUp(button=button if button != "double" else "left")
        elif self._backend == "pynput" and self._mouse_ctrl:
            from pynput.mouse import Button

            self._mouse_ctrl.release(Button.left if button != "right" else Button.right)

    # ── Internal: keyboard helpers ────────────────────────────────────────────

    def _type_char(self, char: str) -> None:
        """Type a single character, unicode-safe."""
        if self._backend == "direct":
            import pydirectinput

            try:
                # pydirectinput only handles ASCII printable chars
                if ord(char) < 128 and char.isprintable():
                    pydirectinput.typewrite(char, interval=0)
                else:
                    self._unicode_type(char)
            except Exception:
                self._unicode_type(char)

        elif self._backend == "pyautogui":
            import pyautogui

            try:
                if ord(char) < 128 and char.isprintable():
                    pyautogui.typewrite(char, interval=0)
                else:
                    self._unicode_type(char)
            except Exception:
                self._unicode_type(char)

        elif self._backend == "pynput" and self._kbd_ctrl:
            try:
                self._kbd_ctrl.type(char)
            except Exception:
                pass

        else:
            self._unicode_type(char)

    def _unicode_type(self, char: str) -> None:
        """Type a unicode character using the best available method."""
        if sys.platform == "win32":
            try:
                import ctypes

                # SendInput with KEYEVENTF_UNICODE
                INPUT_KEYBOARD = 1
                KEYEVENTF_UNICODE = 0x0004
                KEYEVENTF_KEYUP = 0x0002

                class KEYBDINPUT(ctypes.Structure):
                    _fields_ = [
                        ("wVk", ctypes.c_ushort),
                        ("wScan", ctypes.c_ushort),
                        ("dwFlags", ctypes.c_ulong),
                        ("time", ctypes.c_ulong),
                        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
                    ]

                class INPUT(ctypes.Structure):
                    class _INPUT(ctypes.Union):
                        _fields_ = [("ki", KEYBDINPUT)]

                    _anonymous_ = ("_input",)
                    _fields_ = [("type", ctypes.c_ulong), ("_input", _INPUT)]

                extra = ctypes.c_ulong(0)
                for codepoint in char:
                    code = ord(codepoint)
                    inp_down = INPUT(
                        type=INPUT_KEYBOARD,
                        ki=KEYBDINPUT(
                            wVk=0,
                            wScan=code,
                            dwFlags=KEYEVENTF_UNICODE,
                            time=0,
                            dwExtraInfo=ctypes.pointer(extra),
                        ),
                    )
                    inp_up = INPUT(
                        type=INPUT_KEYBOARD,
                        ki=KEYBDINPUT(
                            wVk=0,
                            wScan=code,
                            dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                            time=0,
                            dwExtraInfo=ctypes.pointer(extra),
                        ),
                    )
                    ctypes.windll.user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
                    ctypes.windll.user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))
                return
            except Exception:
                pass

        # Fallback: pynput (works on all platforms for unicode)
        if self._kbd_ctrl is not None:
            try:
                self._kbd_ctrl.type(char)
                return
            except Exception:
                pass

        # Last resort: xdotool on Linux
        if sys.platform == "linux":
            try:
                import subprocess

                subprocess.run(
                    ["xdotool", "type", "--clearmodifiers", char],
                    check=False,
                    timeout=2,
                    capture_output=True,
                )
            except Exception:
                pass

    def _map_key(self, key_str: str) -> Any:
        """Map a string like 'ctrl', 'shift', 'a' to a pynput Key or KeyCode."""
        from pynput.keyboard import Key, KeyCode

        _special: dict[str, Any] = {
            "ctrl": Key.ctrl,
            "control": Key.ctrl,
            "shift": Key.shift,
            "alt": Key.alt,
            "cmd": Key.cmd,
            "super": Key.cmd,
            "win": Key.cmd,
            "enter": Key.enter,
            "return": Key.enter,
            "esc": Key.esc,
            "escape": Key.esc,
            "tab": Key.tab,
            "space": Key.space,
            "backspace": Key.backspace,
            "delete": Key.delete,
            "home": Key.home,
            "end": Key.end,
            "pageup": Key.page_up,
            "pagedown": Key.page_down,
            "up": Key.up,
            "down": Key.down,
            "left": Key.left,
            "right": Key.right,
            "f1": Key.f1,
            "f2": Key.f2,
            "f3": Key.f3,
            "f4": Key.f4,
            "f5": Key.f5,
            "f6": Key.f6,
            "f7": Key.f7,
            "f8": Key.f8,
            "f9": Key.f9,
            "f10": Key.f10,
            "f11": Key.f11,
            "f12": Key.f12,
        }
        if key_str in _special:
            return _special[key_str]
        return KeyCode.from_char(key_str[0]) if key_str else Key.space

    # ── Internal: horizontal scroll (Windows fallback) ────────────────────────

    def _win32_hscroll(self, x: int, y: int, dx: int) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes

            MOUSEEVENTF_HWHEEL = 0x01000
            WHEEL_DELTA = 120
            ctypes.windll.user32.mouse_event(MOUSEEVENTF_HWHEEL, x, y, dx * WHEEL_DELTA, 0)
        except Exception:
            pass

    # ── Fallback for non-standard key type ────────────────────────────────────

    def _fallback_type(self, char: str) -> None:
        self._unicode_type(char)

    # ── Bézier motion path ────────────────────────────────────────────────────

    def _bezier_path(self, x0: int, y0: int, x1: int, y1: int, steps: int) -> list[tuple[int, int]]:
        dist = math.hypot(x1 - x0, y1 - y0)
        if dist < 2:
            return [(x1, y1)]

        var = dist * self._config.bezier_control_variance

        # Two randomised cubic Bézier control points
        cx1 = x0 + random.gauss(dist * 0.33, var * 0.15)
        cy1 = y0 + random.gauss(dist * 0.10, var * 0.20)
        cx2 = x0 + random.gauss(dist * 0.66, var * 0.15)
        cy2 = y1 + random.gauss(dist * -0.10, var * 0.20)

        pts: list[tuple[int, int]] = []
        for i in range(1, steps + 1):
            t = self._ease(i / steps)
            mt = 1 - t
            bx = mt**3 * x0 + 3 * mt**2 * t * cx1 + 3 * mt * t**2 * cx2 + t**3 * x1
            by = mt**3 * y0 + 3 * mt**2 * t * cy1 + 3 * mt * t**2 * cy2 + t**3 * y1
            px, py = self._clamp(int(bx), int(by))
            pts.append((px, py))
        return pts

    def _ease(self, t: float) -> float:
        """Cubic ease-in-out: slow start, fast middle, slow end."""
        if t < 0.5:
            return 4 * t * t * t
        return 1 - (-2 * t + 2) ** 3 / 2

    def _calc_steps(self, x0: int, y0: int, x1: int, y1: int) -> int:
        dist = math.hypot(x1 - x0, y1 - y0)
        speed = self._config.mouse_speed_px_per_sec
        duration = dist / speed
        # Minimum 5 steps for very short moves; ~200 Hz interpolation rate
        return max(5, int(duration * 200))
