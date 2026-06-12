# Security & Architecture Review

**Date:** 2026-05-30 · **Scope:** `core/`, `backend/` (Python). Native Rust/Tauri
and C++ engine reviewed only at their Python boundaries.

This is a focused source review, not a penetration test. Findings are recorded
honestly: the codebase is in good shape, so most of this documents *strengths*
and explains why the static-analysis flags are not vulnerabilities.

## Summary

No vulnerabilities were found. The security-critical code uses modern,
correctly-applied primitives, and the dangerous-pattern sweep came back clean.

| Area | Assessment |
|------|-----------|
| Cryptography | ✅ Strong — modern AEAD + ECDH + signatures, used correctly |
| Secrets handling | ✅ No hardcoded secrets; env-var driven |
| Command execution | ✅ Gated (length cap + safety supervisor + deny-list) |
| File I/O | ✅ Sandboxed allowlist with traversal protection + audit |
| Network egress | ✅ SSRF blocklist with resolve-then-check + tests |
| Dangerous APIs | ✅ No `eval`/`exec`/`pickle.loads`/`yaml.load`/`os.system`/`verify=False` |

## What was checked

1. **Pattern sweep** across `core/` and `backend/` for `eval`/`exec`, `pickle.loads`,
   `yaml.load`, `os.system`, `verify=False`, TLS bypass, `debug=True`, and
   hardcoded `password|secret|api_key|token = "…"`. **Zero** hits (one false
   positive: a docstring mentioning `debug=True`).
2. **Cryptography** (`core/security/crypto.py`):
   - AES-256-GCM (AEAD) with a fresh `os.urandom(12)` nonce, associated data,
     and a minimum-length check on decrypt.
   - X25519 ECDH whose raw output is run through **HKDF-SHA256** (salt + info) —
     not used as a key directly.
   - Ed25519 signature verification with a pinned server public key.
   - Challenge response is `HMAC-SHA256(shared_secret, nonce‖fingerprint‖key)`.
3. **IPC auth** (`core/security/ipc_auth.py`): tokens are `secrets.token_bytes(32)`
   and compared with `hmac.compare_digest` (constant-time — no timing oracle).
4. **Shell execution** (`core/tools/executor.py`): commands are length-capped
   (4096) and routed through the tool safety supervisor; `shell=True` is an
   intentional, gated capability.
5. **File system** (`core/agents/file_system_agent.py`): every path is
   `resolve()`-d and checked against a directory allowlist; traversal and symlink
   escapes are blocked and audited (`fs_agent.sandbox_violation`).
6. **Web egress** (`core/agents/web_search_agent.py`): URLs are validated against
   an SSRF blocklist (10/8, 172.16/12, 192.168/16, 127/8, 169.254/16, ::1) by
   resolving the host and checking the IP. Covered by `TestIsSafeUrl`.

## Static-analysis flags — reviewed, not vulnerabilities

| Flag | Location | Why it is safe |
|------|----------|----------------|
| `S311` non-crypto RNG | human_simulator, resilience, license_manager:204 | Used for input-timing jitter and backoff/anti-fingerprinting delays — never for key/token material (that path uses `secrets`/`os.urandom`). |
| `S324` md5/sha1 | pixel_analyzer, visual_memory | Content-addressing / dedup cache keys, now annotated `usedforsecurity=False`. |
| `S104` bind 0.0.0.0 | config.py, air_gap.py | Deployment-configurable server host; in `air_gap.py` it is a *local-host constant* used to allow/deny, not a bind. |
| `S105` "hardcoded password" | ipc_auth.py:65 | The string is an **env-var name** (`AGENTMAX_IPC_TOKEN`), not a secret. |
| `S603`/`S607` subprocess | tools/executor, updater | Core to a desktop-automation agent; gated and length-capped. |

## Recommended hardening (incremental, none urgent)

1. **SSRF — pin the validated IP.** `_is_safe_url` resolves the host and checks
   the IP, but the subsequent `httpx` request re-resolves the name, leaving a
   small DNS-rebinding/TOCTOU window. Connecting to the already-validated IP
   (with the original `Host` header) closes it.
2. **Bind defaults.** Default the server host to `127.0.0.1` and require an
   explicit opt-in for `0.0.0.0`, so a misconfiguration can't expose the API.
3. **Dependency scanning.** A `dependabot.yml` is now included (pip, npm, cargo,
   actions). Consider adding `pip-audit` as an informational CI job — relevant
   given this project's prior npm supply-chain incident.
4. **Type-checking.** Burn down the `mypy --strict` backlog so the informational
   CI job can become blocking; types catch a class of correctness/security bugs.

## Architecture note

The multi-agent design (priority event bus + Supervisor delegating to specialist
agents) cleanly separates concerns, and security is enforced at well-defined
choke points — the permission manager, tool safety supervisor, file sandbox, and
SSRF guard — rather than being scattered. This is a sound, defensible structure.
