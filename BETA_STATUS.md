# AgentMax Closed Beta Status

Last QA pass: 2026-06-06 UTC

Final decision: READY_WITH_WARNINGS

## Current State

AgentMax is ready for a limited Windows closed beta candidate, not a broad beta. The local beta path works: config loads, SQLite migrates, Redis degraded fallback works, redacted logs write, feedback saves, diagnostics export, HTTP beta endpoints respond, UI typecheck/build pass, Rust check passes, browser preview renders the production bundle, and Tauri packaging produces MSI/NSIS artifacts.

The release still has warnings: Redis is not connected, Beyline is not bundled, CI is missing, native packaged Tauri visual QA was not executed, and macOS/Linux remain untested.

## Final Verification Results

| Check | Result | Evidence |
| --- | --- | --- |
| Python tests | Pass | `python -m pytest -q` passed with Pillow deprecation warnings only. |
| UI typecheck | Pass | `npm run test` passed. |
| Tauri build | Pass | Final `npm run tauri build` produced MSI and NSIS artifacts. |
| Cargo check | Pass with warnings | `cargo check` passed with dead-code/unnecessary-unsafe warnings. |
| Beta smoke | Pass | `python scripts\beta_cli.py smoke-test --output-dir diagnostics\smoke-qa-final-release` returned `ok: true`. |
| Redis | Degraded pass | Redis unavailable on `localhost:6379`; SQLite fallback passed. |
| Local endpoints | Pass | `/health`, `/api/beta/status`, `/api/beta/config`, `/api/feedback`, `/api/diagnostics/export`, `/api/tasks/recent`, `/api/beta/smoke-test` responded. |
| Browser visual QA | Pass with limitation | Production preview showed Closed Beta, STOP/Pause, Feedback, Diagnostics, SQLite/Redis/AgentPilot statuses, no old brand, and no current asset errors. |
| Native Tauri visual QA | Not done | Installer and exe built, but packaged app window was not launched. |

## Critical Fixes From QA

- Fixed production blank-screen crash by disabling obfuscator property renaming across app/vendor chunks.
- Added visible SQLite, Redis, and AgentPilot status pills to the default cockpit topbar.
- Added invalid beta config error reporting.
- Hardened redaction for secrets, tokens, cookies, JWT-like values, emails, private IPs, and user paths.
- Hardened safety supervisor against remote pipe-to-shell patterns.
- Added Redis heartbeat and tests.
- Added root `pytest.ini` to prevent generated/model artifact collection.
- Declared missing Python dependencies.
- Updated visible beta text to AgentMax Closed Beta.

## Ready For Closed Beta

- Windows local closed beta candidate.
- SQLite durable store and migrations.
- Redis-down degraded mode.
- Logs, feedback, diagnostics, smoke test.
- Visible STOP/Pause, Feedback, Diagnostics, SQLite, Redis, AgentPilot statuses.
- AgentPilot tester access UI with per-action consent still required.
- MSI and NSIS artifacts generated.

## Blocked

- Native packaged Tauri screenshot/manual QA.
- Redis connected-mode validation if Redis is required.
- Bundled licensed Beyline font asset.
- CI workflow.
- macOS/Linux validation.

## Recommended Next Fixes

1. Launch the packaged app and run passive screenshot QA on the actual Tauri window.
2. Add a licensed Beyline font asset or document fallback clearly.
3. Decide if Redis is optional. If required, ship Redis setup and require `redis.available=true`.
4. Add CI for Python tests, UI typecheck/build, cargo check, and beta smoke.
5. Clean remaining Vite and Rust warnings before a wider beta.
