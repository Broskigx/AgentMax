"""
Crypto service -- orchestrates challenge/response lifecycle.

Challenge-Response Protocol
---------------------------
1. Server generates ephemeral X25519 key pair.
2. Server stores {private_key_enc, nonce, license_key, machine_fp} in Redis (TTL=5min).
3. Client receives server ephemeral public key + nonce.
4. Client generates ephemeral X25519 key pair.
5. Both sides compute ECDH shared_secret = X25519(server_priv, client_pub).
6. Server derives: expected_hmac = HMAC-SHA256(shared_secret, nonce || machine_fp || license_key)
7. Client sends response = HMAC-SHA256(shared_secret, nonce || machine_fp || license_key)
8. Server verifies response == expected_hmac.

This proves:
  - Client possesses the ECDH shared secret (i.e. has the license key to look up the server pubkey)
  - The machine fingerprint is bound to the session
  - Replay attacks fail (nonce is consumed on first use; challenge is single-use)
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid

import structlog
from backend.core.config import get_settings
from backend.core.redis_pool import redis_delete, redis_get_json, redis_set_json
from backend.core.security import (
    AESCipher,
    EphemeralKeyPair,
    derive_shared_secret,
    generate_x25519_keypair,
    get_signer,
    secure_nonce,
)

log = structlog.get_logger(__name__)
_settings = get_settings()
_aes = AESCipher()


# ── Challenge generation ───────────────────────────────────────────────────────


async def create_challenge(
    license_key: str,
    machine_fingerprint: str,
) -> dict:
    """
    Generate a one-time ECDH challenge and cache it in Redis.
    Returns the data to send to the client.
    """
    kp: EphemeralKeyPair = generate_x25519_keypair()
    nonce = secure_nonce(32)  # 64-char hex
    challenge_id = str(uuid.uuid4())
    now = int(time.time())

    # Encrypt the ephemeral private key before storing (belt-and-suspenders)
    priv_raw = kp.private_key.private_bytes_raw()
    priv_encrypted = _aes.encrypt_str(priv_raw.hex())

    cache_payload = {
        "challenge_id": challenge_id,
        "nonce": nonce,
        "license_key": license_key,
        "machine_fingerprint": machine_fingerprint,
        "server_ephemeral_public": kp.public_key_b64,
        "server_ephemeral_private_enc": priv_encrypted,
        "created_at": now,
        "used": False,
    }

    await redis_set_json(
        f"challenge:{challenge_id}",
        cache_payload,
        ttl=_settings.challenge_ttl_seconds,
    )

    signer = get_signer()

    return {
        "challenge_id": uuid.UUID(challenge_id),
        "nonce": nonce,
        "timestamp": now,
        "server_ephemeral_public": kp.public_key_b64,
        "server_signing_public": signer.public_key_b64(),
    }


# ── Response verification ──────────────────────────────────────────────────────


async def verify_challenge_response(
    challenge_id: uuid.UUID,
    client_ephemeral_public: str,
    response_hex: str,
    machine_fingerprint: str,
) -> dict:
    """
    Verify the client's ECDH challenge response.

    Returns the stored challenge data on success.
    Raises InvalidChallenge / InvalidChallengeResponse on failure.
    """
    from backend.core.exceptions import InvalidChallenge, InvalidChallengeResponse
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    key = f"challenge:{challenge_id}"
    stored = await redis_get_json(key)

    if stored is None:
        raise InvalidChallenge("Challenge not found or expired")
    if stored.get("used"):
        raise InvalidChallenge("Challenge already consumed")

    # Mark used immediately -- prevents replay even if verification fails
    stored["used"] = True
    await redis_delete(key)

    # Reconstruct server ephemeral private key
    priv_hex = _aes.decrypt_str(stored["server_ephemeral_private_enc"])
    server_private = X25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))

    # Derive shared secret
    nonce = stored["nonce"]
    license_key = stored["license_key"]
    stored_mfp = stored["machine_fingerprint"]

    # Machine fingerprint must match what was in the challenge
    if not hmac.compare_digest(stored_mfp, machine_fingerprint):
        log.warning(
            "challenge.machine_fingerprint_mismatch",
            stored=stored_mfp[:8],
            received=machine_fingerprint[:8],
        )
        raise InvalidChallengeResponse("Machine fingerprint mismatch")

    shared_secret = derive_shared_secret(
        server_private=server_private,
        client_public_b64=client_ephemeral_public,
        salt=bytes.fromhex(nonce),
    )

    # Compute expected response
    msg = (nonce + stored_mfp + license_key).encode()
    expected = hmac.new(shared_secret, msg, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, response_hex.lower()):
        log.warning("challenge.response_mismatch", challenge_id=str(challenge_id))
        raise InvalidChallengeResponse("Invalid challenge response")

    return stored


# ── Refresh token helpers ─────────────────────────────────────────────────────


def generate_refresh_token() -> tuple[str, str]:
    """
    Returns (plaintext_token, token_hash).
    Store hash in DB; send plaintext to client (encrypted in ActivateResponse).
    """
    token = secrets.token_urlsafe(48)
    h = hashlib.sha256(token.encode()).hexdigest()
    return token, h


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def encrypt_refresh_token(plaintext: str) -> str:
    """AES-256-GCM encrypt refresh token before sending to client."""
    return _aes.encrypt_str(plaintext)


def decrypt_refresh_token(blob: str) -> str:
    return _aes.decrypt_str(blob)
