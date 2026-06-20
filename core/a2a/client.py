"""A2A client — discover and call external A2A agents.

Adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis),
licensed under the Apache License 2.0. See core/a2a/NOTICE.
"""

from __future__ import annotations

from typing import Any

from core.a2a.protocol import A2ARequest, A2ATask, AgentCard


class A2AClient:
    """Client for calling external A2A-compatible agents.

    Discovers agent capabilities via ``/.well-known/agent.json`` and sends
    tasks via ``/a2a/tasks``.
    """

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._card: AgentCard | None = None

    def discover(self) -> AgentCard:
        """Fetch the agent card from ``/.well-known/agent.json``."""
        import httpx

        resp = httpx.get(
            f"{self._base_url}/.well-known/agent.json",
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        self._card = AgentCard(
            name=data.get("name", ""),
            description=data.get("description", ""),
            url=data.get("url", self._base_url),
            version=data.get("version", ""),
            capabilities=data.get("capabilities", []),
            skills=data.get("skills", []),
        )
        return self._card

    def send_task(self, input_text: str, **kwargs: Any) -> A2ATask:
        """Send a task to the remote agent and return the result."""
        return self._task_request(
            "tasks/send",
            {"message": {"role": "user", "parts": [{"text": input_text}]}},
            fallback_input=input_text,
        )

    def get_task(self, task_id: str) -> A2ATask:
        """Get the status of a previously submitted task."""
        return self._task_request("tasks/get", {"id": task_id}, fallback_id=task_id)

    def cancel_task(self, task_id: str) -> A2ATask:
        """Cancel a running task."""
        return self._task_request(
            "tasks/cancel", {"id": task_id}, fallback_id=task_id, default_state="canceled"
        )

    def _task_request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        fallback_input: str = "",
        fallback_id: str = "",
        default_state: str = "unknown",
    ) -> A2ATask:
        import httpx

        request = A2ARequest(method=method, params=params)
        resp = httpx.post(
            f"{self._base_url}/a2a/tasks",
            json=request.to_dict(),
            timeout=self._timeout,
        )
        resp.raise_for_status()
        result = resp.json().get("result", {})
        return A2ATask(
            task_id=result.get("id", fallback_id),
            state=result.get("state", default_state),
            input_text=result.get("input", fallback_input),
            output_text=result.get("output", ""),
            history=result.get("history", []),
        )


__all__ = ["A2AClient"]
