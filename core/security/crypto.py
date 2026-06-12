"""
Client-side cryptography.

Responsibilities
----------------
- Derive and store a machine-bound AES-256 key for encrypting tokens at rest
- X25519 ECDH ephemeral key generation (for challenge-response)
- HMAC-SHA256 response computation
- Ed25519 signature verification (offline token, update manifests)
- All sensitive material is held in memory only; never logged
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import platform
import sys
import uuid
from typing import Final

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# ── Machine-derived AES key ───────────────────────────────────────────────────
# Key = HKDF(SHA256(machine_entropy), salt=b"AgentMax-client-v1", length=32)
# Machine entropy = SHA256(MAC | platform.node() | sys.platform)
# This key is NOT the same as the server's AES key.
# It's used solely to encrypt tokens stored on disk.

_CLIENT_SALT: Final = b"AgentMax-client-key-v1"
_CLIENT_INFO: Final = b"AgentMax-token-encryption-v1"


def _machine_entropy() -> bytes:
    parts = [
        hex(uuid.getnode()),
        platform.node(),
        sys.platform,
        platform.machine(),
    ]
    raw = "|".join(parts).encode()
    return hashlib.sha256(raw).digest()


def _derive_client_key() -> bytes:
    """Derive a machine-bound 256-bit AES key deterministically."""
    hkdf = HKDF(
        algorithm=SHA256(),
        length=32,
        salt=_CLIENT_SALT,
        info=_CLIENT_INFO,
    )
    return hkdf.derive(_machine_entropy())


# Lazy singleton -- derived once per process
_CLIENT_KEY: bytes | None = None


def _get_client_key() -> bytes:
    global _CLIENT_KEY
    if _CLIENT_KEY is None:
        _CLIENT_KEY = _derive_client_key()
    return _CLIENT_KEY


# ── AES-256-GCM helpers ───────────────────────────────────────────────────────


def encrypt_to_disk(plaintext: str) -> bytes:
    """Encrypt a string for storage on disk using the machine-derived key."""
    key = _get_client_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), b"AgentMax-disk")
    return nonce + ct


def decrypt_from_disk(blob: bytes) -> str:
    """Decrypt a blob previously written by encrypt_to_disk()."""
    if len(blob) < 12 + 16:
        raise ValueError("Ciphertext too short")
    key = _get_client_key()
    aesgcm = AESGCM(key)
    nonce, ct = blob[:12], blob[12:]
    return aesgcm.decrypt(nonce, ct, b"AgentMax-disk").decode()


# ── X25519 ECDH for challenge-response ───────────────────────────────────────


class EphemeralX25519:
    """Single-use X25519 keypair for one challenge exchange."""

    def __init__(self) -> None:
        self._private = X25519PrivateKey.generate()
        pub_raw = self._private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.public_key_b64 = base64.urlsafe_b64encode(pub_raw).decode()

    def compute_shared_secret(
        self,
        server_public_b64: str,
        salt_hex: str,
        info: bytes = b"AgentMax-challenge-v1",
    ) -> bytes:
        """
        ECDH + HKDF-SHA256 → 32-byte shared secret.
        salt_hex is the nonce from the server (hex-encoded).
        """
        server_pub_raw = base64.urlsafe_b64decode(server_public_b64 + "==")
        server_pub = X25519PublicKey.from_public_bytes(server_pub_raw)
        raw_shared = self._private.exchange(server_pub)

        hkdf = HKDF(
            algorithm=SHA256(),
            length=32,
            salt=bytes.fromhex(salt_hex),
            info=info,
        )
        return hkdf.derive(raw_shared)


def compute_hmac_response(
    shared_secret: bytes,
    nonce: str,
    machine_fingerprint: str,
    license_key: str,
) -> str:
    """Compute the challenge response: HMAC-SHA256(shared_secret, nonce||mfp||key)."""
    msg = (nonce + machine_fingerprint + license_key).encode()
    return hmac.new(shared_secret, msg, hashlib.sha256).hexdigest()


# ── Ed25519 verification (server pubkey pinning) ──────────────────────────────


def verify_ed25519_signature(
    *,
    public_key_b64: str,
    data: bytes,
    signature_b64: str,
) -> bool:
    """
    Verify a server Ed25519 signature.
    Used for offline token and update manifest verification.
    """
    try:
        pub_raw = base64.urlsafe_b64decode(public_key_b64 + "==")
        pub = Ed25519PublicKey.from_public_bytes(pub_raw)
        sig = base64.urlsafe_b64decode(signature_b64 + "==")
        pub.verify(sig, data)
        return True
    except Exception:
        return False


def decode_offline_token(token: str, server_public_key_b64: str) -> dict | None:
    """
    Decode and verify an offline token (Ed25519-signed JWT).
    Returns payload dict on success, None on any failure.
    Does NOT require network access.
    """
    try:
        import jwt as pyjwt

        pub_raw = base64.urlsafe_b64decode(server_public_key_b64 + "==")
        pub = Ed25519PublicKey.from_public_bytes(pub_raw)
        pub_pem = pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        payload = pyjwt.decode(
            token,
            pub_pem,
            algorithms=["EdDSA"],
            options={"require": ["exp", "sub", "mfp", "pln"]},
        )
        return payload
    except Exception:
        return None
