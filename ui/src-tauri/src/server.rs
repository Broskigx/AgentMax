/*!
 * AgentMax HTTP server — port 7789
 * Serves the API surface that Python's RustVisionBridge expects.
 * Also provides a CORS-aware LM Studio proxy at /api/lm/chat
 * so the WebView can stream completions without CORS preflight issues.
 */

use axum::{
    body::Body,
    extract::Json,
    http::{HeaderValue, Method, Request, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
    Router,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::Deserialize;
use serde_json::json;

use crate::native;

fn extract_lm_content(val: &serde_json::Value) -> String {
    if let Some(text) = val.get("output_text").and_then(|v| v.as_str()) {
        return text.to_string();
    }

    if let Some(output) = val.get("output").and_then(|v| v.as_array()) {
        let mut normal_text = Vec::new();
        let mut reasoning_text = Vec::new();
        for item in output {
            if let Some(content) = item.get("content").and_then(|v| v.as_array()) {
                for part in content {
                    let text = part
                        .get("text")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default();
                    if text.is_empty() {
                        continue;
                    }
                    match part
                        .get("type")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default()
                    {
                        "reasoning_text" => reasoning_text.push(text),
                        _ => normal_text.push(text),
                    }
                }
            }
        }
        let text = if normal_text.is_empty() {
            reasoning_text.join("")
        } else {
            normal_text.join("")
        };
        if !text.trim().is_empty() {
            return text;
        }
    }

    if let Some(content) = val
        .pointer("/choices/0/message/content")
        .and_then(|v| v.as_str())
    {
        return content.to_string();
    }

    if let Some(parts) = val
        .pointer("/choices/0/message/content")
        .and_then(|v| v.as_array())
    {
        let text = parts
            .iter()
            .filter_map(|part| {
                part.get("text")
                    .and_then(|v| v.as_str())
                    .or_else(|| part.get("content").and_then(|v| v.as_str()))
            })
            .collect::<Vec<_>>()
            .join("");
        if !text.is_empty() {
            return text;
        }
    }

    if let Some(text) = val.pointer("/choices/0/text").and_then(|v| v.as_str()) {
        return text.to_string();
    }

    if let Some(content) = val.pointer("/message/content").and_then(|v| v.as_str()) {
        return content.to_string();
    }

    if let Some(content) = val.get("content").and_then(|v| v.as_str()) {
        return content.to_string();
    }

    if let Some(text) = val.get("text").and_then(|v| v.as_str()) {
        return text.to_string();
    }

    String::new()
}

fn select_lm_model(models: &[String], requested: Option<String>) -> String {
    if let Some(model) = requested.filter(|m| !m.trim().is_empty()) {
        return model;
    }

    let preferences = ["meta-llama", "llama", "qwen2.5-coder", "qwen", "deepseek"];

    for preferred in preferences {
        if let Some(model) = models
            .iter()
            .find(|m| m.to_lowercase().contains(preferred) && !m.to_lowercase().contains("embed"))
        {
            return model.clone();
        }
    }

    models
        .iter()
        .find(|m| !m.to_lowercase().contains("embed"))
        .cloned()
        .unwrap_or_else(|| "local-model".to_string())
}

async fn fetch_lm_models(client: &reqwest::Client) -> Vec<String> {
    match client.get("http://127.0.0.1:1234/v1/models").send().await {
        Ok(resp) if resp.status().is_success() => {
            let val: serde_json::Value = resp.json().await.unwrap_or_default();
            val["data"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter_map(|m| m["id"].as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default()
        }
        _ => Vec::new(),
    }
}

fn response_input_from_messages(messages: &[serde_json::Value]) -> String {
    messages
        .iter()
        .filter_map(|msg| {
            let role = msg.get("role").and_then(|v| v.as_str()).unwrap_or("user");
            let content = msg
                .get("content")
                .and_then(|v| v.as_str())
                .unwrap_or_default();
            if content.trim().is_empty() {
                None
            } else {
                Some(format!("{role}: {content}"))
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}

// ── CORS middleware ────────────────────────────────────────────────────────────
// Adds Access-Control-Allow-Origin: * to every response and short-circuits
// OPTIONS preflight requests so the WebView never sees a blocked request.

async fn cors_layer(req: Request<Body>, next: Next) -> Response {
    if req.method() == Method::OPTIONS {
        return Response::builder()
            .status(204)
            .header("access-control-allow-origin", "*")
            .header("access-control-allow-methods", "GET, POST, OPTIONS")
            .header(
                "access-control-allow-headers",
                "content-type, authorization",
            )
            .header("access-control-max-age", "86400")
            .body(Body::empty())
            .unwrap();
    }
    let mut res = next.run(req).await;
    res.headers_mut()
        .insert("access-control-allow-origin", HeaderValue::from_static("*"));
    res
}

// ── LM Studio streaming proxy ──────────────────────────────────────────────────

#[derive(Deserialize)]
struct LmChatRequest {
    messages: Vec<serde_json::Value>,
    model: Option<String>,
    temperature: Option<f32>,
    max_tokens: Option<u32>,
}

const SYSTEM_PROMPT: &str = "\
Eres AgentMax Core, agente de escritorio inteligente para Windows.\n\
Responde en español. Sé técnico y preciso.\n\
\n\
Cuando necesites información del sistema, solicítala con [SANDBOX: <comando PowerShell>].\n\
Código siempre en bloques Markdown con lenguaje: ```python, ```powershell, etc.";

async fn lm_chat_handler(Json(req): Json<LmChatRequest>) -> impl IntoResponse {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(90))
        .build()
        .unwrap();

    let mut messages = vec![json!({ "role": "system", "content": SYSTEM_PROMPT })];
    messages.extend(req.messages);

    let models = fetch_lm_models(&client).await;
    let model = select_lm_model(&models, req.model);

    let responses_body = json!({
        "model": model,
        "instructions": SYSTEM_PROMPT,
        "input": response_input_from_messages(&messages),
        "temperature": req.temperature.unwrap_or(0.4_f32),
        "max_output_tokens": req.max_tokens.unwrap_or(2048),
        "store": false,
    });

    match client
        .post("http://127.0.0.1:1234/v1/responses")
        .json(&responses_body)
        .send()
        .await
    {
        Ok(resp) => {
            let status = resp.status();
            if let Ok(val) = resp.json::<serde_json::Value>().await {
                let content = extract_lm_content(&val);
                if status.is_success() && !content.trim().is_empty() {
                    return (
                        StatusCode::OK,
                        Json(
                            json!({ "content": content, "model": model, "endpoint": "responses" }),
                        ),
                    )
                        .into_response();
                }
            }
        }
        Err(e) => {
            log::warn!(
                "LM Studio /v1/responses failed, falling back to chat/completions: {}",
                e
            );
        }
    }

    let chat_body = json!({
        "model": model,
        "messages": messages,
        "stream": false,
        "temperature": req.temperature.unwrap_or(0.4_f32),
        "max_tokens": req.max_tokens.unwrap_or(2048),
    });

    match client
        .post("http://127.0.0.1:1234/v1/chat/completions")
        .json(&chat_body)
        .send()
        .await
    {
        Ok(resp) => {
            let status = resp.status();
            match resp.json::<serde_json::Value>().await {
                Ok(val) => {
                    let content = extract_lm_content(&val);
                    if !status.is_success() {
                        return (
                            StatusCode::BAD_GATEWAY,
                            Json(json!({
                                "error": format!("LM Studio HTTP {}", status.as_u16()),
                                "raw": val
                            })),
                        )
                            .into_response();
                    }

                    if content.trim().is_empty() {
                        return (
                            StatusCode::BAD_GATEWAY,
                            Json(json!({
                                "error": "LM Studio respondió, pero no se pudo extraer texto del mensaje.",
                                "raw": val
                            })),
                        )
                            .into_response();
                    }

                    (StatusCode::OK, Json(json!({ "content": content }))).into_response()
                }
                Err(e) => (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({ "error": e.to_string() })),
                )
                    .into_response(),
            }
        }
        Err(e) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "error": format!("LM Studio no disponible: {}", e) })),
        )
            .into_response(),
    }
}

// ── Request types ──────────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct OcrRequest {
    #[allow(dead_code)]
    image_b64: Option<String>,
}

// ── Handlers ───────────────────────────────────────────────────────────────────

async fn screenshot_base64_handler() -> impl IntoResponse {
    match tokio::task::spawn_blocking(native::gdi_capture_png).await {
        Ok(Ok(bytes)) => (
            StatusCode::OK,
            Json(json!({ "image_b64": STANDARD.encode(&bytes) })),
        )
            .into_response(),
        Ok(Err(e)) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": e })),
        )
            .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": e.to_string() })),
        )
            .into_response(),
    }
}

async fn capture_handler() -> impl IntoResponse {
    let s = native::get_screen_size();
    (
        StatusCode::OK,
        Json(json!({ "width": s.width, "height": s.height, "captured": true })),
    )
        .into_response()
}

async fn screen_handler() -> impl IntoResponse {
    let s = native::get_screen_size();
    (
        StatusCode::OK,
        Json(json!({ "width": s.width, "height": s.height })),
    )
        .into_response()
}

async fn ocr_handler(Json(_body): Json<OcrRequest>) -> impl IntoResponse {
    (
        StatusCode::OK,
        Json(json!({ "items": [], "note": "OCR handled by Python" })),
    )
        .into_response()
}

async fn accessibility_focused_handler() -> impl IntoResponse {
    match native::focused_window_title() {
        Some(title) => (
            StatusCode::OK,
            Json(json!({ "role": "window", "name": title, "focused": true })),
        )
            .into_response(),
        None => (StatusCode::OK, Json(json!({}))).into_response(),
    }
}

async fn status_handler() -> impl IntoResponse {
    (
        StatusCode::OK,
        Json(json!({
            "status": "ok",
            "version": env!("CARGO_PKG_VERSION"),
            "capabilities": ["vision", "input", "accessibility"]
        })),
    )
        .into_response()
}

async fn ai_status_handler() -> impl IntoResponse {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .build()
        .unwrap();

    match client.get("http://127.0.0.1:1234/v1/models").send().await {
        Ok(resp) if resp.status().is_success() => {
            let val: serde_json::Value = resp.json().await.unwrap_or_default();
            let models: Vec<String> = val["data"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter_map(|m| m["id"].as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            let has_loaded = !models.is_empty();
            let model = models.first().cloned().unwrap_or_else(|| "local-model".to_string());
            (
                StatusCode::OK,
                Json(json!({
                    "backend": "lmstudio",
                    "model": model,
                    "models": models,
                    "has_loaded_model": has_loaded,
                    "supports_vision": false,
                    "health": { "ok": has_loaded }
                })),
            )
                .into_response()
        }
        _ => (
            StatusCode::OK,
            Json(json!({
                "backend": "lmstudio",
                "model": "",
                "models": [],
                "has_loaded_model": false,
                "supports_vision": false,
                "health": { "ok": false, "error": "LM Studio no disponible en 127.0.0.1:1234 o sin modelo cargado" }
            })),
        )
            .into_response(),
    }
}

async fn lmstudio_models_handler() -> impl IntoResponse {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .build()
        .unwrap();

    match client.get("http://127.0.0.1:1234/v1/models").send().await {
        Ok(resp) if resp.status().is_success() => {
            let val: serde_json::Value = resp.json().await.unwrap_or_default();
            let models: Vec<String> = val["data"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter_map(|m| m["id"].as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            let has_loaded = !models.is_empty();
            (
                StatusCode::OK,
                Json(json!({ "models": models, "has_loaded_model": has_loaded })),
            )
                .into_response()
        }
        _ => (
            StatusCode::OK,
            Json(json!({ "models": [], "has_loaded_model": false })),
        )
            .into_response(),
    }
}

async fn shutdown_handler() -> impl IntoResponse {
    tokio::spawn(async {
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
        std::process::exit(0);
    });
    (StatusCode::OK, Json(json!({ "shutdown": "scheduled" }))).into_response()
}

// ── Router + server entry point ────────────────────────────────────────────────

pub fn build_router() -> Router {
    Router::new()
        // LM Studio streaming proxy (CORS-safe, used by the WebView for chat)
        .route("/api/lm/chat", post(lm_chat_handler))
        // Vision / screen APIs (called by Python RustVisionBridge)
        .route(
            "/api/vision/screenshot/base64",
            get(screenshot_base64_handler),
        )
        .route("/api/vision/capture", get(capture_handler))
        .route("/api/vision/screen", get(screen_handler))
        .route("/api/vision/ocr", post(ocr_handler))
        .route(
            "/api/vision/accessibility/focused",
            get(accessibility_focused_handler),
        )
        // Status / AI
        .route("/api/status", get(status_handler))
        .route("/api/ai/status", get(ai_status_handler))
        .route("/api/ai/lmstudio/models", get(lmstudio_models_handler))
        .route("/api/shutdown", post(shutdown_handler))
        // CORS for every route — handles OPTIONS preflights and adds Allow-Origin header
        .layer(middleware::from_fn(cors_layer))
}

pub async fn start_server() {
    let app = build_router();
    let listener = match tokio::net::TcpListener::bind("127.0.0.1:7789").await {
        Ok(l) => l,
        Err(e) => {
            log::warn!("AgentMax HTTP server failed to bind :7789 — {}", e);
            return;
        }
    };
    log::info!("AgentMax HTTP server listening on http://127.0.0.1:7789");
    if let Err(e) = axum::serve(listener, app).await {
        log::warn!("AgentMax HTTP server exited: {}", e);
    }
}
