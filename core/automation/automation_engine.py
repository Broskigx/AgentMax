"""
AgentMax Automation Engine

Advanced automation capabilities with workflow support,
scheduled tasks, and event-driven automation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import structlog

if TYPE_CHECKING:
    from fastapi import APIRouter

log = structlog.get_logger(__name__)


class AutomationType(str, Enum):
    """Types of automations available"""

    WORKFLOW = "workflow"
    SCHEDULED = "scheduled"
    EVENT_DRIVEN = "event_driven"
    CONDITIONAL = "conditional"
    CHAIN = "chain"
    PIPELINE = "pipeline"


class TriggerType(str, Enum):
    """Triggers that can start an automation"""

    MANUAL = "manual"
    SCHEDULE = "schedule"
    WEBHOOK = "webhook"
    EVENT = "event"
    CONDITION = "condition"
    API_CALL = "api_call"


class ActionType(str, Enum):
    """Available actions in automations"""

    HTTP_REQUEST = "http_request"
    TRANSFORM_DATA = "transform_data"
    RUN_CODE = "run_code"
    SEND_NOTIFICATION = "send_notification"
    DATABASE_QUERY = "database_query"
    CALL_PLUGIN = "call_plugin"
    WAIT = "wait"
    CONDITION = "condition"
    LOOP = "loop"
    PARALLEL = "parallel"
    LOG = "log"
    TRANSFORM_JSON = "transform_json"


class ExecutionStatus(str, Enum):
    """Status of automation execution"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass
class AutomationStep:
    """Individual step in an automation"""

    step_id: str
    action_type: ActionType
    config: dict[str, Any]
    retry_config: dict[str, Any] | None = None
    timeout_seconds: int = 30
    continue_on_error: bool = False

    def get_hash(self) -> str:
        """Generate unique hash for this step"""
        content = f"{self.step_id}{self.action_type.value}{json.dumps(self.config, sort_keys=True)}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]


@dataclass
class Automation:
    """Automation definition"""

    automation_id: str
    name: str
    description: str | None
    automation_type: AutomationType
    trigger_type: TriggerType
    created_by: str
    created_at: datetime
    updated_at: datetime

    steps: list[AutomationStep] = field(default_factory=list)

    trigger_config: dict[str, Any] = field(default_factory=dict)
    execution_config: dict[str, Any] = field(default_factory=dict)

    is_active: bool = True
    is_public: bool = False

    schedule: str | None = None  # Cron expression
    webhook_path: str | None = None

    tags: list[str] = field(default_factory=list)
    version: int = 1

    max_execution_time: int = 300  # 5 minutes
    max_retries: int = 3
    retry_delay: int = 5

    def validate(self) -> bool:
        """Validate automation configuration"""
        if not self.name or len(self.name) < 3:
            return False

        if not self.steps:
            return False

        if self.trigger_type == TriggerType.SCHEDULE and not self.schedule:
            return False

        if self.trigger_type == TriggerType.WEBHOOK and not self.webhook_path:
            return False

        return True


@dataclass
class ExecutionContext:
    """Context for automation execution"""

    execution_id: str
    automation_id: str
    trigger_type: TriggerType
    trigger_data: dict[str, Any]
    started_at: datetime

    variables: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)

    completed_at: datetime | None = None

    current_step_index: int = 0
    step_results: list[dict[str, Any]] = field(default_factory=list)

    status: ExecutionStatus = ExecutionStatus.PENDING
    error: str | None = None

    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """Result of an automation execution"""

    execution_id: str
    automation_id: str
    status: ExecutionStatus

    started_at: datetime
    completed_at: datetime

    step_results: list[dict[str, Any]]
    output: Any | None = None

    error: str | None = None
    traceback: str | None = None

    duration_ms: int = 0
    tokens_used: int = 0


class ActionExecutor:
    """Executor for individual automation actions"""

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self.http_client = http_client or httpx.AsyncClient(timeout=30.0)
        self._action_handlers: dict[ActionType, Callable] = {}
        self._register_default_handlers()

    def _register_default_handlers(self):
        """Register default action handlers"""
        self._action_handlers[ActionType.HTTP_REQUEST] = self._execute_http
        self._action_handlers[ActionType.WAIT] = self._execute_wait
        self._action_handlers[ActionType.LOG] = self._execute_log
        self._action_handlers[ActionType.TRANSFORM_DATA] = self._execute_transform
        self._action_handlers[ActionType.CONDITION] = self._execute_condition
        self._action_handlers[ActionType.LOOP] = self._execute_loop

    async def execute(self, step: AutomationStep, context: ExecutionContext) -> dict[str, Any]:
        """Execute a single automation step"""
        handler = self._action_handlers.get(step.action_type)

        if not handler:
            return {
                "success": False,
                "error": f"No handler for action type: {step.action_type.value}",
                "step_id": step.step_id,
            }

        try:
            result = await asyncio.wait_for(handler(step, context), timeout=step.timeout_seconds)

            return {"success": True, "step_id": step.step_id, "result": result}

        except TimeoutError:
            return {
                "success": False,
                "error": f"Step timed out after {step.timeout_seconds}s",
                "step_id": step.step_id,
                "step_hash": step.get_hash(),
            }

        except Exception as exc:
            log.error("automation_step_error", step_id=step.step_id, error=str(exc))

            return {
                "success": False,
                "error": str(exc),
                "step_id": step.step_id,
                "step_hash": step.get_hash(),
            }

    async def _execute_http(
        self, step: AutomationStep, context: ExecutionContext
    ) -> dict[str, Any]:
        """Execute HTTP request action"""
        config = step.config

        url = self._resolve_variables(config.get("url", ""), context)
        method = config.get("method", "GET").upper()
        headers = config.get("headers", {})
        body = config.get("body")

        # Resolve variables in body
        if body and isinstance(body, str):
            body = self._resolve_variables(body, context)

        # Make request
        response = await self.http_client.request(
            method=method,
            url=url,
            headers=headers,
            json=body if body and method in ["POST", "PUT", "PATCH"] else None,
            params=config.get("params"),
        )

        return {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": response.text[:10000],  # Limit response size
            "elapsed_ms": int(response.elapsed.total_seconds() * 1000),
        }

    async def _execute_wait(
        self, step: AutomationStep, context: ExecutionContext
    ) -> dict[str, Any]:
        """Execute wait action"""
        seconds = step.config.get("seconds", 1)
        await asyncio.sleep(seconds)
        return {"waited_seconds": seconds}

    async def _execute_log(self, step: AutomationStep, context: ExecutionContext) -> dict[str, Any]:
        """Execute log action"""
        message = self._resolve_variables(step.config.get("message", ""), context)
        level = str(step.config.get("level", "info")).lower()

        log_method = getattr(log, level, log.info)
        log_method("automation_log", message=message, execution_id=context.execution_id)

        return {"logged": True, "message": message}

    async def _execute_transform(
        self, step: AutomationStep, context: ExecutionContext
    ) -> dict[str, Any]:
        """Execute data transformation action"""
        transform_type = step.config.get("type", "map")

        if transform_type == "map":
            # Simple key-value mapping
            mapping = step.config.get("mapping", {})
            result = {}

            for key, value in mapping.items():
                resolved_value = self._resolve_variables(str(value), context)
                result[key] = resolved_value

            context.variables.update(result)
            return {"transformed": result}

        elif transform_type == "filter":
            # Filter items from list
            input_list = context.variables.get(step.config.get("input_var", "data"), [])
            condition = step.config.get("condition", {})

            filtered = [item for item in input_list if self._evaluate_condition(condition, item)]

            context.variables[step.config.get("output_var", "filtered")] = filtered
            return {"filtered_count": len(filtered)}

        elif transform_type == "merge":
            # Merge multiple sources
            sources = step.config.get("sources", [])
            merged = {}

            for source in sources:
                var_name = source.get("var")
                if var_name and var_name in context.variables:
                    merged.update(context.variables[var_name])

            context.variables[step.config.get("output_var", "merged")] = merged
            return {"merged_keys": len(merged)}

        return {"transform_type": transform_type}

    async def _execute_condition(
        self, step: AutomationStep, context: ExecutionContext
    ) -> dict[str, Any]:
        """Execute conditional action"""
        condition = step.config.get("condition", {})

        result = self._evaluate_condition(condition, context.variables)

        return {"condition_met": result, "condition": condition}

    async def _execute_loop(
        self, step: AutomationStep, context: ExecutionContext
    ) -> dict[str, Any]:
        """Execute loop action"""
        loop_type = step.config.get("type", "for")

        if loop_type == "for":
            items = self._resolve_variables(step.config.get("items", []), context)
            max_iterations = step.config.get("max_iterations", 100)

            results = []
            for i, item in enumerate(items[:max_iterations]):
                context.variables["loop_item"] = item
                context.variables["loop_index"] = i

                # Execute nested steps would go here
                results.append({"index": i, "item": str(item)[:100]})

            return {"iterations": len(results), "results": results[:10]}

        return {"loop_type": loop_type}

    def _resolve_variables(self, value: str, context: ExecutionContext) -> Any:
        """Resolve variable references in strings"""
        if not isinstance(value, str):
            return value

        import re

        pattern = r"\{\{([^}]+)\}\}"

        def replace_var(match):
            var_path = match.group(1).strip()

            # Handle nested access
            parts = var_path.split(".")
            current = context.variables

            for part in parts:
                if isinstance(current, dict):
                    current = current.get(part)
                elif isinstance(current, list) and part.isdigit():
                    current = current[int(part)]
                else:
                    return match.group(0)

            return str(current) if current is not None else match.group(0)

        resolved = re.sub(pattern, replace_var, value)

        # Try to parse as JSON if it looks like it
        if resolved.startswith("{") or resolved.startswith("["):
            try:
                return json.loads(resolved)
            except json.JSONDecodeError:
                pass

        return resolved

    def _evaluate_condition(self, condition: dict[str, Any], data: dict[str, Any]) -> bool:
        """Evaluate a condition against data"""
        operator = condition.get("operator", "eq")
        field = condition.get("field", "")
        value = condition.get("value")

        # Get field value
        field_value = data
        for part in field.split("."):
            if isinstance(field_value, dict):
                field_value = field_value.get(part)
            else:
                field_value = None
                break

        # Evaluate
        if operator == "eq":
            return field_value == value
        elif operator == "ne":
            return field_value != value
        elif operator == "gt":
            return field_value > value
        elif operator == "lt":
            return field_value < value
        elif operator == "contains":
            return value in (field_value or "")
        elif operator == "exists":
            return field_value is not None
        elif operator == "empty":
            return not field_value

        return False


def _cron_field_matches(field: str, value: int, min_value: int, max_value: int) -> bool:
    """Match a single standard-cron field (``*``, ``*/n``, ``a-b``, ``a,b,c``, ``n``)."""
    field = field.strip()
    if field in ("*", "?"):
        return True
    for part in field.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            base, _, step_str = part.partition("/")
            if not step_str.isdigit() or int(step_str) <= 0:
                continue
            step = int(step_str)
            part = base.strip() or "*"
        if part in ("*", "?"):
            lo, hi = min_value, max_value
        elif "-" in part:
            lo_str, _, hi_str = part.partition("-")
            if not (lo_str.isdigit() and hi_str.isdigit()):
                continue
            lo, hi = int(lo_str), int(hi_str)
        elif part.isdigit():
            lo = hi = int(part)
        else:
            continue
        if lo > hi:
            continue
        if value < lo or value > hi:
            continue
        if (value - lo) % step == 0:
            return True
    return False


def cron_matches(expression: str, when: datetime) -> bool:
    """Return True if a 5-field cron expression is due at ``when`` (minute precision).

    Fields: ``minute hour day-of-month month day-of-week`` (Sunday = 0 or 7).
    Supports ``*``, ``*/n``, ranges ``a-b``, and lists ``a,b,c``.
    """
    fields = expression.split()
    if len(fields) != 5:
        return False
    minute, hour, dom, month, dow = fields
    dow_value = when.isoweekday() % 7  # Mon..Sun -> 1..6,0  (Sunday = 0)
    return (
        _cron_field_matches(minute, when.minute, 0, 59)
        and _cron_field_matches(hour, when.hour, 0, 23)
        and _cron_field_matches(dom, when.day, 1, 31)
        and _cron_field_matches(month, when.month, 1, 12)
        and (
            _cron_field_matches(dow, dow_value, 0, 6)
            or _cron_field_matches(dow, 7 if dow_value == 0 else dow_value, 0, 7)
        )
    )


class AutomationEngine:
    """Main automation execution engine"""

    def __init__(self, config: AutomationEngineConfig | None = None):
        self.config = config or AutomationEngineConfig()
        self.executor = ActionExecutor()
        self._active_executions: dict[str, ExecutionContext] = {}
        self._scheduled_tasks: dict[str, asyncio.Task] = {}
        # In-memory registry of known automations, keyed by automation_id.
        self._automations: dict[str, Automation] = {}
        # Minute-truncated timestamp of the last scheduled fire per automation,
        # so a cron entry runs at most once per matching minute.
        self._last_scheduled_run: dict[str, datetime] = {}
        self._scheduler_task: asyncio.Task | None = None
        # Strong refs to schedule-triggered executions: asyncio keeps only weak
        # references, so an unreferenced task can be garbage-collected mid-run.
        self._triggered_tasks: set[asyncio.Task] = set()
        self._running = False

    # ── Registry ──────────────────────────────────────────────────────────────

    def register_automation(self, automation: Automation) -> None:
        """Register (or replace) an automation so it can be executed or scheduled."""
        self._automations[automation.automation_id] = automation
        log.info(
            "automation_registered",
            automation_id=automation.automation_id,
            name=automation.name,
            trigger_type=automation.trigger_type.value,
        )

    def unregister_automation(self, automation_id: str) -> bool:
        """Remove an automation from the registry. Returns True if it existed."""
        self._last_scheduled_run.pop(automation_id, None)
        return self._automations.pop(automation_id, None) is not None

    def get_automation(self, automation_id: str) -> Automation | None:
        """Return a registered automation by id, or None."""
        return self._automations.get(automation_id)

    def list_automations(self) -> list[Automation]:
        """Return all registered automations."""
        return list(self._automations.values())

    async def start(self):
        """Start the automation engine"""
        self._running = True
        log.info("automation_engine_started")

        # Start scheduler
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def stop(self):
        """Stop the automation engine"""
        self._running = False

        # Stop the scheduler loop immediately instead of waiting for its next tick
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            self._scheduler_task = None

        # Cancel all scheduled tasks
        for task in self._scheduled_tasks.values():
            task.cancel()

        # Wait for active executions
        if self._active_executions:
            log.warning("stopping_with_active_executions", count=len(self._active_executions))

        log.info("automation_engine_stopped")

    async def execute_automation(
        self,
        automation: Automation,
        trigger_data: dict[str, Any] | None = None,
        trigger_type: TriggerType | None = None,
    ) -> ExecutionResult:
        """Execute an automation"""

        # Create execution context. Trigger data is seeded into the variable
        # scope so steps can reference inputs via {{key}} (and the full payload
        # via {{trigger.key}}).
        execution_id = str(uuid4())
        trigger_data = trigger_data or {}
        context = ExecutionContext(
            execution_id=execution_id,
            automation_id=automation.automation_id,
            trigger_type=trigger_type or automation.trigger_type,
            trigger_data=trigger_data,
            started_at=datetime.now(UTC),
            status=ExecutionStatus.RUNNING,
            variables={"trigger": trigger_data, **trigger_data},
        )

        self._active_executions[execution_id] = context

        try:
            # Validate automation
            if not automation.validate():
                raise ValueError("Invalid automation configuration")

            # Execute each step
            for i, step in enumerate(automation.steps):
                context.current_step_index = i

                result = await self.executor.execute(step, context)
                context.step_results.append(result)

                # Check if step failed
                if not result.get("success", False):
                    if not step.continue_on_error:
                        context.status = ExecutionStatus.FAILED
                        context.error = result.get("error")
                        break

                # Update variables from step result
                if result.get("result"):
                    context.variables["last_result"] = result["result"]

            # Mark as completed
            if context.status == ExecutionStatus.RUNNING:
                context.status = ExecutionStatus.COMPLETED

        except Exception as exc:
            context.status = ExecutionStatus.FAILED
            context.error = str(exc)
            log.error(
                "automation_execution_error", automation_id=automation.automation_id, error=str(exc)
            )

        finally:
            context.completed_at = datetime.now(UTC)
            del self._active_executions[execution_id]

        # Create result
        duration_ms = int((context.completed_at - context.started_at).total_seconds() * 1000)

        result = ExecutionResult(
            execution_id=execution_id,
            automation_id=automation.automation_id,
            status=context.status,
            started_at=context.started_at,
            completed_at=context.completed_at,
            step_results=context.step_results,
            error=context.error,
            duration_ms=duration_ms,
        )

        log.info(
            "automation_execution_completed",
            execution_id=execution_id,
            status=result.status.value,
            duration_ms=duration_ms,
        )

        return result

    async def execute_workflow(
        self, workflow_id: str, input_data: dict[str, Any]
    ) -> ExecutionResult:
        """Execute a previously registered workflow/automation by id."""
        automation = self._automations.get(workflow_id)
        if automation is None:
            raise KeyError(f"Automation not found: {workflow_id}")

        return await self.execute_automation(
            automation, input_data, trigger_type=TriggerType.API_CALL
        )

    async def get_execution_status(self, execution_id: str) -> ExecutionContext | None:
        """Get status of a running execution"""
        return self._active_executions.get(execution_id)

    async def cancel_execution(self, execution_id: str) -> bool:
        """Cancel a running execution"""
        if execution_id in self._active_executions:
            context = self._active_executions[execution_id]
            context.status = ExecutionStatus.CANCELLED
            context.completed_at = datetime.now(UTC)
            return True
        return False

    async def _scheduler_loop(self):
        """Main scheduler loop: fire registered SCHEDULE automations when due."""
        interval = max(1, self.config.schedule_check_interval_seconds)
        while self._running:
            try:
                await self._run_due_schedules(datetime.now(UTC))
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("scheduler_loop_error", error=str(exc))
                await asyncio.sleep(5)

    async def _run_due_schedules(self, now: datetime) -> int:
        """Fire every scheduled automation whose cron matches ``now``.

        Returns the number of automations launched. Each automation runs at most
        once per matching minute (deduplicated via ``_last_scheduled_run``).
        """
        launched = 0
        current_minute = now.replace(second=0, microsecond=0)

        for automation in list(self._automations.values()):
            if not automation.is_active:
                continue
            if automation.trigger_type != TriggerType.SCHEDULE or not automation.schedule:
                continue
            if not cron_matches(automation.schedule, now):
                continue
            if self._last_scheduled_run.get(automation.automation_id) == current_minute:
                continue

            self._last_scheduled_run[automation.automation_id] = current_minute
            log.info(
                "scheduled_automation_triggered",
                automation_id=automation.automation_id,
                schedule=automation.schedule,
            )
            task = asyncio.create_task(
                self.execute_automation(automation, {}, trigger_type=TriggerType.SCHEDULE)
            )
            self._triggered_tasks.add(task)
            task.add_done_callback(self._triggered_tasks.discard)
            launched += 1

        return launched


@dataclass
class AutomationEngineConfig:
    """Configuration for automation engine"""

    max_concurrent_executions: int = 10
    default_timeout_seconds: int = 300
    enable_scheduling: bool = True
    schedule_check_interval_seconds: int = 10
    max_execution_history: int = 100


class AutomationAPIRouter:
    """FastAPI router for automation endpoints"""

    def __init__(self, engine: AutomationEngine):
        self.engine = engine

    def get_router(self) -> APIRouter:
        from fastapi import APIRouter, HTTPException

        router = APIRouter(prefix="/automations", tags=["Automations"])

        @router.post("/execute/{automation_id}")
        async def execute_automation(
            automation_id: str, trigger_data: dict[str, Any] | None = None
        ):
            try:
                result = await self.engine.execute_workflow(automation_id, trigger_data or {})
            except KeyError:
                raise HTTPException(status_code=404, detail="Automation not found") from None

            return {
                "execution_id": result.execution_id,
                "status": result.status.value,
                "duration_ms": result.duration_ms,
            }

        @router.get("/executions/{execution_id}")
        async def get_execution(execution_id: str):
            context = await self.engine.get_execution_status(execution_id)

            if not context:
                raise HTTPException(status_code=404, detail="Execution not found")

            return {
                "execution_id": context.execution_id,
                "status": context.status.value,
                "current_step": context.current_step_index,
                "started_at": context.started_at.isoformat(),
            }

        @router.post("/executions/{execution_id}/cancel")
        async def cancel_execution(execution_id: str):
            cancelled = await self.engine.cancel_execution(execution_id)

            if not cancelled:
                raise HTTPException(status_code=404, detail="Execution not found")

            return {"status": "cancelled"}

        return router
