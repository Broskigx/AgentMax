"""Map planning steps to canonical tool ids."""

from __future__ import annotations

from typing import Any

from core.tools.models import ToolRequest


class ToolRouter:
    LEGACY_STEP_MAP = {
        "click": "mouse.click",
        "mouse_click": "mouse.click",
        "double_click": "mouse.double_click",
        "right_click": "mouse.right_click",
        "move_mouse": "mouse.move",
        "drag": "mouse.drag",
        "scroll": "mouse.scroll",
        "type": "keyboard.type_text",
        "type_text": "keyboard.type_text",
        "key": "keyboard.hotkey",
        "hotkey": "keyboard.hotkey",
        "screenshot": "screen.screenshot",
        "screen": "screen.screenshot",
        "screen_resolution": "screen.resolution",
        "active_window": "window.active",
        # Legacy name-based navigation removed for pure vision + mouse (OpenAI Computer Use style)
        # Use "computer" tool or vision "navigate" with target/description instead.
        "navigate": "app.open",
        "close_app": "app.close",
        "shell": "shell.run",
        "read_file": "filesystem.read",
        "write_file": "filesystem.write",
        "list_dir": "filesystem.list",
        "move_file": "filesystem.move",
        "delete_file": "filesystem.delete",
        "search": "browser.search",
        "read_page": "browser.read_page",
        "wait": "task.wait",
        "computer": "computer.execute",
        "raw": "reasoning.raw",
    }

    def route_step(self, step: dict[str, Any], *, task_id: str | None = None) -> ToolRequest:
        step_type = str(step.get("type") or step.get("tool_id") or "raw")
        tool_id = str(step.get("tool_id") or self.LEGACY_STEP_MAP.get(step_type, step_type))
        payload = self._normalize_payload(tool_id, step)
        return ToolRequest(
            tool_id=tool_id,
            input=payload,
            task_id=task_id,
            dry_run=bool(step.get("dry_run", False)),
            safe_mode=bool(step.get("safe_mode", False)),
            approved_risk=bool(step.get("_confirmed") or step.get("approved_risk", False)),
            metadata={
                "description": step.get("description", ""),
                "legacy_type": step_type,
                "critical": bool(step.get("critical", False)),
                "expected_outcome": step.get("expected_outcome"),
                "confidence": step.get("confidence"),
            },
        )

    def _normalize_payload(self, tool_id: str, step: dict[str, Any]) -> dict[str, Any]:
        payload = dict(step.get("input") or {})
        if payload:
            return payload

        if tool_id.startswith("mouse."):
            target = step.get("target", {})
            if isinstance(target, dict):
                payload.update(
                    {k: v for k, v in target.items() if k in {"x", "y", "text", "bounds"}}
                )
            elif isinstance(target, (list, tuple)) and len(target) >= 2:
                payload.update({"x": int(target[0]), "y": int(target[1])})
            elif isinstance(target, str) and target.strip():
                payload["target"] = {"text": target}
            if "bounds" in step:
                payload.setdefault("target", {})
                if isinstance(payload["target"], dict):
                    payload["target"]["bounds"] = step["bounds"]
            if "confidence" in step:
                payload["confidence"] = step["confidence"]
            if tool_id in {"mouse.click", "mouse.right_click", "mouse.double_click"}:
                payload.setdefault("button", step.get("click_type", "left"))
            if "x" in step:
                payload["x"] = step["x"]
            if "y" in step:
                payload["y"] = step["y"]
            if "direction" in step:
                payload["direction"] = step["direction"]
            if "amount" in step:
                payload["amount"] = step["amount"]
            if tool_id == "mouse.drag":
                if "path" in step:
                    payload["path"] = step["path"]
                for key in ("x1", "y1", "x2", "y2", "duration_ms"):
                    if key in step:
                        payload[key] = step[key]

        elif tool_id.startswith("keyboard."):
            if "text" in step:
                payload["text"] = step["text"]
            if "value" in step:
                payload.setdefault("text", step["value"])
                payload.setdefault("keys", step["value"])
            if "keys" in step:
                payload["keys"] = step["keys"]
            if "target" in step:
                payload["target"] = step["target"]

        elif tool_id in ("app.open", "ui.navigate_vision"):
            # Force vision target; no app names
            payload["target"] = step.get("target") or step.get("description") or step.get("command")
            if "app" in step:
                # Convert old app name to visual description (AI should have screenshot context)
                payload["description"] = f"visually locate and interact with {step['app']} using mouse"
        elif tool_id in ("app.close", "ui.close_window"):
            payload["target"] = step.get("target") or step.get("description") or step.get("process")
            if "app" in step:
                payload["description"] = f"visually locate close button for {step['app']}"
        elif tool_id == "shell.run":
            payload["command"] = step.get("command", "")
            if "cwd" in step:
                payload["cwd"] = step["cwd"]
            if "timeout_sec" in step:
                payload["timeout_ms"] = int(float(step["timeout_sec"]) * 1000)
        elif tool_id == "computer.execute":
            payload["actions"] = list(step.get("actions") or [])
        elif tool_id.startswith("filesystem."):
            for key in ("path", "source", "destination", "content"):
                if key in step:
                    payload[key] = step[key]
        elif tool_id == "browser.search":
            payload["query"] = step.get("query") or step.get("description", "")
        elif tool_id == "browser.read_page":
            payload["url"] = step.get("url", "")
        elif tool_id == "task.wait":
            payload["duration_ms"] = int(float(step.get("duration_sec", 0.5)) * 1000)
        else:
            payload.update({k: v for k, v in step.items() if k not in {"type", "tool_id"}})

        return payload
