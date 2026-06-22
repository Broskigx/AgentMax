# AgentMax Closed Beta — Tester Quickstart (Windows)

## Proprietary software (closed source)

AgentMax is **proprietary, closed-source software**. As a closed-beta tester you receive an **installer only** (MSI/EXE), not the source repository.

- Do **not** redistribute, publish, or share the installer, binaries, or internal documentation outside the beta program.
- Do **not** reverse-engineer, decompile, or attempt to extract source code from the application.
- Feedback, logs, and diagnostics ZIPs may be shared **only** with the AgentMax team through the channels we provide.
- All rights reserved; no license is granted beyond limited beta evaluation on your machine.

## Supported platforms

| Platform | Status |
|----------|--------|
| **Windows 10/11** | Supported — installer provided for this closed beta |
| **Linux / macOS** | Experimental / unsupported this round. Desktop control is implemented but not built or QA-tested, and needs external tools (`cliclick` on macOS; `xdotool` or `ydotool`+`grim` on Linux) |

## Requirements

- Windows 10/11 x64
- **Python 3.11+** on PATH (`python --version`)
- **Node.js 20+** and npm (dev mode only)
- **Rust + Cargo** (dev mode only)
- ~2 GB free disk for logs/diagnostics/SQLite

## API ports (do not guess)

| Port | Service |
|------|---------|
| **7790** | Python beta backend — chat, tasks, diagnostics, feedback |
| **7789** | Rust helper — vision/LM proxy only |
| **7788** | WebSocket events (full runtime only; optional in beta) |
| **1420** | Vite dev UI (dev mode only) |

## First-time setup

```powershell
cd C:\path\to\AgentMax   ;# or wherever you installed it - paths are resolved automatically for any user
.\scripts\setup.ps1
```

This creates `.venv`, installs `requirements.txt`, installs UI deps, creates `.env` from `.env.example` when needed, and runs `cargo check`.

## Start AgentMax (recommended dev flow)

```powershell
.\scripts\dev.ps1
```

This starts `scripts/agentmax_server.py` on **:7790** and then `npm run tauri dev`.

## Start manually (two terminals)

**Terminal 1 — backend**

```powershell
cd C:\path\to\AgentMax   ;# or wherever you installed it - paths are resolved automatically for any user
.\.venv\Scripts\Activate.ps1
python scripts\agentmax_server.py
```

**Terminal 2 — desktop UI**

```powershell
cd C:\path\to\AgentMax\ui   ;# dev only - production resolves paths relative to the installed exe + %LOCALAPPDATA%\AgentMax
$env:AGENTMAX_SKIP_BACKEND_SPAWN = "1"
npm run tauri dev
```

## Installed app (MSI/EXE)

The desktop app auto-spawns the Python beta backend when:

1. Python 3.11+ is installed and on PATH
2. Bundled resources include `scripts/agentmax_server.py` and `core/`

If Python is missing, the UI shows **Backend offline** with recovery logs under `%LOCALAPPDATA%\AgentMax\logs\`.

**After installing or updating the MSI/EXE:** close any running AgentMax window, then launch again from the Start Menu. The app refreshes its local integrity snapshot automatically on version changes.

**What you should see when it works:**

- Status banner **not** red — backend reachable on `:7790`
- Chat uses the real Python backend (no silent demo replies)
- **Diagnostics** button exports a ZIP under `diagnostics/` or `%LOCALAPPDATA%\AgentMax`

**Requirements testers must install themselves:** Python 3.11+ on PATH. The MSI does **not** bundle Python.

## Smoke test (backend must be running)

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\release_smoke_test.py
python scripts\beta_cli.py smoke-test --output-dir diagnostics\smoke-manual
```

## Feature flags in this beta

From `agentmax.config.json` (conservative defaults):

| Feature | Default |
|---------|---------|
| Chat / beta API | **ON** |
| Diagnostics export | **ON** |
| Feedback | **ON** |
| SQLite storage | **ON** |
| Screen vision metadata | **ON** |
| Mouse control | **OFF** |
| Keyboard control | **OFF** |
| Terminal command runner | **OFF** (UI panel exists; feature flag off) |
| File actions | **OFF** |
| Cloud AgentPilot | **OFF** |
| Telemetry | **OFF** |

Computer control requires explicit in-chat consent even when flags are enabled later.

## IPC auth (closed beta)

- Default: **ON** (`AGENTMAX_IPC_AUTH=1`)
- Shared token file: `%LOCALAPPDATA%\AgentMax\ipc_token`
- Legacy `ipc.key` is migrated automatically
- When enabled, the UI sends `X-AgentMax-Token` on `:7790` requests
- Disable only for isolated maintainer diagnostics with `AGENTMAX_IPC_AUTH=0`

## Logs and diagnostics

| Item | Location |
|------|----------|
| Beta JSONL logs | `logs/agentmax/` (project) or under `%LOCALAPPDATA%\AgentMax` |
| Recovery test log | `%LOCALAPPDATA%\AgentMax\logs\recovery-test.jsonl` |
| Diagnostics ZIP | `diagnostics/` or via UI **Diagnostics** button |
| SQLite beta DB | `data/agentmax_beta.sqlite` |
| Tester id | `data/agentmax_tester_id.txt` |

## Report bugs

1. Reproduce with backend running (`:7790/health` returns `ok: true`)
2. Export diagnostics from the UI or `python scripts\beta_cli.py export-diagnostics`
3. Attach `recovery-test.jsonl` if the app crashed on last run
4. Include steps, expected vs actual, and whether Python was installed

## Build verification (maintainers)

```powershell
python -m pytest -q
cd ui; npm run test; npm run build
cd src-tauri; cargo check
cd ..; npm run tauri build
.\scripts\packaged_smoke_test.ps1
```

`packaged_smoke_test.ps1` launches the release `AgentMax.exe`, waits for `:7790`, and runs `release_smoke_test.py`. It sets `AGENTMAX_BETA_SECURITY=0` so the smoke test can run on maintainer machines with debuggers attached. **Testers do not set this** — they install the MSI normally.
