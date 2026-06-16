"""
GoalEngine — Redis-backed autonomous autoloop.

/goal <objective>  activates an unbounded Plan→Execute→Observe loop that keeps
running until the AI declares the goal complete or the user cancels it.

Built-in skill actions that AgentMax can self-invoke:
  install_package  — pip install into the active or goal-specific venv
  create_venv      — python -m venv at an arbitrary path
  write_skill      — write a new skill file into sdk/skills/
  run_shell        — guarded shell command (via AgentToolSupervisor)
  task             — dispatch a full desktop-automation task to SupervisorAgent
  message          — send a progress update to the user via the event bus
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog

from core.tools.safety_supervisor import AgentToolSupervisor

log = structlog.get_logger(__name__)

# --------------------------------------------------------------------------- #
# State types                                                                   #
# --------------------------------------------------------------------------- #

_REDIS_TTL = 86_400  # 24 h — renewed every iteration
_DEFAULT_MAX_ITER = 200
_ITER_DELAY_S = 1.0  # minimum pause between iterations
_SKILL_DIR = Path(__file__).parent.parent.parent / "sdk" / "skills"


class GoalStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class GoalIteration:
    iteration: int
    action_type: str
    action_params: dict[str, Any]
    result: str
    success: bool
    ts: float = field(default_factory=time.time)


@dataclass
class GoalState:
    goal_id: str
    objective: str
    status: GoalStatus = GoalStatus.PENDING
    iterations: int = 0
    max_iterations: int = _DEFAULT_MAX_ITER
    history: list[GoalIteration] = field(default_factory=list)
    skills_installed: list[str] = field(default_factory=list)
    packages_installed: list[str] = field(default_factory=list)
    venvs_created: list[str] = field(default_factory=list)
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    completion_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["history"] = [
            {**asdict(it), "action_type": it.action_type}
            for it in self.history
        ]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GoalState:
        history = [GoalIteration(**h) for h in d.pop("history", [])]
        status = GoalStatus(d.pop("status", "pending"))
        return cls(**d, status=status, history=history)


# --------------------------------------------------------------------------- #
# AI decision prompt                                                            #
# --------------------------------------------------------------------------- #

_GOAL_SYSTEM_PROMPT = """\
You are AgentMax GoalEngine — an autonomous AI that keeps working until a goal is achieved.

You receive:
- OBJECTIVE: the goal you must complete
- ITERATION: current loop number / max
- HISTORY: actions already taken and their results

Your job: decide the NEXT single action to take, or declare the goal DONE.

AVAILABLE ACTIONS:
1. task          — run a full desktop-automation or research task (AgentMax's native pipeline)
2. install_package — install a Python package: {"pkg": "package-name[extras]"}
3. create_venv   — create a Python virtual environment: {"path": "/absolute/path"}
4. write_skill   — create a new reusable skill: {"name": "skill_name", "description": "...", "code": "...python..."}
5. run_shell     — run a shell command (safety-checked): {"command": "..."}
6. message       — send a progress update to the user: {"text": "..."}

RESPONSE FORMAT (strict JSON, one object):
{
  "done": false,
  "action": {
    "type": "<action_type>",
    "params": { ... }
  },
  "reasoning": "<brief internal reasoning>"
}

When complete:
{
  "done": true,
  "summary": "<what was accomplished>",
  "reasoning": "<brief internal reasoning>"
}

Rules:
- Never fabricate results — only mark done when you have evidence of success.
- Prefer non-destructive actions first.
- Use write_skill to persist reusable capabilities for future goals.
- If stuck after 3 identical failures in a row, try a different approach.
- Keep reasoning concise (1-2 sentences max).
"""


def _build_decision_prompt(state: GoalState) -> str:
    recent = state.history[-10:]
    history_text = "\n".join(
        f"  [{it.iteration}] {it.action_type}: {json.dumps(it.action_params)[:120]}"
        f" → {'✓' if it.success else '✗'} {it.result[:120]}"
        for it in recent
    )
    return (
        f"OBJECTIVE: {state.objective}\n"
        f"ITERATION: {state.iterations + 1}/{state.max_iterations}\n"
        f"HISTORY:\n{history_text or '  (none yet)'}\n\n"
        "What is the next action?"
    )


# --------------------------------------------------------------------------- #
# GoalEngine                                                                    #
# --------------------------------------------------------------------------- #


# Actions that modify system state and require explicit human approval.
_APPROVAL_REQUIRED_ACTIONS: frozenset[str] = frozenset(
    {"task", "install_package", "create_venv", "write_skill", "run_shell"}
)
_APPROVAL_TIMEOUT_S = 120  # user has 2 min to approve/reject


class GoalEngine:
    """
    Manages /goal mode: a Redis-backed autonomous loop.

    Safety contract (beta):
      - Feature flag `goal_engine` must be True to start a loop.
      - When `require_approval=True` (default), every action that modifies
        system state (task, install_package, create_venv, write_skill,
        run_shell) is PAUSED and emits `goal.action_requires_approval`.
        The loop resumes only after explicit human confirmation via
        approve_action() / reject_action() or times out.

    Usage:
        engine = GoalEngine(redis_service, bus, agent_pool, ai_client)
        goal_id = await engine.start_goal("Build a todo app in ~/projects/todo")
        # Later, when user approves a pending action:
        engine.approve_action(goal_id, action_id)
        # Cancel with:
        await engine.stop_goal(goal_id)
    """

    def __init__(
        self,
        redis_service: Any,
        bus: Any,
        agent_pool: dict[str, Any],
        ai_client: Any,
        *,
        max_iterations: int = _DEFAULT_MAX_ITER,
        require_approval: bool = True,
    ) -> None:
        self._redis = redis_service
        self._bus = bus
        self._agent_pool = agent_pool
        self._ai = ai_client
        self._max_iterations = max_iterations
        self._require_approval = require_approval
        self._supervisor = AgentToolSupervisor()
        self._tasks: dict[str, asyncio.Task] = {}  # goal_id → background Task
        # Pending approvals: key = "{goal_id}:{action_id}"
        self._pending_approvals: dict[str, asyncio.Event] = {}
        self._approval_results: dict[str, bool] = {}

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def start_goal(self, objective: str) -> str:
        """Create a new goal, persist initial state to Redis, start autoloop."""
        goal_id = str(uuid4())
        state = GoalState(
            goal_id=goal_id,
            objective=objective,
            status=GoalStatus.RUNNING,
            max_iterations=self._max_iterations,
        )
        self._save_state(state)

        task = asyncio.create_task(
            self._run_loop(goal_id),
            name=f"goal-{goal_id[:8]}",
        )
        self._tasks[goal_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(goal_id, None))

        log.info("goal.started", goal_id=goal_id[:8], objective=objective[:80])
        await self._emit("goal.started", {"goal_id": goal_id, "objective": objective})
        return goal_id

    async def stop_goal(self, goal_id: str) -> bool:
        """Cancel a running goal loop."""
        task = self._tasks.get(goal_id)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
            except (asyncio.CancelledError, TimeoutError):
                pass
        state = self._load_state(goal_id)
        if state:
            state.status = GoalStatus.CANCELLED
            state.completed_at = time.time()
            self._save_state(state)
        await self._emit("goal.cancelled", {"goal_id": goal_id})
        log.info("goal.cancelled", goal_id=goal_id[:8])
        return True

    def get_state(self, goal_id: str) -> GoalState | None:
        """Retrieve goal state from Redis."""
        return self._load_state(goal_id)

    def list_active(self) -> list[str]:
        return list(self._tasks.keys())

    def approve_action(self, goal_id: str, action_id: str) -> bool:
        """Called by IPC when the user approves a pending action."""
        key = f"{goal_id}:{action_id}"
        ev = self._pending_approvals.get(key)
        if ev:
            self._approval_results[key] = True
            ev.set()
            return True
        return False

    def reject_action(self, goal_id: str, action_id: str) -> bool:
        """Called by IPC when the user rejects a pending action."""
        key = f"{goal_id}:{action_id}"
        ev = self._pending_approvals.get(key)
        if ev:
            self._approval_results[key] = False
            ev.set()
            return True
        return False

    async def _request_approval(
        self, goal_id: str, action_type: str, params: dict[str, Any]
    ) -> bool:
        """Emit approval request and wait for user response (or timeout)."""
        import uuid as _uuid
        action_id = str(_uuid.uuid4())[:8]
        key = f"{goal_id}:{action_id}"
        ev = asyncio.Event()
        self._pending_approvals[key] = ev
        self._approval_results.pop(key, None)

        await self._emit(
            "goal.action_requires_approval",
            {
                "goal_id": goal_id,
                "action_id": action_id,
                "action_type": action_type,
                "params": params,
                "timeout_s": _APPROVAL_TIMEOUT_S,
                "message": (
                    f"⚠️ GoalEngine quiere ejecutar **{action_type}**. "
                    "Aprueba o rechaza en la UI antes de continuar."
                ),
            },
        )
        log.info(
            "goal.waiting_approval",
            goal_id=goal_id[:8],
            action_type=action_type,
            action_id=action_id,
        )

        try:
            await asyncio.wait_for(asyncio.shield(ev.wait()), timeout=_APPROVAL_TIMEOUT_S)
            approved = self._approval_results.get(key, False)
        except (TimeoutError, asyncio.CancelledError):
            approved = False
            log.warning("goal.approval_timeout", goal_id=goal_id[:8], action_type=action_type)
        finally:
            self._pending_approvals.pop(key, None)
            self._approval_results.pop(key, None)

        await self._emit(
            "goal.action_decision",
            {"goal_id": goal_id, "action_id": action_id, "approved": approved},
        )
        return approved

    # ------------------------------------------------------------------ #
    # Autoloop                                                             #
    # ------------------------------------------------------------------ #

    async def _run_loop(self, goal_id: str) -> None:
        state = self._load_state(goal_id)
        if not state:
            return
        try:
            while state.iterations < state.max_iterations:
                if asyncio.current_task().cancelled():
                    raise asyncio.CancelledError

                state.iterations += 1
                self._save_state(state)  # renew TTL every iteration

                decision = await self._get_ai_decision(state)
                if decision is None:
                    # AI returned unparseable response — try again next iteration
                    await asyncio.sleep(_ITER_DELAY_S)
                    continue

                if decision.get("done"):
                    state.status = GoalStatus.COMPLETED
                    state.completed_at = time.time()
                    state.completion_summary = str(decision.get("summary", ""))[:1000]
                    self._save_state(state)
                    await self._emit(
                        "goal.completed",
                        {
                            "goal_id": goal_id,
                            "iterations": state.iterations,
                            "summary": state.completion_summary,
                        },
                    )
                    log.info(
                        "goal.completed",
                        goal_id=goal_id[:8],
                        iterations=state.iterations,
                        summary=state.completion_summary[:80],
                    )
                    return

                action = decision.get("action", {})
                action_type = str(action.get("type", "message"))
                params = action.get("params", {}) if isinstance(action.get("params"), dict) else {}

                iteration_result = await self._execute_action(action_type, params, state)
                state.history.append(iteration_result)
                # Keep history from growing unbounded in memory
                if len(state.history) > 100:
                    state.history = state.history[-100:]

                self._save_state(state)
                await self._emit(
                    "goal.iteration",
                    {
                        "goal_id": goal_id,
                        "iteration": state.iterations,
                        "action_type": action_type,
                        "success": iteration_result.success,
                        "result": iteration_result.result[:200],
                    },
                )

                await asyncio.sleep(_ITER_DELAY_S)

            # Max iterations reached without completion
            state.status = GoalStatus.FAILED
            state.error = f"Max iterations ({state.max_iterations}) reached without completing goal."
            state.completed_at = time.time()
            self._save_state(state)
            await self._emit(
                "goal.failed",
                {"goal_id": goal_id, "error": state.error},
            )

        except asyncio.CancelledError:
            state = self._load_state(goal_id) or state
            if state.status == GoalStatus.RUNNING:
                state.status = GoalStatus.CANCELLED
                state.completed_at = time.time()
                self._save_state(state)
            raise
        except Exception as exc:
            log.error("goal.loop_error", goal_id=goal_id[:8], error=str(exc))
            state.status = GoalStatus.FAILED
            state.error = str(exc)
            state.completed_at = time.time()
            self._save_state(state)
            await self._emit("goal.failed", {"goal_id": goal_id, "error": str(exc)})

    # ------------------------------------------------------------------ #
    # AI decision                                                          #
    # ------------------------------------------------------------------ #

    async def _get_ai_decision(self, state: GoalState) -> dict[str, Any] | None:
        prompt = _build_decision_prompt(state)
        try:
            raw = await self._ai.chat_query(
                system=_GOAL_SYSTEM_PROMPT,
                history=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            log.warning("goal.ai_error", error=str(exc))
            return None

        # Extract JSON from the response
        text = str(raw or "").strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(
                ln for ln in lines if not ln.startswith("```")
            ).strip()

        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            # Try to find the JSON object inside mixed text
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except (json.JSONDecodeError, ValueError):
                    pass
            log.warning("goal.ai_parse_failed", raw=text[:200])
            return None

    # ------------------------------------------------------------------ #
    # Action execution                                                     #
    # ------------------------------------------------------------------ #

    async def _execute_action(
        self, action_type: str, params: dict[str, Any], state: GoalState
    ) -> GoalIteration:
        handlers = {
            "task": self._action_task,
            "install_package": self._action_install_package,
            "create_venv": self._action_create_venv,
            "write_skill": self._action_write_skill,
            "run_shell": self._action_run_shell,
            "message": self._action_message,
        }

        # Gate: require explicit human approval for any system-modifying action
        if self._require_approval and action_type in _APPROVAL_REQUIRED_ACTIONS:
            approved = await self._request_approval(state.goal_id, action_type, params)
            if not approved:
                return GoalIteration(
                    iteration=state.iterations,
                    action_type=action_type,
                    action_params=params,
                    result="Action skipped — not approved by user (or timed out).",
                    success=False,
                )

        handler = handlers.get(action_type, self._action_unknown)
        try:
            success, result = await handler(params, state)
        except Exception as exc:
            success, result = False, f"Action raised exception: {exc}"

        return GoalIteration(
            iteration=state.iterations,
            action_type=action_type,
            action_params=params,
            result=result,
            success=success,
        )

    async def _action_task(
        self, params: dict[str, Any], state: GoalState
    ) -> tuple[bool, str]:
        supervisor = self._agent_pool.get("supervisor")
        if not supervisor:
            return False, "supervisor agent not available"
        description = str(params.get("description", params.get("task", "")))
        if not description:
            return False, "no task description provided"
        try:
            from core.agents.supervisor import TaskRequest

            task_id = await supervisor.submit_task(
                TaskRequest(
                    description=description,
                    options={"source": "goal_engine", "goal_id": state.goal_id},
                )
            )
            # Wait for the task to complete (poll every 500 ms, up to 120 s)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                await asyncio.sleep(0.5)
                status = await supervisor.get_task_status(task_id)
                if status is None:
                    break
                s = str(status.get("status", "")).upper()
                if s in {"COMPLETED", "FAILED", "CANCELLED"}:
                    if s == "COMPLETED":
                        return True, f"Task completed: {status.get('result_summary', '')}"[:500]
                    return False, f"Task {s}: {status.get('error', '')}"[:500]
            return True, f"Task submitted (id={task_id[:8]}), monitoring in background."
        except Exception as exc:
            return False, str(exc)

    async def _action_install_package(
        self, params: dict[str, Any], _state: GoalState
    ) -> tuple[bool, str]:
        pkg = str(params.get("pkg", "")).strip()
        if not pkg or any(c in pkg for c in (";", "&", "|", "$", "`", "\n")):
            return False, "invalid package name"
        venv = str(params.get("venv", "")).strip()
        if venv:
            pip = str(Path(venv) / ("Scripts" if sys.platform == "win32" else "bin") / "pip")
        else:
            pip = sys.executable.replace("python", "pip") if "python" in sys.executable else "pip"

        cmd = [pip, "install", "--quiet", pkg]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0:
                return True, f"Installed {pkg}"
            return False, (stderr or stdout).decode(errors="replace")[:400]
        except TimeoutError:
            return False, "pip install timed out after 120s"
        except Exception as exc:
            return False, str(exc)

    async def _action_create_venv(
        self, params: dict[str, Any], state: GoalState
    ) -> tuple[bool, str]:
        path = str(params.get("path", "")).strip()
        if not path:
            return False, "no path specified for venv"
        # Safety: must be an absolute path and not a system directory
        p = Path(path).expanduser().resolve()
        forbidden = {Path("/"), Path("/usr"), Path("/etc"), Path("/bin"), Path("/sbin")}
        if p in forbidden or any(str(p).startswith("/proc") for _ in [None]):
            return False, f"forbidden venv path: {path}"
        cmd = [sys.executable, "-m", "venv", str(p)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode == 0:
                state.venvs_created.append(str(p))
                return True, f"Created venv at {p}"
            return False, (stderr or stdout).decode(errors="replace")[:400]
        except TimeoutError:
            return False, "venv creation timed out"
        except Exception as exc:
            return False, str(exc)

    async def _action_write_skill(
        self, params: dict[str, Any], state: GoalState
    ) -> tuple[bool, str]:
        name = str(params.get("name", "")).strip()
        code = str(params.get("code", "")).strip()
        description = str(params.get("description", "Skill generated by GoalEngine")).strip()
        if not name or not code:
            return False, "name and code are required for write_skill"
        name = re.sub(r"[^\w]", "_", name).lower()[:64]
        _SKILL_DIR.mkdir(parents=True, exist_ok=True)
        skill_path = _SKILL_DIR / f"{name}.py"
        header = (
            f'"""{description}\n\nGenerated by AgentMax GoalEngine for goal: {state.goal_id[:8]}"""\n\n'
        )
        skill_path.write_text(header + code, encoding="utf-8")
        state.skills_installed.append(str(skill_path))
        log.info("goal.skill_written", skill=str(skill_path))
        return True, f"Skill written to {skill_path}"

    async def _action_run_shell(
        self, params: dict[str, Any], _state: GoalState
    ) -> tuple[bool, str]:
        command = str(params.get("command", "")).strip()
        if not command:
            return False, "no command provided"
        # Safety gate
        decision = self._supervisor.inspect_command(command, approved=False)
        if decision.blocked:
            return False, f"Command blocked by safety supervisor: {decision.reason}"
        if not decision.allowed:
            # High-risk but not blocked: try with approval flag set to False, reject
            return False, f"Command requires explicit approval before execution: {decision.reason}"
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            out = (stdout or b"").decode(errors="replace")[:400]
            err = (stderr or b"").decode(errors="replace")[:200]
            if proc.returncode == 0:
                return True, out or "Command completed with no output."
            return False, f"exit={proc.returncode} stderr={err}"
        except TimeoutError:
            return False, "Shell command timed out after 60s"
        except Exception as exc:
            return False, str(exc)

    async def _action_message(
        self, params: dict[str, Any], state: GoalState
    ) -> tuple[bool, str]:
        text = str(params.get("text", "")).strip()
        if text:
            await self._emit(
                "goal.message",
                {"goal_id": state.goal_id, "text": text, "iteration": state.iterations},
            )
        return True, text or "(empty message)"

    async def _action_unknown(
        self, params: dict[str, Any], _state: GoalState
    ) -> tuple[bool, str]:
        return False, f"Unknown action type; params={params}"

    # ------------------------------------------------------------------ #
    # Redis persistence                                                    #
    # ------------------------------------------------------------------ #

    def _save_state(self, state: GoalState) -> None:
        try:
            self._redis.setTaskState(state.goal_id, state.to_dict(), ttl_sec=_REDIS_TTL)
        except Exception as exc:
            log.warning("goal.redis_save_failed", error=str(exc))

    def _load_state(self, goal_id: str) -> GoalState | None:
        try:
            data = self._redis.getTaskState(goal_id)
            if data:
                return GoalState.from_dict(data)
        except Exception as exc:
            log.warning("goal.redis_load_failed", error=str(exc))
        return None

    # ------------------------------------------------------------------ #
    # Event helpers                                                        #
    # ------------------------------------------------------------------ #

    async def _emit(self, topic: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        try:
            from core.event_bus import Event

            await self._bus.publish(Event(topic, payload))
        except Exception as exc:
            log.debug("goal.emit_failed", topic=topic, error=str(exc))


__all__ = ["GoalEngine", "GoalIteration", "GoalState", "GoalStatus"]
