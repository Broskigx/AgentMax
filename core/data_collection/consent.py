"""Opt-in consent gate for AgentMax beta data collection.

Collection is **OFF by default**. Logs are only recorded/uploaded when the tester
has *explicitly* opted in, and even then only the **redacted** task logs are kept
(see ``redactor.py``) — never raw screenshots, keystrokes, secrets, or PII.

The choice is persisted to ``data/AgentMax_logs/.consent.json`` and can be
forced for headless/CI runs via the ``AGENTMAX_BETA_DATA_OPTIN`` env var.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from datetime import UTC, datetime

log = logging.getLogger(__name__)

_CONSENT_FILE = pathlib.Path("data") / "AgentMax_logs" / ".consent.json"
_ENV_FLAG = "AGENTMAX_BETA_DATA_OPTIN"

_cache: bool | None = None

CONSENT_NOTICE = """
  ── AgentMax closed beta — help improve AgentMax (optional) ──────────────
  If you opt in, AgentMax stores a REDACTED log of your tasks (the command,
  which tools ran, and the outcome) to help train future AgentMax models.

  Before anything is written or uploaded it is run through an automatic
  redactor that strips emails, secrets/API keys, IP addresses, usernames,
  file paths, machine names, and raw screenshots.

  • Nothing is collected unless you say yes here.
  • Data goes only to the private AgentMax dataset repository.
  • You can change this anytime: set AGENTMAX_BETA_DATA_OPTIN=0, or delete
    data/AgentMax_logs/.consent.json.
"""


def is_enabled() -> bool:
    """Return True only if the tester explicitly opted in to beta data sharing."""
    global _cache
    if _cache is not None:
        return _cache

    env = os.environ.get(_ENV_FLAG, "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        _cache = True
        return True
    if env in ("0", "false", "no", "off"):
        _cache = False
        return False

    try:
        if _CONSENT_FILE.exists():
            data = json.loads(_CONSENT_FILE.read_text(encoding="utf-8"))
            _cache = bool(data.get("opted_in"))
            return _cache
    except Exception as exc:  # noqa: BLE001 — never let consent IO break the app
        log.warning("consent.read_failed: %s", exc)

    _cache = False
    return False


def record_consent(opted_in: bool, *, tester_id: str | None = None) -> None:
    """Persist the tester's opt-in/opt-out choice."""
    global _cache
    _cache = opted_in
    try:
        _CONSENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CONSENT_FILE.write_text(
            json.dumps(
                {
                    "opted_in": opted_in,
                    "tester_id": tester_id,
                    "ts": datetime.now(UTC).isoformat(),
                    "version": 1,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("consent.write_failed: %s", exc)


def reset() -> None:
    """Clear the in-process cache (mainly for tests)."""
    global _cache
    _cache = None


def prompt_consent_cli() -> bool:
    """Show the first-run consent prompt on the CLI and persist the answer.

    No-ops (returns the existing decision) if a choice was already recorded or
    the env flag is set. Safe under non-interactive stdin (defaults to opt-out).
    """
    if os.environ.get(_ENV_FLAG) or _CONSENT_FILE.exists():
        return is_enabled()

    print(CONSENT_NOTICE)
    try:
        answer = input("  Share redacted beta data? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"
    choice = answer in ("y", "yes", "s", "si", "sí")
    record_consent(choice)
    print(f"  → Beta data sharing {'ENABLED' if choice else 'disabled'}.\n")
    return choice


__all__ = ["is_enabled", "record_consent", "reset", "prompt_consent_cli", "CONSENT_NOTICE"]
