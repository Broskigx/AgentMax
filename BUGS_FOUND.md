# AgentMax Bugs Found During Closed Beta QA

## Fixed

| ID | Severity | Module | Steps | Expected | Actual | Fix | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QA-001 | Critical | `ui/vite.config.ts` | Run `npm run build`, serve `ui/dist`, open preview. | UI renders. | `#root` was empty. Browser error: `Class extends value undefined is not a constructor or null`. | Set `javascript-obfuscator` `renameProperties: false` because vendor chunks are not obfuscated. | Fixed, verified with current asset `index-DRjsks_y.js` and zero current asset errors. |
| QA-002 | High | `ui/src/components/MainWindow/MainWindow.tsx` | Open cockpit at 1280x720. | SQLite, Redis, AgentPilot statuses visible. | Statuses existed in beta API but were hidden in a sidebar/not visible at default cockpit view. | Added SQLite/Redis/AgentPilot `StatusPill`s to the visible topbar. | Fixed, browser preview shows `SQLite OK`, `Redis Degradado`, `AgentPilot Local`. |
| QA-003 | High | `core/beta/config.py` | Provide invalid JSON in beta config path. | Config load reports error. | Invalid config could be silently skipped. | Added config error tracking and tests for invalid JSON. | Fixed. |
| QA-004 | High | `core/data_collection/redactor.py` | Redact text with API keys, bearer tokens, cookies, JWT-like values, emails, private IPs, user paths. | Sensitive material redacted without swallowing normal text. | Several formats were missed; one authorization regex overmatched. | Expanded patterns and narrowed authorization/cookie regexes. | Fixed with regression tests. |
| QA-005 | High | `core/tools/safety_supervisor.py` | Submit `curl ... | bash`, `sudo`, or `runas` style commands. | Blocked or approval required. | Remote pipe-to-shell patterns were not blocked strongly enough. | Added supervisor patterns and regression test. | Fixed. |
| QA-006 | Medium | `core/beta/redis_service.py` | Query Redis service degraded health. | Heartbeat/status method available. | No heartbeat method. | Added `heartbeat()` and degraded-mode test. | Fixed. |
| QA-007 | Medium | Pytest config | Run `python -m pytest` from repo root. | Collect active tests only. | Generated/model artifact areas were collected and caused unrelated failures. | Added root `pytest.ini` with testpaths and ignored generated/artifact directories. | Fixed. |
| QA-008 | Medium | `backend/requirements.txt`, `backend/pyproject.toml` | Run full pytest in active interpreter. | Declared dependencies available. | `msgpack` and `beautifulsoup4` were required but not declared. | Added both dependencies. | Fixed after installing dependencies locally. |
| QA-009 | Medium | `ui/src/components/Onboarding/Onboarding.tsx`, `MainWindow.tsx` | Open first screen. | "AgentMax Closed Beta" visible. | Onboarding still said Technical Beta. | Updated onboarding and badge text. | Fixed. |
| QA-010 | Low | `docs/BETA_TESTER_GUIDE.md` | Run secret-pattern scan. | Docs avoid secret-like placeholders. | Placeholder `sk-ant-...` looked like a token. | Replaced with `<your Anthropic API key>`. | Fixed. |

## Open Or Conditional

| ID | Severity | Module | Steps | Expected | Actual | Recommended Fix | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QA-OPEN-001 | Medium | Redis | Run beta status with no Redis server. | `redis.available=true` if Redis-backed queue is required. | Redis is degraded, SQLite fallback passes. | Decide if Redis is optional. If required, ship Redis setup and require connected-mode smoke. | Open, acceptable only if Redis remains optional. |
| QA-OPEN-002 | High | Packaged Tauri app | Launch MSI/NSIS-installed app or release exe. | Native window screenshots prove STOP/Pause, statuses, diagnostics, feedback, and permissions. | Browser preview passed, but native packaged Tauri window was not launched. | Run passive Computer Use/manual screenshot QA on the packaged app. | Open. |
| QA-OPEN-003 | Medium | Typography | Inspect font assets. | Beyline bundled or documented as external. | `@font-face` uses `local("Beyline")`; no Beyline asset is present. | Add licensed `Beyline.woff2` or document fallback. | Open. |
| QA-OPEN-004 | Medium | Build hygiene | Run `cargo check` and `npm run tauri build`. | No concerning warnings. | Rust dead-code/unnecessary-unsafe warnings; Vite CJS/circular chunk/Tauri mixed import warnings. | Remove unused native/security code paths or wire them intentionally; clean Vite chunks/imports. | Open. |
| QA-OPEN-005 | High | Release process | Inspect root `.github/workflows`. | CI runs tests/build/smoke. | No project-root CI workflow found. | Add CI for Python tests, UI typecheck/build, cargo check, beta smoke. | Open. |
| QA-OPEN-006 | Medium | Cross-platform | Validate macOS/Linux. | Packaging/runtime tested or marked unsupported. | Only Windows/local browser preview was tested. | Run platform-specific packaging and permission checks or document unsupported platforms. | Open. |
| QA-OPEN-007 | High | Native privacy flow | Native screenshot/model flow. | Screenshot redaction/send-to-model behavior verified in packaged app. | Browser mode cannot exercise native screenshot flow. | Add native E2E/manual test that verifies screenshot consent, metadata, and no unintended upload. | Open. |
