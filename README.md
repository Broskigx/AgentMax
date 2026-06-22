# AgentMax (Closed Beta)

**Proprietary / closed source.** Windows desktop AI agent — Tauri UI + Python beta backend. Testers receive installers only; source access is limited to maintainers.

## Quick start

See **[TESTER_QUICKSTART.md](TESTER_QUICKSTART.md)** for tester instructions.

```powershell
.\scripts\setup.ps1
.\scripts\dev.ps1
```

## Architecture (closed beta)

- **:7790** — Python `scripts/agentmax_server.py` (tasks, chat, beta, diagnostics)
- **:7789** — Rust Axum inside Tauri (vision/LM proxy)
- **IPC token** — `%LOCALAPPDATA%\AgentMax\ipc_token` (shared with Python)

The packaged app auto-spawns the Python beta backend when Python is available.

## Tests

```powershell
python -m pytest -q
python scripts\release_smoke_test.py   # requires backend on :7790
python scripts\beta_cli.py smoke-test --output-dir diagnostics\smoke
```

## Platform support

- **Windows**: fully supported (primary), and the only platform with an installer this round. Custom titlebar, full ComputerMax automation, recovery diagnostics.
- **Linux / macOS**: core chat + LM Studio / API backends work, and all first-run paths, logs and data directories are cross-platform (Recovery Test and model detection work everywhere). Desktop control (mouse/keyboard/screenshot) *is implemented* via external tools — macOS `screencapture`/`cliclick`, Linux `xdotool` (X11) or `ydotool`+`grim` (Wayland) — but is **not built, packaged, or QA-tested** for this beta. Treat mac/Linux as experimental and unsupported for now.
