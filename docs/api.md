# API Reference

Base URL: `https://api.AgentMax.io`  
All responses are JSON. All timestamps are ISO 8601 UTC.

---

## Authentication

AgentMax uses a two-step ECDH challenge-response flow to activate a license.

### 1. Request challenge

```http
POST /v1/auth/challenge
Content-Type: application/json

{
  "license_key": "AAAA-BBBB-CCCC-DDDD-EEEE",
  "machine_fingerprint": "a3f9c2b1d4e87654"
}
```

**Response 200:**
```json
{
  "challenge_id": "uuid",
  "nonce": "hex-string",
  "server_ephemeral_public": "hex-string (X25519 public key)",
  "expires_at": "2026-05-11T10:00:00Z"
}
```

### 2. Activate (exchange tokens)

```http
POST /v1/auth/activate
Content-Type: application/json

{
  "challenge_id": "uuid",
  "client_ephemeral_public": "hex-string (X25519 public key)",
  "response": "hex-string (HMAC-SHA256 of nonce+fingerprint)",
  "machine_fingerprint": "a3f9c2b1d4e87654",
  "device_info": {
    "platform": "win32",
    "os_version": "10.0.26200",
    "arch": "x64",
    "client_version": "1.2.0"
  }
}
```

**Response 200:**
```json
{
  "access_token": "Ed25519-signed JWT (1h TTL)",
  "refresh_token": "AES-encrypted opaque token (7d TTL)",
  "offline_token": "Ed25519-signed JWT (24h TTL)",
  "token_type": "Bearer",
  "expires_in": 3600,
  "license": {
    "id": "uuid",
    "plan": "pro",
    "expires_at": "2027-05-11T00:00:00Z",
    "actions_per_day": 5000
  }
}
```

### 3. Refresh access token

```http
POST /v1/auth/refresh
Content-Type: application/json

{
  "refresh_token": "opaque-encrypted-token",
  "machine_fingerprint": "a3f9c2b1d4e87654"
}
```

**Response 200:**
```json
{
  "access_token": "new JWT",
  "refresh_token": "new rotated token",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

> **Note:** Refresh tokens rotate on use. Reusing an old refresh token revokes the entire session.

### 4. Logout

```http
POST /v1/auth/logout
Authorization: Bearer <access_token>
```

**Response 200:**
```json
{ "ok": true }
```

---

## License endpoints

### Heartbeat

Send every 30 minutes to keep the session alive.

```http
POST /v1/license/heartbeat
Authorization: Bearer <access_token>
```

**Response 200:**
```json
{ "ok": true, "next_heartbeat_in": 1800 }
```

### Deactivate device

```http
POST /v1/license/deactivate
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "machine_fingerprint": "a3f9c2b1d4e87654"
}
```

**Response 200:**
```json
{ "ok": true }
```

---

## Admin endpoints

All admin endpoints require the `X-Admin-Secret` header.

**In production, protect admin routes with VPN or IP allowlist.**

### Metrics dashboard

```http
GET /v1/admin/metrics
X-Admin-Secret: <admin-secret>
```

**Response 200:**
```json
{
  "total_licenses": 1247,
  "active_licenses": 1180,
  "expired_licenses": 42,
  "revoked_licenses": 25,
  "total_activations": 3891,
  "active_sessions": 483,
  "new_licenses_24h": 17,
  "new_activations_24h": 31
}
```

### Activity timeline

```http
GET /v1/admin/stats/timeline?days=30
X-Admin-Secret: <admin-secret>
```

**Response 200:**
```json
[
  { "date": "May 1", "activations": 12, "sessions": 48 },
  { "date": "May 2", "activations": 18, "sessions": 71 }
]
```

### Plans

```http
GET  /v1/admin/plans
POST /v1/admin/plans
```

**POST body:**
```json
{
  "name": "pro",
  "display_name": "Pro Plan",
  "max_devices": 3,
  "duration_days": 30,
  "is_active": true,
  "metadata": {}
}
```

### Licenses

```http
GET    /v1/admin/licenses?status=active&q=search&limit=50&offset=0
POST   /v1/admin/licenses
GET    /v1/admin/licenses/{id}
PATCH  /v1/admin/licenses/{id}
POST   /v1/admin/licenses/{id}/revoke
```

**POST /v1/admin/licenses body:**
```json
{
  "plan_id": "uuid",
  "user_email": "user@example.com",
  "user_name": "John Doe",
  "notes": "VIP customer",
  "metadata": {}
}
```

**POST /v1/admin/licenses/{id}/revoke body:**
```json
{
  "reason": "Terms of Service violation"
}
```

### Sessions

```http
GET    /v1/admin/sessions?limit=50
DELETE /v1/admin/sessions/{id}
```

### Audit log

```http
GET /v1/admin/audit?limit=100&offset=0&event_type=license.activated
X-Admin-Secret: <admin-secret>
```

**Response 200:**
```json
[
  {
    "id": "uuid",
    "created_at": "2026-05-11T09:00:00Z",
    "event_type": "license.activated",
    "severity": "info",
    "license_id": "uuid",
    "session_id": "uuid",
    "machine_fingerprint": "a3f9c2b1",
    "ip_address": "1.2.3.4",
    "message": "License activated on win32 / AMD64",
    "payload": {}
  }
]
```

---

## Telemetry (optional)

Anonymous, opt-in only. No PII collected.

```http
POST /v1/telemetry/batch
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "events": [
    {
      "event_name": "task.completed",
      "client_version": "1.2.0",
      "platform": "win32",
      "payload": { "duration_ms": 4200, "task_type": "screen_read" }
    }
  ]
}
```

---

## Updates

```http
GET /v1/updates/check?channel=stable&version=1.2.0&platform=win32
Authorization: Bearer <access_token>
```

**Response 200 (update available):**
```json
{
  "update_available": true,
  "version": "1.3.0",
  "mandatory": false,
  "release_notes": "Bug fixes and performance improvements",
  "download_url": "https://...",
  "sha256": "abc123...",
  "signature": "Ed25519 signature of sha256 (base64url)"
}
```

---

## Error format

All errors follow this structure:

```json
{
  "error": "error_code",
  "message": "Human-readable description"
}
```

| Error code | HTTP | Meaning |
|---|---|---|
| `license_not_found` | 404 | Key does not exist |
| `license_expired` | 402 | Subscription has expired |
| `license_revoked` | 403 | License was revoked by admin |
| `device_limit_exceeded` | 409 | Too many devices for this plan |
| `invalid_challenge` | 401 | Challenge expired or not found |
| `invalid_challenge_response` | 401 | HMAC verification failed |
| `invalid_token` | 401 | Token malformed or signature invalid |
| `token_expired` | 401 | JWT has expired |
| `session_expired` | 401 | Session exceeded TTL |
| `session_invalidated` | 401 | Session was revoked |
| `rate_limit_exceeded` | 429 | Too many requests |
| `admin_auth_required` | 401 | Missing or invalid X-Admin-Secret |
| `internal_error` | 500 | Unexpected server error |
