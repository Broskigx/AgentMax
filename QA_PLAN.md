# AgentMax Closed Beta QA Plan

Date: 2026-06-06

## Repository Audit

### Real Stack

- Desktop shell: Tauri 2, Rust, Windows-native automation commands under `ui/src-tauri/src`.
- Frontend: React 18, TypeScript, Vite, Zustand, Framer Motion, lucide-react.
- Backend/core: Python 3.11/3.12-targeted modules under `core`, plus local test server in `scripts/agentpilot_test_server.py`.
- Beta persistence: SQLite through `core/beta/storage.py`.
- Temporary queue/state: Redis through `core/beta/redis_service.py`, with SQLite degraded fallback.
- Logs/diagnostics: `core/beta/logging_service.py`, `core/beta/diagnostics.py`, `logs/agentmax`, `diagnostics`.
- Agent/tool safety: `core/agents`, `core/tools/safety_supervisor.py`, `ui/src-tauri/src/desktop_automation.rs`, `ui/src-tauri/src/commands.rs`.
- Model/API: `core/ai`, `ui/src/lib/AgentMaxService.ts`, `scripts/agentpilot_test_server.py`, local AgentPilot endpoint default `127.0.0.1:1235`.
- UI beta shell: `ui/src/components/MainWindow`, onboarding, diagnostics panel, AgentPilot tester panel.

### Available Scripts

- Root PowerShell scripts: `scripts/setup.ps1`, `scripts/dev.ps1`, `scripts/build.ps1`.
- Beta CLI: `python scripts/beta_cli.py reset-db|export-feedback|export-diagnostics|smoke-test`.
- Local API/model test server: `python scripts/agentpilot_test_server.py`.
- UI: `npm run test`, `npm run build`, `npm run dev`, `npm run preview`, `npm run tauri build`.
- Rust/Tauri: `cargo check` in `ui/src-tauri`.
- Python: `python -m pytest`.
- CI/CD: no project-root `.github/workflows` found outside vendored `node_modules`; CI is effectively missing.

### Critical Modules

- Config: `core/beta/config.py`, `agentmax.config.json`, `beta_config.example.json`, `.env.example`.
- DB: `core/beta/storage.py`.
- Redis: `core/beta/redis_service.py`.
- Logs/diagnostics: `core/beta/logging_service.py`, `core/beta/diagnostics.py`.
- Feedback: `StorageService.save_feedback`, `/api/feedback`.
- Local API: `scripts/agentpilot_test_server.py`.
- Safety: `core/tools/safety_supervisor.py`, `core/agents/security_agent.py`, Tauri desktop automation commands.
- Emergency stop: `core/runtime.py`, `core/ipc.py`, Tauri `emergency_stop`, UI `stopAI`.
- UI: `ui/src/components/MainWindow/MainWindow.tsx`, `ui/src/components/ToolDiagnostics`, `ui/src/components/AgentPilot`.

### Immediate Beta Risks

- No CI workflow was found; release validation is local/manual.
- Redis is optional but not currently running locally; degraded fallback must remain enabled and tested.
- Live visual QA of packaged Tauri app still needs a passive screenshot/manual pass.
- Mac/Linux packaging and permission behavior cannot be executed from this Windows environment.
- Beyline is declared as a local font but no font asset exists in repo.
- Rust/Vite builds pass with warnings that reduce confidence but are not current blockers.
- Screenshot privacy redaction before sending data to a model is still a known high-risk gap.

## Test Matrix

### 1. Smoke Tests

- Run `python scripts/beta_cli.py smoke-test`.
- Verify config, SQLite, migrations, logs, feature flags, Redis fallback, feedback, diagnostics, task states.
- Verify exit code is nonzero on critical failures.
- Probe local HTTP server endpoints.

### 2. Unit Tests

- Run focused beta/tool/security tests.
- Run full pytest when feasible.
- Add regression tests for bugs found.

### 3. Integration Tests

- Start `scripts/agentpilot_test_server.py`.
- Probe `/health`, `/api/beta/status`, `/api/beta/config`, `/api/feedback`, `/api/diagnostics/export`, `/api/tasks/recent`, `/api/beta/smoke-test`, `/api/emergency_stop`.
- Verify state persisted to SQLite.

### 4. UI/E2E Tests

- Run `npm run test` and `npm run build`.
- Start local UI preview or dev server when needed.
- Verify main screen is loadable via HTTP.
- Manual/passive Tauri screenshot checklist remains required for packaged window.

### 5. SQLite Tests

- DB from scratch.
- Repeat migrations.
- WAL and foreign_keys.
- Users, conversations, messages, tasks, events, feedback, errors.
- Task state updates.
- Feedback and diagnostics export.
- Corrupt DB, locked DB, denied write permissions, old migration simulation where feasible.

### 6. Redis Tests

- Redis connected if local Redis is running.
- Redis down.
- Invalid URL.
- Timeout/degraded fallback.
- enqueue/dequeue.
- set/get task state.
- locks and release lock.
- rate limit.
- heartbeat.
- Confirm chats are not stored permanently in Redis.

### 7. Agent Loop Tests

- Mock simple/long/impossible/ambiguous tasks.
- Tool error, timeout, garbage output, empty model response, invalid JSON.
- max_steps, timeout, pause, resume, stop, failed state.
- Confirm no infinite loop and events are logged.

### 8. Safety Tests

- Dangerous commands blocked.
- Unknown commands require approval.
- Write/file/terminal actions require approval.
- Remote pipe execution blocked.
- Repeated tool failure loop blocked.

### 9. Permission Tests

- Mouse/keyboard/screen disabled by flags.
- Consent required.
- Pause on user input.
- STOP interrupts control.
- Permission errors produce clear degraded/paused state.

### 10. Model/API Tests

- Local endpoint up/down.
- Cloud endpoint unavailable/credential missing.
- Timeout, slow response, empty response, invalid response, rate limit.
- Chat must not freeze on failures.

### 11. Offline/Degraded Mode Tests

- Redis down.
- Model down.
- Network unavailable.
- Logs path unavailable.
- Config missing/corrupt.
- SQLite fallback continues where designed.

### 12. Cross-Platform Tests

- Windows: executable, MSI/NSIS, PowerShell paths, spaces in paths.
- Linux/macOS: document manual permission/packaging checklist; cannot execute from this Windows host.

### 13. Installer/Build Tests

- `npm run build`.
- `cargo check`.
- `npm run tauri build`.
- Verify MSI/NSIS artifacts exist.
- Validate setup/dev/build scripts for stale paths.

### 14. Crash/Recovery Tests

- Corrupt config.
- Redis dies mid-task.
- SQLite locked/corrupt.
- Model fails mid-chat.
- STOP pressed repeatedly.
- Server restarts after a task.

### 15. Security Tests

- Redaction for API keys, bearer tokens, JWTs, cookies, passwords, emails, private IPs, user paths, `.env` content.
- Path traversal/file sandbox tests.
- No commands without approval.
- Telemetry off means no upload path is invoked.
- Diagnostics bundle contains no secrets.

### 16. Performance Tests

- Startup/server readiness time.
- SQLite latency.
- Redis degraded latency.
- Diagnostics export time.
- Build duration.
- Package size.
- Log and DB growth after smoke loops.
- Basic memory/CPU sample where feasible.

### 17. Tester Feedback Flow

- Empty, long, category variants, ratings valid/invalid.
- Optional screenshot/log metadata.
- SQLite save.
- JSON/CSV export.
- Offline/local API behavior.

### 18. Regression Tests

- Rerun focused tests, smoke, UI typecheck/build, cargo check, Tauri packaging after fixes.
- Re-scan for secrets and legacy branding.
