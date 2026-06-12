"""Visible response shaping for AgentMax."""

from __future__ import annotations

import json
import re
from typing import Any


class ResponseLayer:
    """Separates private thinking fields from user-visible replies."""

    PRIVATE_KEYS = {
        "think",
        "thinking",
        "internal",
        "internal_notes",
        "hidden_execution_plan",
        "chain_of_thought",
        "reasoning_trace",
    }

    def visible_chat_payload(self, raw: str) -> dict[str, Any]:
        """Parse model output and return only fields safe for the UI."""
        parsed = self._parse_json(raw)
        if parsed is None:
            return {"reply": raw, "action": "none", "task": None}
        parsed = self._strip_private(parsed)
        action = str(parsed.get("action", "none"))
        if action not in {"none", "task"}:
            action = "none"
        task = parsed.get("task")
        if task is not None:
            task = str(task)[:2000]
        return {
            "reply": str(parsed.get("reply", raw)),
            "action": action,
            "task": task,
        }

    def _parse_json(self, raw: str) -> dict[str, Any] | None:
        json_str = raw
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if fence:
            json_str = fence.group(1)
        try:
            parsed = json.loads(json_str)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _strip_private(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: self._strip_private(item)
                for key, item in value.items()
                if key not in self.PRIVATE_KEYS
            }
        if isinstance(value, list):
            return [self._strip_private(item) for item in value]
        return value
