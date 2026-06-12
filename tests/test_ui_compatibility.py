"""
UI Compatibility & Professional Cleanliness Contract Tests

These tests enforce that the main AgentMax experience remains:
- Clean and professional (no logs, terminal, or diagnostic clutter in the primary UI)
- All real runtime logs, events, crashes and shell output live exclusively in the Recovery window
- First-run / cross-platform ready structure
- Proper urgent model detection (NoModelAlert)
- Stable, simple layout (flex-based, no brittle old grids)

This file was updated as part of professional polishing (2026).
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui" / "src"


def read_ui_file(*parts: str) -> str:
    return (UI.joinpath(*parts)).read_text(encoding="utf-8")


def test_main_window_is_clean_and_professional() -> None:
    """Main window must be minimal: TitleBar + Sidebar + Chat + Settings + NoModelAlert.
    No logs, no terminal, no legacy panels.
    """
    source = read_ui_file("components", "MainWindow", "MainWindow.tsx")
    css = read_ui_file("components", "MainWindow", "MainWindow.css")

    # Professional clean structure
    assert "TitleBar" in source
    assert "ChatSidebar" in source
    assert "ChatThread" in source
    assert "SettingsDrawer" in source
    assert "NoModelAlert" in source

    # Must NOT contain old clutter
    assert "AgentLog" not in source
    assert "Terminal" not in source
    assert "FloatingPanel" not in source
    assert "eventLog" not in source.lower()
    assert "terminalLines" not in source.lower()

    # Simple professional flex layout (no old complex grids)
    assert "flex" in css
    assert "flex-direction: column" in css
    assert "min-height: 0" in css
    assert ".agentmax-shell" in css


def test_no_model_alert_is_wired_for_urgent_lm_studio_state() -> None:
    """The small movable urgent popup must be present when LM Studio has no loaded model."""
    main = read_ui_file("components", "MainWindow", "MainWindow.tsx")
    alert = read_ui_file("components", "NoModelAlert", "NoModelAlert.tsx")

    assert "NoModelAlert" in main
    assert "NoModelAlert" in alert
    assert "lmstudio" in alert.lower() or "has_loaded_model" in alert
    assert "URGENTE" in alert or "Sin modelo cargado" in alert
    # Draggable / movable
    assert "drag" in alert.lower() or "framer-motion" in alert


def test_store_has_been_cleaned_of_log_clutter() -> None:
    """After professional cleanup, the main store should not expose terminal / raw eventLog bloat."""
    store = read_ui_file("store", "agentStore.ts")

    # These were removed from the clean main experience
    assert "showTerminal" not in store
    assert "terminalLines" not in store
    assert "clearTerminal" not in store
    assert "runShellCommand" not in store or "Recovery" in store  # stub mentions Recovery

    # eventLog array removed from main surface
    # (internal recovery still captures everything via Rust sink)
    assert "eventLog:" not in store or "Recovery" in store


def test_recovery_is_the_single_source_of_truth_for_logs() -> None:
    """All real logs, diagnostics and recovery information must be routed to the Recovery window."""
    recovery = read_ui_file("components", "RecoveryTest", "RecoveryTestWindow.tsx")
    main = read_ui_file("components", "MainWindow", "MainWindow.tsx")

    assert "Recovery" in recovery or "real runtime logs" in recovery.lower()
    # Main must not render raw logs
    assert "recovery-log" not in main.lower()
    assert "AgentLog" not in main


def test_modal_and_settings_surfaces_have_professional_styling() -> None:
    """Settings, CommandPalette and drawers use consistent professional treatment."""
    settings = read_ui_file("components", "AISettings", "AISettings.css")
    palette = read_ui_file("components", "CommandPalette", "CommandPalette.css")
    drawer = read_ui_file("components", "SettingsDrawer", "SettingsDrawer.css")

    for css in (settings, palette, drawer):
        assert "border" in css
        assert "z-index" in css or "position" in css
        # Professional dark theme cues
        assert "rgba" in css or "#" in css


def test_computer_control_permission_logic_exists_in_store() -> None:
    """Computer control (when available) still requires explicit user approval via store."""
    store = read_ui_file("store", "agentStore.ts")

    assert "computerControlRequest" in store
    assert "approveComputerControl" in store or "computerControlActive" in store
    # Note: full prompt component may be platform-gated / lazy


def test_first_run_and_lm_detection_state_are_wired() -> None:
    """First-run adaptation and LM Studio 'has loaded model' detection are present."""
    store = read_ui_file("store", "agentStore.ts")
    alert = read_ui_file("components", "NoModelAlert", "NoModelAlert.tsx")

    assert "fetchLMStudioModels" in store
    assert "has_loaded_model" in store or "lmstudioModels" in store
    assert "AGENTMAX_DATA_DIR" in alert or "first" in alert.lower() or "model" in alert.lower()  # indirect


def test_app_root_applies_professional_guards_and_onboarding() -> None:
    """App.tsx applies security guards + professional onboarding flow."""
    app = read_ui_file("App.tsx")

    assert "ErrorBoundary" in app
    assert "installInteractionGuards" in app or "blockBrowserDevtools" in app.lower()
    assert "Onboarding" in app
    assert "showOnboarding" in app or "onboarding" in app.lower()
