use log::{Level, LevelFilter, Log, Metadata, Record};
use serde::{Deserialize, Serialize};
use std::{
    collections::VecDeque,
    fs::{self, OpenOptions},
    io::{BufRead, BufReader, Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, OnceLock},
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, Emitter, Manager};
#[cfg(not(test))]
use tauri::{WebviewUrl, WebviewWindowBuilder};

const MAX_RECOVERY_RECORDS: usize = 2_000;
const RECOVERY_EVENT: &str = "recovery-log";
const RECOVERY_STATUS_EVENT: &str = "recovery-status";
const RECOVERY_CRASH_EVENT: &str = "recovery-crash";

static RECOVERY_SINK: OnceLock<Arc<RecoverySink>> = OnceLock::new();

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RecoveryLogRecord {
    pub id: String,
    pub ts: u64,
    pub level: String,
    pub source: String,
    pub message: String,
    pub details: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RecoveryStatus {
    pub configured: bool,
    pub log_path: Option<String>,
    pub dirty_start_detected: bool,
    pub record_count: usize,
}

pub struct RecoverySink {
    buffer: Mutex<VecDeque<RecoveryLogRecord>>,
    app: Mutex<Option<AppHandle>>,
    log_path: Mutex<Option<PathBuf>>,
    marker_path: Mutex<Option<PathBuf>>,
    dirty_start_detected: Mutex<bool>,
    max_records: usize,
}

struct RecoveryLogBridge {
    env: env_logger::Logger,
    sink: Arc<RecoverySink>,
}

impl Log for RecoveryLogBridge {
    fn enabled(&self, metadata: &Metadata<'_>) -> bool {
        self.env.enabled(metadata)
    }

    fn log(&self, record: &Record<'_>) {
        if self.enabled(record.metadata()) {
            self.env.log(record);
        }

        let level = match record.level() {
            Level::Error => "error",
            Level::Warn => "warn",
            Level::Info => "info",
            Level::Debug => "debug",
            Level::Trace => "trace",
        };
        let source = if record.target().is_empty() {
            "tauri".to_string()
        } else {
            format!("tauri.{}", record.target())
        };
        let details = serde_json::json!({
            "file": record.file(),
            "line": record.line(),
            "module": record.module_path(),
        });
        self.sink
            .record(level, &source, &record.args().to_string(), Some(details));
    }

    fn flush(&self) {
        self.env.flush();
    }
}

impl RecoverySink {
    fn new(max_records: usize) -> Self {
        Self {
            buffer: Mutex::new(VecDeque::with_capacity(max_records.min(256))),
            app: Mutex::new(None),
            log_path: Mutex::new(None),
            marker_path: Mutex::new(None),
            dirty_start_detected: Mutex::new(false),
            max_records,
        }
    }

    pub fn record(
        &self,
        level: &str,
        source: &str,
        message: &str,
        details: Option<serde_json::Value>,
    ) -> RecoveryLogRecord {
        let record = RecoveryLogRecord {
            id: uuid::Uuid::new_v4().to_string(),
            ts: now_millis(),
            level: level.to_string(),
            source: source.to_string(),
            message: redact(message),
            details,
        };

        if let Ok(mut guard) = self.buffer.lock() {
            guard.push_back(record.clone());
            while guard.len() > self.max_records {
                guard.pop_front();
            }
        }

        if let Some(path) = self.log_path.lock().ok().and_then(|p| p.clone()) {
            if let Some(parent) = path.parent() {
                let _ = fs::create_dir_all(parent);
            }
            if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&path) {
                if let Ok(line) = serde_json::to_string(&record) {
                    let _ = writeln!(file, "{line}");
                }
            }
        }

        if let Some(app) = self.app.lock().ok().and_then(|a| a.clone()) {
            let _ = app.emit(RECOVERY_EVENT, &record);
        }

        record
    }

    pub fn records(&self) -> Vec<RecoveryLogRecord> {
        self.buffer
            .lock()
            .map(|guard| guard.iter().cloned().collect())
            .unwrap_or_default()
    }

    pub fn clear_view(&self) {
        if let Ok(mut guard) = self.buffer.lock() {
            guard.clear();
        }
        self.record("info", "recovery", "Recovery Test view cleared", None);
    }

    pub fn status(&self) -> RecoveryStatus {
        let records = self.buffer.lock().map(|guard| guard.len()).unwrap_or(0);
        RecoveryStatus {
            configured: self.log_path.lock().map(|p| p.is_some()).unwrap_or(false),
            log_path: self
                .log_path
                .lock()
                .ok()
                .and_then(|p| p.clone())
                .map(|p| p.display().to_string()),
            dirty_start_detected: self
                .dirty_start_detected
                .lock()
                .map(|v| *v)
                .unwrap_or(false),
            record_count: records,
        }
    }

    fn configure(&self, app: AppHandle) {
        if let Ok(mut guard) = self.app.lock() {
            *guard = Some(app.clone());
        }

        let data_dir = dirs::data_local_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("AgentMax");
        let log_dir = data_dir.join("logs");
        let diagnostics_dir = data_dir.join("diagnostics");
        let _ = fs::create_dir_all(&log_dir);
        let _ = fs::create_dir_all(&diagnostics_dir);

        let log_path = log_dir.join("recovery-test.jsonl");
        let marker_path = data_dir.join("recovery-startup.json");
        if let Ok(mut guard) = self.log_path.lock() {
            *guard = Some(log_path.clone());
        }
        if let Ok(mut guard) = self.marker_path.lock() {
            *guard = Some(marker_path.clone());
        }

        if marker_path.exists() {
            if let Ok(mut dirty) = self.dirty_start_detected.lock() {
                *dirty = true;
            }
            let previous = fs::read_to_string(&marker_path).unwrap_or_default();
            let record = self.record(
                "error",
                "recovery.crash",
                "Previous AgentMax session did not shut down cleanly",
                Some(serde_json::json!({ "previousMarker": previous })),
            );
            let _ = app.emit(RECOVERY_CRASH_EVENT, &record);
        }

        let marker = serde_json::json!({
            "pid": std::process::id(),
            "ts": now_millis(),
            "note": "Removed on clean AgentMax shutdown",
        });
        let _ = fs::write(
            &marker_path,
            serde_json::to_string_pretty(&marker).unwrap_or_else(|_| "{}".to_string()),
        );

        self.record(
            "info",
            "recovery",
            "Recovery Test logging configured",
            Some(serde_json::json!({ "logPath": log_path.display().to_string() })),
        );
        let _ = app.emit(RECOVERY_STATUS_EVENT, self.status());
    }

    fn mark_clean(&self) {
        if let Some(path) = self.marker_path.lock().ok().and_then(|p| p.clone()) {
            let _ = fs::remove_file(path);
        }
        self.record("info", "recovery", "AgentMax shutdown marker cleared", None);
    }
}

pub fn init_logging() -> Arc<RecoverySink> {
    let sink = RECOVERY_SINK
        .get_or_init(|| Arc::new(RecoverySink::new(MAX_RECOVERY_RECORDS)))
        .clone();

    let logger =
        env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).build();

    if log::set_boxed_logger(Box::new(RecoveryLogBridge {
        env: logger,
        sink: sink.clone(),
    }))
    .is_ok()
    {
        log::set_max_level(LevelFilter::Trace);
    }

    install_panic_hook(sink.clone());
    sink
}

pub fn sink() -> Arc<RecoverySink> {
    RECOVERY_SINK
        .get_or_init(|| Arc::new(RecoverySink::new(MAX_RECOVERY_RECORDS)))
        .clone()
}

pub fn configure(app: AppHandle) {
    sink().configure(app.clone());
    import_python_logs_once();
    start_python_log_tailer();
}

pub fn mark_clean_shutdown() {
    sink().mark_clean();
}

/// Open Recovery Test automatically only after a dirty shutdown or explicit debug flag.
pub fn should_open_on_start() -> bool {
    if std::env::var("AGENTMAX_RECOVERY_AUTO_OPEN").ok().as_deref() == Some("1") {
        return true;
    }
    sink().status().dirty_start_detected
}

pub fn record_frontend(
    level: String,
    source: String,
    message: String,
    details: Option<serde_json::Value>,
) -> RecoveryLogRecord {
    let source = if source.trim().is_empty() {
        "frontend".to_string()
    } else {
        format!("frontend.{}", source.trim())
    };
    sink().record(&level, &source, &message, details)
}

pub fn record_process_line(source: &str, level: &str, line: &str) {
    sink().record(level, source, line, None);
}

pub fn spawn_pipe_reader<R>(reader: R, source: &'static str, level: &'static str)
where
    R: Read + Send + 'static,
{
    thread::spawn(move || {
        let buffered = BufReader::new(reader);
        for line in buffered.lines() {
            match line {
                Ok(text) if !text.trim().is_empty() => record_process_line(source, level, &text),
                Ok(_) => {}
                Err(error) => {
                    record_process_line(
                        "recovery.pipe",
                        "warn",
                        &format!("{source} pipe read failed: {error}"),
                    );
                    break;
                }
            }
        }
    });
}

#[cfg(not(test))]
pub fn open_window(app: &AppHandle) -> tauri::Result<()> {
    if let Some(window) = app.get_webview_window("recovery-test") {
        window.show().ok();
        window.set_focus().ok();
        return Ok(());
    }

    match WebviewWindowBuilder::new(
        app,
        "recovery-test",
        WebviewUrl::App("index.html#recovery-test".into()),
    )
    .title("AgentMax — Recovery & Diagnostics")
    .inner_size(960.0, 640.0)
    .min_inner_size(640.0, 420.0)
    .decorations(true)
    .resizable(true)
    .visible(true)
    .build()
    {
        Ok(_) => {
            sink().record(
                "info",
                "recovery.window",
                "Recovery Test window opened",
                None,
            );
            Ok(())
        }
        Err(error) => {
            sink().record(
                "warn",
                "recovery.window",
                &format!("Recovery Test window failed to create: {error}"),
                None,
            );
            Err(error)
        }
    }
}

#[cfg(test)]
pub fn open_window(_app: &AppHandle) -> tauri::Result<()> {
    sink().record(
        "info",
        "recovery.window",
        "Recovery Test window open skipped in test build",
        None,
    );
    Ok(())
}

pub fn export_bundle() -> Result<String, String> {
    let data_dir = dirs::data_local_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("AgentMax");
    let diagnostics_dir = data_dir.join("diagnostics");
    fs::create_dir_all(&diagnostics_dir).map_err(|e| e.to_string())?;
    let export_path = diagnostics_dir.join(format!("recovery-test-{}.jsonl", now_millis()));
    let records = sink().records();
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&export_path)
        .map_err(|e| e.to_string())?;
    for record in &records {
        let line = serde_json::to_string(record).map_err(|e| e.to_string())?;
        writeln!(file, "{line}").map_err(|e| e.to_string())?;
    }
    sink().record(
        "info",
        "recovery.export",
        "Recovery Test bundle exported",
        Some(serde_json::json!({
            "path": export_path.display().to_string(),
            "records": records.len(),
        })),
    );
    Ok(export_path.display().to_string())
}

fn install_panic_hook(sink: Arc<RecoverySink>) {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |panic_info| {
        let location = panic_info.location().map(|location| {
            format!(
                "{}:{}:{}",
                location.file(),
                location.line(),
                location.column()
            )
        });
        let payload = panic_info
            .payload()
            .downcast_ref::<&str>()
            .map(|s| s.to_string())
            .or_else(|| panic_info.payload().downcast_ref::<String>().cloned())
            .unwrap_or_else(|| "Unknown panic payload".to_string());
        let backtrace = std::backtrace::Backtrace::force_capture().to_string();
        let record = sink.record(
            "error",
            "tauri.panic",
            &payload,
            Some(serde_json::json!({
                "location": location,
                "backtrace": backtrace,
            })),
        );
        if let Some(app) = sink.app.lock().ok().and_then(|a| a.clone()) {
            let _ = app.emit(RECOVERY_CRASH_EVENT, &record);
        }
        previous(panic_info);
    }));
}

fn import_python_logs_once() {
    for path in python_log_files() {
        if let Ok(file) = fs::File::open(&path) {
            let mut lines = BufReader::new(file)
                .lines()
                .filter_map(Result::ok)
                .filter(|line| !line.trim().is_empty())
                .collect::<Vec<_>>();
            if lines.len() > 40 {
                lines = lines.split_off(lines.len() - 40);
            }
            let source = python_source_name(&path);
            for line in lines {
                sink().record("info", &source, &line, None);
            }
        }
    }
}

fn start_python_log_tailer() {
    static STARTED: OnceLock<()> = OnceLock::new();
    if STARTED.set(()).is_err() {
        return;
    }

    thread::spawn(|| {
        let mut positions: std::collections::HashMap<PathBuf, u64> =
            std::collections::HashMap::new();
        loop {
            for path in python_log_files() {
                if let Ok(mut file) = fs::File::open(&path) {
                    let len = file.metadata().map(|m| m.len()).unwrap_or(0);
                    let pos = positions.entry(path.clone()).or_insert(len);
                    if len < *pos {
                        *pos = 0;
                    }
                    if len > *pos && file.seek(SeekFrom::Start(*pos)).is_ok() {
                        let mut text = String::new();
                        if file.read_to_string(&mut text).is_ok() {
                            let source = python_source_name(&path);
                            for line in text.lines().filter(|line| !line.trim().is_empty()) {
                                sink().record("info", &source, line, None);
                            }
                            *pos = len;
                        }
                    }
                }
            }
            thread::sleep(Duration::from_secs(2));
        }
    });
}

fn python_log_files() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        roots.push(cwd);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            roots.push(parent.to_path_buf());
        }
    }
    // Professional: also honor AGENTMAX_DATA_DIR (first-run / installed user layout)
    if let Ok(data) = std::env::var("AGENTMAX_DATA_DIR") {
        roots.push(PathBuf::from(data));
    }

    let mut files = Vec::new();
    for root in roots {
        // Prefer structured location under data/logs/agentmax
        for candidate_dir in [
            root.join("logs").join("agentmax"),
            root.join("data").join("logs").join("agentmax"),
            root.join("AgentMax").join("logs"), // in case data_dir points to parent
        ] {
            for name in ["app.log", "agent.log", "tools.log", "errors.log"] {
                let path = candidate_dir.join(name);
                if path.exists() {
                    files.push(path);
                }
            }
        }
    }
    files.sort();
    files.dedup();
    files
}

fn python_source_name(path: &Path) -> String {
    let stem = path
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("log");
    format!("python.{stem}")
}

fn redact(input: &str) -> String {
    let mut value = input.replace('\0', "");
    let secret_terms = [
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "SECRET_KEY",
        "ADMIN_SECRET",
        "token",
        "api_key",
        "password",
    ];
    for term in secret_terms {
        value = redact_term(&value, term);
    }
    if value.len() > 8_000 {
        value.truncate(8_000);
        value.push_str("...[truncated]");
    }
    value
}

fn redact_term(input: &str, term: &str) -> String {
    let lower = input.to_lowercase();
    let needle = term.to_lowercase();
    if !lower.contains(&needle) {
        return input.to_string();
    }

    input
        .split_whitespace()
        .map(|part| {
            if part.to_lowercase().contains(&needle) {
                if let Some((key, _)) = part.split_once('=') {
                    format!("{key}=***")
                } else if let Some((key, _)) = part.split_once(':') {
                    format!("{key}:***")
                } else {
                    "***".to_string()
                }
            } else {
                part.to_string()
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_secret_like_values() {
        let redacted = redact("OPENAI_API_KEY=sk-test token:abcd safe");
        assert!(redacted.contains("OPENAI_API_KEY=***"));
        assert!(redacted.contains("token:***"));
        assert!(!redacted.contains("sk-test"));
    }

    #[test]
    fn keeps_buffer_bounded() {
        let sink = RecoverySink::new(2);
        sink.record("info", "test", "one", None);
        sink.record("info", "test", "two", None);
        sink.record("info", "test", "three", None);
        let records = sink.records();
        assert_eq!(records.len(), 2);
        assert_eq!(records[0].message, "two");
        assert_eq!(records[1].message, "three");
    }
}
