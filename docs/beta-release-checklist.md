# AgentMax Beta Release Checklist

## Must Pass

- `cargo test --lib` passes in `ui/src-tauri`.
- `cargo check` passes in `ui/src-tauri`.
- `npm test` passes in `ui`.
- `npm run build` passes in `ui`.
- `npm run tauri build` passes in `ui` and produces MSI/NSIS bundles.
- `.venv\Scripts\python.exe -m core.cli doctor` passes.
- `.venv\Scripts\python.exe -m pytest tests/test_tools_backend.py` passes.

## Manual Desktop Tool Checks

Open the Tauri desktop app and use Tool Diagnostics:

- Enable `Screen`, then run `Test Screenshot`.
- Confirm preview appears with non-zero width/height metadata.
- Confirm `Locate On Screen` is listed as unavailable/fail-clear until OCR bridging is connected.
- Enable `Automation` and `Mouse`, then run `Test Move`.
- Run `Test Click` and confirm the click stays inside AgentMax.
- Enable `Keyboard`, then run `Test Type`.
- Confirm text appears only in the diagnostics input.
- Run `Test Hotkey` and confirm it affects only the diagnostics input.
- On Windows, confirm diagnostics shows `Input monitor active`.
- On Windows, start a longer mouse move and move the physical mouse during it; confirm automation pauses.
- Run `Pause`, then confirm input tools fail with an automation-paused error.
- Run `Resume`, then confirm tools can run again when permissions are enabled.

## Platform Checks

Windows:

- Verify the app can capture the main display.
- Verify mouse movement works on a normal non-elevated app window.
- Verify text typing works in AgentMax's diagnostics input.
- Verify physical mouse/keyboard input during an active action pauses automation.
- Verify tools return clear errors across elevated/UAC boundaries.

Linux:

- On X11, install `xdotool`, `xrandr`, and either ImageMagick `import` or `gnome-screenshot`.
- On Wayland, verify diagnostics clearly report limited automation.

macOS:

- Verify missing Screen Recording permission produces a clear screenshot error.
- Verify missing Accessibility permission produces a clear input error.
- Do not mark mouse tools ready until a Quartz/CoreGraphics implementation is added.

## Release Notes To Include

- Windows native desktop tools are ready for initial beta.
- Locate-on-screen target finding is a registered contract but not a completed native tool yet.
- Linux X11 and macOS paths are prepared but require dependency/permission validation.
- Wayland global input automation is limited by OS security policy.
- Real input automation always requires explicit permission state in the app.
- The user can pause/resume automation; on Windows, physical mouse/keyboard input during an active action pauses automation.
