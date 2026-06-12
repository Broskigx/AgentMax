# AgentMax Closed Beta Performance Report

Date: 2026-06-06 UTC

Scope: local Windows test server, production web bundle, Tauri build artifacts, SQLite/log sizes. This does not measure the packaged desktop app process after launching the native Tauri window.

## Summary

Performance is acceptable for a closed beta candidate. The local API starts in about 5 seconds, normal endpoints respond under 40 ms, diagnostics export is under 400 ms, and the smoke endpoint completes in about 5 seconds. The main UI bundle is still large but below the final configured warning threshold after the obfuscator fix.

## Measurements

| Metric | Result | Notes |
| --- | --- | --- |
| Local API startup | 4947 ms | `scripts/agentpilot_test_server.py` to `/health` ready. |
| Test server memory sample | 4.53 MB | PowerShell `WorkingSet64` for the started Python process; not a desktop app memory measurement. |
| Test server CPU sample | 0.016 s | Short local probe only. |
| `GET /health` | 3 ms | Localhost. |
| `GET /api/beta/status` | 18 ms | Includes SQLite and Redis degraded status. |
| `GET /api/beta/config` | 3 ms | Sanitized config. |
| `GET /api/tasks/recent` | 7 ms | Local SQLite. |
| `POST /api/feedback` | 35 ms | Saves redacted feedback to SQLite/log. |
| `POST /api/diagnostics/export` | 312 ms | Creates diagnostics bundle. |
| `POST /api/beta/smoke-test` | 4942 ms | Runs full smoke checks, expected to be slower. |
| UI dist size | 1.39 MB | Built `ui/dist`. |
| Main JS chunk | 788.44 kB minified, 330.02 kB gzip | `assets/index-DRjsks_y.js`. |
| MSI size | 3.93 MB | `AgentMax_0.1.1_x64_en-US.msi`. |
| NSIS size | 2.90 MB | `AgentMax_0.1.1_x64-setup.exe`. |
| SQLite beta DB size | 0.121 MB | `data/agentmax_beta.sqlite` after QA probes. |
| Logs | `app.log` 4.04 KB, `beta_feedback.log` 2.56 KB, `tools.log` 0.16 KB | Redacted JSONL logs. |

## Redis

Redis was not running on `localhost:6379`. The measured state was:

- `available=false`
- `degraded=true`
- fallback to SQLite passed smoke queue/state/rate-limit checks

This is fine only if Redis queue remains an optional feature flag during closed beta.

## Build Time Observations

- Final `npm run tauri build` passed and took several minutes on this Windows host.
- Final `npm run test` passed in about 5 seconds.
- Final `python -m pytest -q` passed in about 14 seconds.
- `cargo check` passed but emitted many dead-code/unnecessary-unsafe warnings.

## Bottlenecks And Risks

- The smoke endpoint is intentionally slower because it executes real checks. Keep it as a diagnostic/admin action, not a frequent UI poll.
- The main chunk remains heavy. Consider route/component splitting before a wider beta.
- Build warnings are not performance failures, but they can hide future chunking/runtime issues.
- No native Tauri process memory/CPU sample was captured because the packaged app window was not launched.
