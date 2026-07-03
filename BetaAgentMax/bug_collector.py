#!/usr/bin/env python3
"""
DataCollector — recopila ejemplos para re-entrenar AgentMax.

Principios:
  1. SOLO datos útiles para fine-tuning (no rastreo de comportamiento)
  2. SIN datos personales (PII scrubbing automático en TODO contenido)
  3. CONSENTIMIENTO granular por categoría (opt-in)
  4. DEDUPLICACIÓN para no inflar el dataset con repetidos
  5. EJEMPLOS POSITIVOS (éxitos) Y NEGATIVOS (correcciones, errores)

Tipos capturados:
  - tool_call_success: el agente eligió bien la herramienta + args correctos
  - tool_call_failure: la herramienta falló o el args estaba mal
  - correction:        usuario corrigió la respuesta del agente
  - training_error:    crashes durante fine-tune (OOM, NaN, etc.)
  - oom_error:         config que causa OOM → para aprender límites
  - workflow_pattern:  secuencias multi-step exitosas (estructura, sin contenido)

Privacidad:
  - PII detectada se reemplaza con placeholders: <email>, <path>, <ip>, <phone>, <token>, <name>
  - Si tras el scrub aún hay PII residual con alta confianza, se descarta el ejemplo
  - Por defecto NO se guardan hostnames, usernames, IPs reales ni rutas absolutas
  - User puede deshabilitar todo con AgentMax_NO_TELEMETRY=1
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Any

# Crypto DB optional
try:
    from crypto_db import CryptoDB, get_db

    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False

FALLBACK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FALLBACK_PATH = os.path.join(FALLBACK_DIR, "training_corpus.jsonl")
DEDUP_PATH = os.path.join(FALLBACK_DIR, ".dedup_index.json")


# ─── Consent / config ──────────────────────────────────────────────────────


@dataclass
class CollectionConsent:
    """
    Per-category opt-in. Default = collect only error stats (no content).

    To collect interaction content for re-training the user must opt in
    explicitly via UI/config:
      ConsentConfig(
        tool_outcomes=True,    # save tool call success/failure structure
        corrections=True,       # save user corrections (most valuable)
        workflows=True,         # save multi-step patterns (anonymized)
      )
    """

    # Always-on (no PII, just structural):
    training_errors: bool = True  # config + exception type, no content
    oom_errors: bool = True  # batch/seq/vram only

    # Opt-in for content:
    tool_outcomes: bool = False  # tool name + args (PII-scrubbed)
    corrections: bool = False  # user → model → corrected (PII-scrubbed)
    workflows: bool = False  # tool-call sequences (no content)

    # Global kill-switch
    enabled: bool = True

    @classmethod
    def from_env(cls) -> CollectionConsent:
        if os.environ.get("AgentMax_NO_TELEMETRY", "").strip() in ("1", "true", "yes"):
            return cls(enabled=False, training_errors=False, oom_errors=False)
        c = cls()
        c.tool_outcomes = os.environ.get("AgentMax_COLLECT_TOOLS", "0") == "1"
        c.corrections = os.environ.get("AgentMax_COLLECT_CORRECTIONS", "0") == "1"
        c.workflows = os.environ.get("AgentMax_COLLECT_WORKFLOWS", "0") == "1"
        return c


# ─── PII scrubbing ─────────────────────────────────────────────────────────

# Regex patterns (compiled once)
_RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_RE_IP_V4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_RE_IP_V6 = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{0,4}\b")
# Phone: requires + prefix (international) OR () prefix, then 2+ groups separated by space/dash
_RE_PHONE = re.compile(r"(?:\+\d{1,3}|\(\d{2,4}\))[\s-]?\d{1,5}(?:[\s-]\d{1,5}){1,5}\b")
# Credit card: exactly 13-19 consecutive digits, no dots (otherwise it'd catch IPs)
_RE_CC = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{1,7}\b")
_RE_WIN_PATH = re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+", re.IGNORECASE)
_RE_UNIX_HOME = re.compile(r"/(?:home|Users)/[^/\s]+")
_RE_API_KEY = re.compile(
    r"(?i)(?:api[_-]?key|token|secret|password|bearer)[\"\'\s:=]+[A-Za-z0-9_\-]{12,}"
)
_RE_BEARER = re.compile(r"\b(?:eyJ|sk-|hf_|ghp_|gho_|ghs_|gho_|xoxb-|xoxp-)[A-Za-z0-9_\-]{16,}\b")
_RE_URL_QUERY = re.compile(r"(https?://[^/\s?]+)/[^\s?]*\?[^\s]*")
_RE_HOSTNAME = re.compile(
    r"\b(?:[a-z0-9-]+\.){1,3}(?:com|net|org|io|ai|dev|app|local|lan)\b", re.IGNORECASE
)

# Whitelisted hostnames (don't scrub — they're our infra / public)
_HOST_WHITELIST = {
    "huggingface.co",
    "github.com",
    "pypi.org",
    "python.org",
    "runpod.io",
    "lmstudio.ai",
    "openai.com",
    "anthropic.com",
    "qwen.ai",
    "unsloth.ai",
}

# Common username placeholder
_USER_PATTERNS = ["agust", "admin", "user", "root"]


def scrub_pii(text: str, aggressive: bool = False) -> str:
    """
    Replace PII with placeholders. Returns scrubbed text.

    aggressive=True also scrubs hostnames not in whitelist.
    """
    if not text:
        return text

    s = text

    # High-confidence patterns (order matters!)
    s = _RE_BEARER.sub("<token>", s)
    s = _RE_API_KEY.sub("<api_key>", s)
    s = _RE_EMAIL.sub("<email>", s)

    # IPs FIRST (before phone) to avoid 200.42.13.5 being matched as phone
    def _ip_replace(m: re.Match) -> str:
        ip = m.group(0)
        if ip.startswith(("127.", "10.", "192.168.", "172.")):
            return "<lan_ip>"
        return "<ip>"

    s = _RE_IP_V4.sub(_ip_replace, s)

    # CC before phone (CC requires 4+4+4+N format, very specific)
    s = _RE_CC.sub("<card>", s)
    s = _RE_PHONE.sub("<phone>", s)

    # Paths with usernames
    s = _RE_WIN_PATH.sub(r"C:\\Users\\<user>", s)
    s = _RE_UNIX_HOME.sub("/home/<user>", s)

    # URL query strings (may contain tokens, session IDs)
    s = _RE_URL_QUERY.sub(r"\1/<path>?<query>", s)

    # Aggressive: hostname scrubbing
    if aggressive:

        def _host_replace(m: re.Match) -> str:
            host = m.group(0).lower()
            if any(w in host for w in _HOST_WHITELIST):
                return host
            return "<host>"

        s = _RE_HOSTNAME.sub(_host_replace, s)

    return s


def has_residual_pii(text: str) -> bool:
    """Conservative check after scrubbing — returns True if suspicious patterns remain."""
    if not text:
        return False
    suspects = [_RE_EMAIL, _RE_PHONE, _RE_CC, _RE_BEARER, _RE_API_KEY]
    return any(p.search(text) for p in suspects)


def anonymize_dict(d: dict, aggressive: bool = False) -> dict:
    """Recursively scrub PII from a dict (strings only)."""
    out: dict = {}
    for k, v in d.items():
        if isinstance(v, str):
            out[k] = scrub_pii(v, aggressive)
        elif isinstance(v, dict):
            out[k] = anonymize_dict(v, aggressive)
        elif isinstance(v, list):
            out[k] = [
                scrub_pii(x, aggressive)
                if isinstance(x, str)
                else anonymize_dict(x, aggressive)
                if isinstance(x, dict)
                else x
                for x in v
            ]
        else:
            out[k] = v
    return out


# ─── Deduplication ─────────────────────────────────────────────────────────


class DedupIndex:
    """In-memory + on-disk dedup of (category, content_hash) → occurrence count."""

    def __init__(self, path: str = DEDUP_PATH, max_entries: int = 50_000):
        self.path = path
        self.max_entries = max_entries
        self._index: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, encoding="utf-8") as f:
                    self._index = json.load(f)
        except Exception:
            self._index = {}

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._index, f)
            os.replace(tmp, self.path)
        except Exception as e:
            print(f"[DataCollector] dedup save failed: {e}", file=sys.stderr)

    @staticmethod
    def _hash(payload: str) -> str:
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def seen(self, category: str, normalized_content: str) -> int:
        """Return current occurrence count for this (category, content)."""
        key = f"{category}:{self._hash(normalized_content)}"
        return self._index.get(key, {}).get("count", 0)

    def record(self, category: str, normalized_content: str) -> int:
        """Record an occurrence. Returns the new count."""
        key = f"{category}:{self._hash(normalized_content)}"
        entry = self._index.setdefault(key, {"count": 0, "first": time.time()})
        entry["count"] += 1
        entry["last"] = time.time()
        # Keep size bounded by dropping oldest
        if len(self._index) > self.max_entries:
            oldest = sorted(self._index.items(), key=lambda kv: kv[1].get("last", 0))[
                : self.max_entries // 10
            ]
            for k, _ in oldest:
                self._index.pop(k, None)
        return entry["count"]


# ─── DataCollector ─────────────────────────────────────────────────────────


class DataCollector:
    """
    Privacy-respecting data collector for re-training AgentMax.

    Usage::

        consent = CollectionConsent.from_env()
        col = DataCollector(consent=consent)

        # On every tool call:
        col.capture_tool_call(tool="click", args={"x":100,"y":200},
                              success=True, latency_ms=42)

        # On user correction:
        col.capture_correction(user_msg, model_resp, corrected_resp)

        # On training failure (always allowed in default config):
        col.capture_training_error(exc, config, stage="forward")

        # Export for fine-tuning:
        examples = col.export_for_finetuning()
    """

    # Public categories
    CAT_TOOL_OK = "tool_call_success"
    CAT_TOOL_FAIL = "tool_call_failure"
    CAT_CORRECTION = "correction"
    CAT_TRAIN_ERR = "training_error"
    CAT_OOM = "oom_error"
    CAT_WORKFLOW = "workflow_pattern"

    # Limits (chars)
    MAX_USER_MSG = 1000
    MAX_MODEL_MSG = 2000
    MAX_TRACEBACK = 2000
    MIN_CONTENT = 8

    # Dedup: same item is recorded at most this many times
    MAX_OCCURRENCES = 5

    def __init__(
        self,
        db: CryptoDB | None = None,
        db_path: str = "data/AgentMax.db",
        password: str = "AgentMax_default",
        consent: CollectionConsent | None = None,
        aggressive_scrub: bool = True,
    ):
        self.consent = consent or CollectionConsent.from_env()
        self.aggressive = aggressive_scrub
        self.dedup = DedupIndex()

        if db:
            self.db = db
        elif _DB_AVAILABLE and self.consent.enabled:
            try:
                self.db = get_db(db_path, password)
            except Exception:
                self.db = None
        else:
            self.db = None

        # Session-scoped stats
        self._session_stats = {
            "captured": 0,
            "dropped_consent": 0,
            "dropped_dedup": 0,
            "dropped_pii": 0,
            "dropped_quality": 0,
        }

    # ── Public API ───────────────────────────────────────────────────────

    def capture_tool_call(
        self,
        tool: str,
        args: dict,
        success: bool,
        output: str = "",
        error: str = "",
        latency_ms: float = 0.0,
    ) -> int | None:
        """Capture a tool call result. Most valuable training signal."""
        if not self.consent.enabled or not self.consent.tool_outcomes:
            self._session_stats["dropped_consent"] += 1
            return None

        # Anonymize args + output + error
        clean_args = anonymize_dict(args, self.aggressive)
        clean_output = scrub_pii(output[: self.MAX_MODEL_MSG], self.aggressive)
        clean_error = scrub_pii(error[:500], self.aggressive)

        # PII residual check
        merged = json.dumps(clean_args) + clean_output + clean_error
        if has_residual_pii(merged):
            self._session_stats["dropped_pii"] += 1
            return None

        category = self.CAT_TOOL_OK if success else self.CAT_TOOL_FAIL
        normalized = f"{tool}::{sorted(args.keys())}"
        return self._save(
            category,
            {
                "tool": tool,
                "args": clean_args,
                "success": success,
                "output": clean_output if clean_output else None,
                "error": clean_error if clean_error else None,
                "latency_ms": round(latency_ms, 2),
            },
            normalized=normalized,
            severity="info",
        )

    def capture_correction(
        self,
        user_input: str,
        model_output: str,
        corrected_output: str,
        context_messages: list[dict] | None = None,
    ) -> int | None:
        """User corrected the model — high-quality training signal."""
        if not self.consent.enabled or not self.consent.corrections:
            self._session_stats["dropped_consent"] += 1
            return None

        # Length / quality filter
        if (
            len(user_input.strip()) < self.MIN_CONTENT
            or len(corrected_output.strip()) < self.MIN_CONTENT
        ):
            self._session_stats["dropped_quality"] += 1
            return None

        clean_user = scrub_pii(user_input[: self.MAX_USER_MSG], self.aggressive)
        clean_bad = scrub_pii(model_output[: self.MAX_MODEL_MSG], self.aggressive)
        clean_good = scrub_pii(corrected_output[: self.MAX_MODEL_MSG], self.aggressive)

        if has_residual_pii(clean_user + clean_bad + clean_good):
            self._session_stats["dropped_pii"] += 1
            return None

        # Anonymize context too
        clean_ctx = None
        if context_messages:
            clean_ctx = [anonymize_dict(m, self.aggressive) for m in context_messages[-4:]]

        normalized = clean_user[:100] + "::" + clean_good[:100]
        return self._save(
            self.CAT_CORRECTION,
            {
                "input": clean_user,
                "bad_output": clean_bad,
                "correction": clean_good,
                "context": clean_ctx,
            },
            normalized=normalized,
            severity="high",
        )

    def capture_workflow(
        self,
        tool_sequence: list[str],
        success: bool,
        duration_ms: float = 0.0,
    ) -> int | None:
        """Capture a tool sequence pattern — NO content, just structure."""
        if not self.consent.enabled or not self.consent.workflows:
            self._session_stats["dropped_consent"] += 1
            return None

        if not tool_sequence or len(tool_sequence) < 2:
            self._session_stats["dropped_quality"] += 1
            return None

        normalized = "→".join(tool_sequence)
        return self._save(
            self.CAT_WORKFLOW,
            {
                "tools": tool_sequence,
                "length": len(tool_sequence),
                "success": success,
                "duration_ms": round(duration_ms, 2),
            },
            normalized=normalized,
            severity="info",
        )

    def capture_training_error(
        self,
        exception: Exception,
        config: dict,
        stage: str = "training",
    ) -> int | None:
        """Capture training crashes. Always allowed (structural info only)."""
        if not self.consent.enabled or not self.consent.training_errors:
            self._session_stats["dropped_consent"] += 1
            return None

        # VRAM info (no PII)
        vram_info: dict[str, float] = {}
        try:
            import torch

            if torch.cuda.is_available():
                vram_info = {
                    "vram_allocated_gb": round(torch.cuda.memory_allocated() / 1e9, 2),
                    "vram_reserved_gb": round(torch.cuda.memory_reserved() / 1e9, 2),
                    "vram_peak_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2),
                }
        except Exception:
            pass

        # Scrub traceback (may contain user paths)
        tb = scrub_pii(traceback.format_exc()[: self.MAX_TRACEBACK], aggressive=True)
        msg = scrub_pii(str(exception)[:500], aggressive=True)

        safe_config = {
            k: v
            for k, v in config.items()
            if isinstance(v, (str, int, float, bool))
            and not any(s in k.lower() for s in ("key", "token", "secret", "password", "path"))
        }

        normalized = f"{type(exception).__name__}::{stage}"
        severity = "critical" if "out of memory" in msg.lower() else "high"
        return self._save(
            self.CAT_TRAIN_ERR,
            {
                "exception_type": type(exception).__name__,
                "exception_msg": msg,
                "traceback": tb,
                "stage": stage,
                "config": safe_config,
                **vram_info,
            },
            normalized=normalized,
            severity=severity,
        )

    def capture_oom(self, config: dict, batch_size: int, seq_len: int) -> int | None:
        """OOM is config-only data, always allowed."""
        if not self.consent.enabled or not self.consent.oom_errors:
            self._session_stats["dropped_consent"] += 1
            return None

        safe_config = {k: v for k, v in config.items() if isinstance(v, (str, int, float, bool))}
        normalized = f"oom::bs={batch_size}::seq={seq_len}"
        return self._save(
            self.CAT_OOM,
            {
                "batch_size": batch_size,
                "seq_len": seq_len,
                "config": safe_config,
                "suggestion": f"Reducir batch_size a {max(1, batch_size // 2)} "
                f"o seq_len a {max(512, seq_len // 2)}",
            },
            normalized=normalized,
            severity="high",
        )

    # ── Export for fine-tuning ────────────────────────────────────────────

    def export_for_finetuning(
        self,
        balance: bool = True,
        max_per_category: int = 500,
    ) -> list[dict]:
        """
        Convert captured records into training examples.

        Args:
            balance:  If True, downsample over-represented categories.
            max_per_category:  Cap per category.

        Returns list of {"messages": [...], "_meta": {...}} dicts.
        """
        records = self._load_all()
        by_cat: dict[str, list[dict]] = {}
        for r in records:
            cat = r.get("_category", r.get("_bug_type", "other"))
            by_cat.setdefault(cat, []).append(r)

        examples: list[dict] = []
        system = (
            "Eres AgentMax, un agente de IA de escritorio desarrollado por AgentMax. "
            "Usas herramientas reales y reportas errores con honestidad."
        )

        for cat, items in by_cat.items():
            if balance:
                items = items[:max_per_category]
            for r in items:
                ex = self._record_to_example(cat, r, system)
                if ex:
                    examples.append(
                        {
                            "messages": ex,
                            "_meta": {
                                "category": cat,
                                "occurrences": r.get("_occurrences", 1),
                                "source_id": r.get("_id"),
                            },
                        }
                    )

        return examples

    def _record_to_example(self, cat: str, r: dict, system: str) -> list[dict] | None:
        if cat == self.CAT_CORRECTION:
            return [
                {"role": "system", "content": system},
                {"role": "user", "content": r.get("input", "")},
                {"role": "assistant", "content": r.get("correction", "")},
            ]

        if cat == self.CAT_TOOL_OK:
            tool, args = r.get("tool"), r.get("args", {})
            return [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Ejecuta la herramienta {tool}"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"name": tool, "arguments": args},
                    ],
                },
                {"role": "tool", "content": r.get("output") or json.dumps({"ok": True})},
            ]

        if cat == self.CAT_TOOL_FAIL:
            tool, args, err = r.get("tool"), r.get("args", {}), r.get("error", "")
            return [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Ejecuta la herramienta {tool}"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"name": tool, "arguments": args},
                    ],
                },
                {"role": "tool", "content": json.dumps({"ok": False, "error": err})},
                {
                    "role": "assistant",
                    "content": f"La herramienta {tool} falló: {err}. Diagnostico con screenshot y pruebo alternativa.",
                },
            ]

        if cat == self.CAT_TRAIN_ERR:
            return [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"Inicia el entrenamiento con config {r.get('config', {})}",
                },
                {
                    "role": "assistant",
                    "content": f"Error en etapa '{r.get('stage', '?')}': {r.get('exception_type', '?')}. "
                    f"Detalle: {r.get('exception_msg', '')[:200]}",
                },
            ]

        if cat == self.CAT_OOM:
            return [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"Entrena con batch_size={r.get('batch_size')}, seq_len={r.get('seq_len')}",
                },
                {
                    "role": "assistant",
                    "content": f"CUDA OOM con batch_size={r.get('batch_size')}, seq_len={r.get('seq_len')}. "
                    f"{r.get('suggestion', '')}",
                },
            ]

        return None

    # ── Internals ─────────────────────────────────────────────────────────

    def _save(self, category: str, data: dict, *, normalized: str, severity: str) -> int | None:
        # Dedup check
        count = self.dedup.seen(category, normalized)
        if count >= self.MAX_OCCURRENCES:
            self._session_stats["dropped_dedup"] += 1
            return None

        new_count = self.dedup.record(category, normalized)
        data["_timestamp"] = time.time()
        data["_category"] = category
        data["_occurrences"] = new_count

        # Persist
        bid = None
        if self.db:
            try:
                bid = self.db.save_bug(category, data, severity)
            except Exception as e:
                print(f"[DataCollector] DB error: {e}", file=sys.stderr)

        if bid is None:
            try:
                os.makedirs(FALLBACK_DIR, exist_ok=True)
                with open(FALLBACK_PATH, "a", encoding="utf-8") as f:
                    f.write(
                        json.dumps(
                            {"category": category, "severity": severity, **data},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except Exception as e:
                print(f"[DataCollector] fallback write error: {e}", file=sys.stderr)

        self._session_stats["captured"] += 1
        # Periodically persist dedup index (every 20 captures)
        if self._session_stats["captured"] % 20 == 0:
            self.dedup.save()
        return bid

    def _load_all(self) -> list[dict]:
        records: list[dict] = []
        if self.db:
            try:
                records = list(self.db.load_bugs(only_unfixed=False, limit=10_000))
            except Exception:
                records = []
        if not records and os.path.exists(FALLBACK_PATH):
            with open(FALLBACK_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except Exception:
                            pass
        return records

    def status(self) -> dict:
        out = dict(self._session_stats)
        out["consent"] = {
            "enabled": self.consent.enabled,
            "tool_outcomes": self.consent.tool_outcomes,
            "corrections": self.consent.corrections,
            "workflows": self.consent.workflows,
            "training_errors": self.consent.training_errors,
            "oom_errors": self.consent.oom_errors,
        }
        if self.db:
            try:
                s = self.db.stats()
                out["db_bugs_total"] = s.get("bugs_total", 0)
                out["db_path"] = s.get("db_path", "")
            except Exception:
                pass
        elif os.path.exists(FALLBACK_PATH):
            with open(FALLBACK_PATH) as f:
                out["fallback_lines"] = sum(1 for ln in f if ln.strip())
            out["fallback_path"] = FALLBACK_PATH
        return out

    def flush(self) -> None:
        """Persist all in-memory state."""
        self.dedup.save()


# ─── Backwards-compat alias ────────────────────────────────────────────────


# Old name kept so existing imports don't break
class BugCollector(DataCollector):
    """Alias for backwards compatibility — prefer DataCollector."""

    def capture_chat_feedback(
        self, user_input: str, model_output: str, corrected_output: str, session_id: str = ""
    ) -> int | None:
        """Legacy method — delegates to capture_correction (session_id discarded for privacy)."""
        return self.capture_correction(user_input, model_output, corrected_output)

    def capture_tool_error(self, tool_name: str, args: dict, error: str) -> int | None:
        """Legacy method — delegates to capture_tool_call(success=False)."""
        return self.capture_tool_call(tool=tool_name, args=args, success=False, error=error)


# ─── Hooks ─────────────────────────────────────────────────────────────────


def install_training_hook(collector: DataCollector):
    """Hook sys.excepthook to capture training crashes automatically."""
    _original = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            _original(exc_type, exc_value, exc_tb)
            return
        try:
            collector.capture_training_error(exc_value, {}, stage="unhandled")
            collector.flush()
        except Exception:
            pass
        _original(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


# ─── CLI smoke test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== DataCollector smoke test ===\n")

    # Force-enable all consent categories for testing
    consent = CollectionConsent(
        enabled=True,
        tool_outcomes=True,
        corrections=True,
        workflows=True,
    )
    col = DataCollector(db_path="data/test_collector.db", password="t", consent=consent)
    print("status inicial:", col.status(), "\n")

    # PII scrub demo
    sample = (
        "Mi correo es agust@gmail.com y mi clave es sk-abc123def456ghi789. "
        "Vivo en C:\\Users\\agustin\\Desktop\\proyecto. Mi IP es 200.42.13.5. "
        "Llamame al +54 9 11 4444-5555."
    )
    print("ORIGINAL:", sample)
    print("SCRUBBED:", scrub_pii(sample), "\n")

    # Training error (always allowed)
    try:
        raise MemoryError("CUDA out of memory. Tried to allocate 2.5 GiB on /home/agust/.cache")
    except MemoryError as e:
        col.capture_training_error(e, {"batch_size": 4, "lora_rank": 64}, stage="forward")

    # Tool call success (opt-in)
    col.capture_tool_call(tool="click", args={"x": 120, "y": 300}, success=True, latency_ms=15)

    # Tool call failure (opt-in)
    col.capture_tool_call(
        tool="open_app", args={"name": "chrome"}, success=False, error="App not found"
    )

    # User correction (opt-in)
    col.capture_correction(
        user_input="Abre Chrome",
        model_output="Abriendo Firefox...",
        corrected_output="Abriendo Chrome.",
    )

    # Workflow (opt-in)
    col.capture_workflow(
        ["screenshot", "click", "type", "screenshot"], success=True, duration_ms=3200
    )

    # Dedup test: same OOM 10 times → only first MAX_OCCURRENCES are saved
    for _ in range(10):
        col.capture_oom({}, batch_size=8, seq_len=4096)

    print("\nstatus final:", col.status())

    # Export
    examples = col.export_for_finetuning()
    print(f"\nEjemplos exportados para fine-tuning: {len(examples)}")
    if examples:
        print("Primer ejemplo:", json.dumps(examples[0], indent=2, ensure_ascii=False)[:600])

    col.flush()
