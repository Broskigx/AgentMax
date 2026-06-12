#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod anti_vm;
mod commands;
mod desktop_automation;
mod integrity;
mod native;
mod overlay;
mod recovery;
mod security;
mod server;
mod tray;

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::Emitter;
use tauri::Manager;

#[cfg(target_os = "windows")]
struct SingleInstanceGuard(windows::Win32::Foundation::HANDLE);

#[cfg(target_os = "windows")]
impl Drop for SingleInstanceGuard {
    fn drop(&mut self) {
        unsafe {
            let _ = windows::Win32::Foundation::CloseHandle(self.0);
        }
    }
}

#[cfg(target_os = "windows")]
fn acquire_single_instance() -> Result<Option<SingleInstanceGuard>, String> {
    use windows::core::w;
    use windows::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS};
    use windows::Win32::System::Threading::CreateMutexW;

    let handle = unsafe { CreateMutexW(None, true, w!("Local\\AgentMax.Desktop.Singleton")) }
        .map_err(|error| format!("single-instance mutex failed: {error}"))?;
    let already_exists = unsafe { GetLastError() } == ERROR_ALREADY_EXISTS;
    if already_exists {
        unsafe {
            let _ = windows::Win32::Foundation::CloseHandle(handle);
        }
        return Ok(None);
    }
    Ok(Some(SingleInstanceGuard(handle)))
}

#[cfg(not(target_os = "windows"))]
struct SingleInstanceGuard;

#[cfg(not(target_os = "windows"))]
fn acquire_single_instance() -> Result<Option<SingleInstanceGuard>, String> {
    Ok(Some(SingleInstanceGuard))
}

#[cfg(target_os = "windows")]
fn focus_existing_instance() {
    use windows::core::{w, PCWSTR};
    use windows::Win32::UI::WindowsAndMessaging::{
        FindWindowW, SetForegroundWindow, ShowWindow, SW_RESTORE,
    };

    unsafe {
        if let Ok(window) = FindWindowW(PCWSTR::null(), w!("AgentMax")) {
            let _ = ShowWindow(window, SW_RESTORE);
            let _ = SetForegroundWindow(window);
        }
    }
}

#[cfg(not(target_os = "windows"))]
fn focus_existing_instance() {}

fn configure_runtime_defaults() {
    if std::env::var_os("AGENTMAX_IPC_AUTH").is_none() {
        std::env::set_var("AGENTMAX_IPC_AUTH", "1");
    }
}

struct CoreProcess(Arc<Mutex<Option<std::process::Child>>>);
struct TrayState(Arc<Mutex<Option<tauri::tray::TrayIcon>>>);

use commands::{
    BackendLaunchState, BackendLaunchStatus, HardwareFpState, LlamaCppProcess, ModuleState,
    Modules, TokenState,
};
use desktop_automation::{DesktopAutomationService, DesktopAutomationState};

fn main() {
    let _single_instance = match acquire_single_instance() {
        Ok(Some(guard)) => Some(guard),
        Ok(None) => {
            focus_existing_instance();
            return;
        }
        Err(error) => {
            eprintln!("AgentMax could not initialize single-instance protection: {error}");
            None
        }
    };

    configure_runtime_defaults();
    recovery::init_logging();

    // Binary integrity check — degrades silently in dev builds (verify() returns true).
    // In release builds, exits if the executable was patched after installation.
    if !integrity::verify() {
        std::process::exit(1);
    }

    let ipc_token = commands::init_token();
    let hw_fp = security::hardware_fingerprint();

    let server_hw_fp = hw_fp.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(CoreProcess(Arc::new(Mutex::new(None))))
        .manage(TrayState(Arc::new(Mutex::new(None))))
        .manage(LlamaCppProcess(Mutex::new(None)))
        .manage(TokenState(Mutex::new(ipc_token.clone())))
        .manage(BackendLaunchState(Mutex::new(BackendLaunchStatus::default())))
        .manage(ModuleState(Mutex::new(Modules::new())))
        .manage(HardwareFpState(hw_fp))
        .manage(DesktopAutomationState(Mutex::new(
            DesktopAutomationService::new(),
        )))
        .setup(move |app| {
            // === First-run / any-user adaptation: ensure professional data layout (Windows, macOS, Linux) ===
            if let Ok(data_dir) = app.path().app_data_dir() {
                let agent_dir = data_dir.join("AgentMax");
                let _ = std::fs::create_dir_all(&agent_dir);
                let _ = std::fs::create_dir_all(agent_dir.join("logs"));
                let _ = std::fs::create_dir_all(agent_dir.join("diagnostics"));
                let _ = std::fs::create_dir_all(agent_dir.join("data"));
                // Expose to Python backend and recovery so first-time users (any OS) get consistent writable paths
                std::env::set_var("AGENTMAX_DATA_DIR", agent_dir.to_string_lossy().as_ref());
            } else if let Some(local) = dirs::data_local_dir() {
                // Fallback for very early init or unusual envs
                let agent_dir = local.join("AgentMax");
                let _ = std::fs::create_dir_all(&agent_dir);
                let _ = std::fs::create_dir_all(agent_dir.join("logs"));
                std::env::set_var("AGENTMAX_DATA_DIR", agent_dir.to_string_lossy().as_ref());
            }

            recovery::configure(app.handle().clone());
            if recovery::should_open_on_start() {
                recovery::open_window(&app.handle()).ok();
            }

            // Spawn the HTTP server that Python's RustVisionBridge calls
            let _hw = server_hw_fp; // captured for potential future auth
            tauri::async_runtime::spawn(server::start_server());

            // ── EL CHEQUEO (versión más dura y pulida - closed source) ─────────────────
            // Detección de debugger, herramientas de reverse, VM o manipulación
            // = terminación inmediata del proceso en builds de release.
            //
            // Orden de ejecución:
            //   1. Chequeo ultra-temprano (antes de lanzar backend, UI, servidores, etc.)
            //   2. Si falla en release → log forense a Recovery + exit(1) inmediato
            //   3. Watchdog en background que sigue chequeando cada 5s y mata si detecta algo
            //
            // Técnicas combinadas (ver security.rs para detalles):
            //   - IsDebuggerPresent / CheckRemoteDebuggerPresent
            //   - NtQueryInformationProcess (DebugPort + DebugObjectHandle)
            //   - RDTSC timing anomaly (single-step detection)
            //   - Escaneo de procesos crackers (x64dbg, IDA, Ghidra, Frida, CheatEngine...)
            //   - Detección de VM / hypervisor
            //   - Chequeos adicionales de padre / entorno hostil
            //
            // Esto está diseñado para ser lo más molesto y efectivo posible contra
            // atacantes que intenten depurar o crackear un binario cerrado.

            let security_mode = security::security_mode();
            let security_findings = if security_mode == security::SecurityMode::Off {
                Vec::new()
            } else {
                security::security_findings()
            };

            #[cfg(not(debug_assertions))]
            {
                if !security_findings.is_empty()
                    && security_mode == security::SecurityMode::Audit
                {
                    recovery::sink().record(
                        "warn",
                        "security.environment",
                        "Security audit detected environment signals; AgentMax will continue for tester diagnostics",
                        Some(serde_json::json!({
                            "mode": security_mode.as_str(),
                            "findings": security_findings,
                        })),
                    );
                } else if !security_findings.is_empty()
                    && security_mode == security::SecurityMode::Enforce
                {
                    // Log mínimo pero útil para el dueño legítimo (aparece en recovery logs).
                    recovery::sink().record(
                        "error",
                        "security.anti_debug",
                        "HOSTILE ENVIRONMENT DETECTED — debugger/RE-tool/VM/tamper. Terminating (closed-source).",
                        None,
                    );
                    recovery::mark_clean_shutdown();
                    std::process::exit(1);
                }
            }

            #[cfg(debug_assertions)]
            if !security_findings.is_empty() {
                recovery::sink().record(
                    "warn",
                    "security.environment",
                    "Security audit detected environment signals (debug build)",
                    Some(serde_json::json!({
                        "mode": security_mode.as_str(),
                        "findings": security_findings,
                    })),
                );
            }

            security::start_security_watchdog();

            launch_core(app, &ipc_token);
            let tray_icon = tray::setup_tray(&app.handle())?;
            if let Some(state) = app.try_state::<TrayState>() {
                if let Ok(mut guard) = state.0.lock() {
                    *guard = Some(tray_icon);
                }
            }
            configure_main_window(app)?;
            create_hud_overlay(app)?;
            create_widget_window(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } | tauri::WindowEvent::Destroyed = event
            {
                if window.label() != "main" {
                    return;
                }
                let ipc_token = window
                    .try_state::<TokenState>()
                    .and_then(|state| state.0.lock().ok().map(|token| token.clone()));
                if let Some(state) = window.try_state::<CoreProcess>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(mut child) = guard.take() {
                            post_local_shutdown(7789, None);
                            post_local_shutdown(7790, ipc_token.as_deref());
                            let deadline =
                                std::time::Instant::now() + std::time::Duration::from_secs(2);
                            loop {
                                if child.try_wait().ok().flatten().is_some() {
                                    break;
                                }
                                if std::time::Instant::now() >= deadline {
                                    let _ = child.kill();
                                    break;
                                }
                                std::thread::sleep(std::time::Duration::from_millis(100));
                            }
                        }
                    }
                }
                recovery::mark_clean_shutdown();
            }
        })
        .invoke_handler(tauri::generate_handler![
            // IPC auth
            commands::get_ipc_token,
            commands::get_backend_launch_status,
            // Direct AI
            commands::chat_stream,
            commands::chat_sync,
            commands::classify_intent,
            // Modules
            commands::get_module_states,
            commands::toggle_module,
            commands::capture_screen_colors,
            commands::get_focused_window_info,
            // Native (security / screen / input / sandbox)
            commands::get_hardware_fingerprint,
            commands::screenshot_base64,
            commands::desktop_get_permissions,
            commands::desktop_set_permissions,
            commands::desktop_get_tool_status,
            commands::desktop_take_screenshot,
            commands::desktop_get_screen_info,
            commands::desktop_locate_on_screen,
            commands::desktop_get_mouse_position,
            commands::desktop_move_mouse,
            commands::desktop_click,
            commands::desktop_double_click,
            commands::desktop_drag,
            commands::desktop_scroll,
            commands::desktop_type_text,
            commands::desktop_press_key,
            commands::desktop_key_combo,
            commands::desktop_copy,
            commands::desktop_paste,
            commands::desktop_select_all,
            commands::desktop_pause_automation,
            commands::desktop_resume_automation,
            commands::desktop_record_user_intervention,
            commands::inject_input,
            commands::run_shell_command,
            // Python beta/runtime API (:7790)
            commands::submit_task,
            commands::get_task_status,
            commands::emergency_stop,
            commands::confirm_task,
            commands::get_system_status,
            commands::minimize_to_panel,
            commands::restore_window,
            commands::show_hud_overlay,
            commands::hide_hud_overlay,
            commands::get_ai_status,
            commands::switch_ai_backend,
            commands::get_lmstudio_models,
            commands::recovery_get_logs,
            commands::recovery_clear_view,
            commands::recovery_export_bundle,
            commands::recovery_open_window,
            commands::recovery_log_frontend,
            commands::recovery_get_status,
            commands::llamacpp_get_status,
            commands::llamacpp_start,
            commands::llamacpp_stop,
            // Input control
            commands::inject_mouse_move,
            commands::type_text,
            // Widget window
            commands::show_widget,
            commands::hide_widget,
            commands::restore_main_window,
        ])
        .run(tauri::generate_context!())
        .expect("error while running AgentMax")
}

fn launch_core(app: &tauri::App, ipc_token: &str) {
    if std::env::var("AGENTMAX_SKIP_BACKEND_SPAWN").ok().as_deref() == Some("1") {
        recovery::sink().record(
            "info",
            "backend",
            "Python backend spawn skipped (AGENTMAX_SKIP_BACKEND_SPAWN=1)",
            None,
        );
        return;
    }

    update_backend_launch_status(app, |status| {
        status.python_spawn_attempted = true;
        status.ipc_auth_enabled = commands::ipc_auth_enabled();
    });

    #[cfg(not(debug_assertions))]
    if try_launch_agentcore(app, ipc_token) {
        update_backend_launch_status(app, |status| {
            status.python_running = true;
            status.last_error = None;
        });
        return;
    }

    match try_launch_python_backend(app, ipc_token) {
        Ok(()) => {
            update_backend_launch_status(app, |status| {
                status.python_running = true;
                status.last_error = None;
            });
        }
        Err(error) => {
            update_backend_launch_status(app, |status| {
                status.python_running = false;
                status.last_error = Some(error.clone());
            });
            recovery::sink().record(
                "error",
                "backend",
                &error,
                Some(serde_json::json!({ "port": 7790 })),
            );
            let _ = app.emit("backend-launch-failed", &error);
        }
    }
}

fn update_backend_launch_status<F>(app: &tauri::App, update: F)
where
    F: FnOnce(&mut BackendLaunchStatus),
{
    if let Some(state) = app.try_state::<BackendLaunchState>() {
        if let Ok(mut guard) = state.0.lock() {
            update(&mut guard);
        }
    }
}

fn try_launch_agentcore(app: &tauri::App, ipc_token: &str) -> bool {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()));
    let core_path = exe_dir.map(|d| {
        d.join("agentcore").join(if cfg!(windows) {
            "agentcore.exe"
        } else {
            "agentcore"
        })
    });
    let Some(path) = core_path else {
        return false;
    };
    if !path.exists() {
        return false;
    }

    let mut cmd = Command::new(&path);
    cmd.env("AGENTCORE_IPC_MODE", "stdio");
    cmd.env("AGENTCORE_HTTP_PORT", "7789");
    cmd.env("AGENTMAX_IPC_TOKEN", ipc_token);
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());
    if std::env::var("ANTHROPIC_API_KEY").is_ok() {
        cmd.env("AGENTCORE_AI_PROVIDER", "claude");
    }
    match cmd.spawn() {
        Ok(mut child) => {
            if let Some(stdout) = child.stdout.take() {
                recovery::spawn_pipe_reader(stdout, "agentcore.stdout", "info");
            }
            if let Some(stderr) = child.stderr.take() {
                recovery::spawn_pipe_reader(stderr, "agentcore.stderr", "error");
            }
            recovery::sink().record(
                "info",
                "agentcore",
                &format!("agentcore launched at {}", path.display()),
                None,
            );
            if let Some(state) = app.try_state::<CoreProcess>() {
                if let Ok(mut guard) = state.0.lock() {
                    *guard = Some(child);
                }
                watch_core_process(state.0.clone());
            }
            true
        }
        Err(error) => {
            recovery::sink().record(
                "error",
                "agentcore",
                &format!("agentcore failed to launch: {error}"),
                Some(serde_json::json!({ "path": path.display().to_string() })),
            );
            false
        }
    }
}

fn try_launch_python_backend(app: &tauri::App, ipc_token: &str) -> Result<(), String> {
    let root = resolve_backend_root(app).ok_or_else(|| {
        "Python backend not found. Install Python 3.11+ and ensure scripts/agentmax_server.py is bundled next to AgentMax.".to_string()
    })?;
    let script = resolve_backend_script(&root).ok_or_else(|| {
        format!(
            "Missing backend entrypoint under {}",
            root.join("scripts").display()
        )
    })?;

    let python = resolve_python_executable().ok_or_else(|| {
        "Python 3.11+ not found on PATH. Install Python from python.org and restart AgentMax."
            .to_string()
    })?;

    let mut cmd = Command::new(&python);
    cmd.arg(&script);
    cmd.current_dir(&root);
    cmd.env("AGENTMAX_IPC_TOKEN", ipc_token);
    cmd.env("AGENTMAX_PRODUCT_MODE", "1");
    // IPC auth is on by default; an explicit AGENTMAX_IPC_AUTH=0 is inherited.
    cmd.env("PYTHONPATH", &root);
    cmd.env("PYTHONIOENCODING", "utf-8");
    // Professional first-run support: give Python a stable user-writable location
    if let Ok(data) = std::env::var("AGENTMAX_DATA_DIR") {
        cmd.env("AGENTMAX_DATA_DIR", &data);
    }
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());

    match cmd.spawn() {
        Ok(mut child) => {
            if let Some(stdout) = child.stdout.take() {
                recovery::spawn_pipe_reader(stdout, "python.stdout", "info");
            }
            if let Some(stderr) = child.stderr.take() {
                recovery::spawn_pipe_reader(stderr, "python.stderr", "error");
            }
            recovery::sink().record(
                "info",
                "backend",
                &format!(
                    "Python beta backend launched: {} (cwd={})",
                    script.display(),
                    root.display()
                ),
                Some(serde_json::json!({ "python": python, "port": 7790 })),
            );
            if let Some(state) = app.try_state::<CoreProcess>() {
                if let Ok(mut guard) = state.0.lock() {
                    *guard = Some(child);
                }
                watch_core_process(state.0.clone());
            }
            Ok(())
        }
        Err(error) => Err(format!("Failed to start Python backend: {error}")),
    }
}

fn resolve_backend_root(app: &tauri::App) -> Option<PathBuf> {
    if let Ok(root) = std::env::var("AGENTMAX_BACKEND_ROOT") {
        let path = PathBuf::from(root);
        if backend_script_exists(&path) {
            return Some(path);
        }
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        if backend_script_exists(&resource_dir) {
            return Some(resource_dir);
        }
    }

    if let Ok(exe) = std::env::current_exe() {
        if let Some(exe_dir) = exe.parent() {
            // Tauri Windows bundle layout: scripts live under _up_/_up_ next to AgentMax.exe.
            for candidate in [exe_dir.join("_up_").join("_up_"), exe_dir.join("resources")] {
                if backend_script_exists(&candidate) {
                    return Some(candidate);
                }
            }
        }

        let mut dir = exe.parent().map(|p| p.to_path_buf());
        for _ in 0..8 {
            let Some(current) = dir else {
                break;
            };
            if backend_script_exists(&current) {
                return Some(current);
            }
            let bundled = current.join("_up_").join("_up_");
            if backend_script_exists(&bundled) {
                return Some(bundled);
            }
            let resources = current.join("resources");
            if backend_script_exists(&resources) {
                return Some(resources);
            }
            dir = current.parent().map(|p| p.to_path_buf());
        }
    }

    if let Ok(cwd) = std::env::current_dir() {
        if backend_script_exists(&cwd) {
            return Some(cwd);
        }
    }

    None
}

fn backend_script_exists(root: &Path) -> bool {
    resolve_backend_script(root).is_some()
}

fn resolve_backend_script(root: &Path) -> Option<PathBuf> {
    let prod = root.join("scripts").join("agentmax_server.py");
    if prod.is_file() {
        return Some(prod);
    }
    let legacy = root.join("scripts").join("agentpilot_test_server.py");
    if legacy.is_file() {
        return Some(legacy);
    }
    None
}

fn resolve_python_executable() -> Option<String> {
    for candidate in ["python", "python3", "py"] {
        let ok = Command::new(candidate)
            .arg("--version")
            .output()
            .map(|output| output.status.success())
            .unwrap_or(false);
        if ok {
            return Some(candidate.to_string());
        }
    }
    None
}

fn watch_core_process(state: Arc<Mutex<Option<std::process::Child>>>) {
    std::thread::spawn(move || loop {
        let status = {
            let mut guard = match state.lock() {
                Ok(guard) => guard,
                Err(_) => return,
            };
            let Some(child) = guard.as_mut() else {
                return;
            };
            match child.try_wait() {
                Ok(Some(status)) => {
                    *guard = None;
                    Some(Ok(status))
                }
                Ok(None) => None,
                Err(error) => {
                    *guard = None;
                    Some(Err(error))
                }
            }
        };

        match status {
            Some(Ok(status)) => {
                recovery::sink().record(
                    if status.success() { "info" } else { "error" },
                    "backend",
                    &format!("Python backend exited with status {status}"),
                    Some(serde_json::json!({ "code": status.code() })),
                );
                return;
            }
            Some(Err(error)) => {
                recovery::sink().record(
                    "error",
                    "backend",
                    &format!("Backend status check failed: {error}"),
                    None,
                );
                return;
            }
            None => std::thread::sleep(std::time::Duration::from_millis(500)),
        }
    });
}

fn post_local_shutdown(port: u16, token: Option<&str>) {
    match std::net::TcpStream::connect(("127.0.0.1", port)) {
        Ok(mut stream) => {
            let auth_header = token
                .filter(|value| !value.trim().is_empty())
                .map(|value| format!("X-AgentMax-Token: {}\r\n", value.trim()))
                .unwrap_or_default();
            let request = format!(
                "POST /api/shutdown HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n{auth_header}Content-Length: 0\r\nConnection: close\r\n\r\n"
            );
            let _ = stream.write_all(request.as_bytes());
            recovery::sink().record(
                "info",
                "shutdown",
                &format!("shutdown POST sent to 127.0.0.1:{port}"),
                None,
            );
        }
        Err(error) => {
            recovery::sink().record(
                "warn",
                "shutdown",
                &format!("shutdown endpoint 127.0.0.1:{port} unavailable: {error}"),
                None,
            );
        }
    }
}

fn configure_main_window(app: &tauri::App) -> tauri::Result<()> {
    let window = app.get_webview_window("main").unwrap();
    window.set_decorations(false).ok();
    window.set_always_on_top(false).ok();
    window
        .eval(
            r#"
        (() => {
          window.__AGENTMAX_GUARDS_ACTIVE = true;
          document.documentElement.dataset.AgentMaxGuards = 'active';
          const blockContextMenu = (event) => {
            event.preventDefault();
            event.stopPropagation();
          };
          const blockDevtoolsKeys = (event) => {
            const key = String(event.key || '').toLowerCase();
            const blocked =
              event.key === 'F12' ||
              (event.ctrlKey && event.shiftKey && ['i', 'j', 'c'].includes(key)) ||
              (event.metaKey && event.altKey && ['i', 'j', 'c'].includes(key)) ||
              (event.ctrlKey && key === 'u') ||
              (event.metaKey && key === 'u');
            if (blocked) {
              event.preventDefault();
              event.stopPropagation();
            }
          };
          window.addEventListener('contextmenu', blockContextMenu, true);
          document.addEventListener('contextmenu', blockContextMenu, true);
          window.addEventListener('keydown', blockDevtoolsKeys, true);
        })();
        "#,
        )
        .ok();
    Ok(())
}

fn create_widget_window(app: &tauri::App) -> tauri::Result<()> {
    // Cross-platform monitor size via Tauri (no hard Win32 dependency for position)
    let (sw, sh) = if let Ok(Some(monitor)) = app.primary_monitor() {
        let size = monitor.size();
        (size.width as f64, size.height as f64)
    } else {
        // Reasonable fallback for unusual environments
        (1920.0f64, 1080.0f64)
    };

    let w = 320.0f64;
    let h = 170.0f64;
    let margin = 16.0f64;
    let taskbar = 48.0f64;
    let x = sw - w - margin;
    let y = sh - h - margin - taskbar;

    match tauri::WebviewWindowBuilder::new(
        app,
        "widget",
        tauri::WebviewUrl::App("index.html#widget".into()),
    )
    .title("")
    .inner_size(w, h)
    .position(x, y)
    .decorations(false)
    .transparent(true)
    .always_on_top(true)
    .skip_taskbar(true)
    .resizable(false)
    .visible(false)
    .build()
    {
        Ok(_) => {}
        Err(e) => log::warn!("Widget window failed to create: {}", e),
    }
    Ok(())
}

fn create_hud_overlay(app: &tauri::App) -> tauri::Result<()> {
    match tauri::WebviewWindowBuilder::new(app, "hud", tauri::WebviewUrl::App("index.html".into()))
        .title("")
        .fullscreen(true)
        .transparent(true)
        .decorations(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .visible(false)
        .build()
    {
        Ok(hud) => {
            hud.set_title("").ok();
            hud.set_decorations(false).ok();
            hud.set_fullscreen(true).ok();
            hud.set_ignore_cursor_events(true).ok();
            hud.eval("window.__AGENTMAX_HUD = true;").ok();
        }
        Err(e) => {
            log::warn!("AgentMax-hud overlay failed to create: {}", e);
        }
    }
    Ok(())
}
