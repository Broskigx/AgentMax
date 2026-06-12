# AgentMax Platform Support

This document describes the current beta behavior for desktop automation by OS.

## Windows

Status: implemented for the current beta path.

Implemented:

- Screenshot capture through GDI, returned as PNG metadata/base64 and optionally saved under the OS temp directory.
- Screen information through Win32 system metrics.
- Mouse position, move, left/right/middle click, double click, drag and scroll through `SendInput`.
- Unicode text input and key combinations through `SendInput`.
- Shortcut mapping: copy/paste/select-all use Ctrl on Windows.
- Native physical mouse/keyboard intervention monitoring through low-level Win32 hooks.
- Smooth cursor movement still checks for unexpected cursor deviation as an additional guard.

Known limits:

- AgentMax cannot inject input into UAC secure desktop or protected/elevated windows from a non-elevated process.
- Some protected/video/DRM content may not appear in screenshots.
- Anti-cheat/game contexts may reject normal `SendInput` automation.
- Physical-input monitoring ignores AgentMax-injected events; it is intended to pause active automation when the user uses the real mouse or keyboard.

## Linux

Status: partial, fail-clear outside supported paths.

Implemented/prepared:

- X11 path uses `xdotool` for mouse and keyboard when `DISPLAY` is available.
- X11 screenshot attempts ImageMagick `import` first, then `gnome-screenshot`.
- Wayland is detected and reported as limited.
- Wayland screenshot can use `grim` when available, but global input automation is blocked by compositor policy unless a portal/compositor integration is added.

Required dependencies for X11:

- `xdotool`
- `xrandr`
- `imagemagick` or `gnome-screenshot`

Known limits:

- Wayland global mouse/keyboard automation is intentionally restricted.
- Multi-monitor metadata is basic in this beta.
- Native physical input intervention monitoring is not implemented yet on Linux.

## macOS

Status: screenshot prepared, keyboard prepared through Accessibility, mouse pending.

Implemented/prepared:

- Screenshot capture uses `screencapture -x`, which requires Screen Recording permission.
- Text and key-combo automation use AppleScript/System Events, which requires Accessibility permission.
- Cmd mapping is used for copy/paste/select-all.
- Permission failures return clear errors.

Known limits:

- Mouse move/click/drag/scroll still need a Quartz/CoreGraphics implementation before being considered beta-ready on macOS.
- The app must guide users to grant Screen Recording and Accessibility permissions.
- Native physical input intervention monitoring is not implemented yet on macOS.

## Beta Policy

- Tools must return `success=false` with a clear error when a permission, platform, dependency, or OS security boundary blocks execution.
- Input automation requires `automation_enabled` plus the specific mouse/keyboard permission.
- Screenshot capture requires `screen_capture_enabled`.
- `desktop_locate_on_screen` is currently a contract-only tool: it validates permission/query and fails clearly until the Python OCR/accessibility bridge is connected.
- AgentMax must never silently report success for a tool that did not run.
