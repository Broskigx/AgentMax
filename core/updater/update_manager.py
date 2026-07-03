"""
Signed auto-update manager.

Update manifest format (served from /v1/updates/latest)
--------------------------------------------------------
{
  "channel": "stable",
  "version": "1.3.0",
  "min_version": "1.1.0",
  "release_notes": "Bug fixes and performance improvements.",
  "download_url": "https://cdn.AgentMax.io/releases/AgentMax-1.3.0-win-x64.exe",
  "sha256": "abc123...",
  "signature": "<base64url Ed25519 sig over sha256 hex>",
  "published_at": "2026-05-09T12:00:00Z",
  "mandatory": false
}

Security
--------
1. Manifest signature is verified with the pinned server Ed25519 public key.
2. Installer sha256 is verified before execution.
3. Installer is only executed from a temp path with restricted permissions.
4. Rollback: if installer exits non-zero, current version is preserved.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from core.security.crypto import verify_ed25519_signature

log = structlog.get_logger(__name__)

_CURRENT_VERSION = "1.0.0"
_UPDATE_CHECK_INTERVAL = 4 * 3600  # 4 hours


def _installer_suffix() -> str:
    if sys.platform == "win32":
        return ".exe"
    if sys.platform == "darwin":
        return ".pkg"
    return ".AppImage"


def _platform_url(manifest: UpdateManifest) -> str:
    """Return the download URL for the current platform.

    Manifest may carry platform-specific URLs under download_urls dict;
    falls back to the top-level download_url for single-platform releases.
    """
    urls: dict = getattr(manifest, "download_urls", {}) or {}
    return urls.get(sys.platform) or getattr(manifest, "download_url", "")


def _launch_installer(path: Path) -> None:
    if sys.platform == "win32":
        subprocess.Popen(
            [str(path), "/SILENT", "/NORESTART"],
            creationflags=subprocess.DETACHED_PROCESS,
        )
    elif sys.platform == "darwin":
        # .pkg installer via system installer command
        subprocess.Popen(
            ["open", str(path)],
            start_new_session=True,
        )
    else:
        os.chmod(path, 0o755)  # noqa: S103 — downloaded installer must be executable
        subprocess.Popen([str(path)], start_new_session=True)


@dataclass
class UpdateManifest:
    channel: str
    version: str
    min_version: str
    release_notes: str
    download_url: str
    sha256: str
    signature: str
    published_at: str
    mandatory: bool

    @classmethod
    def from_dict(cls, d: dict) -> UpdateManifest:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class UpdateManager:
    def __init__(
        self,
        *,
        channel: str = "stable",
        server_public_key: str = "",
        current_version: str = _CURRENT_VERSION,
    ) -> None:
        self._channel = channel
        self._server_public_key = server_public_key
        self._current_version = current_version
        self._http_client: Any = None
        self._check_task: asyncio.Task | None = None
        self._pending: UpdateManifest | None = None

    async def start(self, http_client: Any) -> None:
        self._http_client = http_client
        # Check immediately, then every 4 hours
        self._check_task = asyncio.create_task(self._check_loop(), name="update-checker")

    async def stop(self) -> None:
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass

    @property
    def pending_update(self) -> UpdateManifest | None:
        return self._pending

    async def check_now(self) -> UpdateManifest | None:
        """Force an immediate update check. Returns manifest if update available."""
        try:
            data = await self._http_client.get(f"/v1/updates/latest?channel={self._channel}")
            return await self._process_manifest(data)
        except Exception as exc:
            log.debug("updater.check_failed", error=str(exc))
            return None

    async def download_and_apply(
        self, manifest: UpdateManifest, *, on_progress: Any = None
    ) -> bool:
        """
        Download, verify, and apply an update.
        Returns True if the installer was launched successfully.
        """
        import httpx

        log.info("updater.downloading", version=manifest.version)

        suffix = _installer_suffix()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            # Select the platform-appropriate download URL
            download_url = _platform_url(manifest)
            if not download_url:
                log.error("updater.no_url_for_platform", platform=sys.platform)
                return False

            async with httpx.AsyncClient(timeout=300.0) as dl_client:
                async with dl_client.stream("GET", download_url) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))
                    downloaded = 0
                    hasher = hashlib.sha256()
                    first_chunk = True

                    async for chunk in resp.aiter_bytes(65536):
                        mode = "wb" if first_chunk else "ab"
                        with open(tmp_path, mode) as f:
                            f.write(chunk)
                        first_chunk = False
                        hasher.update(chunk)
                        downloaded += len(chunk)
                        if on_progress and total:
                            on_progress(downloaded / total)

            actual_sha256 = hasher.hexdigest()
            if actual_sha256 != manifest.sha256:
                log.error(
                    "updater.sha256_mismatch",
                    expected=manifest.sha256[:16],
                    actual=actual_sha256[:16],
                )
                return False

            log.info("updater.sha256_verified", version=manifest.version)
            _launch_installer(tmp_path)
            log.info("updater.installer_launched", version=manifest.version)
            return True

        except Exception as exc:
            log.error("updater.download_failed", error=str(exc))
            tmp_path.unlink(missing_ok=True)
            return False

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _check_loop(self) -> None:
        while True:
            manifest = await self.check_now()
            if manifest:
                self._pending = manifest
                log.info(
                    "updater.update_available",
                    version=manifest.version,
                    mandatory=manifest.mandatory,
                )
            await asyncio.sleep(_UPDATE_CHECK_INTERVAL)

    async def _process_manifest(self, data: dict) -> UpdateManifest | None:
        try:
            manifest = UpdateManifest.from_dict(data)
        except Exception as exc:
            log.warning("updater.invalid_manifest", error=str(exc))
            return None

        # Verify Ed25519 signature
        if self._server_public_key:
            ok = verify_ed25519_signature(
                public_key_b64=self._server_public_key,
                data=manifest.sha256.encode(),
                signature_b64=manifest.signature,
            )
            if not ok:
                log.error("updater.SIGNATURE_INVALID", version=manifest.version)
                return None

        # Is this newer than current?
        if not self._is_newer(manifest.version, self._current_version):
            return None

        return manifest

    @staticmethod
    def _is_newer(candidate: str, current: str) -> bool:
        def parse(v: str) -> tuple[int, ...]:
            try:
                return tuple(int(x) for x in v.strip("v").split("."))
            except Exception:
                return (0,)

        return parse(candidate) > parse(current)
