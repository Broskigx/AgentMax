"""Memory Agent -- manages short-term context, long-term learning, and workflow recall."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import structlog

from core.agents.base_agent import AgentCapability, BaseAgent

if TYPE_CHECKING:
    from core.agents.supervisor import TaskRecord

log = structlog.get_logger(__name__)


class MemoryAgent(BaseAgent):
    """
    Hybrid memory manager:
      - STM: recent events, current task context
      - LTM: ChromaDB semantic store for past tasks/workflows
      - Visual memory: per-app layout cache
      - Workflow memory: successful automation sequences
    """

    @property
    def name(self) -> str:
        return "memory"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return [
            AgentCapability("store_task", "Persist task outcome for learning"),
            AgentCapability("recall_similar", "Retrieve similar past tasks"),
            AgentCapability("get_workflow", "Retrieve cached workflow for app"),
        ]

    async def store_task_outcome(self, record: TaskRecord) -> None:
        entry = {
            "task_id": record.request.id,
            "description": record.request.description,
            "status": record.status.name,
            "steps": len(record.plan.steps) if record.plan else 0,
            "success_rate": self._calc_success_rate(record),
            "duration_sec": (record.end_time or time.monotonic()) - record.start_time,
            "timestamp": time.time(),
        }
        thinking_state = getattr(record, "thinking_state", None)
        if thinking_state:
            entry["thinking_core"] = thinking_state.public_summary()

        self.ctx.stm.put(f"task:{record.request.id}", entry)

        if entry["success_rate"] > 0.8 and record.plan:
            await self.ctx.ltm.store(
                document=json.dumps(
                    {
                        "description": record.request.description,
                        "steps": record.plan.steps,
                        "success_rate": entry["success_rate"],
                        "thinking_core": entry.get("thinking_core"),
                    }
                ),
                metadata={
                    "type": "task_workflow",
                    "task_id": record.request.id,
                    "timestamp": str(time.time()),
                },
                doc_id=record.request.id,
            )
            log.info("memory.workflow_stored", task_id=record.request.id)

    async def recall_similar_tasks(self, description: str, limit: int = 3) -> list[dict]:
        results = await self.ctx.ltm.search(description, n_results=limit)
        return [
            {
                "description": r["description"],
                "steps": r.get("steps", []),
                "score": r.get("score", 0),
            }
            for r in results
        ]

    async def get_app_layout(self, app_name: str) -> dict | None:
        return self.ctx.visual_memory.get(app_name)

    async def store_app_layout(self, app_name: str, layout: dict) -> None:
        self.ctx.visual_memory.put(app_name, layout)

    def _calc_success_rate(self, record: TaskRecord) -> float:
        if not record.results:
            return 0.0
        return sum(1 for r in record.results if r.success) / len(record.results)
