# Desktop Automation Architecture

AgentMax now exposes a Tauri-native desktop automation layer in `ui/src-tauri/src/desktop_automation.rs`.

## Core Services

The implementation is organized around one service object that owns:

- `PermissionService`: `screen_capture_enabled`, `mouse_control_enabled`, `keyboard_control_enabled`, `automation_enabled`.
- `ScreenService`: screenshot capture and screen metadata.
- `MouseService`: position, move, click, double click, drag and scroll.
- `KeyboardService`: type text, press key, key combo, copy, paste and select all.
- `UserInterventionService`: pause/resume state, Windows physical mouse/keyboard monitoring, and cursor deviation detection during mouse movement/drag.
- `ToolRegistry`: status/catalog returned to the UI diagnostics panel.

The Tauri commands are named with a `desktop_` prefix:

- `desktop_get_permissions`
- `desktop_set_permissions`
- `desktop_get_tool_status`
- `desktop_take_screenshot`
- `desktop_get_screen_info`
- `desktop_locate_on_screen`
- `desktop_get_mouse_position`
- `desktop_move_mouse`
- `desktop_click`
- `desktop_double_click`
- `desktop_drag`
- `desktop_scroll`
- `desktop_type_text`
- `desktop_press_key`
- `desktop_key_combo`
- `desktop_copy`
- `desktop_paste`
- `desktop_select_all`
- `desktop_pause_automation`
- `desktop_resume_automation`
- `desktop_record_user_intervention`

Legacy commands remain registered for compatibility, but they now pass through the same permission checks where they can execute real input.

## Result Contract

Every desktop tool returns a normalized result:

```ts
type DesktopToolResult<T> = {
  success: boolean;
  data?: T;
  error?: string;
  platform: "windows" | "linux" | "macos" | "unknown";
  durationMs: number;
  paused: boolean;
};
```

Screenshot results include:

```ts
type ScreenshotResult = {
  width: number;
  height: number;
  imagePath?: string;
  imageBase64?: string;
  monitorId?: string;
  timestamp: number;
};
```

`desktop_locate_on_screen` currently exists as a fail-clear contract. It requires `screen_capture_enabled`, validates the query, and returns `success=false` with an OCR/accessibility bridge error until the native layer is connected to the existing Python vision path (`screen.locate_element` / OCR bounds).

## UI Integration

The React client lives in `ui/src/lib/desktopAutomationService.ts`.

The manual diagnostics panel lives in:

- `ui/src/components/ToolDiagnostics/ToolDiagnosticsPanel.tsx`
- `ui/src/components/ToolDiagnostics/ToolDiagnosticsPanel.css`

Diagnostics can:

- Toggle screen, automation, mouse and keyboard permissions.
- Test screenshot and preview the latest capture.
- Test a small mouse move.
- Test a click inside the AgentMax diagnostics panel.
- Test typing only into an AgentMax-owned input.
- Test a safe select-all hotkey against the same input.
- Pause/resume automation.
- Surface `Locate On Screen` in the tool catalog as not implemented until OCR/accessibility bridging is wired.

## Safety Notes

- Mouse/keyboard commands fail unless `automation_enabled` and their specific permission are active.
- Screenshot commands fail unless `screen_capture_enabled` is active.
- Windows starts a low-level mouse/keyboard hook in the Tauri process and ignores AgentMax-injected `SendInput` events.
- Active actions compare physical input timestamps against their own start time and pause when real mouse/keyboard input arrives during the action.
- Smooth mouse movement and drag also check for unexpected cursor movement and pause if user intervention is detected.
- Temporary computer-control approval in the chat flow enables native permissions for the task and restores the previous permission state afterward.

## Verification

Use:

```powershell
cd ui/src-tauri
cargo test --lib
cargo check
```

```powershell
cd ui
npm test
npm run build
```

```powershell
.venv\Scripts\python.exe -m core.cli doctor
.venv\Scripts\python.exe -m pytest tests/test_tools_backend.py
```
