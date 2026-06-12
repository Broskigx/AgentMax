# AgentMax Closed-Beta Readiness

Target state: technical closed beta, not production.

## Runtime Modes

- Full runtime: `python scripts/agentmax_server.py`
- Explicit limited fallback: `python scripts/agentmax_beta_server.py`
- Explicit development server: `python scripts/agentmax_dev_server.py`

`/health` reports `backend`, `runtime_mode`, `limited`, `fallback_reason`, and
`capabilities`. The UI must display limited or offline state explicitly.

## Configuration

Precedence is:

1. Conservative closed-beta defaults.
2. `agentmax.config.json`.
3. Optional local `beta_config.json`.
4. `AGENTMAX_*` environment variables.

Configuration files are strict JSON. Invalid JSON aborts the full runtime.
Telemetry, datasets, shell, filesystem, mouse, and keyboard are off by default.
IPC authentication is on by default.

## Desktop Safety

- Screen, mouse, and keyboard permissions are separate and task-scoped.
- Sensitive or high-risk actions require confirmation and stronger
  verification.
- Physical actions are serialized from observation through recovery.
- Manual input pauses automation. Resume requires active authorization and two
  seconds without user input.
- Shell/filesystem policy protects secrets, browser profiles, credentials, and
  `.git`, and caps execution at 60 seconds.
- Screenshot persistence and dataset collection require explicit opt-in.

## Verification

Run from the repository root unless a working directory is shown:

```powershell
python -m pytest -q
python scripts/check_repo_safety.py
python scripts/release_smoke_test.py
python scripts/beta_cli.py smoke-test --output-dir diagnostics/smoke

cd ui
npm run test
npm run build

cd src-tauri
cargo test --lib
cargo check
```

The release smoke requires a running server. Force `AGENTMAX_SERVER_MODE=runtime`
when validating the full product so fallback cannot satisfy the check.

Current verified results are recorded in `docs/AUDIT_BETA_HARDENING.md`.
