# AgentMax Closed-Beta Hardening Audit

Date: 2026-06-12
Branch: `feature/beta-hardening-htlgg-foundation`
Status: implementation in progress

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

To be completed after implementation with the exact changed-file list, test
results, manual verification status, and remaining risks.
