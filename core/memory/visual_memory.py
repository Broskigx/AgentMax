"""Lightweight visual memory.

This module intentionally avoids OpenCV, Chroma, numpy, OCR engines, and local
ML runtimes. Screen understanding lives in the Rust/Tauri side; Python keeps only
small layout records and optional opaque template metadata.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

import structlog

from core.data_collection.redactor import redact_record

log = structlog.get_logger(__name__)

MAX_TEMPLATES = 500
CLEANUP_INTERVAL_SEC = 300


class VisualMemory:
    def __init__(self, config: Any) -> None:
        self._cache: dict[str, dict] = {}
        self._timestamps: dict[str, float] = {}
        self._max_size: int = int(getattr(config, "visual_cache_size", 64))
        self._ttl_sec: float = float(getattr(config, "visual_cache_ttl_sec", 300.0))
        self._lock = threading.Lock()

        base = Path(
            getattr(
                config,
                "visual_memory_path",
                getattr(config, "chroma_path", ".AGENTMAX_data/visual_templates"),
            )
        )
        self._tmpl_dir = base
        self._index_path = self._tmpl_dir / "index.jsonl"
        self._legacy_index_path = Path(".AGENTMAX_data") / "visual_templates" / "index.jsonl"
        self._max_templates = int(
            getattr(
                config,
                "max_visual_templates",
                getattr(config, "visual_max_templates", MAX_TEMPLATES),
            )
        )
        self._store_images = bool(getattr(config, "store_visual_images", False))

        self._stop_event = threading.Event()
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            name="visual-memory-cleanup",
            daemon=True,
        )
        self._cleanup_thread.start()

    def get(self, app_name: str) -> dict | None:
        key = self._normalize(app_name)
        with self._lock:
            if key not in self._cache:
                return None
            if time.monotonic() - self._timestamps.get(key, 0) > self._ttl_sec:
                self._cache.pop(key, None)
                self._timestamps.pop(key, None)
                return None
            return self._cache[key]

    def put(self, app_name: str, layout: dict) -> None:
        key = self._normalize(app_name)
        safe_layout = redact_record(layout)
        with self._lock:
            if len(self._cache) >= self._max_size and key not in self._cache:
                oldest = min(self._timestamps, key=lambda k: self._timestamps[k])
                self._cache.pop(oldest, None)
                self._timestamps.pop(oldest, None)
            self._cache[key] = safe_layout
            self._timestamps[key] = time.monotonic()

    def invalidate(self, app_name: str) -> None:
        key = self._normalize(app_name)
        with self._lock:
            self._cache.pop(key, None)
            self._timestamps.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._timestamps.clear()

    @property
    def cached_apps(self) -> list[str]:
        with self._lock:
            return list(self._cache.keys())

    @property
    def cache_stats(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._cache), "max": self._max_size}

    def save_template(
        self,
        app_name: str,
        label: str,
        crop_data: Any,
        metadata: dict | None = None,
    ) -> str:
        """Persist privacy-safe template metadata and optionally an image crop."""
        try:
            from PIL import Image

            if self._is_sensitive_context(app_name, label, metadata):
                log.warning("visual_memory.sensitive_template_blocked", app=app_name, label=label)
                return ""
            safe_metadata = redact_record(metadata or {})
            self._tmpl_dir.mkdir(parents=True, exist_ok=True)
            bounds: list[int] | None = None
            source = "unknown"
            img: Any | None = None
            if isinstance(crop_data, Image.Image):
                img = crop_data
                bounds = [0, 0, int(img.width), int(img.height)]
                source = "provided_image"
            elif isinstance(crop_data, dict):
                raw_bounds = crop_data.get("bounds")
                source = str(crop_data.get("source") or "metadata")
                if raw_bounds and len(raw_bounds) == 4:
                    bounds = [int(value) for value in raw_bounds]
                if self._store_images and bounds:
                    screenshot = self._capture_screenshot()
                    if screenshot:
                        x, y, w, h = bounds
                        img = screenshot.crop((x, y, x + w, y + h))
            else:
                log.warning("visual_memory.save_unknown_type", type=type(crop_data).__name__)
                return ""

            if not bounds:
                log.warning("visual_memory.save_invalid_bounds", crop_data=str(crop_data)[:200])
                return ""
            descriptor = {
                "bounds": bounds,
                "source": source,
                "metadata": safe_metadata,
            }
            tid = self._template_id(app_name, label, descriptor)
            filepath: Path | None = None
            perceptual_hash = self._image_hash(img) if img is not None else ""
            if self._store_images and img is not None:
                filename = (
                    f"{self._normalize(app_name)}__{self._normalize(label)}__{tid}.png"
                )
                filepath = self._tmpl_dir / filename
                img.save(filepath, format="PNG")

            entry = {
                "id": tid,
                "app": self._normalize(app_name),
                "label": self._normalize(label),
                "bounds": bounds,
                "source": source,
                "perceptual_hash": perceptual_hash,
                "path": str(filepath) if filepath else None,
                "image_stored": bool(filepath),
                "saved_at": time.time(),
                "metadata": safe_metadata,
            }
            with self._index_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=True) + "\n")

            self._trim_template_store()
            log.info("visual_memory.template_saved", tid=tid, label=label, app=app_name)
            return tid

        except Exception as exc:
            log.warning("visual_memory.save_error", error=str(exc))
            return ""

    @staticmethod
    def _image_hash(image: Any | None) -> str:
        if image is None:
            return ""
        try:
            grayscale = image.convert("L").resize((8, 8))
            pixels = list(grayscale.getdata())
            average = sum(pixels) / max(1, len(pixels))
            return "".join("1" if value >= average else "0" for value in pixels)
        except Exception:
            return ""

    @staticmethod
    def _is_sensitive_context(
        app_name: str,
        label: str,
        metadata: dict | None,
    ) -> bool:
        text = " ".join(
            [
                str(app_name),
                str(label),
                json.dumps(metadata or {}, ensure_ascii=True),
            ]
        ).lower()
        return any(
            term in text
            for term in (
                "password",
                "passwd",
                "credential",
                "login",
                "sign in",
                "token",
                "secret",
                "api key",
                "authentication",
                "2fa",
                "one-time code",
            )
        )

    def _capture_screenshot(self) -> Any | None:
        """Capture a screenshot for template creation."""
        try:
            from PIL import ImageGrab

            return ImageGrab.grab(all_screens=False)
        except Exception:
            return None

    def match_template(
        self,
        screenshot_data: Any,
        label: str | None = None,
        app_name: str | None = None,
        threshold: float = 0.82,
    ) -> tuple[int, int, int, int, float] | None:
        """
        Find a previously saved template in the current screenshot.

        Uses normalized cross-correlation via numpy (fast) if available,
        with a PIL-only fallback (slower but dependency-free).
        Templates are stored as cropped PNG files on disk.

        Args:
            screenshot_data: PIL Image or base64 string of current screen
            label: Optional template label to match (if None, tries all)
            app_name: Optional app context to narrow search
            threshold: Minimum confidence (0.0-1.0) to consider a match

        Returns:
            (x, y, w, h, confidence) tuple, or None if no match found.
        """
        if screenshot_data is None:
            return None

        # Decode screenshot to PIL Image if needed
        from PIL import Image

        if isinstance(screenshot_data, str):
            try:
                import base64
                import io

                img_bytes = base64.b64decode(screenshot_data)
                screenshot = Image.open(io.BytesIO(img_bytes))
            except Exception:
                return None
        else:
            screenshot = screenshot_data

        # Convert to RGB for consistent processing
        if screenshot.mode != "RGB":
            try:
                screenshot = screenshot.convert("RGB")
            except Exception:
                return None

        # Find candidate templates
        candidates = self._find_candidate_templates(label, app_name)
        if not candidates:
            return None

        best_match: tuple[int, int, int, int, float] | None = None

        for tmpl_path, _tmpl_label in candidates:
            try:
                template = Image.open(tmpl_path).convert("RGB")
                tw, th = template.size

                # Skip if template is larger than screenshot
                if tw > screenshot.width or th > screenshot.height:
                    continue

                ncc_result = self._match_template_ncc(screenshot, template)

                if ncc_result is not None:
                    conf, match_x, match_y = ncc_result
                    if conf >= threshold:
                        if best_match is None or conf > best_match[4]:
                            best_match = (match_x, match_y, tw, th, conf)
            except Exception as exc:
                log.debug("visual_memory.match_error", path=str(tmpl_path), error=str(exc))
                continue

        return best_match

    def _find_candidate_templates(
        self, label: str | None, app_name: str | None
    ) -> list[tuple[Path, str]]:
        """Find template files matching the given label and/or app context."""
        if not self._tmpl_dir.exists():
            return []

        candidates: list[tuple[Path, str]] = []
        try:
            for f in self._tmpl_dir.glob("*.png"):
                # Filename format: {app}__{label}__{id}.png
                parts = f.stem.split("__")
                f_app = parts[0] if len(parts) >= 1 else ""
                f_label = parts[1] if len(parts) >= 2 else ""

                if app_name and f_app and f_app != app_name.lower().strip():
                    continue
                if label and f_label and label.lower().strip() not in f_label.lower():
                    continue

                candidates.append((f, f_label))
        except Exception as exc:
            log.debug("visual_memory.find_templates_error", error=str(exc))

        return candidates

    def _match_template_ncc(self, screenshot: Any, template: Any) -> tuple[float, int, int] | None:
        """
        Compute normalized cross-correlation between screenshot and template.

        Uses numpy for fast FFT-based correlation if available.
        Falls back to a sampling-based pixel comparison.

        Returns:
            (confidence 0.0-1.0, match_x, match_y) tuple, or None on failure.
        """
        tw, th = template.size

        # Try numpy path (fast)
        try:
            import numpy as np
            from numpy.fft import fft2, ifft2

            # Convert to grayscale arrays
            s_gray = np.array(screenshot.convert("L"), dtype=np.float64)
            t_gray = np.array(template.convert("L"), dtype=np.float64)

            # Normalize template
            t_mean = t_gray.mean()
            t_std = t_gray.std()
            if t_std < 0.01:
                return (0.0, 0, 0)
            t_norm = (t_gray - t_mean) / t_std

            # Phase correlation via FFT
            s_fft = fft2(s_gray)
            t_fft = fft2(t_norm, s=s_gray.shape)
            corr = ifft2(s_fft * t_fft.conjugate())
            corr = np.abs(corr)

            # Find peak correlation and its position
            max_idx = int(corr.argmax())
            max_y = max_idx // corr.shape[1]
            max_x = max_idx % corr.shape[1]

            max_corr = float(corr[max_y, max_x])
            # Normalize by template size
            norm_corr = max_corr / (tw * th)
            # Scale to 0-1 range (typical NCC max is ~template_size)
            confidence = min(max(norm_corr / 3.0, 0.0), 1.0)

            return (round(confidence, 4), max_x, max_y)

        except ImportError:
            # NumPy not available — use PIL-only fallback
            return self._match_template_pil(screenshot, template)
        except Exception as exc:
            log.debug("visual_memory.ncc_error", error=str(exc))
            return None

    def _match_template_pil(self, screenshot: Any, template: Any) -> tuple[float, int, int] | None:
        """
        PIL-only template matching using sampling and pixel comparison.

        Samples the screenshot at multiple offsets and compares pixel values
        to find the best match. This is O(n*m*p*q) in the worst case, so we
        use multi-resolution: first check at stride = 20, then refine.

        Returns:
            (confidence 0.0-1.0, best_x, best_y) tuple, or None on failure.
        """
        from PIL import ImageChops, ImageStat

        tw, th = template.size
        sh, sw = screenshot.size[1], screenshot.size[0]

        try:
            # Coarse scan with stride
            stride = max(20, tw // 4, th // 4)
            best_diff = float("inf")
            best_x = 0
            best_y = 0

            for y in range(0, sh - th, stride):
                for x in range(0, sw - tw, stride):
                    try:
                        region = screenshot.crop((x, y, x + tw, y + th))
                        diff = ImageChops.difference(region, template)
                        stat = ImageStat.Stat(diff)
                        mean_diff = sum(stat.mean) / len(stat.mean) if stat.mean else 255.0

                        if mean_diff < best_diff:
                            best_diff = mean_diff
                            best_x = x
                            best_y = y
                    except Exception:
                        continue

            if best_diff == float("inf"):
                return None

            # Convert mean pixel diff to confidence
            confidence = 1.0 - (best_diff / 255.0)
            return (round(max(confidence, 0.0), 4), best_x, best_y)

        except Exception as exc:
            log.debug("visual_memory.pil_match_error", error=str(exc))
            return None

    def find_template(self, app_name: str, label: str) -> dict | None:
        """
        Find a saved template by app name and label.

        Args:
            app_name: Application name (e.g. "chrome")
            label: Element label (e.g. "search_button")

        Returns:
            Template metadata dict with 'bounds' key, or None if not found.
        """
        candidates = self._find_candidate_templates(label, app_name)
        if not candidates:
            return None

        # Return bounds from the first matching index entry
        try:
            index_path = (
                self._index_path
                if self._index_path.exists()
                else self._legacy_index_path
            )
            if index_path.exists():
                for line in index_path.read_text(encoding="utf-8").splitlines():
                    try:
                        entry = json.loads(line)
                        app_norm = self._normalize(app_name)
                        label_norm = label.lower().strip()
                        e_app = entry.get("app", "").lower().strip()
                        e_label = entry.get("label", "").lower().strip()
                        if e_app == app_norm and label_norm in e_label:
                            return entry
                    except (json.JSONDecodeError, ValueError):
                        continue
        except Exception:
            pass

        return None

    def crop_from_screenshot(
        self, screenshot_data: Any, bounds: tuple[int, int, int, int]
    ) -> dict[str, Any]:
        """Return an opaque crop descriptor for Rust-side template handling."""
        x, y, w, h = bounds
        return {"bounds": [int(x), int(y), int(w), int(h)], "source": "rust_vision_bridge"}

    def shutdown(self) -> None:
        self._stop_event.set()
        self._cleanup_thread.join(timeout=1.0)

    def _cleanup_loop(self) -> None:
        while not self._stop_event.wait(timeout=CLEANUP_INTERVAL_SEC):
            try:
                self._evict_expired_cache()
                self._trim_template_store()
            except Exception as exc:
                log.warning("visual_memory.cleanup_error", error=str(exc))

    def _evict_expired_cache(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [key for key, ts in self._timestamps.items() if now - ts > self._ttl_sec]
            for key in expired:
                self._cache.pop(key, None)
                self._timestamps.pop(key, None)
        if expired:
            log.debug("visual_memory.cache_evicted", count=len(expired))

    def _trim_template_store(self) -> None:
        if not self._index_path.exists():
            return
        try:
            lines = self._index_path.read_text(encoding="utf-8").splitlines()
            if len(lines) <= self._max_templates:
                return
            trimmed = lines[-self._max_templates :]
            self._index_path.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
            log.info("visual_memory.templates_trimmed", removed=len(lines) - len(trimmed))
        except Exception as exc:
            log.warning("visual_memory.trim_error", error=str(exc))

    @staticmethod
    def _template_id(app_name: str, label: str, crop_data: Any) -> str:
        raw = json.dumps(
            {"app": app_name, "label": label, "crop": str(crop_data), "ts": time.time()},
            sort_keys=True,
            ensure_ascii=True,
        ).encode("utf-8", errors="ignore")
        return f"tmpl_{hashlib.sha1(raw, usedforsecurity=False).hexdigest()[:16]}"

    @staticmethod
    def _normalize(name: str) -> str:
        return name.lower().strip()[:64]
