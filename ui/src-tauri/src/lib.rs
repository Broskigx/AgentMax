pub mod anti_vm;
pub mod commands;
pub mod desktop_automation;
pub mod integrity;
pub mod native;
pub mod overlay;
#[cfg(not(test))]
pub mod recovery;
#[cfg(test)]
pub mod recovery {
    use serde::{Deserialize, Serialize};
    use std::io::Read;
    use tauri::AppHandle;

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

    pub struct TestSink;

    impl TestSink {
        pub fn records(&self) -> Vec<RecoveryLogRecord> {
            Vec::new()
        }

        pub fn clear_view(&self) {}

        pub fn status(&self) -> RecoveryStatus {
            RecoveryStatus {
                configured: false,
                log_path: None,
                dirty_start_detected: false,
                record_count: 0,
            }
        }

        pub fn record(
            &self,
            level: &str,
            source: &str,
            message: &str,
            details: Option<serde_json::Value>,
        ) -> RecoveryLogRecord {
            RecoveryLogRecord {
                id: "test".to_string(),
                ts: 0,
                level: level.to_string(),
                source: source.to_string(),
                message: message.to_string(),
                details,
            }
        }
    }

    static TEST_SINK: TestSink = TestSink;

    pub fn sink() -> &'static TestSink {
        &TEST_SINK
    }

    pub fn export_bundle() -> Result<String, String> {
        Ok("test-recovery.jsonl".to_string())
    }

    pub fn open_window(_app: &AppHandle) -> tauri::Result<()> {
        Ok(())
    }

    pub fn record_frontend(
        level: String,
        source: String,
        message: String,
        details: Option<serde_json::Value>,
    ) -> RecoveryLogRecord {
        sink().record(&level, &source, &message, details)
    }

    pub fn spawn_pipe_reader<R>(_reader: R, _source: &'static str, _level: &'static str)
    where
        R: Read + Send + 'static,
    {
    }
}
pub mod security;
pub mod server;
pub mod tray;
