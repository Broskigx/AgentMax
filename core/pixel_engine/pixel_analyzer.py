"""
Pixel analysis engine — screen capture, diff, OCR, and smart encoding.

Provides the vision backbone for AgentMax:
  - PIL-based screen capture
  - Perceptual hashing for frame deduplication
  - Structural change detection with region tracking
  - Adaptive quality encoding (full frame vs changed regions)
  - OCR with image preprocessing (threshold, contrast, sharpening)
  - Frame statistics and activity monitoring
"""

from __future__ import annotations

import base64
import io
import time
from collections import deque
from dataclasses import dataclass
from hashlib import md5
from typing import Any

import structlog

log = structlog.get_logger(__name__)


@dataclass
class FrameDiff:
    """Result of comparing two consecutive frames."""

    changed_ratio: float = 0.0
    changed_region: tuple[int, int, int, int] | None = None  # (x, y, w, h)
    motion_detected: bool = False
    mean_color_shift: float = 0.0
    timestamp: float = 0.0


@dataclass
class EncodedFrame:
    """A processed frame ready for AI consumption."""

    base64_data: str = ""
    width: int = 0
    height: int = 0
    format: str = "jpeg"
    quality: int = 75
    perceptual_hash: str = ""
    changed_ratio: float = 0.0
    changed_region: tuple[int, int, int, int] | None = None
    ocr_text: str = ""
    timestamp: float = 0.0
    is_duplicate: bool = False


class PixelAnalyzer:
    """
    Pixel analysis engine — real screen capture, diff, OCR, and encoding.

    Capabilities:
      - Screen capture as PIL Image
      - Perceptual hash deduplication
      - Frame diff with region tracking
      - Adaptive base64 encoding (quality tiers)
      - OCR with image preprocessing
      - Screen region extraction
      - Frame statistics
    """

    HISTORY_SIZE = 30
    HASH_SIZE = 16  # Perceptual hash grid (16×16 → 256-bit hash)

    def __init__(self) -> None:
        self._diff_history: deque[FrameDiff] = deque(maxlen=self.HISTORY_SIZE)
        self._last_frame_pil: Any = None
        self._last_hash: str = ""
        self._dedup_skipped = 0
        self._frames_processed = 0
        self._pil_available = False
        self._ocr_available = False
        self._init_backends()
        log.info(
            "pixel_engine.init",
            pil=self._pil_available,
            ocr=self._ocr_available,
        )

    # ── Backend detection ─────────────────────────────────────────────────────

    def _init_backends(self) -> None:
        try:
            from PIL import Image  # noqa: F401

            self._pil_available = True
        except ImportError:
            self._pil_available = False
            log.warning("pixel_engine.pillow_not_installed")

        try:
            import pytesseract  # noqa: F401

            self._ocr_available = True
        except ImportError:
            self._ocr_available = False

    # ── Screen capture ────────────────────────────────────────────────────────

    def capture_screenshot(self) -> Any | None:
        """Capture the full primary screen as a PIL Image. Returns None if PIL is unavailable."""
        if not self._pil_available:
            log.warning("pixel_engine.capture_failed_no_pillow")
            return None
        try:
            from PIL import ImageGrab

            return ImageGrab.grab(all_screens=False)
        except Exception as exc:
            log.error("pixel_engine.capture_error", error=str(exc))
            return None

    def get_screen_dimensions(self) -> tuple[int, int]:
        """Return (width, height) of the primary screen."""
        if not self._pil_available:
            return 1920, 1080
        try:
            from PIL import ImageGrab

            img = ImageGrab.grab(all_screens=False)
            return img.size
        except Exception:
            return 1920, 1080

    # ── Perceptual hashing (fast frame dedup) ─────────────────────────────────

    @staticmethod
    def _average_hash(img: Any, hash_size: int = 16) -> str:
        """
        Compute a perceptual hash (average hash / aHash) of an image.

        The hash is resistant to minor differences (compression artifacts,
        off-by-one pixel shifts) making it ideal for frame deduplication.

        Args:
            img: PIL Image
            hash_size: Grid size (16 → 256-bit hash). Larger = more sensitive.

        Returns:
            Hex string of the hash. Two similar images will have similar hashes.
        """
        try:
            from PIL import Image

            # Convert to grayscale and resize to hash_size × hash_size
            img = img.convert("L").resize((hash_size, hash_size), Image.LANCZOS)
            # Use direct pixel access to avoid Pillow getdata() deprecation (professional future-proofing)
            pixels = [img.getpixel((x, y)) for y in range(hash_size) for x in range(hash_size)]
            avg = sum(pixels) / len(pixels)
            # Build hash: 1 if pixel > avg, 0 otherwise
            bits = "".join("1" if p > avg else "0" for p in pixels)
            # Pad to multiple of 4 for hex
            if len(bits) % 4:
                bits += "0" * (4 - len(bits) % 4)
            return hex(int(bits, 2))[2:]
        except Exception:
            return ""

    def _fast_frame_token(self, img: Any) -> str:
        """Fast perceptual hash of a PIL image for dedup."""
        if img is None:
            return ""
        try:
            return self._average_hash(img, hash_size=self.HASH_SIZE)
        except Exception:
            # Fallback: md5 of thumbnail (slow but reliable)
            try:
                from PIL import Image

                thumb = img.copy()
                thumb.thumbnail((64, 64), Image.LANCZOS)
                return md5(thumb.tobytes(), usedforsecurity=False).hexdigest()[:16]
            except Exception:
                return str(id(img))

    # ── Frame diff (correct pixel-level change detection) ─────────────────────

    def _compute_diff(self, current: Any, previous: Any) -> FrameDiff:
        """
        Compare two PIL Images and compute the structural difference.

        Uses three methods in order of accuracy:
          1. getbbox() — quick check if anything changed
          2. Histogram subtraction — per-channel change magnitude
          3. Pixel counting — exact changed pixel ratio (bounded for speed)

        Returns a FrameDiff with changed area, motion flag, and bounding box.
        """
        from PIL import ImageChops, ImageStat

        ts = time.time()
        w, h = current.size
        total_pixels = w * h

        if previous is None:
            return FrameDiff(changed_ratio=0.0, motion_detected=False, timestamp=ts)

        try:
            # ── Quick check: bounding box of diff ─────────────────────────────
            diff = ImageChops.difference(current.convert("RGB"), previous.convert("RGB"))
            bbox = diff.getbbox()  # Returns (x1, y1, x2, y2) or None

            if bbox is None:
                # Identical frames
                return FrameDiff(changed_ratio=0.0, motion_detected=False, timestamp=ts)

            changed_w = bbox[2] - bbox[0]
            changed_h = bbox[3] - bbox[1]
            bbox_area = changed_w * changed_h

            # ── Estimate changed pixel ratio via histogram ───────────────────
            try:
                stat = ImageStat.Stat(diff)
                # Mean of all channels approximates average pixel difference
                mean_diff = sum(stat.mean) / len(stat.mean) if stat.mean else 0.0
            except Exception:
                mean_diff = 0.0

            # ── Count actual changed pixels (sample-based for speed) ─────────
            # Use the diff image's histogram to count non-zero pixels
            try:
                gray_diff = diff.convert("L")
                hist = gray_diff.histogram()
                # hist[0] = count of pixels with value 0 (unchanged)
                unchanged = hist[0] if len(hist) > 0 else 0
                changed_pixels = total_pixels - unchanged
                changed_ratio = changed_pixels / max(total_pixels, 1)
            except Exception:
                # Fallback: estimate from bounding box
                changed_ratio = bbox_area / max(total_pixels, 1)

            # Normalize mean color shift to 0-1 range
            color_shift = min(mean_diff / 255.0, 1.0)

            # Motion detection: significant change in a reasonably sized area
            motion = changed_ratio > 0.015 or (bbox_area > 500 and changed_ratio > 0.005)

            return FrameDiff(
                changed_ratio=round(changed_ratio, 4),
                changed_region=(bbox[0], bbox[1], changed_w, changed_h),
                motion_detected=motion,
                mean_color_shift=round(color_shift, 4),
                timestamp=ts,
            )
        except Exception as exc:
            log.warning("pixel_engine.diff_error", error=str(exc))
            return FrameDiff(changed_ratio=0.0, motion_detected=False, timestamp=ts)

    # ── Smart encoding pipeline ───────────────────────────────────────────────

    def _encode_image(
        self,
        img: Any,
        quality: int = 75,
        region: tuple[int, int, int, int] | None = None,
        format: str = "JPEG",
    ) -> str:
        """
        Encode a PIL Image (or region) to base64.

        Args:
            img: PIL Image
            quality: JPEG quality 1-95 (lower = smaller file, faster)
            region: Optional (x, y, w, h) to crop before encoding
            format: "JPEG" (small) or "PNG" (lossless, larger)

        Returns:
            Base64-encoded string, or empty string on failure.
        """
        try:
            if region:
                x, y, w, h = region
                img = img.crop((x, y, x + w, y + h))

            buf = io.BytesIO()

            if format.upper() == "JPEG":
                # JPEG requires RGB mode
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(buf, format="JPEG", quality=quality, optimize=True)
            else:
                img.save(buf, format="PNG", optimize=True)

            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception as exc:
            log.warning("pixel_engine.encode_error", format=format, error=str(exc))
            return ""

    def _choose_quality(self, changed_ratio: float) -> int:
        """
        Adaptive quality selection based on how much the screen changed.

        - Static screen (no change): low quality — will be skipped by dedup anyway
        - Small change (< 5%): high quality on changed region only
        - Moderate change (5-20%): medium quality full frame
        - Large change (> 20%): high quality full frame for detailed analysis
        """
        if changed_ratio < 0.001:
            return 60  # Will be deduped
        elif changed_ratio < 0.05:
            return 85  # High quality — small area matters
        elif changed_ratio < 0.20:
            return 75  # Standard quality
        else:
            return 70  # Slightly lower — large area, speed matters

    # ── Public encoding API ───────────────────────────────────────────────────

    def encode_current_frame(self) -> EncodedFrame:
        """
        Capture, diff, dedup, and encode the current screen.

        This is the main encoding pipeline. It:
          1. Captures the screen
          2. Computes perceptual hash for dedup
          3. Compares with previous frame
          4. Encodes at adaptive quality
          5. Runs OCR if available and screen changed

        Returns:
            EncodedFrame with all data the AI needs.
        """
        if not self._pil_available:
            return EncodedFrame(timestamp=time.time())

        img = self.capture_screenshot()
        if img is None:
            return EncodedFrame(timestamp=time.time())

        w, h = img.size
        ts = time.time()

        # ── Perceptual hash dedup ─────────────────────────────────────────────
        current_hash = self._fast_frame_token(img)
        is_dup = bool(self._last_hash) and current_hash == self._last_hash

        # ── Compare with previous frame ───────────────────────────────────────
        diff = self._compute_diff(img, self._last_frame_pil)

        # Update state
        self._last_frame_pil = img
        self._last_hash = current_hash

        if is_dup:
            self._dedup_skipped += 1
            # Return lightweight response — same hash = same frame
            return EncodedFrame(
                width=w,
                height=h,
                perceptual_hash=current_hash,
                changed_ratio=0.0,
                timestamp=ts,
                is_duplicate=True,
                ocr_text="",  # Don't re-run OCR on identical frame
            )

        self._frames_processed += 1
        self._diff_history.append(diff)

        # ── Adaptive encoding ─────────────────────────────────────────────────
        quality = self._choose_quality(diff.changed_ratio)

        # For small changes, encode only the changed region (super efficient)
        encoded_region = None
        if diff.changed_ratio < 0.05 and diff.changed_ratio > 0.001 and diff.changed_region:
            # Encode just the changed area at high quality
            b64 = self._encode_image(img, quality=quality, region=diff.changed_region)
            encoded_region = diff.changed_region
        else:
            # Encode full frame at adaptive quality
            b64 = self._encode_image(img, quality=quality)

        # ── OCR on changed frames ─────────────────────────────────────────────
        ocr_text = ""
        if diff.changed_ratio > 0.005 and self._ocr_available:
            try:
                ocr_text = self._ocr_with_preprocessing(img)
            except Exception:
                pass

        # ── Trim OCR text to reasonable length ────────────────────────────────
        if len(ocr_text) > 2000:
            ocr_text = ocr_text[:2000] + "..."

        return EncodedFrame(
            base64_data=b64,
            width=w,
            height=h,
            quality=quality,
            perceptual_hash=current_hash,
            changed_ratio=diff.changed_ratio,
            changed_region=encoded_region,
            ocr_text=ocr_text,
            timestamp=ts,
            is_duplicate=False,
        )

    def screenshot_base64(self, quality: int = 75) -> str:
        """
        Legacy API: capture screen and return base64-encoded JPEG.

        Args:
            quality: JPEG quality (1-95). Default 75.

        Returns:
            Base64 string, or empty on failure.
        """
        img = self.capture_screenshot()
        if img is None:
            return ""
        return self._encode_image(img, quality=quality)

    # ── OCR with preprocessing ────────────────────────────────────────────────

    def _ocr_with_preprocessing(self, img: Any, lang: str = "spa+eng") -> str:
        """
        Run OCR with image preprocessing for significantly better accuracy.

        Pipeline:
          1. Convert to grayscale
          2. Increase contrast (2x)
          3. Auto-contrast stretch
          4. Sharpen filter
          5. Upscale if image is small
          6. Binarize with adaptive threshold
          7. Run pytesseract

        Args:
            img: PIL Image
            lang: Tesseract language (default spanish + english)

        Returns:
            Extracted text, or empty string on failure.
        """
        if not self._ocr_available:
            return ""
        try:
            import pytesseract
            from PIL import Image, ImageEnhance, ImageFilter, ImageOps

            # Step 1: Convert to grayscale
            gray = img.convert("L")

            # Step 2: Increase contrast (makes text pop)
            enhanced = ImageEnhance.Contrast(gray).enhance(2.0)

            # Step 3: Auto-contrast (stretch histogram)
            enhanced = ImageOps.autocontrast(enhanced, cutoff=3)

            # Step 4: Sharpen (reduces blur in text)
            # NOTE: Pillow renamed UNSHARP_MASK -> UnsharpMask (class) years ago;
            # using the class form keeps us compatible across Pillow versions.
            enhanced = enhanced.filter(ImageFilter.SHARPEN)
            enhanced = enhanced.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

            # Step 5: Upscale if image is small (improves small text)
            w, h = enhanced.size
            if min(w, h) < 800:
                scale = max(1, 1200 // min(w, h))
                if scale > 1:
                    enhanced = enhanced.resize((w * scale, h * scale), Image.LANCZOS)

            # Step 6: Binarize with threshold (clean background)
            # Using Otsu-like threshold via histogram analysis
            hist = enhanced.histogram()
            total = sum(hist)
            if total > 0:
                weighted_sum = sum(i * count for i, count in enumerate(hist))
                threshold = weighted_sum // total
            else:
                threshold = 128

            binary = enhanced.point(lambda x: 255 if x > threshold else 0, mode="1")

            # Step 7: Run Tesseract with config for better accuracy
            custom_config = (
                "--oem 3 --psm 6 "  # LSTM engine + uniform block of text
                "-c tessedit_char_whitelist= "
                "-c tessedit_enable_dict_correction=1 "
                "-c textord_heavy_nr=1 "
            )
            text = pytesseract.image_to_string(binary, lang=lang, config=custom_config)
            return text.strip()
        except ImportError:
            return ""
        except Exception as exc:
            log.warning("pixel_engine.ocr_error", error=str(exc))
            return ""

    def ocr_text(self, lang: str = "spa+eng") -> str:
        """
        Extract text from the current screen using preprocessed OCR.

        Args:
            lang: Tesseract language code(s). Default is Spanish + English.

        Returns:
            Extracted text string, or empty on failure.
        """
        if not self._ocr_available:
            return ""
        img = self.capture_screenshot()
        if img is None:
            return ""
        return self._ocr_with_preprocessing(img, lang=lang)

    def _ocr_with_bounds(
        self,
        img: Any,
        lang: str = "spa+eng",
        min_conf: int = 30,
    ) -> list[dict[str, Any]]:
        """
        Run OCR returning per-word bounding boxes (needed for click targeting).

        Pipeline mirrors ``_ocr_with_preprocessing`` (grayscale + contrast +
        sharpen + binarize) but uses ``pytesseract.image_to_data`` so we keep
        word-level (text, x, y, w, h, conf) tuples. The coordinates are
        rescaled back to the ORIGINAL image so callers can use them to click
        on the real screen.

        Args:
            img: PIL Image (full screen or region).
            lang: Tesseract language(s).
            min_conf: Drop OCR words whose Tesseract confidence < this (0-100).

        Returns:
            ``[{"text", "x", "y", "w", "h", "conf"}]`` in original-image
            pixel coordinates. Empty list on failure.
        """
        if not self._ocr_available:
            return []
        try:
            import pytesseract
            from PIL import Image, ImageEnhance, ImageFilter, ImageOps

            gray = img.convert("L")
            enhanced = ImageEnhance.Contrast(gray).enhance(2.0)
            enhanced = ImageOps.autocontrast(enhanced, cutoff=3)
            enhanced = enhanced.filter(ImageFilter.SHARPEN)
            enhanced = enhanced.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

            # Track the upscale factor so we can undo it on the bboxes.
            w0, h0 = enhanced.size
            scale = 1
            if min(w0, h0) < 800:
                scale = max(1, 1200 // min(w0, h0))
                if scale > 1:
                    enhanced = enhanced.resize((w0 * scale, h0 * scale), Image.LANCZOS)

            # Otsu-like threshold
            hist = enhanced.histogram()
            total = sum(hist)
            threshold = (sum(i * c for i, c in enumerate(hist)) // total) if total else 128
            binary = enhanced.point(lambda x: 255 if x > threshold else 0, mode="1")

            data = pytesseract.image_to_data(
                binary,
                lang=lang,
                config="--oem 3 --psm 6",
                output_type=pytesseract.Output.DICT,
            )

            out: list[dict[str, Any]] = []
            n = len(data.get("text", []))
            for i in range(n):
                txt = (data["text"][i] or "").strip()
                if not txt:
                    continue
                try:
                    conf = int(float(data["conf"][i]))
                except (ValueError, TypeError):
                    conf = -1
                if conf < min_conf:
                    continue
                # Undo upscale to keep coords in original-image space.
                x = int(int(data["left"][i]) / scale)
                y = int(int(data["top"][i]) / scale)
                w = int(int(data["width"][i]) / scale)
                h = int(int(data["height"][i]) / scale)
                out.append(
                    {
                        "text": txt,
                        "x": x,
                        "y": y,
                        "w": w,
                        "h": h,
                        "bbox": (x, y, w, h),
                        "conf": conf / 100.0,
                    }
                )
            return out
        except Exception as exc:  # noqa: BLE001
            log.warning("pixel_engine.ocr_bounds_error", error=str(exc))
            return []

    def ocr_with_bounds(self, lang: str = "spa+eng", min_conf: int = 30) -> list[dict[str, Any]]:
        """Capture the screen and return word-level OCR with bounding boxes."""
        if not self._ocr_available:
            return []
        img = self.capture_screenshot()
        if img is None:
            return []
        return self._ocr_with_bounds(img, lang=lang, min_conf=min_conf)

    def ocr_regions(
        self, regions: list[tuple[int, int, int, int]], lang: str = "spa+eng"
    ) -> list[str]:
        """
        Run preprocessed OCR on specific (x, y, w, h) regions of the screen.

        Args:
            regions: List of (x, y, w, h) tuples
            lang: Tesseract language code(s)

        Returns:
            List of extracted texts (one per region, empty string for failures).
        """
        if not self._ocr_available:
            return []
        img = self.capture_screenshot()
        if img is None:
            return []
        results = []
        for region in regions:
            try:
                x, y, w, h = region
                crop = img.crop((x, y, x + w, y + h))
                text = self._ocr_with_preprocessing(crop, lang=lang)
                results.append(text)
            except Exception as exc:
                log.warning("pixel_engine.ocr_region_error", region=region, error=str(exc))
                results.append("")
        return results

    # ── Frame processing (legacy API) ─────────────────────────────────────────

    def process_frame(self, frame: Any) -> FrameDiff | None:
        """
        Legacy API: compare a frame against the previous frame.

        Returns FrameDiff or None if the frame is a duplicate.
        """
        if not self._pil_available:
            return FrameDiff(changed_ratio=0.0, timestamp=time.time())

        token = self._fast_frame_token(frame)

        if self._last_frame_pil is None:
            self._last_frame_pil = frame
            return FrameDiff(changed_ratio=0.0, timestamp=time.time())

        if token == self._fast_frame_token(self._last_frame_pil):
            self._dedup_skipped += 1
            return None

        diff = self._compute_diff(frame, self._last_frame_pil)
        self._last_frame_pil = frame
        self._last_hash = token
        self._diff_history.append(diff)
        return diff

    # ── Activity monitoring ───────────────────────────────────────────────────

    def get_activity_level(self) -> float:
        """Return average change ratio over the last 10 frames (0.0 = static)."""
        if not self._diff_history:
            return 0.0
        recent = list(self._diff_history)[-10:]
        return sum(d.changed_ratio for d in recent) / len(recent)

    def is_screen_static(self, threshold: float = 0.01) -> bool:
        """Return True if the screen hasn't changed significantly in recent frames."""
        return self.get_activity_level() < threshold

    def detect_loading_state(self) -> bool:
        """Return True if the last 5 frames all show motion (likely loading/animating)."""
        if len(self._diff_history) < 5:
            return False
        recent = list(self._diff_history)[-5:]
        return all(d.motion_detected for d in recent)

    def get_motion_regions(self) -> list[tuple[int, int, int, int]]:
        """Return bounding boxes of recently changed screen regions."""
        regions = []
        for d in list(self._diff_history)[-5:]:
            if d.changed_region:
                regions.append(d.changed_region)
        return regions

    def wait_for_stable_screen(
        self,
        timeout_sec: float = 5.0,
        poll_interval: float = 0.3,
        stable_threshold: float = 0.02,
        min_stable_frames: int = 3,
    ) -> bool:
        """
        Poll the screen until it stops changing (animations settle).

        This is essential for the AI to not get confused by partial renders,
        loading spinners, or transitions. The method captures frames repeatedly
        and waits until the screen is stable for `min_stable_frames` polls.

        Args:
            timeout_sec: Maximum time to wait before giving up.
            poll_interval: Seconds between polls.
            stable_threshold: Max change ratio to consider "stable".
            min_stable_frames: How many consecutive stable frames required.

        Returns:
            True if screen stabilized, False on timeout.
        """
        if not self._pil_available:
            return False

        stable_count = 0
        start = time.time()

        while time.time() - start < timeout_sec:
            img = self.capture_screenshot()
            if img is None:
                time.sleep(poll_interval)
                continue

            token = self._fast_frame_token(img)
            prev_hash = self._last_hash
            prev_frame = self._last_frame_pil

            # Save state for next iteration
            self._last_frame_pil = img
            self._last_hash = token

            if not prev_hash or prev_frame is None:
                # First frame — no baseline to compare
                stable_count = 0
                time.sleep(poll_interval)
                continue

            if token == prev_hash:
                # Identical frame — definitely stable
                stable_count += 1
            else:
                # Compute diff against the PREVIOUS frame (not the one we just set)
                diff = self._compute_diff(img, prev_frame)
                if diff.changed_ratio < stable_threshold and not diff.motion_detected:
                    stable_count += 1
                else:
                    stable_count = 0  # Still moving, reset counter

            if stable_count >= min_stable_frames:
                return True

            time.sleep(poll_interval)

        return False

    def _detect_animation_spinner(self, diff_history: list[FrameDiff] | None = None) -> bool:
        """
        Detect if the screen shows a loading spinner/indeterminate progress.

        Uses heuristics:
          - Small region oscillating (spinner rotates in place)
          - High frequency of motion events with low change ratio
          - Alternating color shifts in a small area

        Returns:
            True if likely a loading animation.
        """
        recent = diff_history or list(self._diff_history)
        if len(recent) < 4:
            return False

        recent = recent[-6:]  # Look at last 6 frame diffs

        # All frames show motion but with small change ratio (< 5%)
        if not all(d.motion_detected for d in recent):
            return False

        avg_ratio = sum(d.changed_ratio for d in recent) / len(recent)
        if avg_ratio > 0.15:
            # Too much change — this is a scene transition, not a spinner
            return False

        # Check if changes are confined to a small, consistent region
        regions = [d.changed_region for d in recent if d.changed_region]
        if len(regions) >= 3:
            # Calculate region sizes and check if small
            sizes = [(r[2] * r[3]) for r in regions]
            avg_size = sum(sizes) / len(sizes)
            screen_size = (
                self._last_frame_pil.size[0] * self._last_frame_pil.size[1]
                if self._last_frame_pil
                else 1920 * 1080
            )
            if avg_size < screen_size * 0.10:
                return True

        return False

    @property
    def dedup_stats(self) -> dict:
        return {
            "skipped": self._dedup_skipped,
            "processed": self._frames_processed,
            "history_size": len(self._diff_history),
            "activity_level": round(self.get_activity_level(), 4),
        }
