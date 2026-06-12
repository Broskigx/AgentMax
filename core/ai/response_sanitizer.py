"""Public-response sanitizer for AgentMax runtimes.

The model may be a Thinking checkpoint, but AgentMax must never display raw
private reasoning in the product UI.  This module keeps the rule testable on
the backend side; the desktop UI has an equivalent TypeScript sanitizer.

Reasoning leak detection:
  - ``<think>`` or ``</think>`` tags found -> ``reasoning_leak_detected`` is set to True
  - Sanitized text is always clean (stripped of reasoning)
  - The leak flag can be used for logging, telemetry, or training feedback
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

_THINK_BLOCK_RE = re.compile(r"<\s*think\s*>.*?<\s*/\s*think\s*>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN_RE = re.compile(r"<\s*think\s*>", re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"<\s*/\s*think\s*>", re.IGNORECASE)
_SYSTEM_PROMPT_RE = re.compile(r"(?is)^\s*(system|developer)\s*:\s*.*?(?=(assistant|user)\s*:|$)")
# Additional reasoning markers for non-think formats
_REASONING_MARKERS = re.compile(
    r"(?i)(?:\b(?:reasoning|thinking|thought|chain_of_thought|rationale)\b\s*[:\-]\s*"
    r"|```(?:reasoning|think)\s*\n.*?\n```"
    r"|\[reasoning\].*?\[/reasoning\])",
    re.DOTALL,
)
# Dirty JSON with raw reasoning keys (e.g. from structured outputs)
_DIRTY_JSON_KEYS = {"reasoning", "thinking", "internal_thought", "chain_of_thought"}


@dataclass(frozen=True)
class SanitizedResponse:
    visible_text: str
    filtered_thinking: bool = False
    removed_system_prompt: bool = False
    truncated: bool = False
    reasoning_leak_detected: bool = False
    leak_type: str | None = None  # 'think_tag' | 'reasoning_marker' | 'json_leak'


log = logging.getLogger(__name__)


def sanitize_agent_response(
    raw: str,
    *,
    max_chars: int = 12_000,
    log_reasoning: bool = True,
) -> SanitizedResponse:
    """Return text safe for display in chat.

    Rules:
    - remove complete ``<think>...</think>`` blocks;
    - if a stray closing ``</think>`` exists, keep only content after it;
    - if an opening ``<think>`` remains without close, drop everything from it;
    - strip leading pasted system/developer prompt fragments;
    - detect and strip non-standard reasoning markers;
    - detect reasoning leaked in JSON-like structured output;
    - cap very long outputs at a safe boundary;
    - flag ``reasoning_leak_detected`` so callers can log the event.

    Args:
        raw: The raw model response text.
        max_chars: Maximum characters to return.
        log_reasoning: If True, log a warning when reasoning leaks are detected.

    Returns:
        A SanitizedResponse with visible_text safe for display and metadata flags.
    """
    text = str(raw or "")
    detected_leaks: list[str] = []

    # 1. Check for <think> tags
    has_think_open = bool(_THINK_OPEN_RE.search(text))
    has_think_close = bool(_THINK_CLOSE_RE.search(text))
    has_think_block = has_think_open or has_think_close
    if has_think_block:
        detected_leaks.append("think_tag")

    # 2. Check for non-standard reasoning markers
    if _REASONING_MARKERS.search(text):
        detected_leaks.append("reasoning_marker")

    # 3. Check for JSON-structured reasoning leaks
    try:
        # Try to parse as JSON and check for reasoning keys
        import json as _json

        parsed = _json.loads(text)
        if isinstance(parsed, dict):
            for key in parsed:
                if key.lower() in _DIRTY_JSON_KEYS and isinstance(parsed[key], str):
                    detected_leaks.append("json_leak")
                    break
    except (ValueError, TypeError):
        pass

    # --- Sanitize the text ---

    # Strip <think> blocks and stray tags
    if has_think_close:
        pieces = _THINK_CLOSE_RE.split(text)
        text = pieces[-1] if pieces else text

    text = _THINK_BLOCK_RE.sub("", text)
    text = _THINK_OPEN_RE.split(text)[0]
    text = _THINK_CLOSE_RE.sub("", text)

    # Strip non-standard reasoning markers
    text = _REASONING_MARKERS.sub("", text)

    # Strip pasted system prompt
    before_prompt = text
    text = _SYSTEM_PROMPT_RE.sub("", text).strip()
    removed_prompt = before_prompt != text

    # Normalize whitespace
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    # Truncate if too long
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
        truncated = True
        if "." in text[-400:]:
            text = text[: text.rfind(".") + 1]
        text += "\n\n[Respuesta recortada por seguridad.]"

    # Determine leak type (most severe first)
    leak_type: str | None = None
    if "think_tag" in detected_leaks:
        leak_type = "think_tag"
    elif "json_leak" in detected_leaks:
        leak_type = "json_leak"
    elif "reasoning_marker" in detected_leaks:
        leak_type = "reasoning_marker"

    reasoning_leak = len(detected_leaks) > 0

    # Log reasoning leaks for debugging (not shown to user)
    if reasoning_leak and log_reasoning:
        log.warning(
            "reasoning_leak_detected",
            extra={
                "reasoning_leak_detected": True,
                "leak_type": leak_type,
                "detected_patterns": detected_leaks,
                "original_length": len(raw or ""),
                "sanitized_length": len(text),
            },
        )

    return SanitizedResponse(
        visible_text=text,
        filtered_thinking=has_think_block,
        removed_system_prompt=removed_prompt,
        truncated=truncated,
        reasoning_leak_detected=reasoning_leak,
        leak_type=leak_type,
    )


__all__ = ["SanitizedResponse", "sanitize_agent_response"]
