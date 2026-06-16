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
| **Windows 10/11** | Supported for this closed beta |
| **Linux / macOS** | **Not supported** for computer control (mouse/keyboard/screenshot stubs) |

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
cd C:\path\to\AgentMax
.\scripts\setup.ps1
```

This creates `.venv`, installs `requirements.txt`, installs UI deps, creates `.env` from `.env.example` when needed, and runs `cargo check`.

## Start AgentMax (recommended dev flow)

```powershell
.\scripts\dev.ps1
```

This starts `scripts/agentmax_server.py` on **:7790** and then `npm run tauri dev`.

## IPC auth (closed beta)

- Default: **ON** (`AGENTMAX_IPC_AUTH=1`)
- Shared token file: `%LOCALAPPDATA%\AgentMax\ipc_token`
- Legacy `ipc.key` is migrated automatically
- When enabled, the UI sends `X-AgentMax-Token` on `:7790` requests
- Disable only for isolated maintainer diagnostics with `AGENTMAX_IPC_AUTH=0`
