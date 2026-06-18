use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::process::Command;
use std::sync::Mutex;
#[cfg(target_os = "windows")]
use std::sync::{
    atomic::{AtomicBool, AtomicU64, AtomicU8, Ordering},
    OnceLock,
};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

#[cfg(target_os = "windows")]
const INPUT_SOURCE_NONE: u8 = 0;
#[cfg(target_os = "windows")]
const INPUT_SOURCE_MOUSE: u8 = 1;
#[cfg(target_os = "windows")]
const INPUT_SOURCE_KEYBOARD: u8 = 2;

#[cfg(all(target_os = "windows", not(test)))]
static GLOBAL_MONITOR_STARTED: AtomicBool = AtomicBool::new(false);
#[cfg(target_os = "windows")]
static GLOBAL_MONITOR_RUNNING: AtomicBool = AtomicBool::new(false);
#[cfg(target_os = "windows")]
static GLOBAL_LAST_PHYSICAL_INPUT_AT: AtomicU64 = AtomicU64::new(0);
#[cfg(target_os = "windows")]
static GLOBAL_LAST_PHYSICAL_INPUT_SOURCE: AtomicU8 = AtomicU8::new(INPUT_SOURCE_NONE);
#[cfg(target_os = "windows")]
static GLOBAL_MONITOR_ERROR: OnceLock<Mutex<Option<String>>> = OnceLock::new();

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum DesktopPlatform {
    Windows,
    Linux,
    Macos,
    Unknown,
}

impl DesktopPlatform {
    pub fn current() -> Self {
        if cfg!(target_os = "windows") {
            Self::Windows
        } else if cfg!(target_os = "linux") {
            Self::Linux
        } else if cfg!(target_os = "macos") {
            Self::Macos
        } else {
            Self::Unknown
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopToolResult<T>
where
    T: Serialize,
{
    pub success: bool,
    pub data: Option<T>,
    pub error: Option<String>,
    pub platform: DesktopPlatform,
    pub duration_ms: u128,
    pub paused: bool,
}

impl<T> DesktopToolResult<T>
where
    T: Serialize,
{
    pub fn ok(data: T, started: Instant, paused: bool) -> Self {
        Self {
            success: true,
            data: Some(data),
            error: None,
            platform: DesktopPlatform::current(),
            duration_ms: started.elapsed().as_millis(),
            paused,
        }
    }

    pub fn fail(error: impl Into<String>, started: Instant, paused: bool) -> Self {
        Self {
            success: false,
            data: None,
            error: Some(error.into()),
            platform: DesktopPlatform::current(),
            duration_ms: started.elapsed().as_millis(),
            paused,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PermissionState {
    pub screen_capture_enabled: bool,
    pub mouse_control_enabled: bool,
    pub keyboard_control_enabled: bool,
    pub automation_enabled: bool,
}

impl Default for PermissionState {
    fn default() -> Self {
        Self {
            screen_capture_enabled: false,
            mouse_control_enabled: false,
            keyboard_control_enabled: false,
            automation_enabled: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PermissionPatch {
    pub screen_capture_enabled: Option<bool>,
    pub mouse_control_enabled: Option<bool>,
    pub keyboard_control_enabled: Option<bool>,
    pub automation_enabled: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MonitorInfo {
    pub id: String,
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    pub primary: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ScreenInfo {
    pub width: u32,
    pub height: u32,
    pub monitors: Vec<MonitorInfo>,
    pub display_server: Option<String>,
    pub automation_available: bool,
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ScreenshotResult {
    pub width: u32,
    pub height: u32,
    pub image_path: Option<String>,
    pub image_base64: Option<String>,
    pub monitor_id: Option<String>,
    pub timestamp: u128,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LocateOnScreenArgs {
    pub query: String,
    pub min_confidence: Option<f32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BoundingBox {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LocateOnScreenResult {
    pub query: String,
    pub matches: Vec<ScreenMatch>,
    pub source: String,
    pub timestamp: u128,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ScreenMatch {
    pub text: Option<String>,
    pub bounds: BoundingBox,
    pub confidence: f32,
    pub source: String,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum MouseButton {
    Left,
    Right,
    Middle,
}

impl MouseButton {
    fn xdotool_button(self) -> &'static str {
        match self {
            Self::Left => "1",
            Self::Middle => "2",
            Self::Right => "3",
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MousePosition {
    pub x: i32,
    pub y: i32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MouseMoveArgs {
    pub x: i32,
    pub y: i32,
    pub duration_ms: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClickArgs {
    pub button: MouseButton,
    pub clicks: Option<u8>,
    pub x: Option<i32>,
    pub y: Option<i32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DragArgs {
    pub from_x: i32,
    pub from_y: i32,
    pub to_x: i32,
    pub to_y: i32,
    pub duration_ms: Option<u64>,
    pub button: Option<MouseButton>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ScrollArgs {
    pub delta_x: i32,
    pub delta_y: i32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TypeTextArgs {
    pub text: String,
    pub interval_ms: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KeyComboArgs {
    pub keys: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ActionResult {
    pub action: String,
    pub final_x: Option<i32>,
    pub final_y: Option<i32>,
    pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InterventionStatus {
    pub paused: bool,
    pub reason: Option<String>,
    pub last_user_intervention_at: Option<u128>,
    pub last_action: Option<String>,
    pub native_input_monitor: NativeInputMonitorStatus,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NativeInputMonitorStatus {
    pub available: bool,
    pub running: bool,
    pub last_physical_input_at: Option<u128>,
    pub last_source: Option<String>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ToolCatalogItem {
    pub id: String,
    pub name: String,
    pub category: String,
    pub permission: String,
    pub implemented: bool,
    pub risk_level: String,
    pub manual_test: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ToolStatus {
    pub permissions: PermissionState,
    pub intervention: InterventionStatus,
    pub screen: ScreenInfo,
    pub tools: Vec<ToolCatalogItem>,
}

#[derive(Debug, Clone)]
struct AutomationRuntimeState {
    paused: bool,
    pause_reason: Option<String>,
    last_user_intervention_at: Option<u128>,
    last_action: Option<String>,
    expected_mouse: Option<MousePosition>,
}

impl Default for AutomationRuntimeState {
    fn default() -> Self {
        Self {
            paused: false,
            pause_reason: None,
            last_user_intervention_at: None,
            last_action: None,
            expected_mouse: None,
        }
    }
}

pub struct DesktopAutomationState(pub Mutex<DesktopAutomationService>);

pub struct DesktopAutomationService {
    permissions: PermissionState,
    runtime: AutomationRuntimeState,
}

impl DesktopAutomationService {
    pub fn new() -> Self {
        #[cfg(all(target_os = "windows", not(test)))]
        start_native_intervention_monitor();

        Self {
            permissions: PermissionState::default(),
            runtime: AutomationRuntimeState::default(),
        }
    }

    pub fn permissions(&self) -> PermissionState {
        self.permissions.clone()
    }

    pub fn set_permissions(&mut self, patch: PermissionPatch) -> PermissionState {
        if let Some(value) = patch.screen_capture_enabled {
            self.permissions.screen_capture_enabled = value;
        }
        if let Some(value) = patch.mouse_control_enabled {
            self.permissions.mouse_control_enabled = value;
        }
        if let Some(value) = patch.keyboard_control_enabled {
            self.permissions.keyboard_control_enabled = value;
        }
        if let Some(value) = patch.automation_enabled {
            self.permissions.automation_enabled = value;
        }
        self.permissions.clone()
    }

    pub fn resume(&mut self) -> Result<InterventionStatus, String> {
        if !self.permissions.automation_enabled
            || (!self.permissions.mouse_control_enabled
                && !self.permissions.keyboard_control_enabled)
        {
            return Err(
                "Cannot resume automation without an active mouse or keyboard permission"
                    .to_string(),
            );
        }
        let last_input = native_input_monitor_status()
            .last_physical_input_at
            .or(self.runtime.last_user_intervention_at);
        if let Some(last_input_at) = last_input {
            let quiet_ms = now_millis().saturating_sub(last_input_at);
            if quiet_ms < 2_000 {
                return Err(format!(
                    "Cannot resume until user input has been quiet for 2000ms (quiet={quiet_ms}ms)"
                ));
            }
        }
        self.runtime.paused = false;
        self.runtime.pause_reason = None;
        self.runtime.expected_mouse = get_mouse_position_native().ok();
        Ok(self.intervention_status())
    }

    pub fn pause(&mut self, reason: String) -> InterventionStatus {
        self.mark_user_intervention(reason);
        self.intervention_status()
    }

    pub fn intervention_status(&self) -> InterventionStatus {
        InterventionStatus {
            paused: self.runtime.paused,
            reason: self.runtime.pause_reason.clone(),
            last_user_intervention_at: self.runtime.last_user_intervention_at,
            last_action: self.runtime.last_action.clone(),
            native_input_monitor: native_input_monitor_status(),
        }
    }

    pub fn tool_status(&self) -> ToolStatus {
        ToolStatus {
            permissions: self.permissions(),
            intervention: self.intervention_status(),
            screen: get_screen_info_native(),
            tools: default_tool_catalog(),
        }
    }

    pub fn take_screenshot(
        &mut self,
        include_base64: bool,
        save_to_disk: bool,
    ) -> DesktopToolResult<ScreenshotResult> {
        let started = Instant::now();
        if let Err(error) = self.require_screen() {
            return DesktopToolResult::fail(error, started, self.runtime.paused);
        }

        match capture_png_native() {
            Ok((bytes, width, height)) => {
                let image_path = if save_to_disk {
                    match write_screenshot_file(&bytes) {
                        Ok(path) => Some(path.to_string_lossy().to_string()),
                        Err(error) => {
                            return DesktopToolResult::fail(error, started, self.runtime.paused);
                        }
                    }
                } else {
                    None
                };
                let result = ScreenshotResult {
                    width,
                    height,
                    image_path,
                    image_base64: include_base64.then(|| STANDARD.encode(&bytes)),
                    monitor_id: Some("primary".to_string()),
                    timestamp: now_millis(),
                };
                self.runtime.last_action = Some("take_screenshot".to_string());
                DesktopToolResult::ok(result, started, self.runtime.paused)
            }
            Err(error) => DesktopToolResult::fail(error, started, self.runtime.paused),
        }
    }

    pub fn get_screen_info(&self) -> DesktopToolResult<ScreenInfo> {
        let started = Instant::now();
        DesktopToolResult::ok(get_screen_info_native(), started, self.runtime.paused)
    }

    pub fn locate_on_screen(
        &mut self,
        args: LocateOnScreenArgs,
    ) -> DesktopToolResult<LocateOnScreenResult> {
        let started = Instant::now();
        if let Err(error) = self.require_screen() {
            return DesktopToolResult::fail(error, started, self.runtime.paused);
        }
        let query = args.query.trim();
        if query.is_empty() {
            return DesktopToolResult::fail(
                "locate_on_screen requires a non-empty query",
                started,
                self.runtime.paused,
            );
        }
        let min_confidence = args.min_confidence.unwrap_or(0.55).clamp(0.0, 1.0);
        let (png_bytes, _w, _h) = match capture_png_native() {
            Ok(result) => result,
            Err(e) => return DesktopToolResult::fail(e, started, self.runtime.paused),
        };
        // Write screenshot to a temp file for tesseract
        let mut tmp_path = std::env::temp_dir();
        tmp_path.push(format!("AgentMax-ocr-{}.png", now_millis()));
        if let Err(e) = std::fs::write(&tmp_path, &png_bytes) {
            return DesktopToolResult::fail(
                format!("Could not write temp screenshot for OCR: {e}"),
                started,
                self.runtime.paused,
            );
        }
        let tmp_str = tmp_path.to_string_lossy().to_string();
        let tsv_result = run_command("tesseract", &[&tmp_str, "stdout", "--psm", "3", "tsv"]);
        let _ = std::fs::remove_file(&tmp_path);
        let tsv_bytes = match tsv_result {
            Ok(b) => b,
            Err(e) => {
                return DesktopToolResult::fail(
                    format!(
                        "tesseract not found or failed — install with: \
                        https://github.com/tesseract-ocr/tesseract#installing-tesseract: {e}"
                    ),
                    started,
                    self.runtime.paused,
                );
            }
        };
        let tsv = String::from_utf8_lossy(&tsv_bytes);
        let matches = parse_tesseract_tsv(&tsv, query, min_confidence);
        let result = LocateOnScreenResult {
            query: query.to_string(),
            matches,
            source: "tesseract-ocr".to_string(),
            timestamp: now_millis(),
        };
        self.runtime.last_action = Some("locate_on_screen".to_string());
        DesktopToolResult::ok(result, started, self.runtime.paused)
    }

    pub fn get_mouse_position(&self) -> DesktopToolResult<MousePosition> {
        let started = Instant::now();
        if let Err(error) = self.require_mouse() {
            return DesktopToolResult::fail(error, started, self.runtime.paused);
        }
        match get_mouse_position_native() {
            Ok(position) => DesktopToolResult::ok(position, started, self.runtime.paused),
            Err(error) => DesktopToolResult::fail(error, started, self.runtime.paused),
        }
    }

    pub fn move_mouse(&mut self, args: MouseMoveArgs) -> DesktopToolResult<ActionResult> {
        let started = Instant::now();
        if let Err(error) = self.require_mouse() {
            return DesktopToolResult::fail(error, started, self.runtime.paused);
        }
        let target = clamp_to_screen(args.x, args.y);
        let duration = args.duration_ms.unwrap_or(0).min(10_000);
        let action_started_at = now_millis();
        match move_mouse_native(
            target.x,
            target.y,
            duration,
            action_started_at,
            &mut self.runtime,
        ) {
            Ok(()) => {
                self.runtime.expected_mouse = Some(target.clone());
                self.runtime.last_action = Some("move_mouse".to_string());
                DesktopToolResult::ok(
                    ActionResult {
                        action: "move_mouse".to_string(),
                        final_x: Some(target.x),
                        final_y: Some(target.y),
                        detail: format!("Mouse moved to {},{}", target.x, target.y),
                    },
                    started,
                    self.runtime.paused,
                )
            }
            Err(error) => DesktopToolResult::fail(error, started, self.runtime.paused),
        }
    }

    pub fn click(&mut self, args: ClickArgs) -> DesktopToolResult<ActionResult> {
        let started = Instant::now();
        if let Err(error) = self.require_mouse() {
            return DesktopToolResult::fail(error, started, self.runtime.paused);
        }
        let clicks = args.clicks.unwrap_or(1).clamp(1, 3);
        let position = match (args.x, args.y) {
            (Some(x), Some(y)) => clamp_to_screen(x, y),
            _ => match get_mouse_position_native() {
                Ok(position) => position,
                Err(error) => return DesktopToolResult::fail(error, started, self.runtime.paused),
            },
        };
        let action_started_at = now_millis();
        match click_native(
            position.x,
            position.y,
            args.button,
            clicks,
            action_started_at,
            &mut self.runtime,
        ) {
            Ok(()) => {
                self.runtime.expected_mouse = Some(position.clone());
                self.runtime.last_action = Some("click".to_string());
                DesktopToolResult::ok(
                    ActionResult {
                        action: "click".to_string(),
                        final_x: Some(position.x),
                        final_y: Some(position.y),
                        detail: format!("{:?} click x{}", args.button, clicks),
                    },
                    started,
                    self.runtime.paused,
                )
            }
            Err(error) => DesktopToolResult::fail(error, started, self.runtime.paused),
        }
    }

    pub fn double_click(&mut self) -> DesktopToolResult<ActionResult> {
        self.click(ClickArgs {
            button: MouseButton::Left,
            clicks: Some(2),
            x: None,
            y: None,
        })
    }

    pub fn drag(&mut self, args: DragArgs) -> DesktopToolResult<ActionResult> {
        let started = Instant::now();
        if let Err(error) = self.require_mouse() {
            return DesktopToolResult::fail(error, started, self.runtime.paused);
        }
        let from = clamp_to_screen(args.from_x, args.from_y);
        let to = clamp_to_screen(args.to_x, args.to_y);
        let duration = args.duration_ms.unwrap_or(500).clamp(50, 15_000);
        let button = args.button.unwrap_or(MouseButton::Left);
        let action_started_at = now_millis();
        match drag_native(
            from.x,
            from.y,
            to.x,
            to.y,
            duration,
            button,
            action_started_at,
            &mut self.runtime,
        ) {
            Ok(()) => {
                self.runtime.expected_mouse = Some(to.clone());
                self.runtime.last_action = Some("drag".to_string());
                DesktopToolResult::ok(
                    ActionResult {
                        action: "drag".to_string(),
                        final_x: Some(to.x),
                        final_y: Some(to.y),
                        detail: "Drag completed".to_string(),
                    },
                    started,
                    self.runtime.paused,
                )
            }
            Err(error) => DesktopToolResult::fail(error, started, self.runtime.paused),
        }
    }

    pub fn scroll(&mut self, args: ScrollArgs) -> DesktopToolResult<ActionResult> {
        let started = Instant::now();
        if let Err(error) = self.require_mouse() {
            return DesktopToolResult::fail(error, started, self.runtime.paused);
        }
        let action_started_at = now_millis();
        match scroll_native(
            args.delta_x,
            args.delta_y,
            action_started_at,
            &mut self.runtime,
        ) {
            Ok(()) => {
                self.runtime.last_action = Some("scroll".to_string());
                DesktopToolResult::ok(
                    ActionResult {
                        action: "scroll".to_string(),
                        final_x: None,
                        final_y: None,
                        detail: format!("Scrolled {},{}", args.delta_x, args.delta_y),
                    },
                    started,
                    self.runtime.paused,
                )
            }
            Err(error) => DesktopToolResult::fail(error, started, self.runtime.paused),
        }
    }

    pub fn type_text(&mut self, args: TypeTextArgs) -> DesktopToolResult<ActionResult> {
        let started = Instant::now();
        if let Err(error) = self.require_keyboard() {
            return DesktopToolResult::fail(error, started, self.runtime.paused);
        }
        let interval = args.interval_ms.unwrap_or(0).min(1_000);
        let action_started_at = now_millis();
        match type_text_native(&args.text, interval, action_started_at, &mut self.runtime) {
            Ok(()) => {
                self.runtime.last_action = Some("type_text".to_string());
                DesktopToolResult::ok(
                    ActionResult {
                        action: "type_text".to_string(),
                        final_x: None,
                        final_y: None,
                        detail: format!("Typed {} characters", args.text.chars().count()),
                    },
                    started,
                    self.runtime.paused,
                )
            }
            Err(error) => DesktopToolResult::fail(error, started, self.runtime.paused),
        }
    }

    pub fn press_key(&mut self, key: String) -> DesktopToolResult<ActionResult> {
        self.key_combo(KeyComboArgs { keys: vec![key] })
    }

    pub fn key_combo(&mut self, args: KeyComboArgs) -> DesktopToolResult<ActionResult> {
        let started = Instant::now();
        if let Err(error) = self.require_keyboard() {
            return DesktopToolResult::fail(error, started, self.runtime.paused);
        }
        let keys = normalize_keys(&args.keys);
        if keys.is_empty() {
            return DesktopToolResult::fail(
                "key_combo requires at least one key",
                started,
                self.runtime.paused,
            );
        }
        let action_started_at = now_millis();
        match key_combo_native(&keys, action_started_at, &mut self.runtime) {
            Ok(()) => {
                self.runtime.last_action = Some("key_combo".to_string());
                DesktopToolResult::ok(
                    ActionResult {
                        action: "key_combo".to_string(),
                        final_x: None,
                        final_y: None,
                        detail: format!("Pressed {}", keys.join("+")),
                    },
                    started,
                    self.runtime.paused,
                )
            }
            Err(error) => DesktopToolResult::fail(error, started, self.runtime.paused),
        }
    }

    pub fn shortcut(&mut self, key: &str) -> DesktopToolResult<ActionResult> {
        self.key_combo(KeyComboArgs {
            keys: vec![shortcut_modifier().to_string(), key.to_string()],
        })
    }

    pub fn record_user_intervention(&mut self, source: String) -> InterventionStatus {
        self.mark_user_intervention(format!("user_intervention:{source}"));
        self.intervention_status()
    }

    pub fn require_screen(&self) -> Result<(), String> {
        if !self.permissions.screen_capture_enabled {
            return Err("Permission disabled: screen_capture_enabled".to_string());
        }
        Ok(())
    }

    pub fn require_mouse(&self) -> Result<(), String> {
        if !self.permissions.automation_enabled {
            return Err("Permission disabled: automation_enabled".to_string());
        }
        if !self.permissions.mouse_control_enabled {
            return Err("Permission disabled: mouse_control_enabled".to_string());
        }
        if self.runtime.paused {
            return Err(format!(
                "Automation paused: {}",
                self.runtime
                    .pause_reason
                    .clone()
                    .unwrap_or_else(|| "user intervention".to_string())
            ));
        }
        Ok(())
    }

    pub fn require_keyboard(&self) -> Result<(), String> {
        if !self.permissions.automation_enabled {
            return Err("Permission disabled: automation_enabled".to_string());
        }
        if !self.permissions.keyboard_control_enabled {
            return Err("Permission disabled: keyboard_control_enabled".to_string());
        }
        if self.runtime.paused {
            return Err(format!(
                "Automation paused: {}",
                self.runtime
                    .pause_reason
                    .clone()
                    .unwrap_or_else(|| "user intervention".to_string())
            ));
        }
        Ok(())
    }

    fn mark_user_intervention(&mut self, reason: String) {
        self.runtime.paused = true;
        self.runtime.pause_reason = Some(reason);
        self.runtime.last_user_intervention_at = Some(now_millis());
    }
}

fn now_millis() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

#[cfg(all(target_os = "windows", not(test)))]
fn now_millis_u64() -> u64 {
    now_millis().min(u64::MAX as u128) as u64
}

#[cfg(target_os = "windows")]
fn monitor_error_slot() -> &'static Mutex<Option<String>> {
    GLOBAL_MONITOR_ERROR.get_or_init(|| Mutex::new(None))
}

#[cfg(all(target_os = "windows", not(test)))]
fn set_native_monitor_error(error: Option<String>) {
    if let Ok(mut slot) = monitor_error_slot().lock() {
        *slot = error;
    }
}

#[cfg(all(target_os = "windows", not(test)))]
fn record_native_physical_input(source: u8) {
    GLOBAL_LAST_PHYSICAL_INPUT_SOURCE.store(source, Ordering::Release);
    GLOBAL_LAST_PHYSICAL_INPUT_AT.store(now_millis_u64(), Ordering::Release);
}

#[cfg(target_os = "windows")]
fn input_source_label(source: u8) -> Option<&'static str> {
    match source {
        INPUT_SOURCE_MOUSE => Some("mouse"),
        INPUT_SOURCE_KEYBOARD => Some("keyboard"),
        _ => None,
    }
}

fn native_input_monitor_status() -> NativeInputMonitorStatus {
    #[cfg(target_os = "windows")]
    {
        let last_at = GLOBAL_LAST_PHYSICAL_INPUT_AT.load(Ordering::Acquire);
        let source = GLOBAL_LAST_PHYSICAL_INPUT_SOURCE.load(Ordering::Acquire);
        let error = monitor_error_slot()
            .lock()
            .ok()
            .and_then(|slot| slot.clone());
        return NativeInputMonitorStatus {
            available: true,
            running: GLOBAL_MONITOR_RUNNING.load(Ordering::Acquire),
            last_physical_input_at: (last_at > 0).then_some(last_at as u128),
            last_source: input_source_label(source).map(str::to_string),
            error,
        };
    }

    #[cfg(not(target_os = "windows"))]
    {
        NativeInputMonitorStatus {
            available: false,
            running: false,
            last_physical_input_at: None,
            last_source: None,
            error: Some(
                "Native physical input monitoring is currently implemented only on Windows"
                    .to_string(),
            ),
        }
    }
}

fn latest_native_physical_input() -> Option<(u128, &'static str)> {
    #[cfg(target_os = "windows")]
    {
        let at = GLOBAL_LAST_PHYSICAL_INPUT_AT.load(Ordering::Acquire);
        if at == 0 {
            return None;
        }
        let source = GLOBAL_LAST_PHYSICAL_INPUT_SOURCE.load(Ordering::Acquire);
        return input_source_label(source).map(|label| (at as u128, label));
    }

    #[cfg(not(target_os = "windows"))]
    {
        None
    }
}

fn is_newer_physical_input(last_at: Option<u128>, action_started_at: u128) -> bool {
    last_at.is_some_and(|at| at > action_started_at)
}

fn detect_native_physical_intervention(
    runtime: &mut AutomationRuntimeState,
    action_started_at: u128,
) -> Result<(), String> {
    if let Some((last_at, source)) = latest_native_physical_input() {
        if is_newer_physical_input(Some(last_at), action_started_at) {
            let reason = format!("physical_{source}_input");
            runtime.paused = true;
            runtime.pause_reason = Some(reason);
            runtime.last_user_intervention_at = Some(last_at);
            return Err(format!(
                "Paused: user intervention detected ({source} input)"
            ));
        }
    }
    Ok(())
}

#[cfg(all(target_os = "windows", not(test)))]
fn start_native_intervention_monitor() {
    if GLOBAL_MONITOR_STARTED.swap(true, Ordering::AcqRel) {
        return;
    }

    match std::thread::Builder::new()
        .name("AgentMax-input-monitor".to_string())
        .spawn(|| {
            if let Err(error) = windows_intervention_hook_loop() {
                GLOBAL_MONITOR_RUNNING.store(false, Ordering::Release);
                set_native_monitor_error(Some(error));
            }
        }) {
        Ok(_) => set_native_monitor_error(None),
        Err(error) => {
            GLOBAL_MONITOR_RUNNING.store(false, Ordering::Release);
            set_native_monitor_error(Some(format!(
                "Could not start input monitor thread: {error}"
            )));
        }
    }
}

#[cfg(all(target_os = "windows", not(test)))]
fn windows_intervention_hook_loop() -> Result<(), String> {
    use windows::Win32::Foundation::{LPARAM, LRESULT, WPARAM};
    use windows::Win32::UI::WindowsAndMessaging::{
        CallNextHookEx, GetMessageW, SetWindowsHookExW, UnhookWindowsHookEx, KBDLLHOOKSTRUCT,
        LLKHF_INJECTED, LLMHF_INJECTED, MSG, MSLLHOOKSTRUCT, WH_KEYBOARD_LL, WH_MOUSE_LL,
    };

    unsafe extern "system" fn mouse_proc(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
        if code >= 0 && lparam.0 != 0 {
            let event = &*(lparam.0 as *const MSLLHOOKSTRUCT);
            if event.flags & LLMHF_INJECTED == 0 {
                record_native_physical_input(INPUT_SOURCE_MOUSE);
            }
        }
        unsafe { CallNextHookEx(None, code, wparam, lparam) }
    }

    unsafe extern "system" fn keyboard_proc(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
        if code >= 0 && lparam.0 != 0 {
            let event = &*(lparam.0 as *const KBDLLHOOKSTRUCT);
            if !event.flags.contains(LLKHF_INJECTED) {
                record_native_physical_input(INPUT_SOURCE_KEYBOARD);
            }
        }
        unsafe { CallNextHookEx(None, code, wparam, lparam) }
    }

    let mouse_hook = unsafe { SetWindowsHookExW(WH_MOUSE_LL, Some(mouse_proc), None, 0) }
        .map_err(|error| format!("Could not install mouse intervention hook: {error}"))?;
    let keyboard_hook =
        match unsafe { SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard_proc), None, 0) } {
            Ok(hook) => hook,
            Err(error) => {
                let _ = unsafe { UnhookWindowsHookEx(mouse_hook) };
                return Err(format!(
                    "Could not install keyboard intervention hook: {error}"
                ));
            }
        };

    GLOBAL_MONITOR_RUNNING.store(true, Ordering::Release);
    set_native_monitor_error(None);

    let mut message: MSG = unsafe { std::mem::zeroed() };
    loop {
        let result = unsafe { GetMessageW(&mut message, None, 0, 0) };
        if result.0 == -1 {
            GLOBAL_MONITOR_RUNNING.store(false, Ordering::Release);
            let _ = unsafe { UnhookWindowsHookEx(mouse_hook) };
            let _ = unsafe { UnhookWindowsHookEx(keyboard_hook) };
            return Err("Input monitor message loop failed".to_string());
        }
        if result.0 == 0 {
            break;
        }
    }

    GLOBAL_MONITOR_RUNNING.store(false, Ordering::Release);
    let _ = unsafe { UnhookWindowsHookEx(mouse_hook) };
    let _ = unsafe { UnhookWindowsHookEx(keyboard_hook) };
    Ok(())
}

fn write_screenshot_file(bytes: &[u8]) -> Result<PathBuf, String> {
    let mut dir = std::env::temp_dir();
    dir.push("AgentMax");
    dir.push("screenshots");
    std::fs::create_dir_all(&dir).map_err(|e| format!("Could not create screenshot dir: {e}"))?;
    let path = dir.join(format!("AgentMax-shot-{}.png", now_millis()));
    std::fs::write(&path, bytes).map_err(|e| format!("Could not write screenshot: {e}"))?;
    Ok(path)
}

fn clamp_to_screen(x: i32, y: i32) -> MousePosition {
    let info = get_screen_info_native();
    let max_x = info.width.saturating_sub(1) as i32;
    let max_y = info.height.saturating_sub(1) as i32;
    MousePosition {
        x: x.clamp(0, max_x.max(0)),
        y: y.clamp(0, max_y.max(0)),
    }
}

fn normalize_keys(keys: &[String]) -> Vec<String> {
    keys.iter()
        .flat_map(|key| key.split('+'))
        .map(|key| key.trim().to_lowercase())
        .filter(|key| !key.is_empty())
        .map(|key| match key.as_str() {
            "control" => "ctrl".to_string(),
            "command" => "cmd".to_string(),
            "option" => "alt".to_string(),
            "return" => "enter".to_string(),
            other => other.to_string(),
        })
        .collect()
}

fn shortcut_modifier() -> &'static str {
    if cfg!(target_os = "macos") {
        "cmd"
    } else {
        "ctrl"
    }
}

fn default_tool_catalog() -> Vec<ToolCatalogItem> {
    let platform = DesktopPlatform::current();
    let input_implemented = platform == DesktopPlatform::Windows
        || platform == DesktopPlatform::Linux
        || platform == DesktopPlatform::Macos;
    let screenshot_implemented = platform == DesktopPlatform::Windows
        || platform == DesktopPlatform::Macos
        || platform == DesktopPlatform::Linux;
    vec![
        ToolCatalogItem {
            id: "screen.take_screenshot".into(),
            name: "Take Screenshot".into(),
            category: "screen".into(),
            permission: "screen_capture_enabled".into(),
            implemented: screenshot_implemented,
            risk_level: "low".into(),
            manual_test: false,
        },
        ToolCatalogItem {
            id: "screen.locate_on_screen".into(),
            name: "Locate On Screen".into(),
            category: "screen".into(),
            permission: "screen_capture_enabled".into(),
            implemented: screenshot_implemented,
            risk_level: "low".into(),
            manual_test: false,
        },
        ToolCatalogItem {
            id: "screen.info".into(),
            name: "Screen Info".into(),
            category: "screen".into(),
            permission: "none".into(),
            implemented: true,
            risk_level: "low".into(),
            manual_test: false,
        },
        ToolCatalogItem {
            id: "mouse.position".into(),
            name: "Mouse Position".into(),
            category: "mouse".into(),
            permission: "mouse_control_enabled".into(),
            implemented: input_implemented,
            risk_level: "low".into(),
            manual_test: false,
        },
        ToolCatalogItem {
            id: "mouse.move".into(),
            name: "Move Mouse".into(),
            category: "mouse".into(),
            permission: "mouse_control_enabled".into(),
            implemented: input_implemented,
            risk_level: "medium".into(),
            manual_test: true,
        },
        ToolCatalogItem {
            id: "mouse.click".into(),
            name: "Click Mouse".into(),
            category: "mouse".into(),
            permission: "mouse_control_enabled".into(),
            implemented: input_implemented,
            risk_level: "high".into(),
            manual_test: true,
        },
        ToolCatalogItem {
            id: "mouse.drag".into(),
            name: "Drag Mouse".into(),
            category: "mouse".into(),
            permission: "mouse_control_enabled".into(),
            implemented: input_implemented,
            risk_level: "high".into(),
            manual_test: true,
        },
        ToolCatalogItem {
            id: "mouse.scroll".into(),
            name: "Scroll".into(),
            category: "mouse".into(),
            permission: "mouse_control_enabled".into(),
            implemented: input_implemented,
            risk_level: "medium".into(),
            manual_test: true,
        },
        ToolCatalogItem {
            id: "keyboard.type_text".into(),
            name: "Type Text".into(),
            category: "keyboard".into(),
            permission: "keyboard_control_enabled".into(),
            implemented: input_implemented,
            risk_level: "high".into(),
            manual_test: true,
        },
        ToolCatalogItem {
            id: "keyboard.key_combo".into(),
            name: "Key Combo".into(),
            category: "keyboard".into(),
            permission: "keyboard_control_enabled".into(),
            implemented: input_implemented,
            risk_level: "high".into(),
            manual_test: true,
        },
        ToolCatalogItem {
            id: "safety.user_intervention".into(),
            name: "User Intervention".into(),
            category: "safety".into(),
            permission: "automation_enabled".into(),
            implemented: true,
            risk_level: "low".into(),
            manual_test: false,
        },
    ]
}

fn is_wayland() -> bool {
    std::env::var("XDG_SESSION_TYPE")
        .map(|v| v.eq_ignore_ascii_case("wayland"))
        .unwrap_or(false)
        || std::env::var("WAYLAND_DISPLAY").is_ok()
}

fn display_server() -> Option<String> {
    if cfg!(target_os = "linux") {
        if is_wayland() {
            Some("wayland".to_string())
        } else if std::env::var("DISPLAY").is_ok() {
            Some("x11".to_string())
        } else {
            Some("unknown".to_string())
        }
    } else {
        None
    }
}

fn run_command(command: &str, args: &[&str]) -> Result<Vec<u8>, String> {
    let output = Command::new(command)
        .args(args)
        .output()
        .map_err(|e| format!("{command} not available or failed to start: {e}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            format!("{command} exited with status {}", output.status)
        } else {
            stderr
        });
    }
    Ok(output.stdout)
}

fn run_command_no_output(command: &str, args: &[&str]) -> Result<(), String> {
    run_command(command, args).map(|_| ())
}

fn png_dimensions(bytes: &[u8]) -> Option<(u32, u32)> {
    if bytes.len() < 24 || &bytes[0..8] != b"\x89PNG\r\n\x1a\n" {
        return None;
    }
    let width = u32::from_be_bytes([bytes[16], bytes[17], bytes[18], bytes[19]]);
    let height = u32::from_be_bytes([bytes[20], bytes[21], bytes[22], bytes[23]]);
    Some((width, height))
}

#[cfg(target_os = "windows")]
fn capture_png_native() -> Result<(Vec<u8>, u32, u32), String> {
    use windows::Win32::Graphics::Gdi::{
        BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, DeleteDC, DeleteObject, GetDC,
        GetDIBits, ReleaseDC, SelectObject, BITMAPINFO, BITMAPINFOHEADER, CAPTUREBLT,
        DIB_RGB_COLORS, SRCCOPY,
    };
    use windows::Win32::UI::WindowsAndMessaging::{GetSystemMetrics, SM_CXSCREEN, SM_CYSCREEN};

    unsafe {
        let sw = GetSystemMetrics(SM_CXSCREEN);
        let sh = GetSystemMetrics(SM_CYSCREEN);
        if sw <= 0 || sh <= 0 {
            return Err("Windows reported an invalid screen size".to_string());
        }
        let hdc = GetDC(None);
        if hdc.is_invalid() {
            return Err("GetDC failed while capturing screen".to_string());
        }
        let mdc = CreateCompatibleDC(Some(hdc));
        if mdc.is_invalid() {
            let _ = ReleaseDC(None, hdc);
            return Err("CreateCompatibleDC failed while capturing screen".to_string());
        }
        let bmp = CreateCompatibleBitmap(hdc, sw, sh);
        if bmp.is_invalid() {
            let _ = DeleteDC(mdc);
            let _ = ReleaseDC(None, hdc);
            return Err("CreateCompatibleBitmap failed while capturing screen".to_string());
        }
        let old = SelectObject(mdc, bmp.into());
        let copied = BitBlt(mdc, 0, 0, sw, sh, Some(hdc), 0, 0, SRCCOPY | CAPTUREBLT);
        if copied.is_err() {
            let _ = SelectObject(mdc, old);
            let _ = DeleteObject(bmp.into());
            let _ = DeleteDC(mdc);
            let _ = ReleaseDC(None, hdc);
            return Err("BitBlt failed while capturing screen".to_string());
        }

        let mut bmi: BITMAPINFO = std::mem::zeroed();
        bmi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        bmi.bmiHeader.biWidth = sw;
        bmi.bmiHeader.biHeight = -sh;
        bmi.bmiHeader.biPlanes = 1;
        bmi.bmiHeader.biBitCount = 32;

        let mut pixels = vec![0u8; (sw as usize) * (sh as usize) * 4];
        let scanlines = GetDIBits(
            mdc,
            bmp,
            0,
            sh as u32,
            Some(pixels.as_mut_ptr() as *mut _),
            &mut bmi,
            DIB_RGB_COLORS,
        );

        let _ = SelectObject(mdc, old);
        let _ = DeleteObject(bmp.into());
        let _ = DeleteDC(mdc);
        let _ = ReleaseDC(None, hdc);

        if scanlines == 0 {
            return Err("GetDIBits returned no pixels".to_string());
        }

        for chunk in pixels.chunks_exact_mut(4) {
            chunk.swap(0, 2);
        }

        Ok((
            encode_png(&pixels, sw as u32, sh as u32)?,
            sw as u32,
            sh as u32,
        ))
    }
}

#[cfg(target_os = "macos")]
fn capture_png_native() -> Result<(Vec<u8>, u32, u32), String> {
    let path = write_temp_capture_path();
    let path_str = path.to_string_lossy().to_string();
    run_command_no_output("screencapture", &["-x", &path_str])
        .map_err(|e| format!("macOS Screen Recording permission may be missing: {e}"))?;
    let bytes =
        std::fs::read(&path).map_err(|e| format!("Could not read screencapture output: {e}"))?;
    let _ = std::fs::remove_file(path);
    let (width, height) = png_dimensions(&bytes).unwrap_or((0, 0));
    Ok((bytes, width, height))
}

#[cfg(target_os = "linux")]
fn capture_png_native() -> Result<(Vec<u8>, u32, u32), String> {
    let path = write_temp_capture_path();
    let path_str = path.to_string_lossy().to_string();
    if is_wayland() {
        run_command_no_output("grim", &[&path_str]).map_err(|e| {
            format!("Wayland screenshot requires grim/portal support and permission: {e}")
        })?;
    } else if std::env::var("DISPLAY").is_ok() {
        run_command_no_output("import", &["-window", "root", &path_str])
            .or_else(|_| run_command_no_output("gnome-screenshot", &["-f", &path_str]))
            .map_err(|e| {
                format!("X11 screenshot requires ImageMagick import or gnome-screenshot: {e}")
            })?;
    } else {
        return Err("Linux screenshot requires X11 DISPLAY or Wayland grim support".to_string());
    }
    let bytes =
        std::fs::read(&path).map_err(|e| format!("Could not read screenshot output: {e}"))?;
    let _ = std::fs::remove_file(path);
    let (width, height) = png_dimensions(&bytes).unwrap_or((0, 0));
    Ok((bytes, width, height))
}

#[cfg(not(any(target_os = "windows", target_os = "linux", target_os = "macos")))]
fn capture_png_native() -> Result<(Vec<u8>, u32, u32), String> {
    Err("Screenshot is not implemented for this platform".to_string())
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
fn write_temp_capture_path() -> PathBuf {
    let mut path = std::env::temp_dir();
    path.push(format!("AgentMax-capture-{}.png", now_millis()));
    path
}

fn encode_png(rgba: &[u8], w: u32, h: u32) -> Result<Vec<u8>, String> {
    let mut out: Vec<u8> = Vec::new();
    let mut enc = png::Encoder::new(&mut out, w, h);
    enc.set_color(png::ColorType::Rgba);
    enc.set_depth(png::BitDepth::Eight);
    let mut writer = enc.write_header().map_err(|e| format!("PNG header: {e}"))?;
    writer
        .write_image_data(rgba)
        .map_err(|e| format!("PNG data: {e}"))?;
    drop(writer);
    Ok(out)
}

/// Parse tesseract TSV output and return matching `ScreenMatch` items.
/// TSV columns (0-based): level, page_num, block_num, par_num, line_num, word_num,
/// left, top, width, height, conf, text
fn parse_tesseract_tsv(tsv: &str, query: &str, min_confidence: f32) -> Vec<ScreenMatch> {
    let query_lower = query.to_lowercase();
    let mut matches = Vec::new();
    for line in tsv.lines().skip(1) {
        let fields: Vec<&str> = line.split('\t').collect();
        if fields.len() < 12 {
            continue;
        }
        let conf: f32 = match fields[10].trim().parse::<f32>() {
            Ok(c) => c,
            Err(_) => continue,
        };
        // Negative confidence means non-word row (e.g., block/line level) — skip
        if conf < 0.0 {
            continue;
        }
        let text = fields[11].trim();
        if text.is_empty() {
            continue;
        }
        if !text.to_lowercase().contains(&query_lower) {
            continue;
        }
        let confidence = (conf / 100.0).clamp(0.0, 1.0);
        if confidence < min_confidence {
            continue;
        }
        let left: i32 = fields[6].trim().parse().unwrap_or(0);
        let top: i32 = fields[7].trim().parse().unwrap_or(0);
        let width: u32 = fields[8].trim().parse().unwrap_or(0);
        let height: u32 = fields[9].trim().parse().unwrap_or(0);
        matches.push(ScreenMatch {
            text: Some(text.to_string()),
            bounds: BoundingBox { x: left, y: top, width, height },
            confidence,
            source: "tesseract-ocr".to_string(),
        });
    }
    matches
}

#[cfg(target_os = "windows")]
fn get_screen_info_native() -> ScreenInfo {
    use windows::Win32::UI::WindowsAndMessaging::{
        GetSystemMetrics, SM_CMONITORS, SM_CXSCREEN, SM_CXVIRTUALSCREEN, SM_CYSCREEN,
        SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
    };
    unsafe {
        let width = GetSystemMetrics(SM_CXSCREEN).max(0) as u32;
        let height = GetSystemMetrics(SM_CYSCREEN).max(0) as u32;
        let virtual_x = GetSystemMetrics(SM_XVIRTUALSCREEN);
        let virtual_y = GetSystemMetrics(SM_YVIRTUALSCREEN);
        let virtual_w = GetSystemMetrics(SM_CXVIRTUALSCREEN).max(0) as u32;
        let virtual_h = GetSystemMetrics(SM_CYVIRTUALSCREEN).max(0) as u32;
        let monitor_count = GetSystemMetrics(SM_CMONITORS).max(1);
        ScreenInfo {
            width,
            height,
            monitors: vec![
                MonitorInfo {
                    id: "primary".to_string(),
                    x: 0,
                    y: 0,
                    width,
                    height,
                    primary: true,
                },
                MonitorInfo {
                    id: format!("virtual-{monitor_count}"),
                    x: virtual_x,
                    y: virtual_y,
                    width: virtual_w,
                    height: virtual_h,
                    primary: false,
                },
            ],
            display_server: None,
            automation_available: true,
            warnings: windows_warnings(),
        }
    }
}

#[cfg(target_os = "linux")]
fn get_screen_info_native() -> ScreenInfo {
    let server = display_server();
    let mut warnings = Vec::new();
    let automation_available = !is_wayland() && std::env::var("DISPLAY").is_ok();
    if is_wayland() {
        warnings.push("Wayland detected: global mouse/keyboard automation is blocked unless a portal/compositor integration is added.".to_string());
    }
    if !automation_available {
        warnings.push("Linux automation currently requires X11 and xdotool.".to_string());
    }
    let (width, height) = linux_xrandr_size().unwrap_or((0, 0));
    ScreenInfo {
        width,
        height,
        monitors: vec![MonitorInfo {
            id: "primary".to_string(),
            x: 0,
            y: 0,
            width,
            height,
            primary: true,
        }],
        display_server: server,
        automation_available,
        warnings,
    }
}

#[cfg(target_os = "macos")]
fn macos_screen_size() -> Option<(u32, u32)> {
    let out = run_command("osascript", &[
        "-e",
        "tell application \"Finder\" to get bounds of window of desktop",
    ]).ok()?;
    let text = String::from_utf8_lossy(&out);
    // Output: "0, 0, 2560, 1600"
    let parts: Vec<u32> = text.trim()
        .split(", ")
        .filter_map(|s| s.trim().parse().ok())
        .collect();
    (parts.len() == 4).then(|| (parts[2], parts[3]))
}

#[cfg(target_os = "macos")]
fn get_screen_info_native() -> ScreenInfo {
    let (width, height) = macos_screen_size().unwrap_or((0, 0));
    let monitors = if width > 0 {
        vec![MonitorInfo { id: "primary".to_string(), x: 0, y: 0, width, height, primary: true }]
    } else {
        vec![]
    };
    ScreenInfo {
        width,
        height,
        monitors,
        display_server: None,
        automation_available: true,
        warnings: vec![
            "macOS automation uses cliclick — install with: brew install cliclick".to_string(),
            "Accessibility permission required for mouse/keyboard control.".to_string(),
            "Screen Recording permission required for screenshots.".to_string(),
        ],
    }
}

#[cfg(not(any(target_os = "windows", target_os = "linux", target_os = "macos")))]
fn get_screen_info_native() -> ScreenInfo {
    ScreenInfo {
        width: 0,
        height: 0,
        monitors: vec![],
        display_server: None,
        automation_available: false,
        warnings: vec!["Unsupported platform".to_string()],
    }
}

#[cfg(target_os = "windows")]
fn windows_warnings() -> Vec<String> {
    vec![
        "Windows UAC secure desktop and elevated apps may reject input from a non-elevated AgentMax process.".to_string(),
        "Protected content may be blacked out in screenshots.".to_string(),
    ]
}

#[cfg(target_os = "linux")]
fn linux_xrandr_size() -> Option<(u32, u32)> {
    let output = Command::new("xrandr").arg("--current").output().ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    for line in text.lines() {
        if let Some(rest) = line.split("current ").nth(1) {
            let size = rest.split(',').next()?.trim();
            let mut parts = size.split(" x ");
            let w = parts.next()?.trim().parse().ok()?;
            let h = parts.next()?.trim().parse().ok()?;
            return Some((w, h));
        }
    }
    None
}

#[cfg(target_os = "windows")]
fn get_mouse_position_native() -> Result<MousePosition, String> {
    use windows::Win32::Foundation::POINT;
    use windows::Win32::UI::WindowsAndMessaging::GetCursorPos;
    unsafe {
        let mut point = POINT { x: 0, y: 0 };
        GetCursorPos(&mut point).map_err(|e| format!("GetCursorPos failed: {e}"))?;
        Ok(MousePosition {
            x: point.x,
            y: point.y,
        })
    }
}

#[cfg(target_os = "linux")]
fn get_mouse_position_native() -> Result<MousePosition, String> {
    if is_wayland() {
        return Err("Mouse position is unavailable on Wayland without portal support".to_string());
    }
    let out = run_command("xdotool", &["getmouselocation", "--shell"])?;
    let text = String::from_utf8_lossy(&out);
    let mut x = None;
    let mut y = None;
    for line in text.lines() {
        if let Some(value) = line.strip_prefix("X=") {
            x = value.parse::<i32>().ok();
        }
        if let Some(value) = line.strip_prefix("Y=") {
            y = value.parse::<i32>().ok();
        }
    }
    match (x, y) {
        (Some(x), Some(y)) => Ok(MousePosition { x, y }),
        _ => Err("xdotool did not return X/Y cursor position".to_string()),
    }
}

#[cfg(target_os = "macos")]
fn get_mouse_position_native() -> Result<MousePosition, String> {
    let out = run_command("cliclick", &["p"])
        .map_err(|e| format!("Mouse position requires cliclick (brew install cliclick): {e}"))?;
    let text = String::from_utf8_lossy(&out);
    let text = text.trim();
    let (xs, ys) = text.split_once(',')
        .ok_or_else(|| format!("cliclick p returned unexpected output: {text}"))?;
    let x = xs.trim().parse::<i32>().map_err(|_| format!("cliclick p bad x: {xs}"))?;
    let y = ys.trim().parse::<i32>().map_err(|_| format!("cliclick p bad y: {ys}"))?;
    Ok(MousePosition { x, y })
}

#[cfg(not(any(target_os = "windows", target_os = "linux", target_os = "macos")))]
fn get_mouse_position_native() -> Result<MousePosition, String> {
    Err("Mouse position is not implemented for this platform".to_string())
}

#[cfg(target_os = "windows")]
fn move_mouse_native(
    x: i32,
    y: i32,
    duration_ms: u64,
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    let start = get_mouse_position_native().unwrap_or(MousePosition { x, y });
    if duration_ms == 0 {
        detect_native_physical_intervention(runtime, action_started_at)?;
        send_mouse_move_windows(x, y)?;
        detect_native_physical_intervention(runtime, action_started_at)?;
        return Ok(());
    }
    let steps = (duration_ms / 16).clamp(2, 240);
    let sleep_ms = (duration_ms / steps).max(1);
    let mut expected = start;
    for step in 1..=steps {
        detect_native_physical_intervention(runtime, action_started_at)?;
        detect_manual_mouse_override(runtime, &expected)?;
        let t = step as f64 / steps as f64;
        let nx = start.x as f64 + (x - start.x) as f64 * t;
        let ny = start.y as f64 + (y - start.y) as f64 * t;
        expected = MousePosition {
            x: nx.round() as i32,
            y: ny.round() as i32,
        };
        send_mouse_move_windows(expected.x, expected.y)?;
        runtime.expected_mouse = Some(expected.clone());
        std::thread::sleep(Duration::from_millis(sleep_ms));
    }
    detect_native_physical_intervention(runtime, action_started_at)?;
    Ok(())
}

#[cfg(target_os = "linux")]
fn move_mouse_native(
    x: i32,
    y: i32,
    duration_ms: u64,
    action_started_at: u128,
    _runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    let _ = action_started_at;
    if is_wayland() {
        return run_command_no_output("ydotool", &[
            "mousemove", "--absolute", "-x", &x.to_string(), "-y", &y.to_string(),
        ]).map_err(|e| format!("Wayland mouse requires ydotool with ydotoold running: {e}"));
    }
    let x_s = x.to_string();
    let y_s = y.to_string();
    if duration_ms > 0 {
        run_command_no_output(
            "xdotool",
            &[
                "mousemove",
                "--sync",
                "--duration",
                &duration_ms.to_string(),
                &x_s,
                &y_s,
            ],
        )
    } else {
        run_command_no_output("xdotool", &["mousemove", "--sync", &x_s, &y_s])
    }
}

#[cfg(target_os = "macos")]
fn move_mouse_native(
    x: i32,
    y: i32,
    _duration_ms: u64,
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    detect_native_physical_intervention(runtime, action_started_at)?;
    run_command_no_output("cliclick", &[&format!("m:{},{}", x, y)])
        .map_err(|e| format!("Mouse movement requires cliclick (brew install cliclick): {e}"))?;
    detect_native_physical_intervention(runtime, action_started_at)
}

#[cfg(not(any(target_os = "windows", target_os = "linux", target_os = "macos")))]
fn move_mouse_native(
    _x: i32,
    _y: i32,
    _duration_ms: u64,
    _action_started_at: u128,
    _runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    Err("Mouse movement is not implemented for this platform".to_string())
}

#[cfg(target_os = "windows")]
fn send_mouse_move_windows(x: i32, y: i32) -> Result<(), String> {
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_0, INPUT_MOUSE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_MOVE, MOUSEINPUT,
    };
    use windows::Win32::UI::WindowsAndMessaging::{GetSystemMetrics, SM_CXSCREEN, SM_CYSCREEN};
    unsafe {
        let sw = GetSystemMetrics(SM_CXSCREEN).max(1);
        let sh = GetSystemMetrics(SM_CYSCREEN).max(1);
        let nx = (x.clamp(0, sw - 1) * 65535) / (sw - 1).max(1);
        let ny = (y.clamp(0, sh - 1) * 65535) / (sh - 1).max(1);
        let input = INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: nx,
                    dy: ny,
                    mouseData: 0,
                    dwFlags: MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        };
        let sent = SendInput(&[input], std::mem::size_of::<INPUT>() as i32);
        if sent == 1 {
            Ok(())
        } else {
            Err("SendInput mouse move sent 0 events".to_string())
        }
    }
}

#[cfg(target_os = "windows")]
fn detect_manual_mouse_override(
    runtime: &mut AutomationRuntimeState,
    expected: &MousePosition,
) -> Result<(), String> {
    let actual = get_mouse_position_native()?;
    let dx = (actual.x - expected.x).abs();
    let dy = (actual.y - expected.y).abs();
    if dx > 18 || dy > 18 {
        runtime.paused = true;
        runtime.pause_reason = Some("mouse_moved_by_user".to_string());
        runtime.last_user_intervention_at = Some(now_millis());
        return Err("Paused: user intervention detected (mouse moved manually)".to_string());
    }
    Ok(())
}

#[cfg(target_os = "windows")]
fn click_native(
    x: i32,
    y: i32,
    button: MouseButton,
    clicks: u8,
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    detect_native_physical_intervention(runtime, action_started_at)?;
    send_mouse_move_windows(x, y)?;
    detect_native_physical_intervention(runtime, action_started_at)?;
    for index in 0..clicks {
        send_mouse_button_windows(button, true)?;
        std::thread::sleep(Duration::from_millis(35));
        send_mouse_button_windows(button, false)?;
        detect_native_physical_intervention(runtime, action_started_at)?;
        if index + 1 < clicks {
            std::thread::sleep(Duration::from_millis(80));
        }
    }
    Ok(())
}

#[cfg(target_os = "linux")]
fn click_native(
    x: i32,
    y: i32,
    button: MouseButton,
    clicks: u8,
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    detect_native_physical_intervention(runtime, action_started_at)?;
    if is_wayland() {
        let code = match button {
            MouseButton::Left => "0xC0",
            MouseButton::Right => "0xC1",
            MouseButton::Middle => "0xC2",
        };
        run_command_no_output("ydotool", &["mousemove", "--absolute", "-x", &x.to_string(), "-y", &y.to_string()])
            .map_err(|e| format!("Wayland mouse requires ydotool with ydotoold running: {e}"))?;
        for _ in 0..clicks {
            run_command_no_output("ydotool", &["click", code])
                .map_err(|e| format!("Wayland click requires ydotool with ydotoold running: {e}"))?;
        }
        return detect_native_physical_intervention(runtime, action_started_at);
    }
    let x_s = x.to_string();
    let y_s = y.to_string();
    let button_s = button.xdotool_button().to_string();
    run_command_no_output("xdotool", &["mousemove", "--sync", &x_s, &y_s])?;
    for _ in 0..clicks {
        run_command_no_output("xdotool", &["click", &button_s])?;
    }
    detect_native_physical_intervention(runtime, action_started_at)?;
    Ok(())
}

#[cfg(target_os = "macos")]
fn click_native(
    x: i32,
    y: i32,
    button: MouseButton,
    clicks: u8,
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    detect_native_physical_intervention(runtime, action_started_at)?;
    let action = match button {
        MouseButton::Right => "rc",
        _ => "c",
    };
    let coord_action = format!("{}:{},{}", action, x, y);
    let args: Vec<String> = std::iter::repeat(coord_action)
        .take(clicks.clamp(1, 3) as usize)
        .collect();
    let args_ref: Vec<&str> = args.iter().map(String::as_str).collect();
    run_command_no_output("cliclick", &args_ref)
        .map_err(|e| format!("Mouse click requires cliclick (brew install cliclick): {e}"))?;
    detect_native_physical_intervention(runtime, action_started_at)
}

#[cfg(not(any(target_os = "windows", target_os = "linux", target_os = "macos")))]
fn click_native(
    _x: i32,
    _y: i32,
    _button: MouseButton,
    _clicks: u8,
    _action_started_at: u128,
    _runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    Err("Mouse click is not implemented for this platform".to_string())
}

#[cfg(target_os = "windows")]
fn send_mouse_button_windows(button: MouseButton, down: bool) -> Result<(), String> {
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_0, INPUT_MOUSE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP,
        MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP,
        MOUSEINPUT, MOUSE_EVENT_FLAGS,
    };
    let flags: MOUSE_EVENT_FLAGS = match (button, down) {
        (MouseButton::Left, true) => MOUSEEVENTF_LEFTDOWN,
        (MouseButton::Left, false) => MOUSEEVENTF_LEFTUP,
        (MouseButton::Right, true) => MOUSEEVENTF_RIGHTDOWN,
        (MouseButton::Right, false) => MOUSEEVENTF_RIGHTUP,
        (MouseButton::Middle, true) => MOUSEEVENTF_MIDDLEDOWN,
        (MouseButton::Middle, false) => MOUSEEVENTF_MIDDLEUP,
    };
    let input = INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 {
            mi: MOUSEINPUT {
                dx: 0,
                dy: 0,
                mouseData: 0,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    };
    unsafe {
        let sent = SendInput(&[input], std::mem::size_of::<INPUT>() as i32);
        if sent == 1 {
            Ok(())
        } else {
            Err("SendInput mouse button sent 0 events".to_string())
        }
    }
}

#[cfg(target_os = "windows")]
fn drag_native(
    from_x: i32,
    from_y: i32,
    to_x: i32,
    to_y: i32,
    duration_ms: u64,
    button: MouseButton,
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    detect_native_physical_intervention(runtime, action_started_at)?;
    send_mouse_move_windows(from_x, from_y)?;
    send_mouse_button_windows(button, true)?;
    let move_result = move_mouse_native(to_x, to_y, duration_ms, action_started_at, runtime);
    let release_result = send_mouse_button_windows(button, false);
    move_result?;
    release_result
}

#[cfg(target_os = "linux")]
fn drag_native(
    from_x: i32,
    from_y: i32,
    to_x: i32,
    to_y: i32,
    duration_ms: u64,
    button: MouseButton,
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    detect_native_physical_intervention(runtime, action_started_at)?;
    if is_wayland() {
        // move to start, press button, move to end, release
        run_command_no_output("ydotool", &["mousemove", "--absolute", "-x", &from_x.to_string(), "-y", &from_y.to_string()])
            .map_err(|e| format!("Wayland drag requires ydotool with ydotoold running: {e}"))?;
        run_command_no_output("ydotool", &["click", "0x40"]) // left button down
            .map_err(|e| format!("Wayland drag requires ydotool with ydotoold running: {e}"))?;
        run_command_no_output("ydotool", &["mousemove", "--absolute", "-x", &to_x.to_string(), "-y", &to_y.to_string()])
            .map_err(|e| format!("Wayland drag requires ydotool with ydotoold running: {e}"))?;
        run_command_no_output("ydotool", &["click", "0x80"]) // left button up
            .map_err(|e| format!("Wayland drag requires ydotool with ydotoold running: {e}"))?;
        return detect_native_physical_intervention(runtime, action_started_at);
    }
    let button_s = button.xdotool_button().to_string();
    run_command_no_output(
        "xdotool",
        &[
            "mousemove",
            "--sync",
            &from_x.to_string(),
            &from_y.to_string(),
            "mousedown",
            &button_s,
            "mousemove",
            "--sync",
            "--duration",
            &duration_ms.to_string(),
            &to_x.to_string(),
            &to_y.to_string(),
            "mouseup",
            &button_s,
        ],
    )?;
    detect_native_physical_intervention(runtime, action_started_at)
}

#[cfg(target_os = "macos")]
fn drag_native(
    from_x: i32,
    from_y: i32,
    to_x: i32,
    to_y: i32,
    _duration_ms: u64,
    _button: MouseButton,
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    detect_native_physical_intervention(runtime, action_started_at)?;
    run_command_no_output("cliclick", &[
        &format!("dd:{},{}", from_x, from_y),
        &format!("m:{},{}", to_x, to_y),
        &format!("du:{},{}", to_x, to_y),
    ])
    .map_err(|e| format!("Mouse drag requires cliclick (brew install cliclick): {e}"))?;
    detect_native_physical_intervention(runtime, action_started_at)
}

#[cfg(not(any(target_os = "windows", target_os = "linux", target_os = "macos")))]
fn drag_native(
    _from_x: i32,
    _from_y: i32,
    _to_x: i32,
    _to_y: i32,
    _duration_ms: u64,
    _button: MouseButton,
    _action_started_at: u128,
    _runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    Err("Mouse drag is not implemented for this platform".to_string())
}

#[cfg(target_os = "windows")]
fn scroll_native(
    delta_x: i32,
    delta_y: i32,
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    detect_native_physical_intervention(runtime, action_started_at)?;
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_0, INPUT_MOUSE, MOUSEEVENTF_HWHEEL, MOUSEEVENTF_WHEEL, MOUSEINPUT,
    };
    let mut inputs = Vec::new();
    if delta_y != 0 {
        inputs.push(INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: 0,
                    dy: 0,
                    mouseData: delta_y.saturating_mul(120) as u32,
                    dwFlags: MOUSEEVENTF_WHEEL,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        });
    }
    if delta_x != 0 {
        inputs.push(INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: 0,
                    dy: 0,
                    mouseData: delta_x.saturating_mul(120) as u32,
                    dwFlags: MOUSEEVENTF_HWHEEL,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        });
    }
    if inputs.is_empty() {
        return Ok(());
    }
    unsafe {
        let sent = SendInput(&inputs, std::mem::size_of::<INPUT>() as i32);
        if sent == inputs.len() as u32 {
            detect_native_physical_intervention(runtime, action_started_at)
        } else {
            Err(format!(
                "SendInput scroll sent {sent}/{} events",
                inputs.len()
            ))
        }
    }
}

#[cfg(target_os = "linux")]
fn scroll_native(
    delta_x: i32,
    delta_y: i32,
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    detect_native_physical_intervention(runtime, action_started_at)?;
    if is_wayland() {
        if delta_y != 0 {
            run_command_no_output("ydotool", &["scroll", "0", &delta_y.to_string()])
                .map_err(|e| format!("Wayland scroll requires ydotool with ydotoold running: {e}"))?;
        }
        if delta_x != 0 {
            run_command_no_output("ydotool", &["scroll", &delta_x.to_string(), "0"])
                .map_err(|e| format!("Wayland scroll requires ydotool with ydotoold running: {e}"))?;
        }
        return detect_native_physical_intervention(runtime, action_started_at);
    }
    let mut result = Ok(());
    let vertical_button = if delta_y < 0 { "5" } else { "4" };
    for _ in 0..delta_y.abs().min(50) {
        result = run_command_no_output("xdotool", &["click", vertical_button]);
        result.as_ref()?;
    }
    let horizontal_button = if delta_x < 0 { "7" } else { "6" };
    for _ in 0..delta_x.abs().min(50) {
        result = run_command_no_output("xdotool", &["click", horizontal_button]);
        result.as_ref()?;
    }
    result?;
    detect_native_physical_intervention(runtime, action_started_at)
}

#[cfg(target_os = "macos")]
fn scroll_native(
    delta_x: i32,
    delta_y: i32,
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    detect_native_physical_intervention(runtime, action_started_at)?;
    let mut args: Vec<String> = Vec::new();
    if delta_y != 0 {
        let n = delta_y.abs().min(50);
        args.push(format!("{}:{}", if delta_y > 0 { "su" } else { "sd" }, n));
    }
    if delta_x != 0 {
        let n = delta_x.abs().min(50);
        args.push(format!("{}:{}", if delta_x > 0 { "sr" } else { "sl" }, n));
    }
    if args.is_empty() {
        return Ok(());
    }
    let args_ref: Vec<&str> = args.iter().map(String::as_str).collect();
    run_command_no_output("cliclick", &args_ref)
        .map_err(|e| format!("Scroll requires cliclick v4+ (brew install cliclick): {e}"))?;
    detect_native_physical_intervention(runtime, action_started_at)
}

#[cfg(not(any(target_os = "windows", target_os = "linux", target_os = "macos")))]
fn scroll_native(
    _delta_x: i32,
    _delta_y: i32,
    _action_started_at: u128,
    _runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    Err("Mouse scroll is not implemented for this platform".to_string())
}

#[cfg(target_os = "windows")]
fn type_text_native(
    text: &str,
    interval_ms: u64,
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    for ch in text.encode_utf16() {
        detect_native_physical_intervention(runtime, action_started_at)?;
        send_unicode_char_windows(ch)?;
        if interval_ms > 0 {
            std::thread::sleep(Duration::from_millis(interval_ms));
        }
    }
    detect_native_physical_intervention(runtime, action_started_at)?;
    Ok(())
}

#[cfg(target_os = "linux")]
fn type_text_native(
    text: &str,
    interval_ms: u64,
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    detect_native_physical_intervention(runtime, action_started_at)?;
    if is_wayland() {
        return run_command_no_output("ydotool", &["type", text])
            .map_err(|e| format!("Wayland typing requires ydotool with ydotoold running: {e}"));
    }
    run_command_no_output(
        "xdotool",
        &[
            "type",
            "--clearmodifiers",
            "--delay",
            &interval_ms.to_string(),
            text,
        ],
    )?;
    detect_native_physical_intervention(runtime, action_started_at)
}

#[cfg(target_os = "macos")]
fn type_text_native(
    text: &str,
    _interval_ms: u64,
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    detect_native_physical_intervention(runtime, action_started_at)?;
    run_command_no_output(
        "osascript",
        &[
            "-e",
            &format!(
                "tell application \"System Events\" to keystroke {}",
                apple_script_string(text)
            ),
        ],
    )
    .map_err(|e| format!("macOS Accessibility permission may be missing: {e}"))?;
    detect_native_physical_intervention(runtime, action_started_at)
}

#[cfg(not(any(target_os = "windows", target_os = "linux", target_os = "macos")))]
fn type_text_native(
    _text: &str,
    _interval_ms: u64,
    _action_started_at: u128,
    _runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    Err("Text input is not implemented for this platform".to_string())
}

#[cfg(target_os = "windows")]
fn send_unicode_char_windows(ch: u16) -> Result<(), String> {
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE,
        VIRTUAL_KEY,
    };
    let inputs = [
        INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: VIRTUAL_KEY(0),
                    wScan: ch,
                    dwFlags: KEYEVENTF_UNICODE,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        },
        INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: VIRTUAL_KEY(0),
                    wScan: ch,
                    dwFlags: KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        },
    ];
    unsafe {
        let sent = SendInput(&inputs, std::mem::size_of::<INPUT>() as i32);
        if sent == inputs.len() as u32 {
            Ok(())
        } else {
            Err(format!(
                "SendInput unicode sent {sent}/{} events",
                inputs.len()
            ))
        }
    }
}

#[cfg(target_os = "windows")]
fn key_combo_native(
    keys: &[String],
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    let mut vk_codes = Vec::with_capacity(keys.len());
    for key in keys {
        vk_codes.push(vk_for_key(key).ok_or_else(|| format!("Unsupported key: {key}"))?);
    }
    let mut pressed = Vec::with_capacity(vk_codes.len());
    for vk in &vk_codes {
        if let Err(error) = detect_native_physical_intervention(runtime, action_started_at) {
            for pressed_vk in pressed.iter().rev() {
                let _ = send_key_windows(*pressed_vk, false);
            }
            return Err(error);
        }
        send_key_windows(*vk, true)?;
        pressed.push(*vk);
        std::thread::sleep(Duration::from_millis(18));
    }
    for vk in pressed.iter().rev() {
        send_key_windows(*vk, false)?;
        std::thread::sleep(Duration::from_millis(12));
    }
    detect_native_physical_intervention(runtime, action_started_at)
}

#[cfg(target_os = "linux")]
fn key_combo_native(
    keys: &[String],
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    detect_native_physical_intervention(runtime, action_started_at)?;
    if is_wayland() {
        let combo = keys.join("+");
        return run_command_no_output("ydotool", &["key", &combo])
            .map_err(|e| format!("Wayland key combo requires ydotool with ydotoold running: {e}"));
    }
    let combo = keys
        .iter()
        .map(|key| linux_key_name(key))
        .collect::<Vec<_>>()
        .join("+");
    run_command_no_output("xdotool", &["key", "--clearmodifiers", &combo])?;
    detect_native_physical_intervention(runtime, action_started_at)
}

#[cfg(target_os = "macos")]
fn key_combo_native(
    keys: &[String],
    action_started_at: u128,
    runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    detect_native_physical_intervention(runtime, action_started_at)?;
    let Some((key, modifiers)) = macos_key_combo(keys) else {
        return Err(format!("Unsupported macOS key combo: {}", keys.join("+")));
    };
    let script = if modifiers.is_empty() {
        format!("tell application \"System Events\" to keystroke {key}")
    } else {
        format!(
            "tell application \"System Events\" to keystroke {key} using {{{}}}",
            modifiers.join(", ")
        )
    };
    run_command_no_output("osascript", &["-e", &script])
        .map_err(|e| format!("macOS Accessibility permission may be missing: {e}"))?;
    detect_native_physical_intervention(runtime, action_started_at)
}

#[cfg(not(any(target_os = "windows", target_os = "linux", target_os = "macos")))]
fn key_combo_native(
    _keys: &[String],
    _action_started_at: u128,
    _runtime: &mut AutomationRuntimeState,
) -> Result<(), String> {
    Err("Keyboard shortcuts are not implemented for this platform".to_string())
}

#[cfg(target_os = "windows")]
fn send_key_windows(vk_code: u16, is_down: bool) -> Result<(), String> {
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYBD_EVENT_FLAGS, KEYEVENTF_KEYUP,
        VIRTUAL_KEY,
    };
    let flags = if is_down {
        KEYBD_EVENT_FLAGS(0)
    } else {
        KEYEVENTF_KEYUP
    };
    let input = INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(vk_code),
                wScan: 0,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    };
    unsafe {
        let sent = SendInput(&[input], std::mem::size_of::<INPUT>() as i32);
        if sent == 1 {
            Ok(())
        } else {
            Err(format!("SendInput key {vk_code} sent 0 events"))
        }
    }
}

#[cfg(target_os = "windows")]
pub fn vk_for_key(key: &str) -> Option<u16> {
    let key = key.trim().to_lowercase();
    if key.len() == 1 {
        let ch = key.chars().next()?;
        if ch.is_ascii_alphabetic() {
            return Some(ch.to_ascii_uppercase() as u16);
        }
        if ch.is_ascii_digit() {
            return Some(ch as u16);
        }
    }
    match key.as_str() {
        "ctrl" | "control" => Some(0x11),
        "shift" => Some(0x10),
        "alt" | "option" => Some(0x12),
        "win" | "super" | "meta" | "cmd" => Some(0x5B),
        "enter" | "return" => Some(0x0D),
        "escape" | "esc" => Some(0x1B),
        "tab" => Some(0x09),
        "space" => Some(0x20),
        "backspace" => Some(0x08),
        "delete" | "del" => Some(0x2E),
        "home" => Some(0x24),
        "end" => Some(0x23),
        "pageup" | "page_up" => Some(0x21),
        "pagedown" | "page_down" => Some(0x22),
        "left" | "arrowleft" => Some(0x25),
        "up" | "arrowup" => Some(0x26),
        "right" | "arrowright" => Some(0x27),
        "down" | "arrowdown" => Some(0x28),
        "insert" => Some(0x2D),
        "capslock" => Some(0x14),
        "f1" => Some(0x70),
        "f2" => Some(0x71),
        "f3" => Some(0x72),
        "f4" => Some(0x73),
        "f5" => Some(0x74),
        "f6" => Some(0x75),
        "f7" => Some(0x76),
        "f8" => Some(0x77),
        "f9" => Some(0x78),
        "f10" => Some(0x79),
        "f11" => Some(0x7A),
        "f12" => Some(0x7B),
        _ => None,
    }
}

#[cfg(target_os = "linux")]
fn linux_key_name(key: &str) -> String {
    match key {
        "ctrl" => "ctrl".to_string(),
        "cmd" | "win" | "super" | "meta" => "super".to_string(),
        "escape" => "Escape".to_string(),
        "enter" => "Return".to_string(),
        "space" => "space".to_string(),
        "left" => "Left".to_string(),
        "right" => "Right".to_string(),
        "up" => "Up".to_string(),
        "down" => "Down".to_string(),
        other => other.to_string(),
    }
}

#[cfg(target_os = "macos")]
fn macos_key_combo(keys: &[String]) -> Option<(String, Vec<String>)> {
    let mut key = None;
    let mut modifiers = Vec::new();
    for item in keys {
        match item.as_str() {
            "cmd" | "command" | "meta" => modifiers.push("command down".to_string()),
            "ctrl" | "control" => modifiers.push("control down".to_string()),
            "alt" | "option" => modifiers.push("option down".to_string()),
            "shift" => modifiers.push("shift down".to_string()),
            other => key = Some(apple_script_string(other)),
        }
    }
    key.map(|k| (k, modifiers))
}

#[cfg(target_os = "macos")]
fn apple_script_string(value: &str) -> String {
    format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\""))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn permission_defaults_block_real_automation() {
        let service = DesktopAutomationService::new();
        assert!(!service.permissions().automation_enabled);
        assert!(!service.permissions().mouse_control_enabled);
        assert!(!service.permissions().keyboard_control_enabled);
        assert!(!service.permissions().screen_capture_enabled);
    }

    #[test]
    fn permission_patch_is_partial() {
        let mut service = DesktopAutomationService::new();
        let state = service.set_permissions(PermissionPatch {
            automation_enabled: Some(true),
            mouse_control_enabled: Some(true),
            keyboard_control_enabled: None,
            screen_capture_enabled: None,
        });
        assert!(state.automation_enabled);
        assert!(state.mouse_control_enabled);
        assert!(!state.keyboard_control_enabled);
        assert!(!state.screen_capture_enabled);
    }

    #[test]
    fn locate_on_screen_rejects_empty_query() {
        let mut service = DesktopAutomationService::new();
        service.set_permissions(PermissionPatch {
            automation_enabled: None,
            mouse_control_enabled: None,
            keyboard_control_enabled: None,
            screen_capture_enabled: Some(true),
        });

        let empty = service.locate_on_screen(LocateOnScreenArgs {
            query: "  ".to_string(),
            min_confidence: None,
        });
        assert!(!empty.success);
        assert_eq!(
            empty.error.as_deref(),
            Some("locate_on_screen requires a non-empty query")
        );
    }

    #[test]
    fn parse_tesseract_tsv_finds_matching_words() {
        let tsv = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n\
                   5\t1\t1\t1\t1\t1\t10\t20\t80\t15\t95.5\tHello\n\
                   5\t1\t1\t1\t1\t2\t100\t20\t90\t15\t87.3\tWorld\n\
                   5\t1\t1\t1\t1\t3\t200\t20\t60\t15\t30.0\tFoo\n\
                   2\t1\t1\t0\t0\t0\t0\t0\t800\t600\t-1\t\n";
        let matches = parse_tesseract_tsv(tsv, "hello", 0.5);
        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].text.as_deref(), Some("Hello"));
        assert_eq!(matches[0].bounds.x, 10);
        assert_eq!(matches[0].bounds.y, 20);
        assert!((matches[0].confidence - 0.955).abs() < 0.001);

        // Case-insensitive match
        let matches2 = parse_tesseract_tsv(tsv, "WORLD", 0.5);
        assert_eq!(matches2.len(), 1);
        assert_eq!(matches2[0].text.as_deref(), Some("World"));

        // Low confidence filtered out
        let matches3 = parse_tesseract_tsv(tsv, "Foo", 0.5);
        assert_eq!(matches3.len(), 0);

        // Negative conf row (block-level) is skipped
        let matches4 = parse_tesseract_tsv(tsv, "", 0.0);
        assert_eq!(matches4.len(), 0); // empty text also filtered
    }

    #[test]
    fn normalize_keys_accepts_plus_delimited_combo() {
        let keys = normalize_keys(&["Control+C".to_string(), " Shift ".to_string()]);
        assert_eq!(keys, vec!["ctrl", "c", "shift"]);
    }

    #[test]
    fn shortcut_modifier_is_platform_correct() {
        if cfg!(target_os = "macos") {
            assert_eq!(shortcut_modifier(), "cmd");
        } else {
            assert_eq!(shortcut_modifier(), "ctrl");
        }
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn windows_vk_mapping_covers_common_shortcuts() {
        assert_eq!(vk_for_key("ctrl"), Some(0x11));
        assert_eq!(vk_for_key("c"), Some(0x43));
        assert_eq!(vk_for_key("enter"), Some(0x0D));
        assert_eq!(vk_for_key("f12"), Some(0x7B));
        assert_eq!(vk_for_key("not-a-key"), None);
    }

    #[test]
    fn tool_result_uses_success_false_for_failures() {
        let result: DesktopToolResult<ActionResult> =
            DesktopToolResult::fail("blocked", Instant::now(), true);
        assert!(!result.success);
        assert_eq!(result.error.as_deref(), Some("blocked"));
        assert!(result.paused);
    }

    #[test]
    fn physical_input_detection_uses_action_boundary() {
        assert!(!is_newer_physical_input(None, 100));
        assert!(!is_newer_physical_input(Some(100), 100));
        assert!(!is_newer_physical_input(Some(99), 100));
        assert!(is_newer_physical_input(Some(101), 100));
    }

    #[test]
    fn png_dimension_parser_reads_ihdr() {
        let mut bytes = vec![0u8; 24];
        bytes[0..8].copy_from_slice(b"\x89PNG\r\n\x1a\n");
        bytes[16..20].copy_from_slice(&1920u32.to_be_bytes());
        bytes[20..24].copy_from_slice(&1080u32.to_be_bytes());
        assert_eq!(png_dimensions(&bytes), Some((1920, 1080)));
    }
}
