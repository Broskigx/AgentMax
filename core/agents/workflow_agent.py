"""Workflow Agent -- records, optimizes, and replays automation workflows."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from core.agents.base_agent import AgentCapability, AgentContext, BaseAgent

log = structlog.get_logger(__name__)


@dataclass
class Workflow:
    id: str
    name: str
    trigger: str
    steps: list[dict[str, Any]]
    app_context: str
    created_at: float = field(default_factory=time.time)
    run_count: int = 0
    avg_duration_sec: float = 0.0
    success_rate: float = 1.0


class WorkflowAgent(BaseAgent):
    """
    Manages reusable automation workflows.

    Features:
      - Record workflows from successful task executions
      - Replay workflows with adaptive step timing
      - Suggest workflows for recurring tasks
      - Persist workflows to disk
    """

    def __init__(self, ctx: AgentContext) -> None:
        super().__init__(ctx)
        self._workflows: dict[str, Workflow] = {}
        self._workflow_dir = Path("./data/workflows")
        self._workflow_dir.mkdir(parents=True, exist_ok=True)
        self._load_all()

    @property
    def name(self) -> str:
        return "workflow"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return [
            AgentCapability("record_workflow", "Save task steps as reusable workflow"),
            AgentCapability("replay_workflow", "Execute a saved workflow"),
            AgentCapability("suggest_workflow", "Find matching workflows for a task"),
        ]

    def _subscribe(self) -> None:
        super()._subscribe()
        self.bus.subscribe("task.completed", self._on_task_completed)

    async def _on_task_completed(self, event: Any) -> None:
        """Auto-save a workflow when a task completes successfully with 3+ steps."""
        payload = getattr(event, "payload", None) or {}
        task_id = payload.get("task_id", "")
        steps_count = payload.get("steps", 0)
        if steps_count < 3:
            return

        # Pull the full task record from the supervisor's history
        supervisor = self.ctx.runtime._agent_pool.get("supervisor")
        if not supervisor:
            return

        status = await supervisor.get_task_status(task_id)
        if not status or status.get("status") != "COMPLETED":
            return

        description = status.get("description", task_id)
        steps = status.get("steps_data", [])
        if not steps:
            return

        existing = self.find_similar(description, limit=1)
        if existing and self._similarity(description.lower(), existing[0].trigger) > 0.85:
            log.debug("workflow.skip_duplicate", trigger=description[:60])
            return

        await self.record_workflow(
            name=description[:80],
            steps=steps,
            app=status.get("app_context", ""),
        )

    async def record_workflow(self, name: str, steps: list[dict], app: str = "") -> str:
        wf_id = f"wf_{int(time.time())}"
        wf = Workflow(id=wf_id, name=name, trigger=name.lower(), steps=steps, app_context=app)
        self._workflows[wf_id] = wf
        self._save(wf)
        log.info("workflow.recorded", id=wf_id, name=name, steps=len(steps))
        return wf_id

    async def replay_workflow(self, workflow_id: str) -> bool:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return False
        supervisor = self.ctx.runtime._agent_pool.get("supervisor")
        if not supervisor:
            return False
        from core.agents.supervisor import TaskRequest

        req = TaskRequest(description=f"[WORKFLOW] {wf.name}", options={"steps": wf.steps})
        await supervisor.submit_task(req)
        return True

    def find_similar(self, description: str, limit: int = 3) -> list[Workflow]:
        desc_lower = description.lower()
        scored = [(wf, self._similarity(desc_lower, wf.trigger)) for wf in self._workflows.values()]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [wf for wf, score in scored[:limit] if score > 0.3]

    def _similarity(self, a: str, b: str) -> float:
        a_words = set(a.split())
        b_words = set(b.split())
        if not a_words or not b_words:
            return 0.0
        return len(a_words & b_words) / len(a_words | b_words)

    def _save(self, wf: Workflow) -> None:
        path = self._workflow_dir / f"{wf.id}.json"
        path.write_text(
            json.dumps(
                {
                    "id": wf.id,
                    "name": wf.name,
                    "trigger": wf.trigger,
                    "steps": wf.steps,
                    "app_context": wf.app_context,
                    "created_at": wf.created_at,
                    "run_count": wf.run_count,
                },
                indent=2,
            )
        )

    def _load_all(self) -> None:
        for path in self._workflow_dir.glob("wf_*.json"):
            try:
                data = json.loads(path.read_text())
                wf = Workflow(**{k: data[k] for k in Workflow.__dataclass_fields__ if k in data})
                self._workflows[wf.id] = wf
            except Exception as exc:
                log.warning("workflow.load_error", path=str(path), error=str(exc))

    @property
    def all_workflows(self) -> list[dict]:
        return [
            {"id": w.id, "name": w.name, "run_count": w.run_count} for w in self._workflows.values()
        ]
