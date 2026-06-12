"""Fallback management for failed tool executions."""

from __future__ import annotations

from typing import Any

from core.tools.models import ToolDefinition, ToolRequest, ToolResult


class ToolFallbackManager:
    def build_fallback_requests(
        self,
        definition: ToolDefinition,
        request: ToolRequest,
        result: ToolResult,
    ) -> list[ToolRequest]:
        if result.success:
            return []
        requests: list[ToolRequest] = []
        for fallback_id in definition.fallback_chain:
            fallback_input = self._input_for_fallback(fallback_id, request.input, result)
            requests.append(
                ToolRequest(
                    tool_id=fallback_id,
                    input=fallback_input,
                    task_id=request.task_id,
                    dry_run=request.dry_run,
                    safe_mode=request.safe_mode,
                    approved_risk=request.approved_risk,
                    metadata={
                        "fallback_for": definition.id,
                        "original_error": result.error,
                    },
                )
            )
        return requests

    def _input_for_fallback(
        self,
        fallback_id: str,
        original_input: dict[str, Any],
        result: ToolResult,
    ) -> dict[str, Any]:
        if fallback_id == "screen.screenshot":
            return {}
        if fallback_id == "screen.locate_element":
            text = original_input.get("text") or original_input.get("target") or ""
            return {"text": text}
        if fallback_id == "mouse.move":
            return {
                "x": int(original_input.get("x", 0) or 0),
                "y": int(original_input.get("y", 0) or 0),
                "duration_ms": 100,
            }
        if fallback_id == "filesystem.list":
            return {"path": original_input.get("path", ".")}
        if fallback_id == "browser.search":
            return {"query": original_input.get("query") or original_input.get("url") or ""}
        if fallback_id == "shell.run":
            target = str(original_input.get("target") or "").strip()
            if target:
                safe_target = target.replace('"', '\\"')
                return {"command": f'start "" "{safe_target}"', "timeout_ms": 10_000}
        return dict(original_input)
