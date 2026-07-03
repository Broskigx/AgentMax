"""
Cryptographic primitives for the AgentMax backend.

Key responsibilities
--------------------
- Ed25519 signing / verification  (license tokens, offline tokens, update manifests)
- X25519 ECDH key agreement       (challenge-response session establishment)
- AES-256-GCM encryption          (refresh tokens at rest, sensitive cache entries)
- JWT creation / parsing          (access tokens, offline tokens)
- Argon2id hashing                (admin passwords)
- Secure random helpers
"""

from __future__ import annotations

import base64
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt as pyjwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import get_settings

_settings = get_settings()

# ── Constants ─────────────────────────────────────────────────────────────────

JWT_ALGORITHM = "EdDSA"
_NONCE_BYTES = 32
_AES_KEY_BYTES = 32
_GCM_TAG_BYTES = 16


# ── Ed25519 signer ────────────────────────────────────────────────────────────


class Ed25519Signer:
    """Singleton wrapper around the server Ed25519 key pair."""

    def __init__(self) -> None:
        seed = _settings.ed25519_private_key_bytes()
        self._private_key = Ed25519PrivateKey.from_private_bytes(seed)
        self._public_key: Ed25519PublicKey = self._private_key.public_key()

    def sign(self, data: bytes) -> bytes:
        return self._private_key.sign(data)

    def verify(self, data: bytes, signature: bytes) -> bool:
        try:
            self._public_key.verify(signature, data)
            return True
        except Exception:
            return False

    def public_key_raw(self) -> bytes:
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def public_key_b64(self) -> str:
        return base64.urlsafe_b64encode(self.public_key_raw()).decode()


_signer: Ed25519Signer | None = None


def get_signer() -> Ed25519Signer:
    global _signer
    if _signer is None:
        _signer = Ed25519Signer()
    return _signer


# ── AES-256-GCM encryption ────────────────────────────────────────────────────


class AESCipher:
    """
    AES-256-GCM encryption with a 96-bit random nonce.
    Output format: <12-byte nonce> || <ciphertext+tag>
    """

    def __init__(self, key: bytes | None = None) -> None:
        if key is None:
            key = _settings.aes_master_key_bytes()
        if len(key) != 32:
            raise ValueError("AES key must be exactly 32 bytes")
        self._aesgcm = AESGCM(key)

    def encrypt(self, plaintext: bytes, aad: bytes = b"") -> bytes:
        nonce = os.urandom(12)
        ct = self._aesgcm.encrypt(nonce, plaintext, aad or None)
        return nonce + ct

    def decrypt(self, blob: bytes, aad: bytes = b"") -> bytes:
        if len(blob) < 12 + _GCM_TAG_BYTES:
            raise ValueError("Ciphertext too short")
        nonce, ct = blob[:12], blob[12:]
        return self._aesgcm.decrypt(nonce, ct, aad or None)

    def encrypt_str(self, plaintext: str, aad: bytes = b"") -> str:
        raw = self.encrypt(plaintext.encode(), aad)
        return base64.urlsafe_b64encode(raw).decode()

    def decrypt_str(self, blob_b64: str, aad: bytes = b"") -> str:
        raw = base64.urlsafe_b64decode(blob_b64)
        return self.decrypt(raw, aad).decode()


# ── X25519 ECDH ───────────────────────────────────────────────────────────────


@dataclass
class EphemeralKeyPair:
    private_key: X25519PrivateKey
    public_key_raw: bytes
    public_key_b64: str


def generate_x25519_keypair() -> EphemeralKeyPair:
    private_key = X25519PrivateKey.generate()
    pub_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return EphemeralKeyPair(
        private_key=private_key,
        public_key_raw=pub_raw,
        public_key_b64=base64.urlsafe_b64encode(pub_raw).decode(),
    )


def derive_shared_secret(
    server_private: X25519PrivateKey,
    client_public_b64: str,
    salt: bytes,
    info: bytes = b"AgentMax-challenge-v1",
) -> bytes:
    """ECDH + HKDF-SHA256 → 32-byte shared secret."""
    client_pub_raw = base64.urlsafe_b64decode(client_public_b64 + "==")
    client_pub = X25519PublicKey.from_public_bytes(client_pub_raw)
    raw_shared = server_private.exchange(client_pub)

    hkdf = HKDF(algorithm=SHA256(), length=32, salt=salt, info=info)
    return hkdf.derive(raw_shared)


# ── JWT (Ed25519-signed) ──────────────────────────────────────────────────────


def _ed25519_private_pem() -> bytes:
    seed = _settings.ed25519_private_key_bytes()
    key = Ed25519PrivateKey.from_private_bytes(seed)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _ed25519_public_pem() -> bytes:
    seed = _settings.ed25519_private_key_bytes()
    key = Ed25519PrivateKey.from_private_bytes(seed)
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def create_access_token(
    license_id: str,
    session_id: str,
    activation_id: str,
    machine_fingerprint: str,
    plan: str,
    ttl: int | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    ttl = ttl or _settings.access_token_ttl_seconds
    now = int(time.time())
    payload: dict[str, Any] = {
        "typ": "access",
        "sub": license_id,
        "ses": session_id,
        "act": activation_id,
        "mfp": machine_fingerprint,
        "pln": plan,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
    }
    if extra_claims:
        payload.update(extra_claims)
    return pyjwt.encode(payload, _ed25519_private_pem(), algorithm=JWT_ALGORITHM)


def create_offline_token(
    license_id: str,
    machine_fingerprint: str,
    plan: str,
    expires_at: float | None = None,
    ttl: int | None = None,
) -> str:
    ttl = ttl or _settings.offline_token_ttl_seconds
    now = int(time.time())
    payload: dict[str, Any] = {
        "typ": "offline",
        "sub": license_id,
        "mfp": machine_fingerprint,
        "pln": plan,
        "lic_exp": expires_at,  # license subscription expiry (may be null)
        "jti": str(uuid.uuid4()),
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
    }
    return pyjwt.encode(payload, _ed25519_private_pem(), algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict[str, Any]:
    """
    Verify an Ed25519-signed JWT.
    Raises InvalidToken / TokenExpired on failure.
    """
    from .exceptions import InvalidToken, TokenExpired

    try:
        payload = pyjwt.decode(
            token,
            _ed25519_public_pem(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
        return payload
    except pyjwt.ExpiredSignatureError as exc:
        raise TokenExpired("Access token has expired") from exc
    except pyjwt.InvalidTokenError as exc:
        raise InvalidToken(f"Token validation failed: {exc}") from exc


# ── Argon2id (admin passwords) ────────────────────────────────────────────────

_argon2 = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    return _argon2.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _argon2.verify(hashed, password)
    except VerifyMismatchError:
        return False


# ── Secure random helpers ─────────────────────────────────────────────────────


def secure_nonce(n: int = _NONCE_BYTES) -> str:
    return secrets.token_hex(n)


def secure_license_key() -> str:
    """Generate a formatted license key: AGMX-XXXX-XXXX-XXXX-XXXX."""
    segments = [secrets.token_hex(2).upper() for _ in range(4)]
    return "AGMX-" + "-".join(segments)
