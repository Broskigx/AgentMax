"""
AgentMax Auto-Update System

Delta updates system that only downloads changed files,
reducing update size and time significantly.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import aiohttp
import structlog

log = structlog.get_logger(__name__)


class UpdateChannel(str, Enum):
    """Update channels"""

    STABLE = "stable"
    BETA = "beta"
    DEV = "dev"


class UpdateStatus(str, Enum):
    """Update installation status"""

    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    READY = "ready"
    INSTALLING = "installing"
    INSTALLED = "installed"
    FAILED = "failed"


@dataclass
class UpdateManifest:
    """Manifest for available updates"""

    version: str
    release_date: datetime
    channel: UpdateChannel

    delta_updates: dict[str, dict[str, str]] = field(default_factory=dict)
    # {"windows": {"from_version": "1.0.0", "patch": "url", "size": 123456}}

    full_updates: dict[str, dict[str, str]] = field(default_factory=dict)
    # {"windows": {"url": "url", "size": 123456, "sha256": "hash"}}

    changelog: list[str] = field(default_factory=list)
    breaking_changes: list[str] = field(default_factory=list)

    min_system_version: dict[str, str] = field(default_factory=dict)
    # {"windows": "10", "macos": "11"}


@dataclass
class UpdateProgress:
    """Progress of update download/install"""

    status: UpdateStatus
    progress_percent: float = 0.0
    bytes_downloaded: int = 0
    total_bytes: int = 0
    current_file: str | None = None
    error: str | None = None
    estimated_time_remaining: int | None = None  # seconds


@dataclass
class UpdateInfo:
    """Information about an available update"""

    current_version: str
    new_version: str
    update_type: str  # "full" or "delta"
    update_size: int
    release_notes: str
    is_mandatory: bool = False
    auto_update_enabled: bool = True


class DeltaPatcher:
    """Handles delta patch application"""

    @staticmethod
    async def apply_delta(
        old_file: str,
        delta_file: str,
        output_file: str,
        progress_callback: Callable[[float], None] | None = None,
    ) -> bool:
        """Apply a bsdiff delta patch to a file"""

        try:
            import subprocess

            # Use bspatch (bsdiff companion)
            result = subprocess.run(
                ["bspatch", old_file, output_file, delta_file], capture_output=True, text=True
            )

            if result.returncode == 0:
                if progress_callback:
                    progress_callback(100.0)
                return True
            else:
                log.error("delta_patch_failed", error=result.stderr)
                return False

        except Exception as e:
            log.error("delta_patch_error", error=str(e))
            return False

    @staticmethod
    async def create_delta(old_file: str, new_file: str, delta_file: str) -> bool:
        """Create a bsdiff delta between two files"""

        try:
            import subprocess

            result = subprocess.run(
                ["bsdiff", old_file, new_file, delta_file], capture_output=True, text=True
            )

            return result.returncode == 0

        except Exception as e:
            log.error("delta_creation_failed", error=str(e))
            return False


class UpdateDownloader:
    """Handles downloading updates"""

    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        chunk_size: int = 1024 * 1024,  # 1MB chunks
    ):
        self.session = session or aiohttp.ClientSession()
        self.chunk_size = chunk_size

    async def download_file(
        self, url: str, dest_path: str, progress_callback: Callable[[int, int], None] | None = None
    ) -> bool:
        """Download a file with progress tracking"""

        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    log.error("download_failed", status=response.status, url=url)
                    return False

                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0

                with open(dest_path, "wb") as f:
                    async for chunk in response.content.iter_chunked(self.chunk_size):
                        f.write(chunk)
                        downloaded += len(chunk)

                        if progress_callback and total_size > 0:
                            progress_callback(downloaded, total_size)

                return True

        except Exception as e:
            log.error("download_error", error=str(e))
            return False

    async def verify_checksum(
        self, file_path: str, expected_hash: str, algorithm: str = "sha256"
    ) -> bool:
        """Verify file checksum"""

        hash_obj = hashlib.new(algorithm)

        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                hash_obj.update(chunk)

        actual_hash = hash_obj.hexdigest()

        if actual_hash != expected_hash:
            log.error("checksum_mismatch", expected=expected_hash, actual=actual_hash)
            return False

        return True


class AutoUpdateManager:
    """
    Main auto-update manager with delta support.
    Minimizes download size by using incremental updates.
    """

    def __init__(
        self, update_server_url: str, app_id: str, current_version: str, storage_path: str
    ):
        self.update_server_url = update_server_url.rstrip("/")
        self.app_id = app_id
        self.current_version = current_version
        self.storage_path = storage_path

        self.downloader = UpdateDownloader()

        self._current_update: UpdateInfo | None = None
        self._update_manifest: UpdateManifest | None = None
        self._progress_callbacks: list[Callable[[UpdateProgress], None]] = []

        # Ensure storage directory exists
        os.makedirs(storage_path, exist_ok=True)

    def add_progress_callback(self, callback: Callable[[UpdateProgress], None]):
        """Add callback for update progress"""
        self._progress_callbacks.append(callback)

    async def check_for_updates(
        self, channel: UpdateChannel = UpdateChannel.STABLE
    ) -> UpdateInfo | None:
        """Check if updates are available"""

        try:
            # Fetch update manifest
            manifest_url = f"{self.update_server_url}/updates/{self.app_id}/manifest.json"

            async with self.session.get(
                manifest_url, params={"channel": channel.value}
            ) as response:
                if response.status != 200:
                    return None

                data = await response.json()

                manifest = UpdateManifest(
                    version=data["version"],
                    release_date=datetime.fromisoformat(data["release_date"]),
                    channel=UpdateChannel(data["channel"]),
                    delta_updates=data.get("delta_updates", {}),
                    full_updates=data.get("full_updates", {}),
                    changelog=data.get("changelog", []),
                    breaking_changes=data.get("breaking_changes", []),
                    min_system_version=data.get("min_system_version", {}),
                )

                self._update_manifest = manifest

                # Compare versions
                if self._is_newer_version(manifest.version, self.current_version):
                    # Determine update type and size
                    update_type, update_size = self._calculate_update_size(manifest, channel)

                    update_info = UpdateInfo(
                        current_version=self.current_version,
                        new_version=manifest.version,
                        update_type=update_type,
                        update_size=update_size,
                        release_notes="\n".join(manifest.changelog[:5]),
                        is_mandatory=data.get("mandatory", False),
                    )

                    self._current_update = update_info

                    log.info(
                        "update_available",
                        current=self.current_version,
                        new=manifest.version,
                        type=update_type,
                        size=update_size,
                    )

                    return update_info

                return None

        except Exception as e:
            log.error("update_check_failed", error=str(e))
            return None

    async def download_update(self, install_callback: Callable[[str], bool] | None = None) -> bool:
        """Download and apply the update"""

        if not self._current_update or not self._update_manifest:
            log.error("no_update_available")
            return False

        try:
            # Report downloading status
            self._report_progress(
                UpdateProgress(status=UpdateStatus.DOWNLOADING, progress_percent=0.0)
            )

            manifest = self._update_manifest

            # Determine if we can use delta or need full update
            platform = self._get_current_platform()

            # Try delta first
            if platform in manifest.delta_updates:
                delta_info = manifest.delta_updates[platform]
                from_version = delta_info.get("from_version")

                if from_version and from_version != self.current_version:
                    # Check if we have the old version cached
                    old_file = self._get_cached_version(from_version)

                    if old_file:
                        success = await self._apply_delta_update(platform, old_file, delta_info)
                        if success:
                            return True

            # Fall back to full update
            if platform in manifest.full_updates:
                return await self._apply_full_update(platform, manifest.full_updates[platform])

            return False

        except Exception as e:
            log.error("update_download_failed", error=str(e))
            self._report_progress(UpdateProgress(status=UpdateStatus.FAILED, error=str(e)))
            return False

    async def _apply_delta_update(
        self, platform: str, old_file_path: str, delta_info: dict[str, str]
    ) -> bool:
        """Apply delta update"""

        self._report_progress(
            UpdateProgress(
                status=UpdateStatus.DOWNLOADING,
                progress_percent=0.0,
                current_file=f"delta-{platform}.bsdiff",
            )
        )

        # Download delta patch
        delta_path = os.path.join(self.storage_path, f"update-{platform}.bsdiff")

        success = await self.downloader.download_file(
            delta_info["patch"],
            delta_path,
            lambda d, t: self._report_progress(
                UpdateProgress(
                    status=UpdateStatus.DOWNLOADING,
                    progress_percent=(d / t) * 50 if t > 0 else 0,
                    bytes_downloaded=d,
                    total_bytes=t,
                    current_file=f"delta-{platform}.bsdiff",
                )
            ),
        )

        if not success:
            return False

        # Verify delta
        if "patch_sha256" in delta_info:
            if not await self.downloader.verify_checksum(delta_path, delta_info["patch_sha256"]):
                return False

        # Download new version header if needed
        new_header_path = os.path.join(self.storage_path, "new-header")
        if "header_url" in delta_info:
            await self.downloader.download_file(delta_info["header_url"], new_header_path)

        # Apply patch
        self._report_progress(UpdateProgress(status=UpdateStatus.VERIFYING, progress_percent=75.0))

        output_path = os.path.join(self.storage_path, f"AgentMax-{platform}-updated")

        success = await DeltaPatcher.apply_delta(old_file_path, delta_path, output_path)

        if not success:
            return False

        # Verify patched file
        if "sha256" in delta_info:
            if not await self.downloader.verify_checksum(output_path, delta_info["sha256"]):
                return False

        # Install
        self._report_progress(UpdateProgress(status=UpdateStatus.INSTALLING, progress_percent=90.0))

        return True

    async def _apply_full_update(self, platform: str, update_info: dict[str, str]) -> bool:
        """Apply full update download"""

        url = update_info["url"]
        expected_hash = update_info["sha256"]

        dest_path = os.path.join(
            self.storage_path, f"AgentMax-{platform}-full.{self._get_extension(platform)}"
        )

        # Download
        success = await self.downloader.download_file(
            url,
            dest_path,
            lambda d, t: self._report_progress(
                UpdateProgress(
                    status=UpdateStatus.DOWNLOADING,
                    progress_percent=(d / t) * 80 if t > 0 else 0,
                    bytes_downloaded=d,
                    total_bytes=t,
                    current_file=os.path.basename(dest_path),
                )
            ),
        )

        if not success:
            return False

        # Verify
        self._report_progress(UpdateProgress(status=UpdateStatus.VERIFYING, progress_percent=85.0))

        if not await self.downloader.verify_checksum(dest_path, expected_hash):
            return False

        # Install
        self._report_progress(UpdateProgress(status=UpdateStatus.INSTALLING, progress_percent=95.0))

        return True

    async def install_update(self, update_file_path: str) -> bool:
        """Install the downloaded update"""

        try:
            # Different installation based on platform
            platform = self._get_current_platform()

            if platform == "windows":
                return await self._install_windows(update_file_path)
            elif platform == "macos":
                return await self._install_macos(update_file_path)
            elif platform == "linux":
                return await self._install_linux(update_file_path)

            return False

        except Exception as e:
            log.error("install_failed", error=str(e))
            return False

    async def _install_windows(self, update_path: str) -> bool:
        """Install update on Windows"""

        import subprocess

        # Run the installer with silent flag
        result = subprocess.run(
            [update_path, "/S", "/D=C:\\Program Files\\AgentMax"], capture_output=True
        )

        return result.returncode == 0

    async def _install_macos(self, update_path: str) -> bool:
        """Install update on macOS"""

        import subprocess

        # Mount DMG and run installer
        result = subprocess.run(
            ["hdiutil", "attach", update_path, "-nobrowse"], capture_output=True, text=True
        )

        if result.returncode != 0:
            return False

        device = result.stdout.strip()

        # Run installer
        install_result = subprocess.run(
            [
                "sudo",
                "-S",
                "installer",
                "-package",
                "/Volumes/AgentMax/AgentMax.pkg",
                "-target",
                "/",
            ],
            capture_output=True,
        )

        # Detach
        subprocess.run(["hdiutil", "detach", device])

        return install_result.returncode == 0

    async def _install_linux(self, update_path: str) -> bool:
        """Install update on Linux"""

        import subprocess

        if update_path.endswith(".deb"):
            result = subprocess.run(["sudo", "dpkg", "-i", update_path], capture_output=True)
        else:
            result = subprocess.run(["sudo", "rpm", "-i", update_path], capture_output=True)

        return result.returncode == 0

    def _is_newer_version(self, new: str, current: str) -> bool:
        """Compare semantic versions"""

        def parse_version(v: str) -> tuple:
            parts = v.lstrip("v").split(".")
            return tuple(int(p) for p in parts if p.isdigit())

        return parse_version(new) > parse_version(current)

    def _calculate_update_size(
        self, manifest: UpdateManifest, channel: UpdateChannel
    ) -> tuple[str, int]:
        """Calculate the size of update (delta vs full)"""

        platform = self._get_current_platform()

        # Check if delta is available and applicable
        if platform in manifest.delta_updates:
            delta_info = manifest.delta_updates[platform]
            if "from_version" in delta_info:
                return "delta", int(delta_info.get("size", 0))

        # Fall back to full
        if platform in manifest.full_updates:
            return "full", int(manifest.full_updates[platform].get("size", 0))

        return "full", 0

    def _get_current_platform(self) -> str:
        """Get current platform"""
        import platform

        system = platform.system().lower()

        if system == "windows":
            return "windows"
        elif system == "darwin":
            return "macos"
        else:
            return "linux"

    def _get_extension(self, platform: str) -> str:
        """Get file extension for platform"""
        extensions = {"windows": "exe", "macos": "dmg", "linux": "deb"}
        return extensions.get(platform, "bin")

    def _get_cached_version(self, version: str) -> str | None:
        """Get path to cached version file"""
        cache_dir = os.path.join(self.storage_path, "cache")
        version_file = os.path.join(cache_dir, f"AgentMax-{version}")

        if os.path.exists(version_file):
            return version_file
        return None

    def _report_progress(self, progress: UpdateProgress):
        """Report progress to all callbacks"""
        for callback in self._progress_callbacks:
            try:
                callback(progress)
            except Exception as e:
                log.warning("callback_error", error=str(e))

    async def cleanup_old_versions(self):
        """Clean up old cached versions"""

        cache_dir = os.path.join(self.storage_path, "cache")

        if not os.path.exists(cache_dir):
            return

        # Keep only last 3 versions
        versions = sorted(os.listdir(cache_dir))

        for version in versions[:-3]:
            path = os.path.join(cache_dir, version)
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)


class UpdateRouter:
    """FastAPI router for update endpoints"""

    def __init__(self, update_manager: AutoUpdateManager):
        self.update_manager = update_manager

    def get_router(self) -> APIRouter:
        router = APIRouter(prefix="/updates", tags=["Updates"])

        @router.get("/check")
        async def check_update(channel: UpdateChannel = UpdateChannel.STABLE):
            """Check for available updates"""

            update_info = await self.update_manager.check_for_updates(channel)

            if not update_info:
                return {
                    "update_available": False,
                    "current_version": self.update_manager.current_version,
                }

            return {
                "update_available": True,
                "current_version": update_info.current_version,
                "new_version": update_info.new_version,
                "update_type": update_info.update_type,
                "update_size": update_info.update_size,
                "release_notes": update_info.release_notes,
                "is_mandatory": update_info.is_mandatory,
            }

        @router.post("/download")
        async def download_update():
            """Download the available update"""

            success = await self.update_manager.download_update()

            return {"success": success, "status": "downloaded" if success else "failed"}

        @router.get("/progress")
        async def get_progress():
            """Get current download progress"""

            # This would need to track progress in a shared state
            return {"progress": 0, "status": "idle"}

        @router.post("/install")
        async def install_update(file_path: str = Body(..., embed=True)):
            """Install downloaded update"""

            success = await self.update_manager.install_update(file_path)

            return {
                "success": success,
                "message": "Update installed successfully" if success else "Installation failed",
            }

        return router


from fastapi import APIRouter, Body
