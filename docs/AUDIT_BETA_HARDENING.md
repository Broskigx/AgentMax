# AgentMax Closed-Beta Hardening Audit

Date: 2026-06-12
Branch: `feature/beta-hardening-htlgg-foundation`
Status: implemented and verified

## Scope

This audit covers the Python runtime, Tauri desktop automation layer, React
status and consent surfaces, beta/fallback servers, local storage, diagnostics,
and repository release guardrails.

Pre-existing worktree changes under `ui/src/components`, `ui/src/lib`, and
`ui/src/store` are user-owned. They must be preserved. Generated diagnostics
from the baseline run are excluded from this change.

## Baseline

- `python -m pytest -q`: failed in
  `tests/test_tools_backend.py::test_tools_catalog_is_professional_and_valid`
  because the tool catalog did not expose the expected `app` category.
- `npm run test`: passed.
- `npm run build`: passed with existing chunk and mixed-import warnings.
- `cargo check`: passed with existing warnings.

## Findings

### Critical

1. Autonomous requests can set `computer_control_granted`, which bypasses
   normal permission checks for input, screen, process, filesystem, and
   network scopes.
2. The frontend grants screen, automation, mouse, and keyboard permissions as
   one bundle even when a task needs only a subset.
3. Failed visual verification can still return a successful action result.
4. The frontend resumes desktop automation before every action, defeating the
   native user-intervention pause.
5. Shell execution uses `shell=True` without a centralized destructive-command,
   secret, working-directory, or exfiltration policy.

### High

1. Python permission defaults silently grant screen, mouse, and keyboard.
2. Physical-action locking covers only an event emission rather than the full
   observe, validate, execute, verify, and recovery sequence.
3. Drag in the Python UI path is represented by clicks instead of a real
   press/move/release sequence.
4. `agentmax.config.json` contains comments and is invalid strict JSON, so its
   intended settings are ignored.
5. The fallback server can identify itself as the normal product runtime and
   can pass release smoke checks without clearly reporting reduced capability.
6. Dataset collection defaults to enabled in the fallback server.
7. Visual memory stores cropped images by default and has mismatched config
   field names.

### Medium

1. Feature flags exist in beta configuration but are not enforced by the real
   tool executor.
2. Input-monitor resume does not revalidate task authorization.
3. Accessibility discovery is limited but can be interpreted as a complete
   accessibility element source.
4. Vision discards OCR-provided confidence and substitutes a derived score.
5. CI does not run for `feature/**` branches and has no tracked-secret,
   model-weight, or large-file gate.
6. Runtime, beta, and development configuration precedence is not documented
   or implemented consistently.

## Implementation Phases

1. Configuration, feature flags, scoped permissions, normalized errors, and
   canonical tool catalog.
2. Physical-action serialization, risk-based verification, real drag, and
   human-intervention pause/resume.
3. HTLGG v0.1 schema, codec, validation, passive bus, and optional Redis bridge.
4. Vision candidates, privacy-first visual memory, dataset and diagnostic
   redaction.
5. Runtime-mode separation, minimal UI status/consent changes, repository
   guardrails, documentation, and full verification.

## Planned Change Areas

- `core/config.py`, `core/beta/config.py`, security and tool execution modules.
- Supervisor, UI automation, input monitoring, vision, and visual memory.
- New `core/htlgg` package and focused tests.
- Runtime server scripts, smoke checks, CI, repository safety script, and
  relevant documentation.
- Minimal React/Tauri permission, pause, fallback-status, and error handling.

## Closed-Beta Defaults

- IPC authentication: enabled.
- Telemetry and dataset collection: disabled.
- Mouse, keyboard, shell, and filesystem actions: disabled until both the
  capability and the required scoped permission are explicitly enabled.
- Screen vision capability: available, but screen capture still requires an
  explicit permission grant.
- Visual image persistence: disabled.
- Development relaxations: explicit only; never inferred merely from a debug
  build.

## Residual Risks To Reassess

- Existing tracked release archives, diagnostics, databases, and generated
  artifacts are historical repository debt. This task will add forward
  guardrails but will not rewrite Git history.
- Windows live automation requires a targetable desktop window. If that cannot
  be established, automated checks will be reported separately from manual
  verification rather than treated as equivalent.
- OCR and accessibility quality depends on optional platform dependencies.
  Missing providers must fail clearly and never fabricate element bounds.

## Final Results

### Implemented

- Closed-beta configuration is strict JSON with precedence:
  defaults, `agentmax.config.json`, `beta_config.json`, environment.
- Telemetry, dataset, shell, filesystem, mouse, and keyboard are disabled by
  default. Screen vision remains capability-gated and permission-gated.
- Permissions are scoped by task/session and TTL. The global autonomous bypass
  was removed, and UI grants are minimal and restored in `finally`.
- Tool execution now enforces feature, permission, risk, confirmation, physical
  serialization, human interruption, and postcondition verification before
  reporting success.
- Shell/filesystem policy centralizes cwd, timeout, environment, redaction,
  output limits, destructive command blocking, and protected paths.
- Real drag, screen bounds, DPI-aware coordinates, and fail-clear verification
  are implemented. `app.open`, `app.close`, and `mouse.drag` are catalogued.
- HTLGG v0.1 provides deterministic records, validation, a passive bus, Redis
  TTL/stream policy, and SQLite plus bounded-memory fallback.
- Vision uses shared `ElementCandidate` metadata. Visual memory stores metadata
  and perceptual hashes by default; images and datasets require explicit opt-in.
- Full, beta fallback, and development servers report distinct health payloads.
  Configuration/security failures abort instead of activating fallback.
- Diagnostics use an allowlist and centralized redaction. CI now covers
  `feature/**` and runs the repository safety gate.

### Verification

- `python -m pytest -q`: passed, 211 tests.
- `python scripts/check_repo_safety.py`: passed with 0 errors and 57 warnings
  for historical tracked archives, databases, and diagnostics.
- `python scripts/release_smoke_test.py`: passed all 15 checks against a forced
  full runtime (`backend=agentmax`, `runtime_mode=full`, `limited=false`).
- Explicit beta fallback release smoke: passed all 15 checks with
  `backend=agentmax_beta_fallback`, `runtime_mode=beta_fallback`,
  `limited=true`.
- `python scripts/beta_cli.py smoke-test --output-dir diagnostics/smoke`:
  passed all 11 checks; Redis was unavailable and degraded explicitly to
  SQLite.
- `npm run test`: passed.
- `npm run build`: passed with existing Vite mixed-import and large-chunk
  warnings.
- `cargo test --lib`: passed, 10 tests.
- `cargo check`: passed with existing dead-code and unnecessary-unsafe
  warnings.
- `cargo fmt --check`, `python -m compileall -q core scripts tests`, and
  `git diff --check`: passed.
- `npm run tauri build -- --debug`: passed and produced current MSI/NSIS debug
  bundles.

### Windows Manual Verification

- The desktop UI showed backend-offline/limited state explicitly rather than
  presenting the fallback as a full runtime.
- Screenshot consent requested only `pantalla`; mouse consent requested only
  `pantalla + mouse`, explicitly excluding keyboard, shell, and filesystem.
- A real screenshot completed locally, and Tool Diagnostics confirmed all
  permissions returned to off afterward.
- Resume without active input permission failed visibly.
- Immediate resume after pause failed visibly at `quiet=184ms`; resume after
  more than two seconds succeeded.
- Native drag completed successfully through the diagnostic pad in 392 ms.
- Denying mouse consent produced a visible cancellation and no mouse action.

### Exact Implementation Files

```text
.github/workflows/ci.yml
.gitignore
agentmax.config.json
beta_config.example.json
core/agents/base_agent.py
core/agents/file_system_agent.py
core/agents/lean_vision_agent.py
core/agents/security_agent.py
core/agents/supervisor.py
core/agents/ui_automation_agent.py
core/ai/tools.json
core/beta/config.py
core/beta/diagnostics.py
core/beta/logging_service.py
core/beta/rest_api.py
core/config.py
core/data_collection/redactor.py
core/feature_flags.py
core/htlgg/__init__.py
core/htlgg/bus.py
core/htlgg/codec.py
core/htlgg/redis_bridge.py
core/htlgg/schema.py
core/htlgg/validator.py
core/input/human_simulator.py
core/memory/visual_memory.py
core/runtime.py
core/rust_vision_bridge.py
core/security/ipc_auth.py
core/security/permission_manager.py
core/security/policy.py
core/tools/executor.py
core/tools/input_monitor.py
core/tools/normalizer.py
core/tools/permissions.py
core/tools/registry.py
core/tools/risk.py
core/tools/router.py
data/agentmax_training/settings.json
docs/AGENTMAX_BETA_READINESS.md
docs/AGENTPILOT_SECURITY.md
docs/AUDIT_BETA_HARDENING.md
scripts/agentmax_beta_server.py
scripts/agentmax_dev_server.py
scripts/agentmax_server.py
scripts/agentpilot_test_server.py
scripts/check_repo_safety.py
scripts/release_smoke_test.py
tests/test_agentpilot_beta_services.py
tests/test_beta_rest_ipc.py
tests/test_closed_beta_hardening.py
tests/test_ipc_auth.py
tests/test_tools_backend.py
ui/src-tauri/src/commands.rs
ui/src-tauri/src/desktop_automation.rs
ui/src/components/ChatThread/ChatThread.tsx
ui/src/components/ComputerMaxPrompt/ComputerMaxPrompt.tsx
ui/src/components/Onboarding/Onboarding.tsx
ui/src/components/ToolDiagnostics/ToolDiagnosticsPanel.tsx
ui/src/lib/apiClient.ts
ui/src/lib/computerMaxTools.ts
ui/src/lib/desktopAutomationService.ts
ui/src/store/agentStore.ts
```

### Residual Risks

- The 57 repository-safety warnings are historical tracked debt. No history was
  rewritten and no historical artifact was deleted in this task.
- Redis was not available for a live integration in this environment; its
  contract is unit-tested and the live SQLite degradation path passed.
- Linux and macOS were not run live. Unsupported physical operations return
  explicit failures, but platform integration still needs native verification.
- The Windows native input monitor was active, and manual pause/resume was
  verified. An out-of-band human interruption during an in-flight agent action
  was not reproduced deterministically.
- OCR/accessibility quality still depends on optional platform providers. The
  limited provider is labelled as limited and does not fabricate bounds.
- Existing frontend chunking warnings and Rust dead-code warnings remain
  outside this hardening scope.
