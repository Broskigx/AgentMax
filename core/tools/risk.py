"""Risk analysis for tool execution."""

from __future__ import annotations

import re
from typing import Any

from core.tools.models import ToolDefinition, ToolRiskLevel


class ToolRiskAnalyzer:
    """Combines static tool risk with input-aware risk scoring."""

    _DANGEROUS_SHELL = (
        r"\brm\s+-rf\b",
        r"\bformat\b",
        r"\bdel\s+/[sq]\b",
        r"\brmdir\s+/[sq]\b",
        r"\bshutdown\b",
        r"\brestart-computer\b",
        r"\bbcdedit\b",
        r"\breg\s+delete\b",
        r"\bset-executionpolicy\b",
        r"\btakeown\b",
        r"\bcipher\s+/w\b",
    )

    def analyze(self, definition: ToolDefinition, payload: dict[str, Any]) -> dict[str, Any]:
        score = {
            ToolRiskLevel.LOW: 0.15,
            ToolRiskLevel.MEDIUM: 0.45,
            ToolRiskLevel.HIGH: 0.75,
            ToolRiskLevel.CRITICAL: 0.95,
        }[definition.risk_level]
        reasons = [f"static:{definition.risk_level.value}"]

        if definition.category == "shell":
            command = str(payload.get("command", "")).lower()
            for pattern in self._DANGEROUS_SHELL:
                if re.search(pattern, command):
                    score = max(score, 0.98)
                    reasons.append(f"dangerous_shell:{pattern}")
            if "powershell" in command or "cmd" in command:
                score = max(score, 0.62)
                reasons.append("shell_interpreter")

        if definition.category == "filesystem":
            if definition.id.endswith("delete"):
                score = max(score, 0.90)
                reasons.append("destructive_file_operation")
            elif definition.id.endswith("write") or definition.id.endswith("move"):
                score = max(score, 0.62)
                reasons.append("filesystem_mutation")

        if definition.category in {"mouse", "keyboard"}:
            if payload.get("text") and len(str(payload.get("text"))) > 2_000:
                score = max(score, 0.65)
                reasons.append("large_text_injection")

        level = "low"
        if score >= 0.90:
            level = "critical"
        elif score >= 0.70:
            level = "high"
        elif score >= 0.35:
            level = "medium"

        return {
            "score": round(score, 3),
            "level": level,
            "reasons": reasons,
            "requires_confirmation": score >= 0.70,
        }

    def blocks_execution(self, definition: ToolDefinition, payload: dict[str, Any]) -> str | None:
        analysis = self.analyze(definition, payload)
        if analysis["score"] >= 0.97:
            return "critical_risk_blocked"
        return None
