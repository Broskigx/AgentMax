"""
Input validation for system boundaries.

Validates at the two main ingestion points:
  1. User-supplied task descriptions (before they reach the LLM prompt)
  2. Screen coordinates (before they reach mouse/keyboard controllers)

Design: raise ValueError with a human-readable message.
Never silently truncate or coerce -- the caller decides how to handle.
"""

from __future__ import annotations

import re
import unicodedata

# ── Task / prompt validation ───────────────────────────────────────────────────

TASK_MAX_LEN = 4_096  # characters
TASK_MAX_LINES = 50  # newlines allowed in a single description

# Characters that could break the LLM prompt or inject instructions.
# We allow everything printable (including Unicode) but strip known injection
# patterns rather than doing a whitelist (whitelists break international users).
_PROMPT_INJECTION_RE = re.compile(
    r"""
    (
        <\s*/?\s*(system|user|assistant|human|ai|inst)\s*>  # XML role tags
      | \[INST\]|\[/INST\]                                  # Llama-style
      | <<<.*?>>>                                            # triple-angle brackets
      | \bignore\s+(all\s+)?(previous|prior|above)\b        # classic injection
      | \bforget\s+(everything|your\s+instructions)\b
      | \byou\s+are\s+now\b                                  # role override
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


class TaskValidationError(ValueError):
    pass


def validate_task_description(description: str) -> str:
    """
    Validate and sanitize a user-supplied task description.

    Returns the cleaned string on success.
    Raises TaskValidationError if the input is unacceptable.
    """
    if not isinstance(description, str):
        raise TaskValidationError("Task description must be a string.")

    # Normalize Unicode to NFC (avoids look-alike attacks with combining chars)
    description = unicodedata.normalize("NFC", description)

    # Strip null bytes and ASCII control chars except tab/newline/CR
    description = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", description)

    stripped = description.strip()

    if not stripped:
        raise TaskValidationError("Task description cannot be empty.")

    if len(stripped) > TASK_MAX_LEN:
        raise TaskValidationError(
            f"Task description too long ({len(stripped)} chars, max {TASK_MAX_LEN})."
        )

    if stripped.count("\n") > TASK_MAX_LINES:
        raise TaskValidationError(f"Task description has too many lines (max {TASK_MAX_LINES}).")

    # Prompt-injection heuristics
    match = _PROMPT_INJECTION_RE.search(stripped)
    if match:
        raise TaskValidationError(
            f"Task description contains a disallowed pattern: '{match.group(0).strip()}'"
        )

    return stripped


# ── Coordinate validation ──────────────────────────────────────────────────────


class CoordinateError(ValueError):
    pass


def validate_coords(
    x: int | float,
    y: int | float,
    screen_w: int,
    screen_h: int,
    *,
    label: str = "coordinate",
) -> tuple[int, int]:
    """
    Validate that (x, y) falls within [0, screen_w) × [0, screen_h).

    Returns (x, y) as integers on success.
    Raises CoordinateError otherwise.
    """
    try:
        xi, yi = int(round(x)), int(round(y))
    except (TypeError, ValueError) as exc:
        raise CoordinateError(f"{label}: cannot convert to int: {exc}") from exc

    if not (0 <= xi < screen_w):
        raise CoordinateError(f"{label}: x={xi} is outside screen width [0, {screen_w}).")
    if not (0 <= yi < screen_h):
        raise CoordinateError(f"{label}: y={yi} is outside screen height [0, {screen_h}).")

    return xi, yi


def clamp_coords(
    x: int | float,
    y: int | float,
    screen_w: int,
    screen_h: int,
) -> tuple[int, int]:
    """
    Silently clamp (x, y) to screen bounds.
    Use this when you prefer robustness over strict rejection.
    """
    xi = max(0, min(int(round(x)), screen_w - 1))
    yi = max(0, min(int(round(y)), screen_h - 1))
    return xi, yi


def validate_region(
    x: int | float,
    y: int | float,
    w: int | float,
    h: int | float,
    screen_w: int,
    screen_h: int,
) -> tuple[int, int, int, int]:
    """
    Validate a (x, y, w, h) capture/click region.
    Width and height must be positive.
    The region must be entirely within screen bounds.
    """
    xi, yi, wi, hi = int(round(x)), int(round(y)), int(round(w)), int(round(h))

    if wi <= 0 or hi <= 0:
        raise CoordinateError(f"Region dimensions must be positive, got w={wi} h={hi}.")

    if xi < 0 or yi < 0:
        raise CoordinateError(f"Region origin must be non-negative, got x={xi} y={yi}.")

    if xi + wi > screen_w:
        raise CoordinateError(f"Region x+w={xi + wi} exceeds screen width {screen_w}.")
    if yi + hi > screen_h:
        raise CoordinateError(f"Region y+h={yi + hi} exceeds screen height {screen_h}.")

    return xi, yi, wi, hi


# ── Key name validation ────────────────────────────────────────────────────────

_ALLOWED_MODIFIERS = frozenset({"ctrl", "alt", "shift", "win", "cmd", "meta", "super"})
_KEY_RE = re.compile(r"^[a-z0-9_]+$")  # pynput key names are lowercase alphanum+underscore


class KeyValidationError(ValueError):
    pass


def validate_key_name(key: str) -> str:
    """
    Validate a key name string coming from a task plan step.
    Returns the lowercased, stripped key on success.
    Raises KeyValidationError for empty, too-long, or suspiciously formatted names.
    """
    if not isinstance(key, str):
        raise KeyValidationError("Key name must be a string.")
    key = key.strip().lower()
    if not key:
        raise KeyValidationError("Key name cannot be empty.")
    if len(key) > 32:
        raise KeyValidationError(f"Key name too long ({len(key)} chars).")
    if not _KEY_RE.match(key):
        raise KeyValidationError(
            f"Key name '{key}' contains invalid characters (only a-z, 0-9, _ allowed)."
        )
    return key
