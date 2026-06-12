# AgentMax Security — Operations Guide

This document covers the **active security features** of the AgentMax runtime
and how to enable / verify / roll them back. Status as of 2026-05-23.

> Honesty rule: nothing in this file is marked "secure" unless it was validated
> in the audit (`docs/AgentMax_ARCHITECTURE_AUDIT.md`) and has tests / smoke
> tests demonstrating the behaviour.

---

## 1. Feature flags overview

| Flag | Default | Purpose | Rollback |
|---|---|---|---|
| `AGENTMAX_DEV_BYPASS` | unset | Skip anti-tamper checks in dev / suppress license-required errors | Unset the env var |
| `AGENTMAX_DEV_TOKEN` | unset | HMAC-derived machine-specific dev bypass for anti-tamper | Unset the env var |
| `AGENTMAX_OBSERVABILITY` | `1` (ON) | Strip `<think>` from LMStudio replies + publish `ai.thinking` / `ai.tokens` / `ai.response` events | Set to `0` |
| `AGENTMAX_IPC_AUTH` | unset (OFF) | Require `X-AgentMax-Token` on REST + WS handshake on the IPC server | Unset the env var |
| `AGENTMAX_AUDIT_HMAC_KEY` | unset | HMAC-sign each audit-log entry for offline tamper verification | Unset the env var |
| `AGENTMAX_AIR_GAP` | `1` in `SecurityConfig` | Block outbound non-loopback `socket.create_connection` and `getaddrinfo` | Set to `0` |
| `AGENTMAX_REQUIRE_CONSENT` | `1` in `SecurityConfig` | Make `PermissionManager.require()` raise instead of auto-granting | Set to `0` |
| `AGENTMAX_TOKEN_DAILY_CAP` | `0` | Per-user-per-day token cap (0 = none) | Set to `0` |
| `AGENTMAX_TOKEN_MONTHLY_CAP` | `0` | Per-user-per-month token cap | Set to `0` |
| `AGENTMAX_TOKEN_PERSIST` | unset | Path to JSON for token-usage persistence | Unset the env var |

---

## 2. IPC authentication (Phase 2)

### What it is

A bearer-token gate on the IPC server, hardening the two surfaces the audit
flagged as **critical** in §5 of the architecture audit:

- **REST**: `http://127.0.0.1:7790/api/*` — any local process can post tasks,
  read tokens, trigger `/api/shutdown`, or `/api/emergency_stop`.
- **WebSocket**: `ws://127.0.0.1:7788` — any local process can subscribe to
  ALL bus events (including `ai.thinking` raw chain-of-thought).

Both are now gateable with a single token shared between the Tauri shell and
the Python IPC server.

### Token lifecycle

The runtime always generates and persists a token on first boot **regardless
of the auth flag** (so the Tauri side can pick it up the moment the flag is
flipped, without a restart):

1. If `AGENTMAX_IPC_TOKEN` is already in the environment, that value is used.
2. Else if `~/AppData/Local/AgentMax/ipc_token` (Windows) or
   `~/.config/AgentMax/ipc_token` (POSIX) exists, that value is loaded.
3. Else a new 32-byte random token is generated (base64-urlsafe, ~43 chars),
   exported to the env var, and written to the file (`0600` on POSIX).

The token persists across restarts. To rotate, delete the file **and** unset
the env var, then restart the runtime.

### Enabling it

```powershell
# Windows
$env:AGENTMAX_IPC_AUTH = "1"
python main.py
```

```bash
# Linux / macOS
AGENTMAX_IPC_AUTH=1 python main.py
```

On boot you'll see:

```
ipc.auth_status enabled=True token_file=C:\Users\<you>\AppData\Local\AgentMax\ipc_token
```

### REST clients

Add the header on every request to a protected route:

```http
GET /api/status HTTP/1.1
Host: 127.0.0.1:7790
X-AgentMax-Token: <token>
```

Exempt routes (never require auth): anything under `/health`.

### WebSocket clients

First message after the connection opens (within 5 seconds):

```json
{"cmd": "auth", "token": "<token>"}
```

Server responds with one of:

```json
{"type": "auth_ok", "ts": 1716507100.123}
```

```json
{"type": "auth_failed", "reason": "invalid_token"}
```

On `auth_failed` the server closes the socket with code `4401`. On
`handshake_timeout` (no message in 5 s) the server closes with code `4401`.

### Validation evidence

Smoke tests run in this session:

| Test | Result |
|---|---|
| Boot with flag off → REST allowed without token | ✅ 200 |
| Boot with flag on → `/health` allowed without token | ✅ 200 |
| Boot with flag on → `/api/status` without token | ✅ 401 |
| Boot with flag on → `/api/status` with bad token | ✅ 401 |
| Boot with flag on → `/api/status` with valid token | ✅ 200 |
| Auth-on WS: no handshake → connection closed | ✅ closed |
| Auth-on WS: wrong token → `auth_failed` + close | ✅ |
| Auth-on WS: valid token → `auth_ok` | ✅ |
| Full test suite regression | ✅ 154/162 (same as pre-Phase 2) |

### Rollback

```powershell
Remove-Item Env:AGENTMAX_IPC_AUTH
```

The runtime falls back to legacy behaviour (no auth) on the next boot. The
token file is **not** deleted (kept for future toggle).

### What this does NOT protect against

- A hostile local user with read access to `%LOCALAPPDATA%\AgentMax\ipc_token`
  or the env var. Same trust boundary as the user account.
- Replay attacks (the token is reused for every request).
- Rate-limit / flooding — separate concern; not addressed in Phase 2.
- Network MITM — not relevant for loopback-only.

### Files touched in Phase 2

- `core/security/ipc_auth.py` — **new**, 250+ LOC, no other module changed before this PR
- `core/security/__init__.py` — re-exports `ipc_auth`
- `core/config.py` — added `ipc_auth_enabled: bool = False`
- `core/ipc.py` — added auth checks in `IPCServer.__init__`, REST `catch_all`, and `_ws_handler`. **Legacy code paths preserved**; the new behaviour only activates when the flag is on.

---

## 3. Audit log

`AGENTMAX_AUDIT_HMAC_KEY` should be set to a 32+ byte random value in production
so every log entry carries an HMAC-SHA256 signature. Without the key,
`AuditLog.verify_log()` returns an empty list (cannot verify).

```bash
AGENTMAX_AUDIT_HMAC_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
```

Default audit log location: `./data/audit.log` (override via `AGENTMAX_AUDIT_LOG`).

---

## 4. Token limits

Token caps are enforced per `(plan, user)`. Set:

```bash
AGENTMAX_TOKEN_DAILY_CAP=100000     # 100k tokens/day per (plan, user)
AGENTMAX_TOKEN_MONTHLY_CAP=2000000  # 2M tokens/month
AGENTMAX_TOKEN_PERSIST=./data/tokens.json
```

When AgentMax observability is on (default), `ai.tokens` events flow into
`token_manager.consume(plan="AgentMax", user="local", ...)` with
`skip_check=True` (counting only — no blocking for local models).

---

## 5. AgentMax thinking observability

When `AGENTMAX_OBSERVABILITY=1` (default), every LM Studio response
is split into the internal `<think>` block and the visible answer.

- The agent receives **only the visible answer** — chain-of-thought never leaks
  to upstream callers.
- `AgentMax.thinking` is logged at `info` level with a 200-char preview.
- `AgentMax.tokens` is logged at `info` level with `prompt`/`completion`/`total` counts.
- Events `ai.thinking`, `ai.tokens`, `ai.response` are published to the bus.

**Security implication crossed with Phase 2**: the bus is broadcast over the
WebSocket to all clients. With `AGENTMAX_IPC_AUTH=0` (default), any local
process can read the thinking blocks. **Turn IPC auth on before opting into
production deployment.**

Rollback to legacy (no thinking-strip, no events):

```bash
AGENTMAX_OBSERVABILITY=0
```

---

## 6. Permissions model

`PermissionManager` (`core/security/permission_manager.py`) defines 8 permissions:

- `SCREEN_READ`, `INPUT_MOUSE`, `INPUT_KEYBOARD` — granted by default at boot.
- `PROCESS_LAUNCH`, `FILE_READ`, `FILE_WRITE`, `REGISTRY`, `NETWORK` — must be
  explicitly granted.

`AGENTMAX_REQUIRE_CONSENT=1` (default in `SecurityConfig`) makes
`PermissionManager.require()` raise `PermissionDeniedError` if the permission
hasn't been granted. With `=0`, `require()` auto-grants — **only safe for dev**.

For production, the UI must call `permission_manager.grant(...)` after the user
confirms each capability in the consent dialog.

---

## 7. Air-gap mode

`AGENTMAX_AIR_GAP=1` (default in `SecurityConfig`) patches `socket.create_connection`
and `socket.getaddrinfo` to refuse non-loopback addresses.

**Limitations** — documented in the audit (§2.7):

- Does NOT patch raw sockets or C libraries that bypass the Python socket
  module (e.g. native libcurl).
- For real air-gap, complement with an OS-level firewall.

---

## 8. Anti-tamper

`core/security/anti_tamper.py` runs a 60-second watchdog with the following
checks (all of which can be bypassed with a valid `AGENTMAX_DEV_TOKEN`):

- Python debugger attached (`sys.gettrace`, pydevd, pdb).
- Cracker tools in process list (~40 names: x64dbg, IDA, frida, etc).
- Code-integrity sentinels for `LicenseManager.*` functions.
- Suspicious env vars (`FRIDA_SCRIPTS`, `LD_PRELOAD`, `DYLD_*`).
- VM environment.

**Known gap** — flagged in audit (§2.4):
The code-integrity check depends on `license_manager` being booted. Today the
license manager is **disabled** in the runtime by product decision, so
`_check_code_integrity()` has no baseline to compare against and the check is
effectively a no-op. When the license manager comes back, the watchdog gains
real teeth.

`get_machine_fingerprint()` is functional (MAC + volume serial + CPU brand →
SHA-256 prefix 20). Usable for telemetry / server-side license binding once
licensing returns.

---

## 9. Updater

`core/updater/update_manager.py` verifies Ed25519 signatures on update
manifests before installing. Without a license manager (current state) the
updater's HTTP client never starts and the updater is dormant.

Update verification chain (when active):

1. Pinned Ed25519 server public key.
2. Manifest signature verified offline.
3. Installer SHA-256 verified before execution.
4. Installer runs from a tempdir with restricted perms.
5. Rollback: non-zero exit → preserve current version.

---

## 10. Telemetry

`core/telemetry/telemetry_client.py` is instantiated but **events are silently
discarded** when no license manager is available (current state). Nothing
leaves the machine.

---

## 11. Hardening checklist for production builds

- [ ] `AGENTMAX_IPC_AUTH=1` and Tauri shell wired to read the token file
- [ ] `AGENTMAX_AUDIT_HMAC_KEY` set to 32+ random bytes
- [ ] `AGENTMAX_REQUIRE_CONSENT=1` (default; do not disable in prod)
- [ ] `AGENTMAX_AIR_GAP=1` if shipping the "Privacy Shield" SKU
- [ ] `AGENTMAX_DEV_BYPASS` not set
- [ ] `AGENTMAX_DEV_TOKEN` not set
- [ ] `AGENTMAX_TOKEN_DAILY_CAP` and `_MONTHLY_CAP` set to plan limits
- [ ] License manager reactivated (currently OFF — product decision)
- [ ] Anti-tamper degradation callback wired (currently no-op)
- [ ] Screenshot privacy redactor wired pre-model (currently MISSING — Phase 4+)
- [ ] Prompt-injection defense layer in place (currently MISSING — Phase 8+)

Items marked "currently MISSING" are tracked in the audit document for future
phases. **Do not claim "production-ready" until each line is checked.**

---

## 12. Validation commands

Re-run any time after touching the security path:

```bash
# Compile-time check
python -m py_compile core/security/ipc_auth.py core/ipc.py core/config.py

# Unit tests for the auth module primitives
python -c "from core.security import ipc_auth; assert ipc_auth.validate_token('a', 'a'); print('OK')"

# Full regression
python -m pytest tests/ -q --no-header
```

Two integration smoke runs (subprocess-isolated to avoid port reuse):

```bash
# Legacy mode: no auth, /api/status returns 200 without token
AGENTMAX_DEV_BYPASS=1 python -c "..."

# Auth mode: /api/status returns 401 without token, 200 with token
AGENTMAX_DEV_BYPASS=1 AGENTMAX_IPC_AUTH=1 python -c "..."
```

(Both ran clean in this session — see the smoke-test transcript.)
