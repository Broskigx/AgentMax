# AgentMax Critical QA Audit - 2026-05-21

Role: senior QA / product hardening review.
Scope: local repository, Python core, FastAPI backend, Tauri/React UI, Rust bridge, dependency/build/deploy surfaces.

## Executive Summary

AgentMax has strong pieces, but it is not yet in professional release shape. The biggest risk is not one isolated bug; it is architectural drift: docs describe a Tauri/Rust-driven app, Python code still contains fallback OCR/capture paths, backend security routes exist but are not mounted, deployment points to missing folders, and the frontend/Tauri app is ignored by git.

Confirmed validation:

- Python tests: 203 passed with `.venv\Scripts\python.exe -m pytest -q`.
- Tauri/Rust: `cargo check --manifest-path ui\src-tauri\Cargo.toml` passed, with 35 warnings.
- UI build: `npm run build` failed.
- Zero CLI: unavailable in PATH, not needed for this local audit.

## Critical Findings

### P0 - Frontend/Tauri Source Is Ignored By Git

Evidence:

- `.gitignore:150` ignores `ui/`.
- `git status --short --ignored ui` reports `!! ui/`.
- `git ls-files ui\package.json ui\package-lock.json ui\vite.config.ts` returns no tracked files.

Impact:

The desktop UI, Tauri commands, Rust bridge, package lock, and build config can disappear from another clone/CI environment. This is the main "works locally but project has no body" issue.

Required fix:

Track `ui/src`, `ui/src-tauri`, `ui/package.json`, `ui/package-lock.json`, `ui/tsconfig.json`, `ui/vite.config.ts`, and `ui/index.html`. Keep ignoring only `ui/node_modules`, `ui/dist`, `ui/src-tauri/target`, and local logs.

### P0 - UI Production Build Fails

Command:

```powershell
cd ui
npm run build
```

Result:

Vite fails because `javascript-obfuscator` is imported from `ui/vite.config.ts:19`, but the installed dependency tree/lockfile does not contain it.

Evidence:

- `ui/package.json` lists `javascript-obfuscator` in `devDependencies`.
- `ui/package-lock.json` root `devDependencies` does not list it.
- `ui/node_modules/javascript-obfuscator` is absent.

Impact:

No reliable desktop/web production build. Tauri release builds are blocked because `tauri.conf.json` runs `npm run build` before packaging.

Required fix:

Regenerate `ui/package-lock.json` with the dependency included, or make obfuscation optional/fail-open behind an explicit env flag. CI must run `npm ci && npm run build`.

### P0 - Docker Compose References Missing Project Pieces

Evidence:

- `docker-compose.yml:13` mounts `./rust-backend/migrations`, but `rust-backend` does not exist.
- `docker-compose.yml:40` builds backend from `./rust-backend`, but that path does not exist.
- `docker-compose.yml:74` expects `ui/Dockerfile`, but it does not exist.
- `docker-compose.yml:98` mounts `./deploy/prometheus.yml`, but `deploy/prometheus.yml` does not exist.

Impact:

Deployment from a clean clone is impossible. This is a professional-readiness blocker.

Required fix:

Choose the real backend deployment target. Either restore the Rust backend and Dockerfiles or update compose to use `backend/` FastAPI with a real Dockerfile and migrations path.

### P0 - Backend Security Router Is Broken And Not Mounted

Evidence:

- `backend/routers/security.py:249`, `275`, `332`, `389`, `422`, and `449` use `Body`/`Query` before those imports appear at `486-488`.
- Importing `backend.routers.security` with the venv raises `NameError: name 'Body' is not defined`.
- `backend/main.py:33` imports only `admin, auth, license, telemetry, updates`.
- `backend/main.py:121-125` mounts no `/security` router.

Impact:

The security API is dead code. Even if mounted, it would fail import before serving.

Required fix:

Move imports to the top, add a test that imports every router, then decide whether `/security` is real product surface or delete/quarantine it.

### P0 - Security Endpoints Are Mocked But Named As Real

Evidence:

- `backend/routers/security.py:110-118` returns mock anti-crack status.
- `backend/routers/security.py:157-164` always returns security checks as passed.
- `backend/routers/security.py:318-325` returns `mock_token_*` activation tokens.
- `backend/routers/security.py:377-382` returns `new_access_token_*`.
- `backend/routers/security.py:410-415` returns mock hardware IDs.

Impact:

This creates false security. A buyer/operator may believe anti-crack, hardware binding, token revocation, and device management exist, but they are not connected.

Required fix:

Remove these routes from production, hide behind explicit dev-only flags, or wire them to the actual backend services and Rust security module.

## High Findings

### P1 - Backend Cannot Start Without Production Secrets, But Dev Docs Do Not Provide Them

Evidence:

- `backend/core/config.py:34-38` requires `DATABASE_URL`.
- `backend/core/config.py:53-66` requires Ed25519 and AES keys.
- `backend/core/config.py:90-94` requires `ADMIN_SECRET`.
- `.env.example` contains runtime/UI variables but not the required backend variables.
- Importing `backend.main` fails in the current environment with missing `DATABASE_URL`, `ED25519_PRIVATE_KEY_HEX`, `AES_MASTER_KEY_HEX`, and `ADMIN_SECRET`.

Impact:

Backend startup is not reproducible from project docs. Tests pass only because `backend/tests/conftest.py:17-23` injects fake env vars.

Required fix:

Add a backend `.env.example`, a key-generation script, and a clear dev profile. Keep production strict, but make local boot explicit and documented.

### P1 - Runtime Entitlements, Telemetry, And Updates Are Present But Disabled

Evidence:

- `core/runtime.py:65-69` sets `license_manager = None` and appends `entitlements-disabled`.
- `core/runtime.py:183-196` creates telemetry but does not start it without license manager.
- `core/runtime.py:198-211` creates updater but does not start it without license manager.

Impact:

Licensing, telemetry, and updates look architected but do not operate in normal runtime. This is acceptable only if explicitly branded as development mode.

Required fix:

Define product modes: dev, beta, production. Make disabled subsystems visible in health/status, and fail production startup if entitlements are required but absent.

### P1 - Rust Security/Integrity Modules Compile But Are Mostly Unused

Evidence:

`cargo check` passes with 35 warnings, including unused anti-VM, integrity, token, debugger, cracker, and security functions.

Impact:

Security code exists but may not protect real flows. The project currently has security-shaped code with limited enforcement.

Required fix:

Treat warnings as failures for release or delete/defer unused security modules. Add integration tests proving the release app actually calls critical checks.

### P1 - Shell Execution Exists In Both Python And Tauri

Evidence:

- Python shell runner uses `subprocess.run(..., shell=True)` in `core/tools/executor.py:562-584`.
- Tauri exposes `run_shell_command` through `ui/src-tauri/src/main.rs:103-105`.
- Tauri command executes PowerShell with `-ExecutionPolicy Bypass` at `ui/src-tauri/src/commands.rs:727-729`.
- UI calls it from `ui/src/store/agentStore.ts:789-793` and tool parsing at `1036-1046`.

Impact:

This is a powerful feature and a security liability. The Tauri side has a denylist and temp working dir, but denylist-based sandboxing is bypass-prone. The Python side executes raw shell strings.

Required fix:

Move to allowlisted commands/actions, require explicit per-command user consent for dangerous operations, log all shell execution, and add tests for bypass attempts.

### P1 - Port Contracts Are Inconsistent

Evidence:

- `core/config.py:207-208` defaults API port to `7790`, WS to `7788`.
- `.env.example:25-26` sets `AGENTMAX_API_PORT=7789`.
- `ui/src/store/agentStore.ts:10-12` uses Python API `7790`, Rust API `7789`, WS `7788`.
- `docker-compose.yml:77-78` sets UI API to `7789` and WS to `7788`.

Impact:

Fresh installs can point the UI at the wrong service. Rust HTTP server and Python IPC API are easy to confuse.

Required fix:

Publish one port map and enforce it through shared config/env. Example: Rust bridge `7789`, Python orchestration API `7790`, event WS `7788`.

### P1 - Vision/OCR Architecture Conflicts With README

Evidence:

- `README.md` says capture/OCR was delegated to Rust/Tauri.
- `core/pixel_engine/pixel_analyzer.py:1-5` says it replaced Rust/Tauri bridge with pure Python.
- `core/rust_vision_bridge.py:1-6` says it uses PIL PixelAnalyzer instead of Rust/Tauri core.
- OCR imports `pytesseract` in `core/pixel_engine/pixel_analyzer.py:67`, `126`, `139` and `core/rust_vision_bridge.py:79`.

Impact:

The architecture is unclear and dependencies vary by install path. OCR can silently return empty results, making automation look "smart" but unable to read UI text.

Required fix:

Choose one capture/OCR path. If Python OCR remains, document and install `pytesseract` plus the native Tesseract binary. If Rust/Tauri owns it, remove Python OCR promises and stubs.

## Medium Findings

### P2 - Tests Pass But Miss Startup, Build, Router Import, And Deployment Reality

Evidence:

- 203 tests pass.
- UI build still fails.
- `backend.routers.security` import fails.
- Docker compose points to missing paths.
- Backend test fixture patches DB/Redis and injects env.

Required fix:

Add CI gates:

- `.venv\Scripts\python.exe -m pytest`
- router import smoke test
- `npm ci && npm run build`
- `cargo check --manifest-path ui/src-tauri/Cargo.toml --all-targets`
- compose config validation after paths are corrected

### P2 - Encoding Damage In Source And Docs

Evidence:

Many files render mojibake such as `â€”`, `â”€`, and `Ã¢â‚¬`.

Impact:

This lowers polish and can break user-visible text.

Required fix:

Normalize files to UTF-8 and add an encoding lint/check.

### P2 - Python Dependency Sources Diverge

Evidence:

- Root `requirements.txt` differs from `pyproject.toml` optional dependency groups.
- Backend has separate `backend/requirements.txt` and `backend/pyproject.toml`.
- `python` command on this machine resolves to a Windows stub, while `.venv\Scripts\python.exe` is the usable interpreter.

Impact:

Developers and CI can install different dependency sets.

Required fix:

Pick one dependency authority per package. Use lockfiles or documented install commands. Scripts should call venv Python or `py -3.12` explicitly on Windows.

## Recommended Professionalization Plan

1. Source control cleanup:
   - Stop ignoring `ui/`.
   - Track source, configs, and lockfiles.
   - Keep generated artifacts ignored.

2. Build/deploy reality:
   - Fix `npm run build`.
   - Replace broken compose paths.
   - Add missing Dockerfiles or remove compose profiles until real.

3. Backend startup:
   - Add backend `.env.example`.
   - Add dev bootstrap script for database URL and keys.
   - Add router import smoke test.

4. Security truth pass:
   - Remove, hide, or wire mocked security routes.
   - Decide what anti-crack/integrity checks are actually enforced.
   - Make production fail when required security is disabled.

5. Contract cleanup:
   - Define ports, ownership, and API boundaries.
   - Remove duplicate/contradictory Rust/Python vision docs.

6. CI gates:
   - Python tests.
   - UI build.
   - Rust check with warnings policy.
   - Backend import/startup smoke.
   - Deployment config validation.

## Current Release Readiness Verdict

Not release-ready.

The core Python test suite is healthy, but the project cannot be considered professional until clean clone reproducibility, UI build, deployment paths, backend startup, and mocked security surfaces are fixed.
