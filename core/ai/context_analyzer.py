"""Context analysis for AgentMax internal reasoning."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

_DESTRUCTIVE_TERMS = {
    "delete",
    "remove",
    "format",
    "wipe",
    "erase",
    "kill",
    "shutdown",
    "overwrite",
    "replace",
    "drop",
    "truncate",
    "borrar",
    "eliminar",
    "formatear",
    "destruir",
    "sobrescribir",
    "reemplazar",
    "cerrar proceso",
}
_NETWORK_TERMS = {"search", "web", "http", "https", "download", "buscar", "internet", "navegar"}
_FILE_TERMS = {"file", "folder", "path", "archivo", "carpeta", "directorio", "repo", "codigo"}
_UI_TERMS = {"click", "screen", "window", "button", "pantalla", "ventana", "boton", "clic", "ui"}
_AMBIGUOUS_TERMS = {"algo", "eso", "aquello", "it", "that", "thing", "whatever"}


@dataclass(slots=True)
class IntentAnalysis:
    """Compact, private summary of the user's intent and task risk."""

    intent: str
    ambiguity_score: float
    risk_score: float
    complexity_score: float
    suggested_tools: list[str] = field(default_factory=list)
    missing_context: list[str] = field(default_factory=list)
    memory_references: list[dict[str, Any]] = field(default_factory=list)

    @property
    def requires_clarification(self) -> bool:
        return self.ambiguity_score >= 0.72 and self.risk_score >= 0.55


class ContextAnalyzer:
    """Fast heuristic analyzer used before model calls and tool execution."""

    async def analyze(
        self, user_input: str, context: dict[str, Any] | None = None
    ) -> IntentAnalysis:
        text = user_input.strip()
        lowered = self._normalize(text)
        tokens = set(lowered.split())

        suggested_tools = self._suggest_tools(lowered, tokens)
        risk_score = self._risk_score(lowered)
        ambiguity_score = self._ambiguity_score(lowered, tokens)
        complexity_score = self._complexity_score(lowered, suggested_tools)
        missing_context = self._missing_context(lowered, suggested_tools)
        memories = await self._recall_memories(text, context or {})

        return IntentAnalysis(
            intent=self._classify_intent(lowered, suggested_tools),
            ambiguity_score=ambiguity_score,
            risk_score=risk_score,
            complexity_score=complexity_score,
            suggested_tools=suggested_tools,
            missing_context=missing_context,
            memory_references=memories,
        )

    def _normalize(self, value: str) -> str:
        value = value.lower()
        value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        value = value.replace("ó", "o").replace("á", "a").replace("é", "e")
        value = value.replace("í", "i").replace("ú", "u").replace("ñ", "n")
        return re.sub(r"\s+", " ", value)

    def _suggest_tools(self, lowered: str, tokens: set[str]) -> list[str]:
        tools: list[str] = []
        if tokens & _FILE_TERMS or re.search(r"[a-z]:\\|/", lowered):
            tools.append("file_system")
        if tokens & _NETWORK_TERMS:
            tools.append("web_search")
        if tokens & _UI_TERMS:
            tools.append("ui_automation")
        if any(term in lowered for term in ("powershell", "cmd", "proceso", "process")):
            tools.append("shell")
        if not tools:
            tools.append("planning")
        return tools

    def _risk_score(self, lowered: str) -> float:
        score = 0.08
        if any(term in lowered for term in _DESTRUCTIVE_TERMS):
            score += 0.55
        if any(term in lowered for term in ("all files", "todo", "todos los archivos", "entire")):
            score += 0.18
        if any(term in lowered for term in ("password", "token", "secret", "credencial")):
            score += 0.20
        if any(term in lowered for term in ("admin", "registry", "system32", "sudo")):
            score += 0.25
        return min(score, 1.0)

    def _ambiguity_score(self, lowered: str, tokens: set[str]) -> float:
        score = 0.12
        if len(tokens) < 4:
            score += 0.25
        if tokens & _AMBIGUOUS_TERMS:
            score += 0.25
        if any(term in lowered for term in ("mejora todo", "arregla todo", "haz todo")):
            score += 0.18
        if "?" in lowered:
            score += 0.08
        if not any(char.isdigit() for char in lowered) and any(
            term in lowered for term in _FILE_TERMS
        ):
            score += 0.10
        return min(score, 1.0)

    def _complexity_score(self, lowered: str, tools: list[str]) -> float:
        connectors = len(re.findall(r"\b(and|then|after|y|luego|despues|ademas)\b", lowered))
        score = 0.10 + min(len(lowered) / 1600, 0.25) + min(connectors * 0.10, 0.30)
        if len(tools) > 1:
            score += 0.15
        return min(score, 1.0)

    def _missing_context(self, lowered: str, tools: list[str]) -> list[str]:
        missing: list[str] = []
        if "file_system" in tools and not re.search(r"[a-z]:\\|/|\.\\|\w+\.\w+", lowered):
            missing.append("file_path")
        if "ui_automation" in tools and not any(t in lowered for t in ("window", "ventana", "app")):
            missing.append("active_window")
        return missing

    def _classify_intent(self, lowered: str, tools: list[str]) -> str:
        if "web_search" in tools:
            return "research"
        if "file_system" in tools:
            return "file_operation"
        if "ui_automation" in tools:
            return "desktop_automation"
        if any(term in lowered for term in ("explain", "explica", "que es")):
            return "explanation"
        return "general_task"

    async def _recall_memories(self, text: str, context: dict[str, Any]) -> list[dict[str, Any]]:
        memory_agent = context.get("memory_agent")
        if not memory_agent:
            return []
        try:
            memories = await memory_agent.recall_similar_tasks(text, limit=3)
        except Exception:
            return []
        return [
            {
                "description": str(item.get("description", ""))[:240],
                "score": float(item.get("score", 0.0) or 0.0),
                "steps": item.get("steps", [])[:5] if isinstance(item.get("steps"), list) else [],
            }
            for item in memories
        ]
