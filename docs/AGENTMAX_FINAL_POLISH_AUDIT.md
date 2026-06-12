# AgentMax — Final Polish Audit

> **Date:** 2026-05-21  
> **Status:** Technical Beta Candidate  
> **AgentMax:** Preview (experimental)

---

## 1. Project Stack (Reality Check)

| Layer | Technology | Status |
|-------|-----------|--------|
| Frontend | React 19 + TypeScript + Vite | ✅ Ready |
| Desktop shell | Tauri v2 (Rust) | ✅ Ready |
| IPC (WebSocket) | Python `websockets` + MessagePack | ✅ Ready |
| REST API | Python `uvicorn` + FastAPI | ✅ Ready |
| Orchestration | Python asyncio (`core/runtime.py`) | ✅ Ready |
| Multi-agent | Supervisor + 9 specialist agents | ✅ Ready |
| AI routing | Claude / LM Studio / local_peft | ✅ Ready |
| Vision | Rust bridge via `RustVisionBridge` | ⚠️ Needs Tauri |
| Security | Permission manager, audit log, token manager | ✅ Ready |
| Backend (cloud) | FastAPI licensing/telemetry | ⚠️ Marked dev-only |
| AgentMax | Preview wrapper + adapter loading | ✅ Preview |
| Data collection | Local redaction + storage | ✅ Ready |
| Build | Vite + `npm run build` + `cargo check` | ⚠️ Needs testing |

## 2. What is Ready for Beta

- ✅ App boots and shows polished UI
- ✅ Dark premium theme (glassmorphism, mesh gradient, smooth animations)
- ✅ Chat interface with markdown rendering
- ✅ Onboarding 3-step flow
- ✅ Token/credit system with Free/Starter/Pro/Local plans
- ✅ Tool registry with safety supervisor (read-only vs. destructive)
- ✅ Screenshot capture + preview (requires Tauri IPC)
- ✅ Terminal panel with command execution
- ✅ Error boundary with full log display
- ✅ Keyboard shortcuts (Ctrl+K palette, Ctrl+Shift+Esc emergency stop)
- ✅ Model configuration (LM Studio, API, local_peft, mock)
- ✅ AgentMax safe wrapper with sanitizer
- ✅ Response sanitizer (strips `<think>` blocks)
- ✅ Session logging with redaction (emails, secrets, IPs, paths)
- ✅ Permission manager for sensitive operations
- ✅ Computer control prompt with approve/deny
- ✅ All Python backend handlers have graceful fallbacks

## 3. What is Incomplete

| Feature | Status | Notes |
|---------|--------|-------|
| Tauri `tauri.conf.json` | ⚠️ Needs review | Permissions, updater, capabilities |
| Tauri signing | ❌ Unsigned | Windows SmartScreen warning expected |
| AgentMax adapter files | ⚠️ Present | `models/AgentMax/V2.1/adapter/` exists |
| AgentMax vision integration | ⚠️ Partial | Screen captured, but vision analysis needs backend |
| Local PEFT runtime | ⚠️ Experimental | Requires GPU dependencies |
| Tests (Python) | ⚠️ Not executed | Need to run `pytest tests/` |
| Tests (Rust/Tauri) | ❌ Not executed | Need `cargo test` |
| Frontend build | ⚠️ Not verified | Need `npm run build` |
| End-to-end demo | ⚠️ Not verified | Demo flow documented but not recorded |

## 4. AgentMax Dependencies

AgentMax Preview is loaded **only when**:
- User explicitly selects it in settings
- The `local_peft` client and adapter files are present
- GPU/dependencies are available

If AgentMax fails:
- ✅ App does NOT crash
- ✅ Error shown inline: "AgentMax Preview is not configured yet"
- ✅ Other backends (LM Studio, API) continue working
- ✅ Sanitizer prevents `<think>` leaks
- ✅ Logs record the failure

## 5. What Can Ship in Beta

- ✅ React/Vite frontend build
- ✅ Python core with all agents
- ✅ Token/credit system (local, no payment integration)
- ✅ Tools with safety supervisor
- ✅ Screenshot (documented that Tauri IPC required)
- ✅ Terminal/CMD/PowerShell controlled
- ✅ AgentMax Preview badge
- ✅ Model unavailable → graceful fallback
- ✅ Sanitizer active
- ✅ Session logs local
- ✅ Documentation

## 6. What Must Be Hidden or Marked Experimental

- ❌ AgentMax → **Preview, experimental**
- ❌ Local PEFT → **Experimental, requires GPU**
- ❌ Anti-crack/VM detect → **Marked dev-only in backend**
- ❌ License enforcement → **Disabled in development**
- ❌ Telemetry upload → **Not active (local only)**

## 7. Risks Before Release

| Risk | Severity | Mitigation |
|------|----------|------------|
| Windows SmartScreen warning (unsigned) | Medium | Document in release notes |
| Tauri permissions too permissive | Medium | Audit `capabilities/` |
| LM Studio timeout (90s default) | Low | User gets clear error message |
| AgentMax adapter not loading | Low | Graceful fallback to other backend |
| Screenshot fails without Tauri | Low | Clear "unavailable" message shown |
| Token system drift (localStorage) | Low | Automatic reset at midnight |

## 8. Real Commands

```powershell
# Run the app (CLI mode)
python main.py

# Run the app (daemon mode)
python main.py --daemon

# Run React dev server (standalone)
cd ui
npm install
npm run dev

# Run Tauri dev shell
cd ui
npm run tauri dev

# Build frontend
cd ui
npm run build

# Check Rust
cd ui/src-tauri
cargo check

# Run Python tests
.venv\Scripts\python.exe -m pytest tests/

# Run all tests
.venv\Scripts\python.exe -m pytest tests/ backend/tests

# Lint
.venv\Scripts\python.exe -m ruff check .

# Format
.venv\Scripts\python.exe -m ruff format .
```

---

*Audit completed for AgentMax Technical Beta Candidate.*
