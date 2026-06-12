# AgentMax Closed Beta QA Report

Date: 2026-06-06 UTC, executed locally on 2026-06-05 23:xx America/Santiago

Final decision: READY_WITH_WARNINGS

## Executive Summary

AgentMax is acceptable for a controlled Windows closed beta candidate, with explicit warnings to testers and maintainers. The critical runtime blocker found during QA was fixed: the production Vite bundle rendered a blank UI because the obfuscator renamed app properties across vendor chunk boundaries. After disabling property renaming, the UI renders, the packaged build succeeds, and browser inspection confirms no active legacy branding, visible Closed Beta label, STOP/Pause controls, Feedback, Diagnostics, SQLite/Redis/AgentPilot status pills, and no current bundle runtime error.

This is not yet a clean wider-beta release. Redis is still unavailable locally and runs in degraded SQLite fallback. The packaged Tauri window was built but not launched for a native desktop screenshot pass. Beyline is only referenced through `local("Beyline")`; no licensed font asset is bundled. CI is missing, macOS/Linux were not tested, and build warnings remain.

## Tested Build Context

- Workspace: `C:\path\to\AgentMax`
- Git: no commits yet on `main`; `git rev-parse --short HEAD` is unavailable.
- OS tested: Windows, PowerShell, local API on `127.0.0.1`
- Product-facing version: `0.1.0-beta.1`
- Installer version: `0.1.1` because MSI rejects prerelease semver.
- Generated artifacts:
  - `ui\src-tauri\target\release\bundle\msi\AgentMax_0.1.1_x64_en-US.msi`
  - `ui\src-tauri\target\release\bundle\nsis\AgentMax_0.1.1_x64-setup.exe`

## Commands Executed

| Command | Result | Notes |
| --- | --- | --- |
| `python -m pytest -q` | Pass | 181 tests passed by progress count; warnings only from Pillow `Image.Image.getdata` deprecation. |
| `npm run test` in `ui` | Pass | `tsc --noEmit` passed. |
| `npm run tauri build` in `ui` | Pass | MSI and NSIS generated. Vite/Rust warnings remain. |
| `cargo check` in `ui\src-tauri` | Pass | Dead-code and unnecessary-unsafe warnings remain. |
| `python scripts\beta_cli.py smoke-test --output-dir diagnostics\smoke-qa-final-release` | Pass | All smoke checks true; Redis degraded fallback active. |
| Temporary `scripts\agentpilot_test_server.py` endpoint probe | Pass | `/health`, beta status/config, feedback, diagnostics, tasks, smoke all responded. |
| Browser preview inspection | Pass with limitation | Production bundle rendered; current asset had zero current errors. This is not a native Tauri window pass. |
| Legacy branding scan | Pass | No old-brand matches in active source/config/docs scan. |
| Secret pattern scan | Pass with expected noise | Hits are field names, tests, and placeholders; no real secret value found in active source scan. |

## Endpoint Results

Measured with the local test server:

| Endpoint | Result | Time |
| --- | --- | --- |
| `GET /health` | Pass | 3 ms |
| `GET /api/beta/status` | Pass | 18 ms |
| `GET /api/beta/config` | Pass | 3 ms |
| `GET /api/tasks/recent` | Pass | 7 ms |
| `POST /api/feedback` | Pass | 35 ms |
| `POST /api/diagnostics/export` | Pass | 312 ms |
| `POST /api/beta/smoke-test` | Pass | 4942 ms |

## Bugs Fixed During QA

- Critical: production UI blank screen from `javascript-obfuscator` `renameProperties`.
- High: SQLite/Redis/AgentPilot status existed in API but was not visible in the default cockpit.
- High: invalid JSON config could be silently ignored.
- High: redaction missed or overmatched several secret formats.
- High: safety supervisor did not block remote pipe-to-shell command patterns.
- Medium: Redis service lacked heartbeat reporting.
- Medium: root pytest collected generated/model artifacts.
- Medium: Python dependencies used by tests were missing from declared backend requirements.
- Medium: onboarding still said Technical Beta instead of Closed Beta.
- Low/medium: docs included a secret-looking `sk-ant-...` placeholder.

Full details are in `BUGS_FOUND.md`.

## Security Risks

- Fixed: expanded redaction for API keys, OpenAI-like tokens, JWT-like values, bearer tokens, cookies, emails, private IPs, and user paths.
- Fixed: destructive and remote shell patterns are more aggressively blocked by the safety supervisor.
- Warning: no full native Tauri desktop automation run was performed after packaging; browser preview cannot prove native permission flows.
- Warning: screenshots are marked unavailable in browser mode. Native screenshot privacy and model-send behavior still need packaged app QA.
- Warning: CI is missing, so security regressions currently depend on local manual command discipline.

## UX Risks

- Fixed: closed beta label is visible.
- Fixed: STOP/Pause controls are visible in browser preview.
- Fixed: Feedback and Diagnostics buttons are visible.
- Fixed: SQLite, Redis, and AgentPilot status pills are visible at 1280x720.
- Warning: Beyline is not bundled; typography depends on tester machines having the font installed.
- Warning: browser preview shows "Redis Degradado" because local Redis is down. This is accurate, but testers need to understand it.

## Performance Risks

- Local API startup was about 4.95 s in this environment.
- Diagnostics export was fast at 312 ms.
- Beta smoke endpoint took about 4.94 s, expected because it runs real checks.
- Main JS chunk is 788.44 kB minified, 330.02 kB gzip. It is below the configured 800 kB warning threshold after the final change, but still large.
- Tauri build takes several minutes locally.

## Untested Or Partially Tested

- Native packaged Tauri window launch and screenshot inspection.
- Installer install/uninstall flow.
- macOS and Linux packaging/runtime.
- Redis connected mode with a real Redis server.
- Real model backend and long-running AgentPilot tasks.
- CI pipeline because none exists.
- Deterministic Beyline typography with a bundled font asset.

## Recommendation

Proceed only as a limited Windows closed beta candidate with a tester note that Redis is optional/degraded, typography may fallback unless Beyline is installed, and native packaged UI still needs a human screenshot pass before broader distribution.

## Ready For Closed Beta

- Config, SQLite, logs, feedback, diagnostics, feature flags, Redis fallback, smoke test, local API endpoints, UI typecheck, Rust check, production build, and Tauri packaging passed.
- Critical blank-screen runtime bug fixed and verified through browser preview.
- STOP/Pause, Feedback, Diagnostics, Closed Beta labeling, and DB/Redis/AgentPilot statuses are visible in browser preview.

## Blocked

- No native packaged Tauri visual QA pass yet.
- No real Redis connected-mode validation.
- No bundled Beyline font asset.
- No CI workflow.
- No macOS/Linux validation.
