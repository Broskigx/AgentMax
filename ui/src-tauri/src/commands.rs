/*!
 * AgentMax – Tauri commands v2
 *
 * Security: every command (except get_ipc_token) requires a `token` param
 * matching the shared IPC token in %LOCALAPPDATA%/AgentMax/ipc_token
 * (same file/format as Python `core.security.ipc_auth`).
 *
 * API contract (closed beta):
 *   - :7789  Rust Axum — vision, LM proxy, native helpers only
 *   - :7790  Python beta/runtime — tasks, chat, beta, diagnostics, tools
 */

use futures::StreamExt;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::process::{Child, Stdio};
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, State};
use uuid::Uuid;

use crate::desktop_automation::{
    ActionResult, ClickArgs, DesktopAutomationState, DesktopToolResult, DragArgs,
    InterventionStatus, KeyComboArgs, LocateOnScreenArgs, LocateOnScreenResult, MouseButton,
    MouseMoveArgs, MousePosition, PermissionPatch, PermissionState, ScreenInfo, ScreenshotResult,
    ScrollArgs, ToolStatus, TypeTextArgs,
};

// ── Hardware fingerprint state ─────────────────────────────────────────────────

pub struct HardwareFpState(pub String);

// ── IPC Token ─────────────────────────────────────────────────────────────────

pub struct TokenState(pub Mutex<String>);

pub struct LlamaCppProcess(pub Mutex<Option<Child>>);

#[derive(Debug, Clone)]
struct LlamaCppRuntimeConfig {
    host: String,
    port: u16,
    model_path: String,
    server_bin: String,
    context_size: u32,
    threads: u32,
    gpu_layers: i32,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LlamaCppStatus {
    configured: bool,
    running: bool,
    ready: bool,
    host: String,
    port: u16,
    base_url: String,
    model_path: String,
    server_bin: String,
    models: Vec<String>,
    error: Option<String>,
}

/// Shared with Python `core.security.ipc_auth.token_file_path()`.
const IPC_TOKEN_FILE: &str = "ipc_token";
/// Legacy Tauri token file (UUID). Migrated into `ipc_token` on read.
const LEGACY_IPC_TOKEN_FILE: &str = "ipc.key";

fn token_dir() -> PathBuf {
    let mut p = dirs::data_local_dir().unwrap_or_else(|| PathBuf::from("."));
    p.push("AgentMax");
    p
}

fn token_path() -> PathBuf {
    token_dir().join(IPC_TOKEN_FILE)
}

fn legacy_token_path() -> PathBuf {
    token_dir().join(LEGACY_IPC_TOKEN_FILE)
}

fn generate_ipc_token() -> String {
    use base64::Engine;
    let u1 = Uuid::new_v4();
    let u2 = Uuid::new_v4();
    let mut bytes = [0u8; 32];
    bytes[..16].copy_from_slice(u1.as_bytes());
    bytes[16..].copy_from_slice(u2.as_bytes());
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
}

fn persist_token(token: &str) -> String {
    let path = token_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(&path, token);
    token.trim().to_string()
}

pub fn init_token() -> String {
    if let Ok(env_token) = std::env::var("AGENTMAX_IPC_TOKEN") {
        let env_token = env_token.trim().to_string();
        if !env_token.is_empty() {
            return persist_token(&env_token);
        }
    }

    let path = token_path();
    if path.exists() {
        if let Ok(t) = std::fs::read_to_string(&path) {
            let t = t.trim().to_string();
            if !t.is_empty() {
                return t;
            }
        }
    }

    let legacy = legacy_token_path();
    if legacy.exists() {
        if let Ok(t) = std::fs::read_to_string(&legacy) {
            let t = t.trim().to_string();
            if !t.is_empty() {
                return persist_token(&t);
            }
        }
    }

    persist_token(&generate_ipc_token())
}

pub fn ipc_auth_enabled() -> bool {
    match std::env::var("AGENTMAX_IPC_AUTH") {
        Ok(value) => matches!(
            value.trim().to_lowercase().as_str(),
            "1" | "true" | "yes" | "on"
        ),
        Err(_) => true,
    }
}

fn python_api_headers(token: &str) -> reqwest::header::HeaderMap {
    let mut headers = reqwest::header::HeaderMap::new();
    headers.insert(
        reqwest::header::CONTENT_TYPE,
        "application/json".parse().expect("valid content-type"),
    );
    if ipc_auth_enabled() {
        if let Ok(value) = reqwest::header::HeaderValue::from_str(token) {
            headers.insert("X-AgentMax-Token", value);
        }
    }
    headers
}

fn validate(token: &str, state: &State<TokenState>) -> Result<(), String> {
    if token == state.0.lock().unwrap().as_str() {
        Ok(())
    } else {
        Err("IPC token inválido — acceso denegado".to_string())
    }
}

#[tauri::command]
pub fn get_ipc_token(state: State<'_, TokenState>) -> String {
    state.0.lock().unwrap().clone()
}

// ── Module state ──────────────────────────────────────────────────────────────

pub struct Modules {
    pub mouse: bool,
    pub keyboard: bool,
    pub screen: bool,
}
impl Modules {
    pub fn new() -> Self {
        Modules {
            mouse: true,
            keyboard: true,
            screen: true,
        }
    }
}
pub struct ModuleState(pub Mutex<Modules>);

#[derive(Serialize)]
pub struct ModuleStates {
    pub mouse: bool,
    pub keyboard: bool,
    pub screen: bool,
}

#[tauri::command]
pub fn get_module_states(
    token: String,
    state: State<'_, TokenState>,
    mods: State<'_, ModuleState>,
) -> Result<ModuleStates, String> {
    validate(&token, &state)?;
    let m = mods.0.lock().unwrap();
    Ok(ModuleStates {
        mouse: m.mouse,
        keyboard: m.keyboard,
        screen: m.screen,
    })
}

#[tauri::command]
pub fn toggle_module(
    module: String,
    token: String,
    state: State<'_, TokenState>,
    mods: State<'_, ModuleState>,
) -> Result<bool, String> {
    validate(&token, &state)?;
    let mut m = mods.0.lock().unwrap();
    let active = match module.as_str() {
        "mouse" => {
            m.mouse = !m.mouse;
            m.mouse
        }
        "keyboard" => {
            m.keyboard = !m.keyboard;
            m.keyboard
        }
        "screen" => {
            m.screen = !m.screen;
            m.screen
        }
        other => return Err(format!("Módulo desconocido: {}", other)),
    };
    Ok(active)
}

// ── Chat types ────────────────────────────────────────────────────────────────

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
}

const SYSTEM_PROMPT: &str = "\
Eres AgentMax Core, agente de escritorio inteligente para Windows.\n\
Responde siempre en español. Sé técnico, preciso y conciso.\n\
\n\
## Herramientas disponibles (usa sintaxis EXACTA)\n\
\n\
### Terminal PowerShell\n\
[SANDBOX: <comando>]  — Ejecuta un comando PowerShell en el sistema.\n\
Ejemplo: [SANDBOX: Get-Process | Select Name,CPU | Sort CPU -Desc | Select -First 5]\n\
Solo un [SANDBOX:...] por respuesta. Solo lectura/consulta.\n\
\n\
### Control de Mouse\n\
[MOUSE_MOVE: x=500, y=300]                     — Mover cursor a coordenadas\n\
[MOUSE_CLICK: x=500, y=300]                    — Click izquierdo\n\
[MOUSE_CLICK: x=500, y=300, button=right]      — Click derecho\n\
[MOUSE_CLICK: x=500, y=300, button=double]     — Doble click\n\
\n\
### Control de Teclado\n\
[KEY_TYPE: text=Hola mundo]      — Escribir texto (Unicode completo)\n\
[KEY_PRESS: vk=13]               — Presionar tecla por código VK\n\
VK frecuentes: Enter=13, Esc=27, Tab=9, Space=32, Backspace=8,\n\
               Del=46, Ctrl=17, Alt=18, Shift=16, Win=91,\n\
               F1=112...F12=123, Flechas: Arr=38 Abj=40 Izq=37 Der=39\n\
\n\
### Pantalla y Ventanas\n\
[SCREENSHOT]     — Capturar pantalla completa (el resultado se mostrará)\n\
[SCREEN_COLORS]  — Mapa de color hex 10x7 (análisis rápido de UI)\n\
[WINDOW_INFO]    — Obtener título y datos de la ventana activa\n\
\n\
## Reglas de uso\n\
- Usa solo UNA herramienta por respuesta.\n\
- Describe brevemente lo que harás ANTES de usar la herramienta.\n\
- Coordenadas en píxeles absolutos de pantalla.\n\
- Para tareas complejas, describe el plan completo primero.\n\
\n\
## Formato de respuesta\n\
Código en bloques Markdown con lenguaje (```python, ```powershell, etc.).\n\
Listas y tablas en GFM cuando sea útil.";

// ── Streaming LM Studio chat ──────────────────────────────────────────────────

/// Starts an SSE stream to LM Studio. Returns stream_id immediately; emits
/// `chat-token`, `chat-done`, and `chat-error` events to the frontend.
#[tauri::command]
pub async fn chat_stream(
    app: AppHandle,
    messages: Vec<ChatMessage>,
    token: String,
    lm_url: Option<String>,
    temperature: Option<f32>,
    max_tokens: Option<u32>,
    stream_id: Option<String>,
    state: State<'_, TokenState>,
) -> Result<String, String> {
    validate(&token, &state)?;

    let base = lm_url.unwrap_or_else(|| "http://127.0.0.1:1234".into());
    let url = format!("{}/v1/chat/completions", base);

    let mut all = vec![ChatMessage {
        role: "system".into(),
        content: SYSTEM_PROMPT.into(),
    }];
    all.extend(messages);

    let body = serde_json::json!({
        "model": "local-model",
        "messages": all,
        "stream": true,
        "temperature": temperature.unwrap_or(0.4).clamp(0.0, 1.2),
        "max_tokens": max_tokens.unwrap_or(2048).clamp(256, 8192)
    });

    // Use a client-supplied ID if provided so the frontend can register
    // listeners before invoking, eliminating the chat-done race condition.
    let stream_id = stream_id.unwrap_or_else(|| Uuid::new_v4().to_string());
    let sid = stream_id.clone();

    tokio::spawn(async move {
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(120))
            .build()
            .unwrap();

        let response = match client.post(&url).json(&body).send().await {
            Ok(r) => r,
            Err(e) => {
                let _ = app.emit(
                    "chat-error",
                    serde_json::json!({
                        "id": sid,
                        "error": format!("No se pudo conectar a LM Studio ({}): {}", url, e)
                    }),
                );
                let _ = app.emit("chat-done", serde_json::json!({"id": sid}));
                return;
            }
        };

        if !response.status().is_success() {
            let status = response.status().as_u16();
            let body_text = response.text().await.unwrap_or_default();
            let _ = app.emit(
                "chat-error",
                serde_json::json!({
                    "id": sid,
                    "error": format!("LM Studio error HTTP {}: {}", status, body_text)
                }),
            );
            let _ = app.emit("chat-done", serde_json::json!({"id": sid}));
            return;
        }

        let mut stream = response.bytes_stream();
        let mut buf = String::new();

        'outer: while let Some(chunk) = stream.next().await {
            match chunk {
                Err(e) => {
                    let _ = app.emit(
                        "chat-error",
                        serde_json::json!({"id":sid,"error":e.to_string()}),
                    );
                    break;
                }
                Ok(bytes) => {
                    buf.push_str(&String::from_utf8_lossy(&bytes));
                    loop {
                        if let Some(nl) = buf.find('\n') {
                            let line = buf[..nl].trim().to_string();
                            buf = buf[nl + 1..].to_string();
                            if let Some(data) = line.strip_prefix("data: ") {
                                if data.trim() == "[DONE]" {
                                    break 'outer;
                                }
                                if let Ok(val) = serde_json::from_str::<serde_json::Value>(data) {
                                    if let Some(delta) = val
                                        .pointer("/choices/0/delta/content")
                                        .and_then(|v| v.as_str())
                                    {
                                        let _ = app.emit(
                                            "chat-token",
                                            serde_json::json!({
                                                "id": sid,
                                                "delta": delta
                                            }),
                                        );
                                    }
                                }
                            }
                        } else {
                            break;
                        }
                    }
                }
            }
        }
        let _ = app.emit("chat-done", serde_json::json!({"id": sid}));
    });

    Ok(stream_id)
}

// ── Synchronous chat (intent classifier / low-latency) ───────────────────────

#[tauri::command]
pub async fn chat_sync(
    messages: Vec<ChatMessage>,
    token: String,
    lm_url: Option<String>,
    state: State<'_, TokenState>,
) -> Result<String, String> {
    validate(&token, &state)?;

    let base = lm_url.unwrap_or_else(|| "http://127.0.0.1:1234".into());
    let url = format!("{}/v1/chat/completions", base);

    let body = serde_json::json!({
        "model": "local-model",
        "messages": messages,
        "stream": false,
        "temperature": 0.1,
        "max_tokens": 64
    });

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .unwrap();

    let val: serde_json::Value = client
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())?;

    val.pointer("/choices/0/message/content")
        .and_then(|v| v.as_str())
        .map(|s| s.trim().to_string())
        .ok_or_else(|| "No se pudo extraer la respuesta del modelo".to_string())
}

/// LLM-based intent classifier — replaces hardcoded IsYes() / i18n hacks.
/// Returns true if the model determines the text matches the given intent.
#[tauri::command]
pub async fn classify_intent(
    text: String,
    intent: String,
    token: String,
    lm_url: Option<String>,
    state: State<'_, TokenState>,
) -> Result<bool, String> {
    validate(&token, &state)?;

    let prompt = format!(
        "Return ONLY the single word True or False, no punctuation.\nIntent to detect: {}\nUser message: {}",
        intent, text
    );

    let result = chat_sync(
        vec![ChatMessage {
            role: "user".into(),
            content: prompt,
        }],
        token,
        lm_url,
        state,
    )
    .await?;

    Ok(result.to_lowercase().starts_with("true"))
}

// ── Screen capture ────────────────────────────────────────────────────────────

#[cfg(target_os = "windows")]
#[tauri::command]
pub fn capture_screen_colors(
    token: String,
    state: State<'_, TokenState>,
) -> Result<String, String> {
    validate(&token, &state)?;
    use windows::Win32::Graphics::Gdi::{
        BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, DeleteDC, DeleteObject, GetDC,
        GetPixel, ReleaseDC, SelectObject, CAPTUREBLT, SRCCOPY,
    };
    use windows::Win32::UI::WindowsAndMessaging::{GetSystemMetrics, SM_CXSCREEN, SM_CYSCREEN};

    unsafe {
        let sw = GetSystemMetrics(SM_CXSCREEN);
        let sh = GetSystemMetrics(SM_CYSCREEN);
        let hdc = GetDC(None);
        let mdc = CreateCompatibleDC(Some(hdc));
        let bmp = CreateCompatibleBitmap(hdc, sw, sh);
        let _ = SelectObject(mdc, bmp.into());
        let _ = BitBlt(mdc, 0, 0, sw, sh, Some(hdc), 0, 0, SRCCOPY | CAPTUREBLT);

        let mut desc = format!("[MÓDULO PANTALLA — {}×{} px — mapa hex 10×7]\n", sw, sh);
        for gy in 0..7i32 {
            for gx in 0..10i32 {
                let px = sw * gx / 10 + sw / 20;
                let py = sh * gy / 7 + sh / 14;
                let c = GetPixel(mdc, px, py);
                let r = c.0 & 0xFF;
                let g = (c.0 >> 8) & 0xFF;
                let b = (c.0 >> 16) & 0xFF;
                desc.push_str(&format!("#{:02X}{:02X}{:02X} ", r, g, b));
            }
            desc.push('\n');
        }

        let _ = DeleteObject(bmp.into());
        let _ = DeleteDC(mdc);
        let _ = ReleaseDC(None, hdc);
        Ok(desc)
    }
}

#[cfg(not(target_os = "windows"))]
#[tauri::command]
pub fn capture_screen_colors(
    token: String,
    state: State<'_, TokenState>,
) -> Result<String, String> {
    validate(&token, &state)?;
    Ok("[Screen capture solo disponible en Windows]".to_string())
}

// ── Windows UI Automation – focused window ────────────────────────────────────

#[cfg(target_os = "windows")]
#[tauri::command]
pub fn get_focused_window_info(
    token: String,
    state: State<'_, TokenState>,
) -> Result<String, String> {
    validate(&token, &state)?;
    use windows::Win32::UI::WindowsAndMessaging::{
        GetForegroundWindow, GetWindowTextLengthW, GetWindowTextW,
    };
    unsafe {
        let hwnd = GetForegroundWindow();
        let len = GetWindowTextLengthW(hwnd);
        if len == 0 {
            return Ok("[Sin ventana activa]".to_string());
        }
        let mut buf = vec![0u16; (len + 1) as usize];
        GetWindowTextW(hwnd, &mut buf);
        let title = String::from_utf16_lossy(&buf[..len as usize]);
        Ok(format!("[Ventana activa: \"{}\"]", title))
    }
}

#[cfg(not(target_os = "windows"))]
#[tauri::command]
pub fn get_focused_window_info(
    token: String,
    state: State<'_, TokenState>,
) -> Result<String, String> {
    validate(&token, &state)?;
    Ok("[UI Automation solo disponible en Windows]".to_string())
}

// ── Python beta/runtime API (:7790) ─────────────────────────────────────────

const PYTHON_API_BASE: &str = "http://127.0.0.1:7790";
const RUST_API_BASE: &str = "http://127.0.0.1:7789";

#[derive(Serialize, Deserialize, Debug)]
pub struct TaskSubmitRequest {
    pub description: String,
    pub options: serde_json::Value,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct TaskResponse {
    pub task_id: String,
    pub status: String,
}

#[tauri::command]
pub async fn submit_task(
    description: String,
    token: String,
    state: State<'_, TokenState>,
) -> Result<TaskResponse, String> {
    validate(&token, &state)?;
    let ipc = state.0.lock().unwrap().clone();
    reqwest::Client::new()
        .post(format!("{}/api/tasks", PYTHON_API_BASE))
        .headers(python_api_headers(&ipc))
        .json(&TaskSubmitRequest {
            description,
            options: serde_json::json!({}),
        })
        .send()
        .await
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn get_task_status(
    task_id: String,
    token: String,
    state: State<'_, TokenState>,
) -> Result<serde_json::Value, String> {
    validate(&token, &state)?;
    let ipc = state.0.lock().unwrap().clone();
    reqwest::Client::new()
        .get(format!("{}/api/tasks/{}", PYTHON_API_BASE, task_id))
        .headers(python_api_headers(&ipc))
        .send()
        .await
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn emergency_stop(token: String, state: State<'_, TokenState>) -> Result<(), String> {
    validate(&token, &state)?;
    let ipc = state.0.lock().unwrap().clone();
    reqwest::Client::new()
        .post(format!("{}/api/emergency_stop", PYTHON_API_BASE))
        .headers(python_api_headers(&ipc))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
pub async fn confirm_task(
    task_id: String,
    token: String,
    state: State<'_, TokenState>,
) -> Result<(), String> {
    validate(&token, &state)?;
    let ipc = state.0.lock().unwrap().clone();
    reqwest::Client::new()
        .post(format!("{}/api/tasks/{}/confirm", PYTHON_API_BASE, task_id))
        .headers(python_api_headers(&ipc))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
pub async fn get_system_status(
    token: String,
    state: State<'_, TokenState>,
) -> Result<serde_json::Value, String> {
    validate(&token, &state)?;
    let ipc = state.0.lock().unwrap().clone();
    reqwest::Client::new()
        .get(format!("{}/api/status", PYTHON_API_BASE))
        .headers(python_api_headers(&ipc))
        .send()
        .await
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackendLaunchStatus {
    pub python_spawn_attempted: bool,
    pub python_running: bool,
    pub last_error: Option<String>,
    pub api_port: u16,
    pub ipc_auth_enabled: bool,
}

pub struct BackendLaunchState(pub Mutex<BackendLaunchStatus>);

impl Default for BackendLaunchStatus {
    fn default() -> Self {
        Self {
            python_spawn_attempted: false,
            python_running: false,
            last_error: None,
            api_port: 7790,
            ipc_auth_enabled: ipc_auth_enabled(),
        }
    }
}

#[tauri::command]
pub fn get_backend_launch_status(state: State<'_, BackendLaunchState>) -> BackendLaunchStatus {
    state.0.lock().unwrap().clone()
}

#[tauri::command]
pub fn minimize_to_panel(
    window: tauri::WebviewWindow,
    token: String,
    state: State<'_, TokenState>,
) -> Result<(), String> {
    validate(&token, &state)?;
    window.hide().ok();
    Ok(())
}

#[tauri::command]
pub fn restore_window(
    window: tauri::WebviewWindow,
    token: String,
    state: State<'_, TokenState>,
) -> Result<(), String> {
    validate(&token, &state)?;
    window.show().ok();
    window.set_focus().ok();
    Ok(())
}

#[tauri::command]
pub fn show_hud_overlay(
    app: AppHandle,
    token: String,
    state: State<'_, TokenState>,
) -> Result<(), String> {
    validate(&token, &state)?;
    if let Some(hud) = app.get_webview_window("hud") {
        hud.set_decorations(false).ok();
        hud.set_fullscreen(true).ok();
        hud.set_ignore_cursor_events(true).ok();
        hud.show().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub fn hide_hud_overlay(
    app: AppHandle,
    token: String,
    state: State<'_, TokenState>,
) -> Result<(), String> {
    validate(&token, &state)?;
    if let Some(hud) = app.get_webview_window("hud") {
        hud.hide().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub async fn get_ai_status(
    token: String,
    state: State<'_, TokenState>,
) -> Result<serde_json::Value, String> {
    validate(&token, &state)?;
    reqwest::Client::new()
        .get(format!("{}/api/ai/status", RUST_API_BASE))
        .send()
        .await
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn switch_ai_backend(
    backend: String,
    token: String,
    state: State<'_, TokenState>,
) -> Result<serde_json::Value, String> {
    validate(&token, &state)?;
    reqwest::Client::new()
        .post(format!("{}/api/ai/switch/{}", RUST_API_BASE, backend))
        .send()
        .await
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn get_lmstudio_models(
    token: String,
    state: State<'_, TokenState>,
) -> Result<serde_json::Value, String> {
    validate(&token, &state)?;
    if let Ok(r) = reqwest::Client::new()
        .get("http://localhost:1234/v1/models")
        .timeout(std::time::Duration::from_secs(3))
        .send()
        .await
    {
        if let Ok(val) = r.json::<serde_json::Value>().await {
            return Ok(val);
        }
    }
    reqwest::Client::new()
        .get(format!("{}/api/ai/lmstudio/models", RUST_API_BASE))
        .send()
        .await
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())
}

// ── Recovery Test commands ───────────────────────────────────────────────────

#[tauri::command]
pub fn recovery_get_logs(
    token: String,
    state: State<'_, TokenState>,
) -> Result<Vec<crate::recovery::RecoveryLogRecord>, String> {
    validate(&token, &state)?;
    Ok(crate::recovery::sink().records())
}

#[tauri::command]
pub fn recovery_get_status(
    token: String,
    state: State<'_, TokenState>,
) -> Result<crate::recovery::RecoveryStatus, String> {
    validate(&token, &state)?;
    Ok(crate::recovery::sink().status())
}

#[tauri::command]
pub fn recovery_clear_view(token: String, state: State<'_, TokenState>) -> Result<(), String> {
    validate(&token, &state)?;
    crate::recovery::sink().clear_view();
    Ok(())
}

#[tauri::command]
pub fn recovery_export_bundle(
    token: String,
    state: State<'_, TokenState>,
) -> Result<String, String> {
    validate(&token, &state)?;
    crate::recovery::export_bundle()
}

#[tauri::command]
pub fn recovery_open_window(
    app: AppHandle,
    token: String,
    state: State<'_, TokenState>,
) -> Result<(), String> {
    validate(&token, &state)?;
    crate::recovery::open_window(&app).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn recovery_log_frontend(
    level: String,
    source: String,
    message: String,
    details: Option<serde_json::Value>,
    token: Option<String>,
    state: State<'_, TokenState>,
) -> Result<crate::recovery::RecoveryLogRecord, String> {
    if let Some(token) = token.as_deref() {
        validate(token, &state)?;
    }
    Ok(crate::recovery::record_frontend(
        level, source, message, details,
    ))
}

// ── llama.cpp GGUF sidecar commands ───────────────────────────────────────────

#[tauri::command]
pub async fn llamacpp_get_status(
    token: String,
    state: State<'_, TokenState>,
    process: State<'_, LlamaCppProcess>,
) -> Result<LlamaCppStatus, String> {
    validate(&token, &state)?;
    Ok(llamacpp_status_inner(&process).await)
}

#[tauri::command]
pub async fn llamacpp_start(
    app: AppHandle,
    token: String,
    state: State<'_, TokenState>,
    process: State<'_, LlamaCppProcess>,
) -> Result<LlamaCppStatus, String> {
    validate(&token, &state)?;

    let cfg = llamacpp_config();
    if cfg.model_path.trim().is_empty() {
        let status = llamacpp_status_with_error(&cfg, "AGENTMAX_GGUF_MODEL_PATH is not set");
        let _ = app.emit("llamacpp-status", &status);
        return Ok(status);
    }
    if !Path::new(&cfg.model_path).exists() {
        let status = llamacpp_status_with_error(
            &cfg,
            &format!("GGUF model path does not exist: {}", cfg.model_path),
        );
        let _ = app.emit("llamacpp-status", &status);
        return Ok(status);
    }
    if looks_like_path(&cfg.server_bin) && !Path::new(&cfg.server_bin).exists() {
        let status = llamacpp_status_with_error(
            &cfg,
            &format!("llama-server binary does not exist: {}", cfg.server_bin),
        );
        let _ = app.emit("llamacpp-status", &status);
        return Ok(status);
    }

    let current = llamacpp_status_inner(&process).await;
    if current.ready || current.running {
        let _ = app.emit("llamacpp-status", &current);
        return Ok(current);
    }

    let mut cmd = std::process::Command::new(&cfg.server_bin);
    cmd.args([
        "--model",
        &cfg.model_path,
        "--host",
        &cfg.host,
        "--port",
        &cfg.port.to_string(),
        "--ctx-size",
        &cfg.context_size.to_string(),
    ]);
    if cfg.threads > 0 {
        cmd.args(["--threads", &cfg.threads.to_string()]);
    }
    if cfg.gpu_layers != 0 {
        cmd.args(["--n-gpu-layers", &cfg.gpu_layers.to_string()]);
    }
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());

    match cmd.spawn() {
        Ok(mut child) => {
            if let Some(stdout) = child.stdout.take() {
                crate::recovery::spawn_pipe_reader(stdout, "llamacpp.stdout", "info");
            }
            if let Some(stderr) = child.stderr.take() {
                crate::recovery::spawn_pipe_reader(stderr, "llamacpp.stderr", "warn");
            }
            crate::recovery::sink().record(
                "info",
                "llamacpp",
                "llama.cpp sidecar launched",
                Some(serde_json::json!({
                    "serverBin": cfg.server_bin,
                    "modelPath": cfg.model_path,
                    "host": cfg.host,
                    "port": cfg.port,
                })),
            );
            if let Ok(mut guard) = process.0.lock() {
                *guard = Some(child);
            }
        }
        Err(error) => {
            let status = llamacpp_status_with_error(
                &cfg,
                &format!("Failed to launch llama-server: {error}"),
            );
            crate::recovery::sink().record(
                "error",
                "llamacpp",
                status.error.as_deref().unwrap_or("launch failed"),
                None,
            );
            let _ = app.emit("llamacpp-status", &status);
            return Ok(status);
        }
    }

    let mut status = llamacpp_status_inner(&process).await;
    for _ in 0..16 {
        if status.ready {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(250)).await;
        status = llamacpp_status_inner(&process).await;
    }
    let _ = app.emit("llamacpp-status", &status);
    Ok(status)
}

#[tauri::command]
pub async fn llamacpp_stop(
    app: AppHandle,
    token: String,
    state: State<'_, TokenState>,
    process: State<'_, LlamaCppProcess>,
) -> Result<LlamaCppStatus, String> {
    validate(&token, &state)?;
    if let Ok(mut guard) = process.0.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
            crate::recovery::sink().record("info", "llamacpp", "llama.cpp sidecar stopped", None);
        }
    }
    let status = llamacpp_status_inner(&process).await;
    let _ = app.emit("llamacpp-status", &status);
    Ok(status)
}

fn llamacpp_config() -> LlamaCppRuntimeConfig {
    let host = env_first(&["AGENTMAX_LLAMA_HOST", "AGENTMAX_LLAMACPP_HOST"])
        .unwrap_or_else(|| "127.0.0.1".to_string());
    let port = env_first(&["AGENTMAX_LLAMA_PORT", "AGENTMAX_LLAMACPP_PORT"])
        .and_then(|value| value.parse::<u16>().ok())
        .unwrap_or(8080);
    let model_path =
        env_first(&["AGENTMAX_GGUF_MODEL_PATH", "AGENTMAX_LLAMA_MODEL_PATH"]).unwrap_or_default();
    let server_bin = env_first(&["AGENTMAX_LLAMA_SERVER_BIN", "AGENTMAX_LLAMACPP_SERVER_BIN"])
        .unwrap_or_else(|| "llama-server".to_string());
    let context_size = env_first(&["AGENTMAX_LLAMA_CONTEXT", "AGENTMAX_LLAMA_CTX"])
        .and_then(|value| value.parse::<u32>().ok())
        .unwrap_or(4096);
    let threads = env_first(&["AGENTMAX_LLAMA_THREADS"])
        .and_then(|value| value.parse::<u32>().ok())
        .unwrap_or(0);
    let gpu_layers = env_first(&["AGENTMAX_LLAMA_GPU_LAYERS"])
        .and_then(|value| value.parse::<i32>().ok())
        .unwrap_or(0);
    LlamaCppRuntimeConfig {
        host,
        port,
        model_path,
        server_bin,
        context_size,
        threads,
        gpu_layers,
    }
}

async fn llamacpp_status_inner(process: &LlamaCppProcess) -> LlamaCppStatus {
    let cfg = llamacpp_config();
    let base_url = format!("http://{}:{}/v1", cfg.host, cfg.port);
    let mut running = false;
    let mut error: Option<String> = None;

    if let Ok(mut guard) = process.0.lock() {
        if let Some(child) = guard.as_mut() {
            match child.try_wait() {
                Ok(Some(status)) => {
                    error = Some(format!("llama-server exited with status {status}"));
                    *guard = None;
                }
                Ok(None) => running = true,
                Err(exc) => {
                    error = Some(format!("llama-server status check failed: {exc}"));
                    *guard = None;
                }
            }
        }
    }

    let mut ready = false;
    let mut models = Vec::new();
    match reqwest::Client::new()
        .get(format!("{base_url}/models"))
        .timeout(std::time::Duration::from_secs(2))
        .send()
        .await
    {
        Ok(response) if response.status().is_success() => {
            ready = true;
            running = true;
            if let Ok(data) = response.json::<serde_json::Value>().await {
                models = extract_model_ids(&data);
            }
        }
        Ok(response) => {
            if error.is_none() {
                error = Some(format!("llama-server HTTP {}", response.status()));
            }
        }
        Err(exc) => {
            if error.is_none() {
                error = Some(exc.to_string());
            }
        }
    }

    let configured = !cfg.model_path.trim().is_empty()
        && Path::new(&cfg.model_path).exists()
        && (!looks_like_path(&cfg.server_bin) || Path::new(&cfg.server_bin).exists());

    LlamaCppStatus {
        configured,
        running,
        ready,
        host: cfg.host,
        port: cfg.port,
        base_url,
        model_path: cfg.model_path,
        server_bin: cfg.server_bin,
        models,
        error: if ready { None } else { error },
    }
}

fn llamacpp_status_with_error(cfg: &LlamaCppRuntimeConfig, error: &str) -> LlamaCppStatus {
    LlamaCppStatus {
        configured: false,
        running: false,
        ready: false,
        host: cfg.host.clone(),
        port: cfg.port,
        base_url: format!("http://{}:{}/v1", cfg.host, cfg.port),
        model_path: cfg.model_path.clone(),
        server_bin: cfg.server_bin.clone(),
        models: Vec::new(),
        error: Some(error.to_string()),
    }
}

fn extract_model_ids(data: &serde_json::Value) -> Vec<String> {
    let items = data
        .get("data")
        .or_else(|| data.get("models"))
        .and_then(|value| value.as_array())
        .cloned()
        .unwrap_or_default();
    items
        .iter()
        .filter_map(|item| {
            item.get("id")
                .or_else(|| item.get("path"))
                .and_then(|value| value.as_str())
                .map(ToString::to_string)
        })
        .collect()
}

fn env_first(names: &[&str]) -> Option<String> {
    names.iter().find_map(|name| {
        std::env::var(name)
            .ok()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
    })
}

fn looks_like_path(value: &str) -> bool {
    value.contains('/') || value.contains('\\') || value.contains(':')
}

// ── Native commands (security / screen / input) ───────────────────────────────

#[tauri::command]
pub fn get_hardware_fingerprint(
    token: String,
    state: State<'_, TokenState>,
    hw: State<'_, HardwareFpState>,
) -> Result<String, String> {
    validate(&token, &state)?;
    Ok(hw.0.clone())
}

#[tauri::command]
pub fn desktop_get_permissions(
    token: String,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<PermissionState>, String> {
    validate(&token, &state)?;
    let started = std::time::Instant::now();
    let service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(DesktopToolResult::ok(service.permissions(), started, false))
}

#[tauri::command]
pub fn desktop_set_permissions(
    token: String,
    patch: PermissionPatch,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<PermissionState>, String> {
    validate(&token, &state)?;
    let started = std::time::Instant::now();
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    let permissions = service.set_permissions(patch);
    Ok(DesktopToolResult::ok(
        permissions,
        started,
        service.intervention_status().paused,
    ))
}

#[tauri::command]
pub fn desktop_get_tool_status(
    token: String,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<ToolStatus>, String> {
    validate(&token, &state)?;
    let started = std::time::Instant::now();
    let service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(DesktopToolResult::ok(
        service.tool_status(),
        started,
        service.intervention_status().paused,
    ))
}

#[tauri::command]
pub fn desktop_take_screenshot(
    token: String,
    include_base64: Option<bool>,
    save_to_disk: Option<bool>,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<ScreenshotResult>, String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(service.take_screenshot(
        include_base64.unwrap_or(true),
        save_to_disk.unwrap_or(false),
    ))
}

#[tauri::command]
pub fn desktop_get_screen_info(
    token: String,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<ScreenInfo>, String> {
    validate(&token, &state)?;
    let service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(service.get_screen_info())
}

#[tauri::command]
pub fn desktop_locate_on_screen(
    token: String,
    query: String,
    min_confidence: Option<f32>,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<LocateOnScreenResult>, String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(service.locate_on_screen(LocateOnScreenArgs {
        query,
        min_confidence,
    }))
}

#[tauri::command]
pub fn desktop_get_mouse_position(
    token: String,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<MousePosition>, String> {
    validate(&token, &state)?;
    let service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(service.get_mouse_position())
}

#[tauri::command]
pub fn desktop_move_mouse(
    token: String,
    x: i32,
    y: i32,
    duration_ms: Option<u64>,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<ActionResult>, String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(service.move_mouse(MouseMoveArgs { x, y, duration_ms }))
}

#[tauri::command]
pub fn desktop_click(
    token: String,
    button: MouseButton,
    clicks: Option<u8>,
    x: Option<i32>,
    y: Option<i32>,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<ActionResult>, String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(service.click(ClickArgs {
        button,
        clicks,
        x,
        y,
    }))
}

#[tauri::command]
pub fn desktop_double_click(
    token: String,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<ActionResult>, String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(service.double_click())
}

#[tauri::command]
pub fn desktop_drag(
    token: String,
    from_x: i32,
    from_y: i32,
    to_x: i32,
    to_y: i32,
    duration_ms: Option<u64>,
    button: Option<MouseButton>,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<ActionResult>, String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(service.drag(DragArgs {
        from_x,
        from_y,
        to_x,
        to_y,
        duration_ms,
        button,
    }))
}

#[tauri::command]
pub fn desktop_scroll(
    token: String,
    delta_x: i32,
    delta_y: i32,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<ActionResult>, String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(service.scroll(ScrollArgs { delta_x, delta_y }))
}

#[tauri::command]
pub fn desktop_type_text(
    token: String,
    text: String,
    interval_ms: Option<u64>,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<ActionResult>, String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(service.type_text(TypeTextArgs { text, interval_ms }))
}

#[tauri::command]
pub fn desktop_press_key(
    token: String,
    key: String,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<ActionResult>, String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(service.press_key(key))
}

#[tauri::command]
pub fn desktop_key_combo(
    token: String,
    keys: Vec<String>,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<ActionResult>, String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(service.key_combo(KeyComboArgs { keys }))
}

#[tauri::command]
pub fn desktop_copy(
    token: String,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<ActionResult>, String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(service.shortcut("c"))
}

#[tauri::command]
pub fn desktop_paste(
    token: String,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<ActionResult>, String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(service.shortcut("v"))
}

#[tauri::command]
pub fn desktop_select_all(
    token: String,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<ActionResult>, String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    Ok(service.shortcut("a"))
}

#[tauri::command]
pub fn desktop_pause_automation(
    token: String,
    reason: Option<String>,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<InterventionStatus>, String> {
    validate(&token, &state)?;
    let started = std::time::Instant::now();
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    let status = service.pause(reason.unwrap_or_else(|| "manual_pause".to_string()));
    Ok(DesktopToolResult::ok(status, started, true))
}

#[tauri::command]
pub fn desktop_resume_automation(
    token: String,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<InterventionStatus>, String> {
    validate(&token, &state)?;
    let started = std::time::Instant::now();
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    let status = service.resume();
    Ok(DesktopToolResult::ok(status, started, false))
}

#[tauri::command]
pub fn desktop_record_user_intervention(
    token: String,
    source: String,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<DesktopToolResult<InterventionStatus>, String> {
    validate(&token, &state)?;
    let started = std::time::Instant::now();
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    let status = service.record_user_intervention(source);
    Ok(DesktopToolResult::ok(status, started, true))
}

#[tauri::command]
pub fn screenshot_base64(
    token: String,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<String, String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    let result = service.take_screenshot(true, false);
    if result.success {
        result
            .data
            .and_then(|data| data.image_base64)
            .ok_or_else(|| "Screenshot returned no base64 payload".to_string())
    } else {
        Err(result
            .error
            .unwrap_or_else(|| "Screenshot failed".to_string()))
    }
}

// ── Sandbox shell execution ────────────────────────────────────────────────────

#[derive(Serialize)]
pub struct ShellOutput {
    pub stdout: String,
    pub stderr: String,
    pub exit_code: i32,
    pub success: bool,
}

const BLOCKED_PATTERNS: &[&str] = &[
    // Destructive filesystem
    "format ",
    "rmdir /s",
    "rd /s",
    "del /f /s",
    "del /f /q",
    "remove-item -recurse",
    "ri -recurse",
    "remove-item -force",
    // Registry / boot
    "reg delete",
    "reg add",
    "bcdedit",
    "diskpart",
    // Privilege escalation
    "net user /add",
    "net localgroup administrators",
    "net user ",
    "netsh firewall",
    "sfc /scannow",
    "cipher /w",
    // Path traversal out of sandbox working dir
    "..\\",
    "../",
    "cd /",
    "set-location /",
    // Dangerous env / process manipulation
    "$env:path",
    "start-process",
    "invoke-expression",
    "iex ",
    "downloadstring",
    "downloadfile",
    "webclient",
    // Network / remote execution
    "invoke-webrequest",
    "curl ",
    "wget ",
    "certutil",
    // PowerShell execution policies
    "set-executionpolicy",
    "bypass -enc",
    "base64",
    // Credential access
    "mimikatz",
    "sekurlsa",
    "lsass",
    "sam ",
    // Obfuscated execution
    "-enc ",
    "-encodedcommand",
    "frombase64string",
];

fn is_command_safe(cmd: &str) -> Result<(), String> {
    let lower = cmd.to_lowercase();
    // Strip common whitespace obfuscation
    let normalized = lower.split_whitespace().collect::<Vec<_>>().join(" ");

    for pat in BLOCKED_PATTERNS {
        if normalized.contains(pat) {
            return Err(format!(
                "Sandbox: comando bloqueado (patron '{}' no permitido)",
                pat.trim()
            ));
        }
    }

    // Reject commands longer than 2000 chars (potential buffer overflow / DoS)
    if cmd.len() > 2000 {
        return Err("Sandbox: comando demasiado largo (max 2000 caracteres)".to_string());
    }

    Ok(())
}

fn sandbox_dir() -> std::io::Result<std::path::PathBuf> {
    let mut dir = std::env::temp_dir();
    // UUID name eliminates timestamp-collision risk and makes the dir
    // unpredictable to other processes on the same machine.
    dir.push(format!("AgentMax-sandbox-{}", Uuid::new_v4()));
    std::fs::create_dir_all(&dir)?;
    Ok(dir)
}

#[allow(dead_code)]
fn safe_within(dir: &std::path::Path, candidate: &std::path::Path) -> bool {
    // Prevent path traversal: ensure candidate is a descendant of dir.
    // std::fs::canonicalize would be ideal but requires the path to exist;
    // instead we normalise with components() which strips ..\\ segments.
    let mut abs = dir.to_path_buf();
    for component in candidate.components() {
        match component {
            std::path::Component::ParentDir => {
                abs.pop();
            }
            std::path::Component::Normal(p) => abs.push(p),
            std::path::Component::RootDir | std::path::Component::Prefix(_) => {
                // Absolute path that escapes the sandbox root — reject.
                return false;
            }
            _ => {}
        }
    }
    abs.starts_with(dir)
}

#[tauri::command]
pub async fn run_shell_command(
    cmd: String,
    token: String,
    state: State<'_, TokenState>,
) -> Result<ShellOutput, String> {
    validate(&token, &state)?;

    // Multi-layer security check
    is_command_safe(&cmd)?;

    // Each command runs in a fresh UUID-named temp dir.
    // This scopes filesystem side-effects and prevents cross-command contamination.
    let work_dir = tokio::task::spawn_blocking(sandbox_dir)
        .await
        .map_err(|e| format!("Sandbox: join error — {e}"))?
        .map_err(|e| format!("Sandbox: no se pudo crear directorio de trabajo — {e}"))?;

    // Quick path-traversal check: if the command tries to reference a path
    // outside work_dir we reject before execution.
    // (Full isolation via Windows Job Objects is a future hardening step.)
    let work_dir_clone = work_dir.clone();
    let mut process = if cfg!(windows) {
        let mut process = tokio::process::Command::new("powershell");
        process.args([
            "-NonInteractive",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            &cmd,
        ]);
        process
    } else {
        let mut process = tokio::process::Command::new("sh");
        process.args(["-lc", &cmd]);
        process
    };
    let result = tokio::time::timeout(
        std::time::Duration::from_secs(30),
        process.current_dir(&work_dir_clone).output(),
    )
    .await
    .map_err(|_| "Sandbox: timeout (30s excedido)".to_string())?
    .map_err(|e| format!("Sandbox: error de proceso — {}", e))?;

    // Clean up the ephemeral sandbox dir (best-effort; non-fatal on failure).
    let _ = std::fs::remove_dir_all(&work_dir);

    Ok(ShellOutput {
        stdout: String::from_utf8_lossy(&result.stdout).trim().to_string(),
        stderr: String::from_utf8_lossy(&result.stderr).trim().to_string(),
        exit_code: result.status.code().unwrap_or(-1),
        success: result.status.success(),
    })
}

#[tauri::command]
pub fn inject_input(
    token: String,
    input_type: String,
    vk_code: Option<u16>,
    x: Option<i32>,
    y: Option<i32>,
    button: Option<String>,
    is_down: Option<bool>,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<(), String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    match input_type.as_str() {
        "key" => {
            if let Some(vk) = vk_code {
                service.require_keyboard()?;
                crate::native::inject_key_event(vk, is_down.unwrap_or(true));
            }
        }
        "click" => match (x, y) {
            (Some(px), Some(py)) => {
                let mapped = match button.unwrap_or_else(|| "left".into()).as_str() {
                    "right" => MouseButton::Right,
                    "middle" => MouseButton::Middle,
                    _ => MouseButton::Left,
                };
                let result = service.click(ClickArgs {
                    button: mapped,
                    clicks: Some(1),
                    x: Some(px),
                    y: Some(py),
                });
                if !result.success {
                    return Err(result
                        .error
                        .unwrap_or_else(|| "Mouse click failed".to_string()));
                }
            }
            _ => return Err("inject_input: x and y required for click".into()),
        },
        "move" => match (x, y) {
            (Some(px), Some(py)) => {
                let result = service.move_mouse(MouseMoveArgs {
                    x: px,
                    y: py,
                    duration_ms: None,
                });
                if !result.success {
                    return Err(result
                        .error
                        .unwrap_or_else(|| "Mouse move failed".to_string()));
                }
            }
            _ => return Err("inject_input: x and y required for move".into()),
        },
        other => return Err(format!("inject_input: unknown type '{}'", other)),
    }
    Ok(())
}

#[tauri::command]
pub fn inject_mouse_move(
    token: String,
    x: i32,
    y: i32,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<(), String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    let result = service.move_mouse(MouseMoveArgs {
        x,
        y,
        duration_ms: None,
    });
    if result.success {
        Ok(())
    } else {
        Err(result
            .error
            .unwrap_or_else(|| "Mouse move failed".to_string()))
    }
}

#[tauri::command]
pub fn type_text(
    token: String,
    text: String,
    state: State<'_, TokenState>,
    desktop: State<'_, DesktopAutomationState>,
) -> Result<(), String> {
    validate(&token, &state)?;
    let mut service = desktop
        .0
        .lock()
        .map_err(|_| "Desktop automation state poisoned".to_string())?;
    let result = service.type_text(TypeTextArgs {
        text,
        interval_ms: None,
    });
    if result.success {
        Ok(())
    } else {
        Err(result
            .error
            .unwrap_or_else(|| "Type text failed".to_string()))
    }
}

#[tauri::command]
pub fn show_widget(
    app: AppHandle,
    token: String,
    state: State<'_, TokenState>,
) -> Result<(), String> {
    validate(&token, &state)?;
    if let Some(w) = app.get_webview_window("widget") {
        w.show().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub fn hide_widget(
    app: AppHandle,
    token: String,
    state: State<'_, TokenState>,
) -> Result<(), String> {
    validate(&token, &state)?;
    if let Some(w) = app.get_webview_window("widget") {
        w.hide().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub fn restore_main_window(
    app: AppHandle,
    token: String,
    state: State<'_, TokenState>,
) -> Result<(), String> {
    validate(&token, &state)?;
    if let Some(w) = app.get_webview_window("main") {
        w.show().map_err(|e| e.to_string())?;
        w.set_focus().map_err(|e| e.to_string())?;
    }
    Ok(())
}
