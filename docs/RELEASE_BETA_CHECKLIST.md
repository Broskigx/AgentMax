# AgentMax — Release Beta Checklist

> **Status:** Technical Beta Candidate  
> **Date:** 2026-05-21  
> **Build target:** Windows (x64)

---

## Build Commands

```powershell
# Frontend build
cd ui
npm install
npm run build

# Tauri desktop build
npm run tauri build

# Check Rust compilation
cd src-tauri
cargo check

# Python core (no build needed, runs from source)
cd ..
python main.py
```

## Test Commands

```powershell
# Python tests
.venv\Scripts\python.exe -m pytest tests/ -v

# Python tests with coverage
.venv\Scripts\python.exe -m pytest tests/ --cov=core --cov-report=term

# Rust tests
cd ui/src-tauri
cargo test

# Lint
cd ../..
.venv\Scripts\python.exe -m ruff check .

# TypeScript type check
cd ui
npx tsc --noEmit
```

## Pre-Release Checklist

### 1. Code Quality
- [ ] `ruff check .` passes
- [ ] `npx tsc --noEmit` passes
- [ ] `cargo check` passes
- [ ] `pytest tests/ -v` passes
- [ ] No `console.log`/`print()` debug statements

### 2. UI/UX
- [ ] App opens without errors
- [ ] Onboarding flow works (3 steps)
- [ ] Chat input accepts and sends messages
- [ ] Token counter displays correctly
- [ ] Screenshot button shows correct state
- [ ] Terminal panel opens and closes
- [ ] Command palette (Ctrl+K) works
- [ ] Emergency stop (Ctrl+Shift+Esc) works
- [ ] Settings panel opens
- [ ] All empty states render (no blank screens)

### 3. Tools & Safety
- [ ] Tool registry displays correctly
- [ ] Read-only tools (CMD, PowerShell) show as available
- [ ] Destructive tools require approval
- [ ] Stop Agent button works
- [ ] Supervisor shows active status

### 4. AgentMax Preview
- [ ] AgentMax appears as "Preview" in UI
- [ ] AgentMax not configured → shows notice, doesn't crash
- [ ] AgentMax adapter missing → shows error, app continues
- [ ] AgentMax selected as backend → shows experimental badge
- [ ] Response sanitizer strips `<think>` tags

### 5. Backend
- [ ] `python main.py` starts without errors
- [ ] `python main.py --daemon` starts as daemon
- [ ] Health endpoint (`/health`) returns 200
- [ ] Token API (`/api/tokens`) returns valid data
- [ ] Tools API (`/api/tools`) returns catalog
- [ ] Chat API (`/api/chat`) responds

### 6. Build Artifacts
- [ ] `ui/dist/` is clean (Vite build)
- [ ] Tauri installer builds (`.msi` or `.exe`)
- [ ] No debug symbols in release build
- [ ] App icon is set

## Known Limitations

| Limitation | Impact | Workaround |
|------------|--------|------------|
| No code signing | Windows SmartScreen warning | Click "Run anyway" |
| AgentMax vision incomplete | Vision analysis may fail | Use LM Studio with vision model |
| Local PEFT requires GPU | Model loading fails on CPU-only | Use LM Studio backend instead |
| Screenshot requires Tauri IPC | Screenshot unavailable in browser | Run as Tauri desktop app |
| Token system is local-only | No cross-device sync | Tokens reset daily per device |

## AgentMax Preview Warning (for release notes)

> **AgentMax Preview** is an experimental feature included for testing
> purposes. It may require GPU dependencies, adapter files, or specific model
> runtimes. If AgentMax is not available, AgentMax falls back to other
> backends (LM Studio, OpenAI-compatible API) without disruption.
>
> AgentMax Preview is **not production-ready**. It is provided for
> evaluation and feedback collection only.

## Signing Status

| Component | Signed | Notes |
|-----------|--------|-------|
| Tauri installer | ❌ No | Beta release unsigned |
| Python packages | N/A | Runs from source |
| Rust binaries | ❌ No | Beta release unsigned |

## Rollback Steps

If a release causes issues:

1. **Revert to previous git tag:**
   ```powershell
   git checkout tags/beta-<previous-version>
   ```

2. **Rebuild:**
   ```powershell
   cd ui
   npm install
   npm run build
   cd ..
   python main.py
   ```

3. **Clear local data:**
   ```powershell
   Remove-Item -Recurse -Force data/ -ErrorAction SilentlyContinue
   Remove-Item -Recurse -Force logs/ -ErrorAction SilentlyContinue
   ```

---

*For questions or issues, open a GitHub issue.*
