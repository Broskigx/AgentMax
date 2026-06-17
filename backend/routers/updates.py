"""
/v1/updates -- signed auto-update manifest endpoint.

The manifest is verified by the client using the pinned Ed25519 public key
before any installer is downloaded or executed. Fields:
- version / min_version: semver strings
- sha256: hex digest of the installer binary
- signature: base64url Ed25519 sig over the sha256 hex string
- mandatory: if true the client must update before proceeding
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/v1/updates", tags=["updates"])

# Replace these values when publishing a real release.
# The signature must be generated offline with the server Ed25519 private key:
#   echo -n "<sha256_hex>" | python -c "
#     import sys, os; from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
#     key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(os.environ['ED25519_PRIVATE_KEY_HEX']))
#     import base64; print(base64.urlsafe_b64encode(key.sign(sys.stdin.buffer.read())).decode())
#   "
_MANIFEST: dict = {
    "channel": "beta",
    # Must match the shipped app version (ui/src-tauri/tauri.conf.json). Keeping
    # these aligned prevents the (currently unwired) updater from ever telling a
    # beta client it is below min_version and forcing an update.
    "version": "0.1.1",
    "min_version": "0.1.1",
    "release_notes": "Closed beta build.",
    # Legacy single-URL field kept for old clients.
    # New clients use download_urls keyed by sys.platform value.
    "download_url": "",
    "download_urls": {
        "win32": "",  # Windows .exe installer
        "darwin": "",  # macOS  .pkg installer
        "linux": "",  # Linux  .AppImage
    },
    "sha256": "",
    "signature": "",
    "published_at": "2026-05-09T00:00:00Z",
    "mandatory": False,
}


@router.get("/latest")
async def latest_update() -> dict:
    return _MANIFEST
